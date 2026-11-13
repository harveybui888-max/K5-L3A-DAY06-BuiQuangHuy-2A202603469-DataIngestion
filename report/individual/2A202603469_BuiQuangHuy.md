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
- Tiêu chí chọn đã cố định trước khi chạy test: tối đa hóa Evidence Hit@3 trên dev; nếu hòa, ưu tiên Hit@1 dev, rồi coherence trên 10 vị trí nguồn đã xác định trước và tài nguyên. Không dùng điểm retrieval test để chỉnh cấu hình.
- Quyết định theo dev: B và C hòa ở Hit@1 và Evidence Hit@3; chọn B làm mặc định vì cùng chất lượng retrieval mà không phụ thuộc filter. Giữ C cho trường hợp có category filter đáng tin; kết quả test chỉ dùng để báo cáo/kiểm chứng, không để đổi cấu hình.

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

## Audit chất lượng và tự chấm

| Khía cạnh | A | B | C | Cách hiểu |
|---|---:|---:|---:|---|
| Coherence trung bình /2 (10 vị trí nguồn) | 1.0/2 | 1.6/2 | 1.6/2 | A thường cắt vào giữa câu/đoạn; B/C giữ được ranh giới đoạn tốt hơn. |
| Metadata đủ và khớp manifest | 45/45 | 45/45 | 45/45 | Đã kiểm tra source, unit/page, category ở top-3 của 5 câu test. |
| Tuân thủ category filter | 1/1 câu; 3/3 hit | 1/1 câu; 3/3 hit | 1/1 câu; 3/3 hit | Top-3 của câu test có filter đều đúng category. A/B không chạy filter; việc kết quả tình cờ cùng category không chứng minh A/B có chức năng lọc. C mới áp dụng filter thật. |
| Grounding: số câu mức 0 / 1 / 2 | 1 / 1 / 3; TB 1.4/2 | 1 / 0 / 4; TB 1.6/2 | 1 / 0 / 4; TB 1.6/2 | Chấm tổng hợp bằng chứng top-3 cho 5 câu test; q019 không có bằng chứng đủ ở cả ba cấu hình. |

- Coherence: cùng 10 unit ID đầu theo thứ tự, tại vị trí ký tự được cố định trước khi xem điểm; từng nhãn và lý do nằm trong `results/manual_coherence_audit.csv`.
- Metadata/filter: 45 kết quả top-3 test mỗi cấu hình được kiểm tra; danh sách chi tiết ở `results/manual_metadata_audit.csv`. `category` giúp thu hẹp phạm vi, nhưng là nhãn chủ đề gán theo module/path chứ không phải taxonomy chính thức từ Python.
- Grounding: chấm 5 câu test cho mỗi cấu hình theo thang 0–2; từng điểm và lý do ở `results/manual_grounding_audit.csv`. Đây là retrieval evidence, không phải độ chính xác câu trả lời LLM.
- Phương pháp/nguy cơ thiên lệch: đây là tự đánh giá do trợ lý AI thực hiện thay mặt học viên theo yêu cầu, dựa trên text top-3, source/page và manifest; không có người chấm độc lập. Học viên cần rà soát và xác nhận nhãn trước khi nộp.
- Kết quả và trace: [`results/metrics_all.json`](../../results/metrics_all.json), [`results/traces_all.jsonl`](../../results/traces_all.jsonl), log thí nghiệm `artifacts/experiment_log.csv`.

## Ví dụ lỗi / phân tích
- `q019` / A/B/C: “Does Python's runtime enforce function and variable type annotations?” — top-3 đều thuộc đúng trang `library_typing` nhưng nói về introspection/metadata, không có bằng chứng trả lời việc runtime có enforce annotation hay không; grounding 0/2 ở cả ba.
- `q020` / A: “In unittest, what do setUp() and tearDown() let a test case do?” — có thông tin về setUp và tearDown rải qua top-3 nhưng thiếu bằng chứng đầy đủ rằng hai hàm thiết lập/chạy trước và sau mỗi test; grounding 1/2. B/C đưa được câu giải thích trực tiếp vào top-1 nên đạt 2/2.

- So sánh A–B và B–C, đánh đổi, và lựa chọn cuối cùng:
- So sánh đo được:
- A→B: dev Evidence Hit@3 7/15 → 8/15; test 3/5 → 4/5. Chunk count 2200 → 1512; thời gian embedding là 129.84 s → 26.30 s.
- B→C: dev: Evidence Hit@3 8/15 vs 8/15, Hit@1 4/15 vs 4/15; test: Evidence Hit@3 4/5 vs 4/5, Hit@1 2/5 vs 2/5. Trong 3 câu C có filter, category khớp ở 9/9 kết quả; đây là kiểm tra tự động, vẫn cần audit metadata thủ công.
- C dùng lại chunks và vectors của B, nên embedding gần 0 là thời gian cập nhật index biên, không phải tổng thời gian nạp mới độc lập.
- Coherence và grounding ủng hộ B/C hơn A trên các mẫu đã chấm; metadata truy vết đúng ở 45/45 kết quả cho cả ba. Filter C có tác dụng ràng buộc category, nhưng trên aggregate dev không tăng Hit@1/Hit@3 so với B.
- Lựa chọn: dùng B làm mặc định theo kết quả dev; bật C khi ngữ cảnh thực sự cung cấp category và cần giới hạn phạm vi. Đây là lựa chọn cho corpus/thử nghiệm này, không tuyên bố tốt nhất tổng quát.
- Hạn chế: corpus là lựa chọn con của tài liệu thư viện chuẩn Python 3.15.2; unit là trang HTML nên độ dài và chủ đề không đồng đều. Category do người làm gán, benchmark chỉ có 20 câu và test có 5 câu; cùng người tạo benchmark/corpus tự đánh giá gây thiên lệch. A có các trường hợp đúng trang nhưng chunk top-3 cắt mất đoạn evidence; q019 không được truy hồi đủ trong cả ba chiến lược. Model có thể cắt văn bản dài hơn giới hạn đầu vào; thời gian C chỉ là chi phí cập nhật biên do dùng lại vector B, không phải cold rebuild.
- Nếu làm lại hoặc mở rộng: (1) tăng benchmark và chia test theo module/category, thêm nhiều câu paraphrase, điều kiện/ngoại lệ và cặp câu có/không filter; giữ test mới hoàn toàn holdout. (2) Chuyển chunking sang ranh giới heading/đoạn kết hợp giới hạn theo token của model; thử lấy thêm chunk lân cận để cứu evidence bị cắt, đặc biệt cho q019 và q020. (3) Kiểm tra tác động filter trên tập câu có filter lớn hơn và so sánh B/C theo từng nhóm thay vì chỉ metric gộp. (4) Nhờ người chấm thứ hai chấm coherence/grounding độc lập, so sánh bất đồng và ghi độ nhất quán. (5) Pin model revision/dependency versions, lưu snapshot + hash và đo cả cold ingest lẫn incremental update riêng.
- Lệnh tái lập: trong `starter\`, `python -m src.crawl_docs`, `python -m src.run --split dev`, sau đó `python -m src.run --split all`.