# Kiến trúc hệ thống chatbot tuyển sinh — phiên bản refactor đề xuất

## 1. Mục tiêu của phiên bản mới

Phiên bản refactor hướng tới 5 mục tiêu:

1. **Giữ RAG là nguồn chính**
2. **Bổ sung web search có điều kiện**
3. **Đảm bảo prompt builders được dùng thật trong pipeline**
4. **Hợp nhất nguồn RAG + web một cách sạch**
5. **Tạo câu trả lời grounded, có citation rõ ràng**

---

## 2. Kiến trúc tổng quan mới

```text
Người dùng
   │
   ▼
FastAPI / UI Layer
   │
   ▼
ChatbotEngine
   │
   ├── MemoryManager
   │
   ├── QueryRouter
   │     ├── rag_only
   │     ├── rag_then_web
   │     ├── web_only
   │     └── ocr_flow
   │
   ├── Retriever
   │     ├── Dense Search (FAISS)
   │     ├── Sparse Search (BM25)
   │     ├── Fusion / Rerank
   │     └── Parent Fetch
   │
   ├── Retrieval Diagnostics
   │
   ├── Web Search Module
   │     ├── Query Builder
   │     ├── Search Engine (SerpAPI)
   │     ├── URL Fetch / Extract
   │     └── Web Context Builder
   │
   ├── Prompt Builder
   │     ├── build_rag_prompt
   │     ├── build_web_search_summary_prompt
   │     └── build_ocr_prompt
   │
   ├── LLM Chain
   │     ├── OpenRouter
   │     ├── Groq
   │     └── Gemini
   │
   └── Response Builder
         ├── Rag Citations
         ├── Web Citations
         ├── Warnings
         └── Final Answer Formatter
```

---

## 3. Luồng xử lý online mới

## 3.1. Bước 1 — nhận câu hỏi
- user gửi câu hỏi
- frontend gửi `provider`
- backend nhận `question`, `provider`, `chat_history`, optional image

## 3.2. Bước 2 — routing
`QueryRouter` quyết định mode:

- `rag_only`
- `rag_then_web`
- `web_only`
- `ocr_flow`

### Tiêu chí routing
- câu hỏi ổn định, bám tài liệu → `rag_only`
- câu hỏi cần cập nhật / entity web-native → `rag_then_web`
- câu hỏi gần như chắc chắn là nội dung website → `web_only`
- ảnh / OCR → `ocr_flow`

---

## 4. Retrieval layer

## 4.1. RAG retrieval
Retriever giữ pipeline hybrid hiện có:

1. Dense search bằng FAISS
2. Sparse search bằng BM25
3. Fusion
4. Cross-encoder rerank
5. Parent fetch

## 4.2. Retrieval diagnostics
Sau retrieval, tạo diagnostics:
- số chunks
- top1 confidence
- avg top3 confidence
- top sources

Diagnostics dùng để quyết định:
- có đủ trả lời chưa
- có cần web augmentation không

---

## 5. Web augmentation layer

## 5.1. Mục tiêu
Web search không thay thế RAG, mà dùng để:
- bổ sung nguồn mới
- bổ sung entity/people pages
- xác minh thông tin cần freshness
- fallback khi RAG yếu

## 5.2. Các bước
1. Build nhiều query variants
2. Search bằng SerpAPI
3. Ưu tiên kết quả `cmcu.edu.vn`, `cmc.com.vn`
4. Fetch top URLs
5. Extract text sạch
6. Build web context block

## 5.3. Loại câu hỏi phù hợp
- đội ngũ giảng viên
- thành tích sinh viên
- bài viết truyền thông
- hợp tác doanh nghiệp
- sự kiện
- lịch mới nhất
- thông báo

---

## 6. Prompt layer

## 6.1. Vai trò
Prompt builder là nơi hợp nhất:
- profile
- episodic summary
- RAG context
- web context
- question

## 6.2. Các builder cần dùng thật
- `build_rag_prompt(...)`
- `build_web_search_summary_prompt(...)`
- `build_ocr_prompt(...)`

## 6.3. Nguyên tắc prompt
- ưu tiên tài liệu nội bộ
- web là bổ sung
- chỉ dùng fact có nguồn
- nếu thiếu dữ liệu thì nói rõ
- nếu dữ liệu theo năm thì ghi năm hiệu lực
- nếu là bảng thì render Markdown

---

## 7. LLM layer

LLM chain tiếp tục giữ abstraction theo provider:

- OpenRouter
- Groq
- Gemini

Điểm thay đổi chính:
- `llm_chain.py` không còn nhận raw question đơn thuần
- nó nhận **prompt đã được build đầy đủ** từ orchestration layer

---

## 8. Response builder mới

## 8.1. Mục tiêu
Response builder không chỉ nối citation vào cuối answer, mà phải:
- lọc nguồn liên quan
- tách RAG citations và Web citations
- cảnh báo nếu confidence thấp
- ghi chú nếu dùng web hoặc thông tin theo năm

## 8.2. Cấu trúc output đề xuất
```text
[Câu trả lời chính]

📎 Nguồn tài liệu nội bộ:
[1] ...
[2] ...

🌐 Nguồn web:
[W1] ...
[W2] ...

⚠️ Lưu ý:
- ...
```

## 8.3. Nguyên tắc
- dedupe rồi mới đánh số
- chỉ giữ top nguồn liên quan
- không dump toàn bộ retrieved chunks

---

## 9. OCR flow

OCR pipeline giữ nguyên về mặt kỹ thuật, nhưng orchestration cần rõ hơn:

```text
User gửi ảnh
   ↓
OCR preprocess
   ↓
OCR extract
   ↓
OCR postprocess
   ↓
build_ocr_prompt(...)
   ↓
LLM answer
   ↓
final response
```

OCR flow không nhất thiết cần web search, trừ khi user yêu cầu đối chiếu với thông tin tuyển sinh hiện tại.

---

## 10. Kiến trúc module đề xuất

```text
src/
├── engine/
│   ├── chatbot.py
│   └── query_router.py
│
├── rag/
│   ├── retriever.py
│   ├── indexer.py
│   ├── chunker.py
│   ├── doc_loader.py
│   └── doc_parser.py
│
├── web/
│   ├── models.py
│   └── web_search.py
│
├── llm/
│   ├── llm_chain.py
│   ├── prompt_templates.py
│   └── response_builder.py
│
├── memory/
│   └── memory.py
│
├── ocr/
│   ├── preprocess.py
│   ├── table.py
│   ├── ocr.py
│   └── postprocess.py
│
└── mcp/
    └── ...
```

---

## 11. Luồng xử lý chi tiết sau refactor

```text
Question
   ↓
QueryRouter
   ↓
[rag_only] ──────────────┐
[rag_then_web] ───────┐  │
[web_only] ────────┐  │  │
[ocr_flow] ─────┐  │  │  │
                ▼  ▼  ▼  ▼
         Appropriate execution path
                │
                ▼
        Prompt Builder
                │
                ▼
            LLM Chain
                │
                ▼
        Response Builder
                │
                ▼
          Final Response
```

---

## 12. Ví dụ behavior theo query

## 12.1. “Học phí ngành CNTT”
- route: `rag_only`
- dùng RAG
- citation nội bộ

## 12.2. “Đội ngũ giảng viên và các giảng viên nổi bật của CMCU”
- route: `rag_then_web`
- RAG lấy profile nếu có
- web lấy trang tổng hợp giảng viên
- response tách rõ nguồn nội bộ và nguồn web

## 12.3. “Lịch tuyển sinh mới nhất năm nay”
- route: `rag_then_web`
- RAG lấy khung chung
- web xác minh bản mới nhất

## 12.4. “Cho mình xem nội dung ảnh học bạ này”
- route: `ocr_flow`
- OCR + prompt OCR
- web không cần mặc định

---

## 13. Các chỉ số nên theo dõi sau refactor

- tỷ lệ câu trả lời chỉ dùng RAG
- tỷ lệ câu trả lời cần web augmentation
- precision của citation
- số lần web search trả nguồn chính thức
- latency khi dùng `rag_only` vs `rag_then_web`
- mức độ hài lòng với query dạng:
  - giảng viên
  - thành tích sinh viên
  - lịch / deadline
  - tin tức

---

## 14. Kết luận

Kiến trúc mới không thay thế hệ thống cũ, mà **nâng cấp orchestration** để:

- dùng đúng nguồn đúng lúc
- giảm nhiễu citation
- tăng độ bao phủ ở câu hỏi thiên web
- vẫn giữ thế mạnh của RAG với tài liệu tuyển sinh chính thức

Tư duy cốt lõi của bản refactor là:

**RAG-first, Web-augmented, Source-grounded, Prompt-driven, Citation-clean**
