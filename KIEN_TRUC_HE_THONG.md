# Kien truc he thong Chatbot Tuyen sinh (ban cap nhat)

## 1) Muc tieu kien truc

He thong hien tai duoc to chuc theo huong:

- **RAG-first**: uu tien tai lieu noi bo da index.
- **Web-augmented**: chi bo sung web khi can freshness/entity.
- **Grounded answer**: khong dua fact neu khong co nguon.
- **Citation tach rieng**: phan biet RAG va web citations.
- **Observability**: theo doi pipeline qua Langfuse.

---

## 2) Kien truc tong quan

```text
Frontend (SSE stream)
        |
        v
FastAPI (api.py)
        |
        v
ChatbotEngine (src/engine/chatbot.py)
   |         |            |            |
   |         |            |            +--> Response Builder (rag/web citations)
   |         |            |
   |         |            +--> LLM Chain (invoke/stream)
   |         |
   |         +--> Web module (search, fetch, context)
   |
   +--> RAG Retriever (dense + sparse + rerank + parent)
```

Thanh phan moi/da cap nhat:

- `src/engine/query_router.py`
- `src/web/models.py`
- `src/web/web_search.py`
- `src/llm/response_builder.py` (model citation moi)
- `src/observability/langfuse_tracer.py`

---

## 3) Luong xu ly moi (sync va streaming)

### 3.1 Sync flow (`POST /chat`)

1. Nhan `question`, `provider`.
2. Lay memory context (profile + history).
3. Router phan loai query:
   - `rag_only`
   - `rag_then_web`
   - `web_only`
   - `ocr_flow`
4. Neu khong phai `web_only`: goi `hybrid_retrieve(...)`.
5. Danh gia co can web augment:
   - theo routing mode
   - theo `needs_web_search(...)` (freshness/entity/confidence)
6. Neu can web:
   - `search_many(question)`
   - `fetch_documents(...)`
   - `build_web_context(...)`
7. Build prompt hop nhat bang `build_rag_prompt(...)`.
8. Goi LLM (`call_llm`).
9. Build final response:
   - `rag_citations`
   - `web_citations`
   - `warnings`

### 3.2 Streaming flow (`POST /chat/stream`)

Giong sync flow, khac o buoc LLM:

- su dung `call_llm_streaming(...)` de tra token SSE.
- cuoi stream goi `finalize_streaming_response(...)`.
- payload done da tra schema moi:
  - `rag_citations`
  - `web_citations`
  - `warnings`
  - `metadata`

---

## 4) Query routing

File: `src/engine/query_router.py`

`RoutingDecision`:

- `mode`: `rag_only | rag_then_web | web_only | ocr_flow`
- `reason`
- `requires_freshness`
- `requires_entity_lookup`

Rule chinh:

- Query lien quan anh/OCR -> `ocr_flow`.
- Query co keyword freshness (`moi nhat`, `nam nay`, `deadline`, `lich`, ...) -> `rag_then_web`.
- Query web-native/entity (`giang vien`, `tin tuc`, `bai viet`, ...) -> `rag_then_web` hoac `web_only`.
- Mac dinh -> `rag_only`.

---

## 5) RAG layer

File: `src/rag/retriever.py`

Pipeline retrieval:

1. Dense search (FAISS)
2. Sparse search (BM25)
3. Reciprocal rank fusion
4. Cross-encoder rerank
5. Parent context fetch
6. Table enrichment theo source file

Dau ra: `List[RetrievedChunk]` gom confidence + context_text.

---

## 6) Web augmentation layer

Files:

- `src/web/models.py`
- `src/web/web_search.py`

Data model:

- `WebSearchItem`
- `WebDocument`
- `WebSearchBundle`

Buoc xu ly:

1. `build_query_variants(question)` (uu tien `site:cmcu.edu.vn`)
2. `serp_search(query)`
3. `search_many(question)` + dedupe URL
4. `fetch_documents(top N)` + strip HTML + limit context
5. `build_web_context(documents)` de dua vao prompt

Nguyen tac:

- Uu tien domain official (`cmcu.edu.vn`, `cmc.com.vn`).
- Chi lay top ket qua can thiet, tranh no context window.

---

## 7) Prompt & LLM layer

Files:

- `src/llm/prompt_templates.py`
- `src/llm/llm_chain.py`

Cap nhat chinh:

- `SYSTEM_PROMPT` theo huong grounded, nguon uu tien ro rang.
- `needs_web_search()` da nang cap (freshness + web-native + confidence logic).
- Builder duoc dung thuc te trong orchestration:
  - `build_rag_prompt`
  - `build_ocr_prompt`

LLM providers:

- OpenRouter
- Groq
- Gemini

---

## 8) Response builder & schema moi

File: `src/llm/response_builder.py`

Model moi:

- `RagCitation`
- `WebCitation`
- `FinalResponse`:
  - `answer_text`
  - `rag_citations`
  - `web_citations`
  - `warnings`
  - `metadata`

Dinh dang hien thi cuoi:

- block nguon noi bo
- block nguon web
- block luu y/warnings

Backend va frontend da dong bo schema nay.

---

## 9) Observability (Langfuse)

Files:

- `src/observability/langfuse_tracer.py`
- gắn tai `chatbot.py`, `llm_chain.py`, `api.py`

Trace/span/generation duoc ghi o cac diem:

- `chat_request` / `chat_stream_request`
- `routing`
- `rag_retrieve`
- `web_augmentation`
- `llm_call` / `llm_streaming_call`
- `response_build`

API lifecycle:

- startup: init langfuse client
- shutdown: flush events

---

## 10) Cau hinh hien tai (thuc te project)

### 10.1 `.env` (da tao)

Project da co file `.env` trong root `files/` de luu key Langfuse:

- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_BASE_URL`
- `LANGFUSE_ENABLED`

### 10.2 `config.py`

- Da bo sung `load_dotenv(BASE_DIR / ".env")`.
- Cac gia tri Langfuse duoc doc tu env.

> Ghi chu: Day la project practice/dummy, nen hien tai chua ep buoc quy trinh quan ly secret production.

---

## 11) API contract (phan chat)

### 11.1 `POST /chat`

Tra ve:

- `answer`
- `rag_citations`
- `web_citations`
- `warnings`
- `metadata`

### 11.2 `POST /chat/stream`

SSE events:

- `{ token: "..." }`
- done payload:
  - `done: true`
  - `full_answer`
  - `rag_citations`
  - `web_citations`
  - `warnings`
  - `metadata`

---

## 12) Ghi chu van hanh

- Chay API: `python api.py`
- UI: `http://localhost:8000/app`
- Docs: `http://localhost:8000/docs`

Neu can tat tracing tam thoi:

- set `LANGFUSE_ENABLED=false` trong `.env`.

---

## 13) Huong mo rong tiep theo

- Truyen `session_id/user_id` vao trace de loc dashboard theo user.
- Dong bo schema moi cho `/chat/stream/image` (hien de rong citations).
- Them dashboard diagnostics retrieval (top1/avg_top3 confidence) tren UI.
