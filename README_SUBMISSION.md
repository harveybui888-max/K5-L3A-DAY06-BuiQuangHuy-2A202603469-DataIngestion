# README bài nộp — Python Documentation Data Ingestion

Bài làm crawl tài liệu thư viện chuẩn từ [Python Official Documentation](https://docs.python.org/3/library/), chuẩn hóa nội dung, chia chunk và so sánh ba chiến lược truy xuất. Hướng dẫn này bổ sung cách chạy cho bài nộp; không thay thế các README hiện có trong repo.

## Yêu cầu

- Windows PowerShell, Python 3.11 trở lên và kết nối Internet lần đầu để crawl tài liệu, tải thư viện và embedding model.
- Embedding chạy local qua FastEmbed/ONNX Runtime, không cần API key. Model mặc định là `sentence-transformers/all-MiniLM-L6-v2` (384 chiều); model được tải vào cache người dùng khi chạy lần đầu.
- Vector store Chroma được lưu bền vững ở `starter/data/chroma/`. Không cần Docker để chạy pipeline này.

## Cài đặt

Chạy tại thư mục gốc repo:

```powershell
Set-Location starter
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Nếu PowerShell chặn kích hoạt môi trường ảo, có thể gọi trực tiếp `.\.venv\Scripts\python.exe` thay cho `python` trong các lệnh bên dưới.

## Crawl dữ liệu

Từ thư mục `starter\`:

```powershell
python -m src.crawl_docs
```

Crawler kiểm tra `robots.txt`, lấy các trang tài liệu đã chọn từ `docs.python.org`, chuẩn hóa nội dung và chỉ giữ đơn vị có ít nhất 1.500 ký tự. Dữ liệu được ghi ở thư mục gốc repo:

- `data/python_docs.jsonl`: một trang tài liệu cho mỗi đơn vị nguồn.
- `data/manifest.csv`: URL, phiên bản tài liệu, giấy phép, thời điểm truy cập, độ dài và SHA-256.
- `data/crawl_errors.csv`: các trang lỗi hoặc không đạt điều kiện.
- `data/COPYRIGHT_NOTICE.txt`: thông tin bản quyền và giấy phép.

Có thể crawl lại để cập nhật snapshot. Vì tài liệu Python có thể thay đổi, hãy giữ corpus và manifest của lần chạy được báo cáo.

## Chạy pipeline và benchmark

Vẫn từ `starter\`:

```powershell
python -m src.run --split dev
python -m src.run --split all
```

Lệnh đầu kiểm tra trên tập dev. Khi đã chốt cấu hình, lệnh thứ hai chạy toàn bộ benchmark và ghi kết quả dev/test; không điều chỉnh cấu hình dựa trên kết quả test. Có thể chạy lại crawl và benchmark bằng `python -m src.run --crawl --split all`.

Pipeline tự kiểm tra corpus, bộ câu hỏi, phân chia dev/test, filter metadata và sự tồn tại của gold evidence. Nếu cần chỉ định đường dẫn khác, các tùy chọn gồm `--corpus`, `--benchmark`, `--store-dir` và `--split {dev,test,all}`:

```powershell
python -m src.run --help
```

## Ba cấu hình

| Cấu hình | Chunking | Truy xuất |
|---|---|---|
| A | Cửa sổ cố định 1.000 ký tự, overlap 150 | Cosine vector search, không filter |
| B | Theo đoạn, tối đa 1.200 ký tự; đoạn dài overlap 100 | Cùng embedding model, không filter |
| C | Giống B và dùng lại embeddings của B | Chỉ áp dụng filter `category` khi câu hỏi có cung cấp filter |

Tất cả cấu hình sử dụng cùng corpus, benchmark, model và Chroma. Category là metadata phân loại theo chủ đề/module được gán cho corpus; filter chỉ dùng giá trị có trong metadata.

## Vị trí kết quả

- `results/traces_all.jsonl`: kết quả top-3, metadata, filter, điểm, thời gian tìm kiếm và đối chiếu evidence từng câu.
- `results/metrics_all.json`: Hit@1 và Evidence Hit@3 theo cấu hình, tách dev/test.
- `results/run_metadata_all.json`: phiên bản thư viện/model, cấu hình, số chunk và thời gian chạy.
- `results/manual_coherence_audit.csv`: 10 vị trí nguồn được chọn trước cho mỗi cấu hình.
- `results/manual_metadata_audit.csv`: các kết quả top-3 test để kiểm tra metadata và filter.
- `results/manual_grounding_audit.csv`: top-3 của 5 câu test cho mỗi cấu hình để chấm grounding.
- `artifacts/experiment_log.csv`: nhật ký cấu hình và chỉ số.
- `report/individual/2A202603469_BuiQuangHuy.md`: báo cáo cá nhân.

