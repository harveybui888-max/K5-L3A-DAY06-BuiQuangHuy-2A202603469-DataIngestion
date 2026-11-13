"""Build three retrieval strategies, persist vectors, and write benchmark artifacts."""

import argparse
import csv
import json
import statistics
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .chunking import by_paragraph, fixed_size
from .embeddings import FastEmbedder
from .evaluation import evaluate_query, summarize, write_jsonl
from .ingestion import load_corpus, normalize_text
from .models import Chunk, DocumentPage
from .store import ChromaVectorStore

STARTER_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = STARTER_DIR.parent
DEFAULT_CORPUS = REPO_DIR / "data" / "python_docs.jsonl"
DEFAULT_BENCHMARK = REPO_DIR / "benchmark" / "questions.jsonl"
DEFAULT_STORE = STARTER_DIR / "data" / "chroma"
RESULTS_DIR = REPO_DIR / "results"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TOP_K = 3
MIN_UNITS = 30
MIN_UNIT_CHARACTERS = 1500


def _read_benchmark(path: Path, pages: list[DocumentPage]) -> list[dict[str, Any]]:
    pages_by_id = {page.document_id: page for page in pages}
    questions = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                question = json.loads(line)
                required = {
                    "id", "split", "question", "expected_answer", "gold_source_id",
                    "gold_page", "evidence_quote", "filter",
                }
                missing = required - question.keys()
                if missing:
                    raise ValueError(f"missing fields: {sorted(missing)}")
                if question["split"] not in {"dev", "test"}:
                    raise ValueError("split must be 'dev' or 'test'")
                if question["gold_source_id"] not in pages_by_id:
                    raise ValueError(f"unknown gold source: {question['gold_source_id']}")
                gold_page = pages_by_id[question["gold_source_id"]]
                if str(question["gold_page"]) != gold_page.page:
                    raise ValueError("gold_page does not match the cited source unit")
                normalized_quote = " ".join(normalize_text(question["evidence_quote"]).split())
                normalized_source = " ".join(normalize_text(gold_page.text).split())
                if normalized_quote.casefold() not in normalized_source.casefold():
                    raise ValueError("evidence_quote is not present verbatim in the cited source")
                filters = question["filter"]
                if filters and any(key != "category" for key in filters):
                    raise ValueError("only the documented category filter is supported")
                if filters and filters.get("category") != gold_page.metadata["category"]:
                    raise ValueError("filter category does not match the gold source")
                questions.append(question)
            except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid benchmark record at line {line_number}: {exc}") from exc

    ids = [question["id"] for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Benchmark question IDs must be unique")
    dev_count = sum(question["split"] == "dev" for question in questions)
    test_count = sum(question["split"] == "test" for question in questions)
    filter_questions = [question for question in questions if question["filter"]]
    if len(questions) < 20 or dev_count < 15 or test_count < 5:
        raise ValueError(
            f"Need >=20 questions, >=15 dev and >=5 test; got {len(questions)}, "
            f"{dev_count} dev, {test_count} test"
        )
    if len(filter_questions) < 3 or not any(
        question["split"] == "test" for question in filter_questions
    ):
        raise ValueError("Need >=3 metadata-filter questions, including >=1 test question")
    return questions


def _build_strategy_chunks(pages: list[DocumentPage]) -> dict[str, list[Chunk]]:
    return {
        "A": [chunk for page in pages for chunk in fixed_size(page, size=1000, overlap=150)],
        "B": [chunk for page in pages for chunk in by_paragraph(page, max_size=1200, overlap=100)],
        "C": [],
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _manual_audits(
    pages: list[DocumentPage],
    chunks_by_strategy: dict[str, list[Chunk]],
    traces: list[dict[str, Any]],
) -> None:
    pages_by_id = {page.document_id: page for page in pages}
    metadata_rows = []
    grounding_rows = []
    coherence_rows = []

    for trace in traces:
        if trace["split"] != "test":
            continue
        question = trace["question"]
        for rank, hit in enumerate(trace["hits"], start=1):
            metadata = hit["metadata"]
            expected_page = pages_by_id[str(metadata["document_id"])]
            metadata_rows.append(
                {
                    "strategy": trace["strategy"],
                    "question_id": question["id"],
                    "rank": rank,
                    "chunk_id": hit["chunk_id"],
                    "source": metadata.get("source", ""),
                    "expected_source": expected_page.source,
                    "page": metadata.get("page", ""),
                    "expected_page": expected_page.page,
                    "category": metadata.get("category", ""),
                    "expected_category": expected_page.metadata["category"],
                    "question_filter": json.dumps(question["filter"], ensure_ascii=False),
                    "filter_compliant_0_or_1": "",
                    "metadata_correct_0_or_1": "",
                    "review_notes": "",
                }
            )
        grounding_rows.append(
            {
                "strategy": trace["strategy"],
                "question_id": question["id"],
                "question": question["question"],
                "gold_source_id": question["gold_source_id"],
                "evidence_quote": question["evidence_quote"],
                "top3_evidence_json": json.dumps(
                    [
                        {
                            "rank": rank,
                            "chunk_id": hit["chunk_id"],
                            "source": hit["metadata"].get("source"),
                            "page": hit["metadata"].get("page"),
                            "text": hit["text"],
                        }
                        for rank, hit in enumerate(trace["hits"], start=1)
                    ],
                    ensure_ascii=False,
                ),
                "grounding_score_0_1_or_2": "",
                "reason": "",
            }
        )

    sample_units = sorted(pages, key=lambda page: page.document_id)[:10]
    for strategy in ("A", "B", "C"):
        strategy_chunks = chunks_by_strategy[strategy]
        for page in sample_units:
            anchor = len(page.text) // 2
            while page.text[anchor].isspace():
                if anchor + 1 < len(page.text) and not page.text[anchor + 1].isspace():
                    anchor += 1
                elif anchor > 0:
                    anchor -= 1
                else:
                    raise RuntimeError(f"No non-whitespace audit position in {page.document_id}")
            selected = next(
                (
                    chunk
                    for chunk in strategy_chunks
                    if chunk.document_id == page.document_id
                    and int(chunk.metadata["start_char"]) <= anchor
                    < int(chunk.metadata["end_char"])
                ),
                None,
            )
            if selected is None:
                raise RuntimeError(
                    f"No {strategy} chunk covers the preselected anchor in {page.document_id}"
                )
            coherence_rows.append(
                {
                    "strategy": strategy,
                    "unit_id": page.document_id,
                    "anchor_char": anchor,
                    "chunk_id": selected.chunk_id,
                    "chunk_text": selected.text,
                    "score_0_1_or_2": "",
                    "reason": "",
                }
            )

    _write_csv(
        RESULTS_DIR / "manual_metadata_audit.csv",
        [
            "strategy", "question_id", "rank", "chunk_id", "source", "expected_source",
            "page", "expected_page", "category", "expected_category", "question_filter",
            "filter_compliant_0_or_1", "metadata_correct_0_or_1", "review_notes",
        ],
        metadata_rows,
    )
    _write_csv(
        RESULTS_DIR / "manual_grounding_audit.csv",
        [
            "strategy", "question_id", "question", "gold_source_id", "evidence_quote",
            "top3_evidence_json", "grounding_score_0_1_or_2", "reason",
        ],
        grounding_rows,
    )
    _write_csv(
        RESULTS_DIR / "manual_coherence_audit.csv",
        [
            "strategy", "unit_id", "anchor_char", "chunk_id", "chunk_text",
            "score_0_1_or_2", "reason",
        ],
        coherence_rows,
    )


def _write_experiment_log(
    strategy_runs: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    model_name: str,
) -> None:
    path = REPO_DIR / "artifacts" / "experiment_log.csv"
    fields = [
        "strategy", "chunking", "chunk_size", "overlap", "metadata", "filter",
        "vectorizer", "hypothesis", "dev_hit1", "dev_hit3", "test_hit1", "test_hit3",
        "notes",
    ]
    rows = []
    for strategy, details in strategy_runs.items():
        config = details["configuration"]
        strategy_metrics = metrics.get(strategy, {})
        dev = strategy_metrics.get("dev", {})
        test = strategy_metrics.get("test", {})
        rows.append(
            {
                "strategy": strategy,
                "chunking": config["chunking"],
                "chunk_size": config["chunk_size"],
                "overlap": config["overlap"],
                "metadata": "document_id, source, page, unit_id, category, title",
                "filter": "category on filtered C questions only" if strategy == "C" else "none",
                "vectorizer": model_name,
                "hypothesis": config["hypothesis"],
                "dev_hit1": f"{dev.get('hit_at_1', {}).get('hits', 0)}/{dev.get('hit_at_1', {}).get('total', 0)}",
                "dev_hit3": f"{dev.get('evidence_hit_at_3', {}).get('hits', 0)}/{dev.get('evidence_hit_at_3', {}).get('total', 0)}",
                "test_hit1": f"{test.get('hit_at_1', {}).get('hits', 0)}/{test.get('hit_at_1', {}).get('total', 0)}",
                "test_hit3": f"{test.get('evidence_hit_at_3', {}).get('hits', 0)}/{test.get('evidence_hit_at_3', {}).get('total', 0)}",
                "notes": (
                    f"chunks={details['chunk_count']}; "
                    f"embedding_seconds={details['embedding_seconds']:.2f}; "
                    f"persist_seconds={details['persist_seconds']:.2f}; "
                    f"ingest_seconds={details['ingest_seconds']:.2f}; "
                    f"average_vector_search_ms={details['average_vector_search_seconds'] * 1000:.2f}; "
                    "local embedding cost=0"
                ),
            }
        )
    _write_csv(path, fields, rows)


def _write_individual_report(
    pages: list[DocumentPage],
    questions: list[dict[str, Any]],
    strategy_runs: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    traces: list[dict[str, Any]],
    run_metadata: dict[str, Any],
) -> None:
    report_path = REPO_DIR / "report" / "individual" / "2A202603469_BuiQuangHuy.md"
    errors_path = REPO_DIR / "data" / "crawl_errors.csv"
    min_characters = min(len(page.text) for page in pages)
    max_characters = max(len(page.text) for page in pages)
    version_label = pages[0].metadata.get("documentation_version", "not recorded")
    categories = ", ".join(sorted({str(page.metadata["category"]) for page in pages}))
    test_filter_count = sum(
        question["split"] == "test" and bool(question["filter"])
        for question in questions
    )
    a_dev = metrics.get("A", {}).get("dev", {})
    b_dev = metrics.get("B", {}).get("dev", {})
    a_test = metrics.get("A", {}).get("test", {})
    b_test = metrics.get("B", {}).get("test", {})
    b_c_deltas = []
    for split_name in ("dev", "test"):
        b_metrics = metrics.get("B", {}).get(split_name)
        c_metrics = metrics.get("C", {}).get(split_name)
        if b_metrics and c_metrics:
            b_c_deltas.append(
                f"{split_name}: Evidence Hit@3 "
                f"{b_metrics['evidence_hit_at_3']['hits']}/{b_metrics['evidence_hit_at_3']['total']} "
                f"vs {c_metrics['evidence_hit_at_3']['hits']}/{c_metrics['evidence_hit_at_3']['total']}, "
                f"Hit@1 {b_metrics['hit_at_1']['hits']}/{b_metrics['hit_at_1']['total']} "
                f"vs {c_metrics['hit_at_1']['hits']}/{c_metrics['hit_at_1']['total']}"
            )
    filtered_c_traces = [
        trace
        for trace in traces
        if trace["strategy"] == "C" and trace["filter_applied"]
    ]
    filtered_c_hits = [
        hit
        for trace in filtered_c_traces
        for hit in trace["hits"]
    ]
    compliant_c_hits = sum(
        hit["metadata"].get("category") == trace["filter_applied"]["category"]
        for trace in filtered_c_traces
        for hit in trace["hits"]
    )
    comparison_lines = [
        f"- A→B: dev Evidence Hit@3 {a_dev.get('evidence_hit_at_3', {}).get('hits', 0)}/"
        f"{a_dev.get('evidence_hit_at_3', {}).get('total', 0)} → "
        f"{b_dev.get('evidence_hit_at_3', {}).get('hits', 0)}/"
        f"{b_dev.get('evidence_hit_at_3', {}).get('total', 0)}; test "
        f"{a_test.get('evidence_hit_at_3', {}).get('hits', 0)}/"
        f"{a_test.get('evidence_hit_at_3', {}).get('total', 0)} → "
        f"{b_test.get('evidence_hit_at_3', {}).get('hits', 0)}/"
        f"{b_test.get('evidence_hit_at_3', {}).get('total', 0)}. "
        f"Chunk count {strategy_runs['A']['chunk_count']} → "
        f"{strategy_runs['B']['chunk_count']}; thời gian embedding là "
        f"{strategy_runs['A']['embedding_seconds']:.2f} s → "
        f"{strategy_runs['B']['embedding_seconds']:.2f} s.",
        f"- B→C: {'; '.join(b_c_deltas)}. Trong {len(filtered_c_traces)} câu C có filter, "
        f"category khớp ở {compliant_c_hits}/{len(filtered_c_hits)} kết quả; "
        "đây là kiểm tra tự động, vẫn cần audit metadata thủ công.",
        "- C dùng lại chunks và vectors của B, nên embedding gần 0 là thời gian cập nhật "
        "index biên, không phải tổng thời gian nạp mới độc lập.",
    ]
    with errors_path.open(encoding="utf-8-sig") as file:
        crawl_error_count = max(0, sum(1 for _ in file) - 1)
    strategy_lines = []
    for strategy in ("A", "B", "C"):
        config = strategy_runs[strategy]["configuration"]
        details = strategy_runs[strategy]
        strategy_lines.append(
            f"| {strategy} | {config['chunking']} | {config['chunk_size']} | "
            f"{config['overlap']} | {details['chunk_count']} | "
            f"{details['embedding_seconds']:.2f} | {details['persist_seconds']:.2f} | "
            f"{details['ingest_seconds']:.2f} |"
        )
    result_lines = []
    for strategy in ("A", "B", "C"):
        for split_name in ("dev", "test"):
            result = metrics.get(strategy, {}).get(split_name)
            if result is None:
                continue
            hit1 = result["hit_at_1"]
            hit3 = result["evidence_hit_at_3"]
            details = strategy_runs[strategy]
            result_lines.append(
                f"| {strategy} | {split_name} | {hit1['hits']}/{hit1['total']} "
                f"({hit1['rate']:.1%}) | {hit3['hits']}/{hit3['total']} "
                f"({hit3['rate']:.1%}) | {details['chunk_count']} | "
                f"{details['ingest_seconds']:.2f} s | "
                f"{details['average_vector_search_seconds'] * 1000:.2f} ms | 0 |"
            )
    misses = [
        trace
        for trace in traces
        if trace["split"] == "test" and not trace["metrics"]["evidence_hit_at_3"]
    ]
    error_lines = []
    seen_question_ids: set[str] = set()
    for trace in misses:
        question = trace["question"]
        if question["id"] in seen_question_ids:
            continue
        seen_question_ids.add(question["id"])
        returned = ", ".join(
            f"{hit['metadata'].get('document_id')} ({hit['metadata'].get('page')})"
            for hit in trace["hits"]
        ) or "không có kết quả"
        gold_unit_ranked = any(
            hit["metadata"].get("document_id") == question["gold_source_id"]
            for hit in trace["hits"]
        )
        reason = (
            "đã truy xuất đúng đơn vị nguồn nhưng top-3 không chứa đoạn evidence cần thiết"
            if gold_unit_ranked
            else "top-3 không có đơn vị nguồn gold"
        )
        error_lines.append(
            f"- `{question['id']}` / {trace['strategy']}: {question['question']} "
            f"— {reason}; kết quả: {returned}."
        )
        if len(error_lines) == 2:
            break
    if not error_lines:
        error_lines.append(
            "- Không có câu test nào thất bại theo Evidence Hit@3 tự động; "
            "hãy chọn hai trường hợp cần thảo luận từ trace sau khi audit thủ công."
        )
    report = f"""# Báo cáo cá nhân — Day 06

## Dữ liệu và pipeline
- Chủ đề: Python Standard Library Documentation; {len(pages)} trang web độc lập, mỗi trang là một đơn vị nguồn.
- Nguồn/quyền: https://docs.python.org/3/library/; {version_label}; Python Software Foundation License Version 2. Bản quyền và liên kết giấy phép được giữ tại `data/COPYRIGHT_NOTICE.txt`.
- Manifest/corpus: [`data/manifest.csv`](../../data/manifest.csv), [`data/python_docs.jsonl`](../../data/python_docs.jsonl); ID đơn vị (`unit_id`) được dùng làm `page` để truy vết trang HTML.
- Quy tắc tính: một trang tài liệu thư viện chuẩn độc lập là một đơn vị; giữ khi có ít nhất {MIN_UNIT_CHARACTERS:,} ký tự sau chuẩn hóa; khoảng đo hiện tại {min_characters:,}–{max_characters:,} ký tự, tổng {sum(len(page.text) for page in pages):,} ký tự.
- Chuẩn hóa: Unicode NFC, khoảng trắng nhất quán, giữ ranh giới đoạn và thụt lề; hash SHA-256 và thời điểm truy cập nằm trong manifest. Số lỗi crawl: {crawl_error_count}; xem `data/crawl_errors.csv`.
- Embedding: `{run_metadata['model']}`, {run_metadata['model_dimensions']} chiều, FastEmbed {run_metadata['package_versions'].get('fastembed')}, chạy local trên `{run_metadata['device']}`; cùng model cho query và document.
- Vector store: Chroma {run_metadata['package_versions'].get('chromadb')}, persistent, cosine distance; thư mục `starter/data/chroma/`. Chi phí dịch vụ embedding = 0; thời gian thực tế tùy phần cứng.
- Metadata: `document_id`, `source`, `page`/`unit_id`, `title`, `category`. `category` là nhãn chủ đề được gán theo module/path trong danh sách crawl, không phải tag chính thức từ Python; các giá trị: {categories}. Bộ lọc C dùng `category` do câu hỏi cung cấp.

## Cấu hình và benchmark
| Strategy | Chunking | Size tối đa (ký tự) | Overlap | Số chunk | Embed (s) | Persist (s) | Ingest (s) |
|---|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(strategy_lines)}

- A: fixed windows 1.000 ký tự, overlap 150.
- B: nhóm theo đoạn, tối đa 1.200 ký tự; đoạn dài được chia với overlap 100.
- C: cùng chunk, vectors và model với B; chỉ thêm filter `category` khi câu hỏi có filter. Vector của B được dùng lại để không thay đổi biến khác giữa B–C.
- Benchmark: {len(questions)} câu, {sum(question['split'] == 'dev' for question in questions)} dev / {sum(question['split'] == 'test' for question in questions)} test; {sum(bool(question['filter']) for question in questions)} câu filter, trong đó {test_filter_count} câu test. Evidence được kiểm tra tự động bằng so khớp sau chuẩn hóa khoảng trắng với trang gold trước truy xuất.
- Tiêu chí chọn đã cố định trước khi chạy test: tối đa hóa Evidence Hit@3 trên dev; nếu hòa, ưu tiên Hit@1 dev, sau đó xem coherence/grounding thủ công và tài nguyên. Không dùng test để chỉnh cấu hình.
- Quy tắc chọn: hoàn tất nhãn audit và quyết định chiến lược từ dev trước khi diễn giải test; hiện mẫu audit bên dưới chưa được học viên chấm.

## Kết quả retrieval
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Ingest (s) | Search avg (ms) | Chi phí embedding |
|---|---|---|---|---:|---:|---:|---:|
{chr(10).join(result_lines)}

Metric là retrieval evidence, không phải độ chính xác câu trả lời do LLM tạo. Ingest là embedding cộng thời gian upsert; search avg là thời gian vector search trung bình, không gồm tạo query embedding. Thời gian chi tiết nằm trong `results/run_metadata_all.json`.

## Audit thủ công — cần hoàn tất
- Coherence: chấm cùng 10 unit ID đầu theo thứ tự, tại vị trí ký tự cố định; điền `results/manual_coherence_audit.csv` theo thang 0–2 và ghi lý do.
- Metadata/filter: kiểm tra source, page, category trên các kết quả top-3 test; điền `results/manual_metadata_audit.csv`. Bộ lọc được kiểm tra độc lập trên câu có filter.
- Grounding: chấm đủ bằng chứng top-3 cho 5 câu test mỗi strategy theo thang 0–2; điền `results/manual_grounding_audit.csv`.
- Kết quả và trace: [`results/metrics_all.json`](../../results/metrics_all.json), [`results/traces_all.jsonl`](../../results/traces_all.jsonl), log thí nghiệm `artifacts/experiment_log.csv`.
- Nhãn coherence/metadata/grounding hiện để trống có chủ đích; không suy diễn điểm audit từ metric tự động.

## Ví dụ lỗi / phân tích
{chr(10).join(error_lines)}

- So sánh A–B và B–C, đánh đổi, và lựa chọn cuối cùng: **chưa kết luận** cho đến khi audit thủ công được điền; đối chiếu kết quả dev trước, test chỉ báo cáo sau.
- So sánh đo được:
{chr(10).join(comparison_lines)}
- A và B/C có cùng model/corpus/benchmark; B tăng chunk theo đoạn và C chỉ thay filter. Test chỉ có 5 câu, nên chênh lệch không đại diện cho tài liệu Python nói chung.
- Hạn chế: corpus là lựa chọn con của tài liệu thư viện chuẩn Python {version_label}; unit là trang HTML nên độ dài và chủ đề không đồng đều. Category là nhãn chủ đề do người làm gán; benchmark nhỏ, do cùng tác giả biên soạn, và audit thủ công còn cần hoàn tất. Model có thể cắt văn bản dài hơn giới hạn đầu vào.
- Lệnh tái lập: trong `starter\\`, `python -m src.crawl_docs`, `python -m src.run --split dev`, sau đó `python -m src.run --split all`.
- AI hỗ trợ tạo/sửa code và hỗ trợ kiểm tra evidence; cần người học hoàn thành nhãn audit và tự giải thích kết luận.
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")


def run(
    corpus_path: Path = DEFAULT_CORPUS,
    benchmark_path: Path = DEFAULT_BENCHMARK,
    store_path: Path = DEFAULT_STORE,
    split: str = "all",
) -> dict[str, Any]:
    pages = load_corpus(corpus_path)
    invalid_units = [
        page.document_id for page in pages if len(page.text) < MIN_UNIT_CHARACTERS
    ]
    if len(pages) < MIN_UNITS or invalid_units:
        raise ValueError(
            f"Corpus needs >=30 units each >=1,500 normalized characters; "
            f"got {len(pages)} units and {len(invalid_units)} short units."
        )
    questions = _read_benchmark(benchmark_path, pages)
    if split != "all":
        questions = [question for question in questions if question["split"] == split]
    if not questions:
        raise ValueError(f"No benchmark questions found for split {split!r}")

    embedder = FastEmbedder(MODEL_NAME)
    store = ChromaVectorStore(store_path, embedder.dimensions)
    chunks_by_strategy = _build_strategy_chunks(pages)
    chunks_by_strategy["C"] = chunks_by_strategy["B"]
    vectors_by_strategy: dict[str, list[list[float]]] = {}
    strategy_runs: dict[str, dict[str, Any]] = {}
    traces = []
    configurations = {
        "A": {
            "chunking": "fixed character windows",
            "chunk_size": 1000,
            "overlap": 150,
            "hypothesis": "Smaller fixed windows provide focused evidence.",
        },
        "B": {
            "chunking": "paragraph grouping",
            "chunk_size": 1200,
            "overlap": 100,
            "hypothesis": "Paragraph-aware chunks preserve self-contained ideas.",
        },
        "C": {
            "chunking": "same as B",
            "chunk_size": 1200,
            "overlap": 100,
            "hypothesis": "Category metadata filters improve scoped retrieval.",
        },
    }

    for strategy in ("A", "B", "C"):
        chunks = chunks_by_strategy[strategy]
        embedding_start = time.perf_counter()
        if strategy == "C":
            vectors = vectors_by_strategy["B"]
        else:
            vectors = embedder.embed_documents([chunk.text for chunk in chunks])
            vectors_by_strategy[strategy] = vectors
        embedding_seconds = time.perf_counter() - embedding_start
        persist_start = time.perf_counter()
        count = store.upsert(strategy, chunks, vectors)
        persist_seconds = time.perf_counter() - persist_start
        lengths = [len(chunk.text) for chunk in chunks]
        strategy_runs[strategy] = {
            "configuration": configurations[strategy],
            "chunk_count": count,
            "total_chunk_characters": sum(lengths),
            "median_chunk_characters": statistics.median(lengths) if lengths else 0,
            "embedding_seconds": embedding_seconds,
            "persist_seconds": persist_seconds,
            "embedding_cost": 0.0,
        }

        for question in questions:
            applied_filter = question["filter"] if strategy == "C" else {}
            query_vector = embedder.embed_query(question["question"])
            search_start = time.perf_counter()
            hits = store.search(
                strategy,
                query_vector,
                top_k=TOP_K,
                filters=applied_filter,
            )
            search_seconds = time.perf_counter() - search_start
            traces.append(
                {
                    "strategy": strategy,
                    "split": question["split"],
                    "question": question,
                    "filter_applied": applied_filter,
                    "hits": hits,
                    "metrics": evaluate_query(question, hits),
                    "search_seconds": search_seconds,
                }
            )

    for strategy, details in strategy_runs.items():
        search_times = [
            trace["search_seconds"]
            for trace in traces
            if trace["strategy"] == strategy
        ]
        details["ingest_seconds"] = details["embedding_seconds"] + details["persist_seconds"]
        details["average_vector_search_seconds"] = (
            sum(search_times) / len(search_times) if search_times else 0.0
        )

    metrics = summarize(traces)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    trace_path = RESULTS_DIR / f"traces_{split}.jsonl"
    metrics_path = RESULTS_DIR / f"metrics_{split}.json"
    write_jsonl(trace_path, traces)
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    run_metadata = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model": embedder.model_name,
        "model_dimensions": embedder.dimensions,
        "device": "CPU (FastEmbed/ONNX Runtime)",
        "vector_store": "Chroma persistent, cosine distance",
        "corpus_units": len(pages),
        "corpus_total_characters": sum(len(page.text) for page in pages),
        "benchmark_questions": len(questions),
        "split": split,
        "top_k": TOP_K,
        "strategies": strategy_runs,
        "metrics": metrics,
        "package_versions": {},
        "trace_file": str(trace_path.relative_to(REPO_DIR)),
        "manual_assessments": "Templates generated; student labels are intentionally blank.",
    }
    for distribution in ("chromadb", "fastembed"):
        try:
            run_metadata["package_versions"][distribution] = version(distribution)
        except PackageNotFoundError:
            run_metadata["package_versions"][distribution] = "not found"
    (RESULTS_DIR / f"run_metadata_{split}.json").write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if split == "all":
        _manual_audits(pages, chunks_by_strategy, traces)
        _write_individual_report(
            pages,
            questions,
            strategy_runs,
            metrics,
            traces,
            run_metadata,
        )
    _write_experiment_log(strategy_runs, metrics, embedder.model_name)
    return run_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--store-dir", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--crawl", action="store_true", help="refresh the official-doc corpus first")
    args = parser.parse_args()
    if args.crawl:
        from .crawl_docs import crawl

        print(json.dumps(crawl(args.corpus.parent), ensure_ascii=False, indent=2))
    result = run(args.corpus, args.benchmark, args.store_dir, args.split)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
