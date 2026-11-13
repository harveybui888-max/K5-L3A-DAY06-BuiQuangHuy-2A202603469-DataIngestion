# Báo cáo cá nhân — Day 06

## Dữ liệu và pipeline
- Chủ đề: Python Standard Library Documentation; 33 trang web độc lập, mỗi trang là một đơn vị nguồn.
- Nguồn/quyền: https://docs.python.org/3/library/; 3.15.2; Python Software Foundation License Version 2. Bản quyền và liên kết giấy phép được giữ tại `data/COPYRIGHT_NOTICE.txt`.
- Manifest/corpus: [`data/manifest.csv`](../../data/manifest.csv), [`data/python_docs.jsonl`](../../data/python_docs.jsonl); ID đơn vị (`unit_id`) được dùng làm `page` để truy vết trang HTML.
- Quy tắc tính: một trang tài liệu thư viện chuẩn độc lập là một đơn vị; giữ khi có ít nhất 1,500 ký tự sau chuẩn hóa; khoảng đo hiện tại 2,180–183,446 ký tự, tổng 1,626,433 ký tự.
- Chuẩn hóa: Unicode NFC, khoảng trắng nhất quán, giữ ranh giới đoạn và thụt lề; hash SHA-256 và thời điểm truy cập nằm trong manifest. Số lỗi crawl: 0; xem `data/crawl_errors.csv`.
- Embedding: `sentence-transformers/all-MiniLM-L6-v2`, 384 chiều, FastEmbed 0.9.1, chạy local trên `CPU (FastEmbed/ONNX Runtime)`; cùng model cho query và document.
- Vector store: Chroma 1.5.9, persistent, cosine distance; thư mục `starter/data/chroma/`. Chi phí dịch vụ embedding = 0; thời gian thực tế tùy phần cứng.
- Metadata: `document_id`, `source`, `page`/`unit_id`, `title`, `category`. `category` là nhãn chủ đề được gán theo module/path trong danh sách crawl, không phải tag chính thức từ Python; các giá trị: application-development, concurrency, data-formats, data-modeling, data-storage, data-structures, date-and-time, files-and-operating-system, networking, numeric-computing, process-management, runtime-and-imports, security-and-cryptography, testing, text-processing. Bộ lọc C dùng `category` do câu hỏi cung cấp.

## Cấu hình và benchmark
| Strategy | Chunking | Size tối đa (ký tự) | Overlap | Số chunk | Embed (s) | Persist (s) | Ingest (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| A | fixed character windows | 1000 | 150 | 2200 | 129.84 | 2.44 | 132.28 |
| B | paragraph grouping | 1200 | 100 | 1512 | 26.30 | 1.86 | 28.16 |
| C | same as B | 1200 | 100 | 1512 | 0.00 | 1.87 | 1.87 |

- A: fixed windows 1.000 ký tự, overlap 150.
- B: nhóm theo đoạn, tối đa 1.200 ký tự; đoạn dài được chia với overlap 100.
- C: cùng chunk, vectors và model với B; chỉ thêm filter `category` khi câu hỏi có filter. Vector của B được dùng lại để không thay đổi biến khác giữa B–C.
- Benchmark: 20 câu, 15 dev / 5 test; 3 câu filter, trong đó 1 câu test. Evidence được kiểm tra tự động bằng so khớp sau chuẩn hóa khoảng trắng với trang gold trước truy xuất.
- Tiêu chí chọn đã cố định trước khi chạy test: tối đa hóa Evidence Hit@3 trên dev; nếu hòa, ưu tiên Hit@1 dev, sau đó xem coherence/grounding thủ công và tài nguyên. Không dùng test để chỉnh cấu hình.
- Quy tắc chọn: hoàn tất nhãn audit và quyết định chiến lược từ dev trước khi diễn giải test; hiện mẫu audit bên dưới chưa được học viên chấm.

## Kết quả retrieval
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Ingest (s) | Search avg (ms) | Chi phí embedding |
|---|---|---|---|---:|---:|---:|---:|
| A | dev | 4/15 (26.7%) | 7/15 (46.7%) | 2200 | 132.28 s | 2.96 ms | 0 |
| A | test | 2/5 (40.0%) | 3/5 (60.0%) | 2200 | 132.28 s | 2.96 ms | 0 |
| B | dev | 4/15 (26.7%) | 8/15 (53.3%) | 1512 | 28.16 s | 3.23 ms | 0 |
| B | test | 2/5 (40.0%) | 4/5 (80.0%) | 1512 | 28.16 s | 3.23 ms | 0 |
| C | dev | 4/15 (26.7%) | 8/15 (53.3%) | 1512 | 1.87 s | 3.15 ms | 0 |
| C | test | 2/5 (40.0%) | 4/5 (80.0%) | 1512 | 1.87 s | 3.15 ms | 0 |

Metric là retrieval evidence, không phải độ chính xác câu trả lời do LLM tạo. Ingest là embedding cộng thời gian upsert; search avg là thời gian vector search trung bình, không gồm tạo query embedding. Thời gian chi tiết nằm trong `results/run_metadata_all.json`.

## Audit thủ công — cần hoàn tất
- Coherence: chấm cùng 10 unit ID đầu theo thứ tự, tại vị trí ký tự cố định; điền `results/manual_coherence_audit.csv` theo thang 0–2 và ghi lý do.
- Metadata/filter: kiểm tra source, page, category trên các kết quả top-3 test; điền `results/manual_metadata_audit.csv`. Bộ lọc được kiểm tra độc lập trên câu có filter.
- Grounding: chấm đủ bằng chứng top-3 cho 5 câu test mỗi strategy theo thang 0–2; điền `results/manual_grounding_audit.csv`.
- Kết quả và trace: [`results/metrics_all.json`](../../results/metrics_all.json), [`results/traces_all.jsonl`](../../results/traces_all.jsonl), log thí nghiệm `artifacts/experiment_log.csv`.
- Nhãn coherence/metadata/grounding hiện để trống có chủ đích; không suy diễn điểm audit từ metric tự động.

## Ví dụ lỗi / phân tích
- `q019` / A: Does Python's runtime enforce function and variable type annotations? — đã truy xuất đúng đơn vị nguồn nhưng top-3 không chứa đoạn evidence cần thiết; kết quả: library_typing (library_typing), library_typing (library_typing), library_typing (library_typing).
- `q020` / A: In unittest, what do setUp() and tearDown() let a test case do? — đã truy xuất đúng đơn vị nguồn nhưng top-3 không chứa đoạn evidence cần thiết; kết quả: library_unittest (library_unittest), library_unittest (library_unittest), library_unittest (library_unittest).

- So sánh A–B và B–C, đánh đổi, và lựa chọn cuối cùng: **chưa kết luận** cho đến khi audit thủ công được điền; đối chiếu kết quả dev trước, test chỉ báo cáo sau.
- So sánh đo được:
- A→B: dev Evidence Hit@3 7/15 → 8/15; test 3/5 → 4/5. Chunk count 2200 → 1512; thời gian embedding là 129.84 s → 26.30 s.
- B→C: dev: Evidence Hit@3 8/15 vs 8/15, Hit@1 4/15 vs 4/15; test: Evidence Hit@3 4/5 vs 4/5, Hit@1 2/5 vs 2/5. Trong 3 câu C có filter, category khớp ở 9/9 kết quả; đây là kiểm tra tự động, vẫn cần audit metadata thủ công.
- C dùng lại chunks và vectors của B, nên embedding gần 0 là thời gian cập nhật index biên, không phải tổng thời gian nạp mới độc lập.
- A và B/C có cùng model/corpus/benchmark; B tăng chunk theo đoạn và C chỉ thay filter. Test chỉ có 5 câu, nên chênh lệch không đại diện cho tài liệu Python nói chung.
- Hạn chế: corpus là lựa chọn con của tài liệu thư viện chuẩn Python 3.15.2; unit là trang HTML nên độ dài và chủ đề không đồng đều. Category là nhãn chủ đề do người làm gán; benchmark nhỏ, do cùng tác giả biên soạn, và audit thủ công còn cần hoàn tất. Model có thể cắt văn bản dài hơn giới hạn đầu vào.
- Lệnh tái lập: trong `starter\`, `python -m src.crawl_docs`, `python -m src.run --split dev`, sau đó `python -m src.run --split all`.
- AI hỗ trợ tạo/sửa code và hỗ trợ kiểm tra evidence; cần người học hoàn thành nhãn audit và tự giải thích kết luận.
