# 📘 Hướng Dẫn Cài Đặt & Chạy Hệ Thống Chatbot Tuyển Sinh

## Yêu Cầu Hệ Thống

- **Python**: 3.10 trở lên
- **RAM**: tối thiểu 8GB (khuyến nghị 16GB)
- **GPU** (tuỳ chọn): NVIDIA GPU hỗ trợ CUDA 11.8+ (chỉ cần cho OCR)
- **Hệ điều hành**: Windows 10/11, Linux, macOS

---

## Bước 1: Cài Đặt Thư Viện

Mở terminal tại thư mục gốc của project:

```bash
pip install -r requirements.txt
```

> **Lưu ý:** Nếu bạn không có GPU hoặc gặp lỗi PaddlePaddle, sửa file `requirements.txt`:
> ```bash
> # Bỏ dòng paddlepaddle-gpu, thay bằng:
> paddlepaddle
> ```

---

## Bước 2: Cấu Hình API Key

Mở file `config.py` và điền API key cho OpenRouter:

```python
OPENROUTER_API_KEY = "sk-or-v1-xxxxxxxxxxxxxxx"  # Lấy tại: https://openrouter.ai/keys
```

Hoặc dùng biến môi trường:
```bash
set OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxx   # Windows
export OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxx # Linux/Mac
```

---

## Bước 3: Chuẩn Bị Dữ Liệu

Đặt các file tài liệu tuyển sinh (PDF, TXT) vào thư mục:

```
data/Tuyển Sinh 2024/
├── thong-tin-tuyen-sinh/
│   ├── diem-chuan-2024.pdf
│   └── phuong-thuc-xet-tuyen.pdf
├── chuong-trinh-dao-tao/
│   └── danh-sach-nganh.pdf
└── ...
```

Hệ thống sẽ tự động quét toàn bộ file PDF/TXT trong `data/` và các thư mục con.

---

## Bước 4: Ingest Tài Liệu (Bắt Buộc Chạy 1 Lần)

```bash
# Ingest tất cả tài liệu lần đầu
python ingest.py

# Nếu muốn build lại toàn bộ index
python ingest.py --force

# Xem trạng thái index hiện tại
python ingest.py --status
```

> **Thời gian:** Lần đầu sẽ download model embedding (~420MB) và mất 5-15 phút tuỳ số lượng tài liệu.

---

## Bước 5: Chạy Chatbot

```bash
streamlit run app.py
```

Mở trình duyệt tại: **http://localhost:8501**

---

## Các Lệnh Hữu Ích

| Lệnh | Mô tả |
|-------|--------|
| `python ingest.py` | Ingest file mới/thay đổi |
| `python ingest.py --force` | Xóa index cũ, build lại mọi thứ |
| `python ingest.py --status` | Xem trạng thái index |
| `streamlit run app.py` | Chạy giao diện chatbot |

---

## Xử Lý Lỗi Thường Gặp

### 1. `ModuleNotFoundError: No module named 'rank_bm25'`
```bash
pip install rank-bm25
```

### 2. `OPENROUTER_API_KEY is not set`
Kiểm tra lại `config.py` hoặc biến môi trường. Đảm bảo key bắt đầu bằng `sk-or-v1-`.

### 3. `Index chưa có sẵn`
Chạy `python ingest.py` trước khi chạy `streamlit run app.py`.

### 4. OCR lỗi PaddlePaddle
Nếu không cần OCR (chỉ dùng chat hỏi đáp), bỏ các dòng `paddlepaddle-gpu`, `paddleocr`, `opencv-python` trong `requirements.txt`.

### 5. Lỗi encoding khi ingest
Đảm bảo file TXT lưu dưới dạng UTF-8. Hệ thống tự động thử nhiều encoding (UTF-8, UTF-8-BOM, CP1258, Latin-1).

---

## Cấu Trúc Thư Mục

```
files/
├── app.py                  # Entry point Streamlit UI
├── ingest.py               # CLI ingest tài liệu
├── config.py               # Cấu hình chung
├── requirements.txt        # Dependencies
│
├── src/                    # Source code chính
│   ├── rag/                # RAG pipeline (load → parse → chunk → index → retrieve)
│   ├── llm/                # LLM calls, prompt templates, response builder
│   ├── memory/             # Short-term, Profile, Episodic memory
│   ├── ocr/                # PaddleOCR pipeline cho ảnh học bạ
│   ├── mcp/                # MCP server/client cho OCR
│   └── engine/             # ChatbotEngine orchestrator
│
├── data/                   # Tài liệu PDF/TXT
├── index/                  # FAISS, BM25, Docstore indexes
├── memory/                 # Profile & Episodic data
└── workers/                # OCR subprocess worker
```
