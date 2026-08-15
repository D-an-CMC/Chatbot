# Hướng dẫn Agent hoạt động + kế hoạch sửa code theo hướng RAG-first, Web-augmented

## 1. Mục tiêu

Tài liệu này mô tả cách agent nên hoạt động trong hệ thống chatbot tuyển sinh của bạn, các điểm cần sửa trong code hiện tại, và lộ trình refactor để đạt kiến trúc:

- **RAG là mặc định**
- **Web search là fallback / augmentation có điều kiện**
- **Response builder hợp nhất nguồn sạch giữa RAG + web**
- **Prompt builder thực sự được dùng trong pipeline**
- **Citations phản ánh đúng nguồn đã dùng để trả lời**

---

## 2. Vấn đề hiện tại

### 2.1. Những gì hệ thống đang làm tốt
- Có pipeline RAG rõ ràng: dense + sparse + rerank
- Có OCR pipeline riêng
- Có LLM abstraction cho nhiều provider
- Có `response_builder.py` để định dạng câu trả lời cuối

### 2.2. Những vấn đề chính cần sửa
1. **Prompt builder chưa chắc đang được dùng đầy đủ**
   - Hiện `llm_chain.py` mới chắc chắn dùng `SYSTEM_PROMPT`
   - Các hàm như `build_rag_prompt`, `build_web_search_summary_prompt`, `build_ocr_prompt`, `needs_web_search`, `is_personalized_question` có tồn tại nhưng chưa có bằng chứng được gọi trong orchestration cuối

2. **Web search chưa được tích hợp như một tầng chuẩn hóa**
   - Mới dừng ở mức search → summary
   - Chưa có bước search nhiều query, fetch top URL, lọc domain chính thức, chuẩn hóa kết quả

3. **Response builder đang gắn nguồn chưa sạch**
   - Citation lấy theo `retrieved_chunks` nói chung
   - Không tách nguồn RAG và nguồn web
   - Dễ sinh citation không liên quan trực tiếp đến nội dung answer
   - Có thể nhảy số citation do dedupe sau enumerate

4. **Thiếu routing layer**
   - Chưa có tầng quyết định khi nào:
     - chỉ RAG
     - RAG rồi mới web
     - chỉ web
     - OCR flow

---

## 3. Agent nên hoạt động như thế nào

## 3.1. Nguyên tắc tổng quát

Agent phải coi mỗi câu hỏi là một bài toán **route → retrieve → validate → augment → answer**.

### Ưu tiên nguồn
1. Tài liệu nội bộ đã index (RAG)
2. Website chính thức của CMCU / CMC
3. Web chung
4. Kiến thức nền của model chỉ dùng để diễn đạt, không dùng để bịa fact

### Mục tiêu trả lời
- Đúng intent
- Dựa trên nguồn thật
- Nói rõ nếu thiếu dữ liệu
- Gắn citation sạch
- Không trộn nguồn không liên quan

---

## 3.2. Decision flow đề xuất

```text
Nhận câu hỏi
   ↓
Phân loại intent / routing
   ↓
Nếu là OCR → đi OCR flow
   ↓
Nếu không:
   ↓
RAG retrieve
   ↓
Đánh giá chất lượng retrieval
   ↓
Nếu đủ tốt và không cần freshness → trả lời bằng RAG
Nếu chưa đủ / cần cập nhật / query thiên web → gọi web search
   ↓
Chuẩn hóa kết quả web
   ↓
Build prompt hợp nhất RAG + web
   ↓
Gọi LLM
   ↓
Build final response với citation sạch
```

---

## 3.3. Khi nào dùng RAG

Dùng **RAG mặc định** nếu câu hỏi thuộc nhóm:
- phương thức xét tuyển
- học phí
- học bổng
- chỉ tiêu
- tổ hợp môn
- điểm chuẩn của năm đã có tài liệu
- chính sách ổn định theo tài liệu

Điều kiện:
- có retrieved chunks
- confidence đủ tốt
- không có dấu hiệu cần cập nhật theo thời gian

---

## 3.4. Khi nào bật web search

Bật **web search bổ sung** nếu:
- không có chunks phù hợp
- tất cả chunks confidence thấp
- câu hỏi chứa dấu hiệu thời gian:
  - hiện tại
  - năm nay
  - mới nhất
  - deadline
  - lịch
  - thông báo
  - sự kiện
- câu hỏi thuộc nhóm web-native:
  - đội ngũ giảng viên
  - hồ sơ giảng viên
  - thành tích sinh viên
  - tin tức
  - hợp tác doanh nghiệp
  - bài viết truyền thông
  - hoạt động mới

---

## 3.5. Khi nào chỉ dùng web

Dùng **web-only** nếu:
- intent gần như chắc chắn là bài viết web / trang giới thiệu
- kho RAG hiện không có loại nội dung phù hợp
- ví dụ:
  - “Đội ngũ giảng viên nổi bật của CMCU”
  - “Có bài viết nào về dự án UAV của sinh viên không?”
  - “Hành trình của Nguyễn Bình Nam ở Viettel Networks”

---

## 3.6. Nguyên tắc cho câu trả lời cuối

Agent phải:
- ưu tiên nguồn chính thức
- không nêu fact nếu không có nguồn
- tách rõ:
  - nguồn nội bộ
  - nguồn web
- nếu RAG và web mâu thuẫn:
  - ưu tiên nguồn chính thức mới hơn
  - ghi chú rằng thông tin có thể thay đổi theo thời gian
- nếu chỉ có web mà không có RAG:
  - nói rõ “thông tin dưới đây được bổ sung từ website chính thức”

---

## 4. Các thay đổi code cần làm

## 4.1. Tạo `src/engine/query_router.py`

### Mục tiêu
Thêm tầng routing để quyết định mode chạy cho từng câu hỏi.

### Đề xuất data model

```python
from dataclasses import dataclass

@dataclass
class RoutingDecision:
    mode: str   # "rag_only" | "rag_then_web" | "web_only" | "ocr_flow"
    reason: str
    requires_freshness: bool = False
    requires_entity_lookup: bool = False
```

### Hàm chính
```python
def route_query(question: str) -> RoutingDecision:
    ...
```

### Rule tối thiểu
- nếu query liên quan ảnh / OCR → `ocr_flow`
- nếu query có “mới nhất”, “năm nay”, “hiện tại”, “deadline”, “lịch” → `rag_then_web`
- nếu query về giảng viên, bài viết, tin tức, thành tích sinh viên → `rag_then_web` hoặc `web_only`
- mặc định → `rag_only`

---

## 4.2. Tạo `src/web/models.py`

```python
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class WebSearchItem:
    title: str
    url: str
    snippet: str
    source: str
    rank: int

@dataclass
class WebDocument:
    title: str
    url: str
    text: str
    domain: str
    published_at: Optional[str] = None
    is_official: bool = False

@dataclass
class WebSearchBundle:
    query_variants: List[str] = field(default_factory=list)
    search_items: List[WebSearchItem] = field(default_factory=list)
    documents: List[WebDocument] = field(default_factory=list)
```

---

## 4.3. Tạo `src/web/web_search.py`

### Mục tiêu
Tách web search thành 3 bước:
1. tạo nhiều query
2. search kết quả
3. fetch nội dung top URLs
4. build context block sạch cho LLM

### Hàm nên có

```python
def build_query_variants(question: str) -> list[str]:
    ...

def serp_search(query: str) -> list[WebSearchItem]:
    ...

def search_many(question: str) -> WebSearchBundle:
    ...

def fetch_documents(items: list[WebSearchItem]) -> list[WebDocument]:
    ...

def build_web_context(documents: list[WebDocument]) -> str:
    ...
```

### Rule triển khai
- ưu tiên `site:cmcu.edu.vn`
- nếu cần có thể thêm `site:cmc.com.vn`
- chỉ fetch top 3–5 URLs tốt nhất
- ưu tiên page chính thức hơn báo ngoài
- loại URL trùng / irrelevant
- limit text để không nổ context window

### Với query kiểu “đội ngũ giảng viên”
Dùng nhiều query:
- `site:cmcu.edu.vn "giảng viên" "Trường Đại học CMC"`
- `site:cmcu.edu.vn "đội ngũ giảng viên" "CMC University"`
- `site:cmcu.edu.vn giang vien cmcu`

---

## 4.4. Sửa `src/llm/prompt_templates.py`

## Việc cần làm

### A. Nâng lại `SYSTEM_PROMPT`
Prompt cần nói rõ:
- RAG là ưu tiên
- web search là fallback
- chỉ dùng fact có nguồn
- nếu thiếu thông tin phải nói rõ
- nếu dữ liệu theo năm thì nêu năm hiệu lực
- nếu dùng web, ưu tiên domain chính thức

### B. Giữ và thực sự dùng các builder
- `build_rag_prompt`
- `build_web_search_summary_prompt`
- `build_ocr_prompt`

### C. Thêm topic `giang_vien`
Thêm vào `_TOPIC_KEYWORDS`:
```python
"giang_vien": [
    "giang vien", "doi ngu giang vien", "thay co",
    "faculty", "lecturer", "ho so giang vien"
]
```

### D. Nâng `needs_web_search()`
Hiện tại logic đang quá đơn giản. Nên kiểm tra:
- freshness keywords
- web-native entity keywords
- retrieved chunk count
- top1 confidence
- avg_top3 confidence

### E. Làm `WEB_SEARCH_GUIDE_TEMPLATE` mạnh hơn
Nó nên ép model:
- chỉ giữ fact có nguồn rõ ràng
- ưu tiên nguồn chính thức
- trích entity names / chức danh / khoa / URL
- bỏ nguồn không liên quan

---

## 4.5. Sửa `src/engine/chatbot.py`

Đây là file quan trọng nhất trong refactor.

### Luồng mới nên là

1. Nhận question, provider, history
2. Lấy profile + episodic summary
3. Gọi `route_query(question)`
4. Nếu không phải OCR:
   - gọi retriever
   - tính diagnostics
   - quyết định có web search không
5. Nếu cần web:
   - gọi `search_many(question)`
   - fetch documents
   - build web context
6. Build prompt hợp nhất bằng `build_rag_prompt(...)`
7. Gọi `call_llm_streaming(...)`
8. Gọi `build_final_response(...)` với cả RAG + web citations

### Pseudocode

```python
decision = route_query(question)

retrieved_chunks = []
web_bundle = None

if decision.mode != "web_only":
    retrieved_chunks = retriever.retrieve(question)
    diagnostics = summarize_retrieval(retrieved_chunks)

use_web = (
    decision.mode in {"rag_then_web", "web_only"}
    or needs_web_search(question, retrieved_chunks)
)

if use_web:
    web_bundle = search_many(question)
    web_context = build_web_context(web_bundle.documents)
else:
    web_context = ""

prompt = build_rag_prompt(
    question=question,
    retrieved_chunks=retrieved_chunks,
    profile_summary=profile_summary,
    episodic_summary=episodic_summary,
    is_personalized=is_personalized_question(question),
    web_search_results=web_context,
)

answer = call_llm(prompt, provider=provider)

final = build_final_response(
    llm_answer=answer,
    retrieved_chunks=retrieved_chunks,
    question=question,
    web_documents=web_bundle.documents if web_bundle else [],
)
```

---

## 4.6. Sửa `src/llm/llm_chain.py`

### Giữ
- `get_llm(provider)`
- `call_llm`
- `call_llm_streaming`

### Thay đổi tư duy
`user_prompt` không còn là raw question đơn thuần nữa.

Nó phải là prompt đã được build từ:
- question
- context nội bộ
- web context
- profile
- lịch sử hội thoại

### Thực tế code có thể sửa rất ít
Chủ yếu sửa phía orchestration để truyền prompt đã tổng hợp vào `call_llm()`.

---

## 4.7. Refactor `src/llm/response_builder.py`

Đây là nơi nên sửa mạnh nhất.

### Vấn đề cần giải quyết
- citation RAG bị nhiễu
- không có citation web
- đánh số citation có thể nhảy
- warning không được hiển thị

### Đề xuất data model mới

```python
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class RagCitation:
    index: int
    source_file: str
    page_number: Optional[int]
    admission_cycle: Optional[str]
    document_type: Optional[str]
    confidence: float = 1.0

@dataclass
class WebCitation:
    index: int
    title: str
    url: str
    domain: str
    is_official: bool = False

@dataclass
class FinalResponse:
    answer_text: str
    rag_citations: List[RagCitation] = field(default_factory=list)
    web_citations: List[WebCitation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    has_low_confidence: bool = False
    has_year_sensitive_info: bool = False
    metadata: dict = field(default_factory=dict)
```

### Logic mới
1. dedupe nguồn RAG trước
2. đánh số lại liên tục sau dedupe
3. tách riêng web citations
4. chỉ giữ top nguồn liên quan nhất
5. warning phải được render ra cuối answer

### Output đề xuất
```text
📎 Nguồn tài liệu nội bộ:
[1] ...

🌐 Nguồn web:
[W1] ...
[W2] ...

⚠️ Lưu ý:
- Một phần thông tin được bổ sung từ website chính thức.
- Thông tin có thể thay đổi theo năm tuyển sinh.
```

---

## 4.8. Sửa `config.py`

### Thêm config mới

```python
WEB_SEARCH_ENABLED = True
WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_FETCH_TOP_N = 3
WEB_SEARCH_OFFICIAL_DOMAINS = ["cmcu.edu.vn", "www.cmc.com.vn"]

RAG_CONFIDENCE_GOOD = 0.65
RAG_CONFIDENCE_LOW = 0.50

MAX_RAG_CITATIONS = 3
MAX_WEB_CITATIONS = 3

ENABLE_WEB_FOR_ENTITY_QUERIES = True
ENABLE_WEB_FOR_FRESH_QUERIES = True
```

---

## 4.9. Có thể bổ sung diagnostics ở `src/rag/retriever.py`

Không bắt buộc phải sửa lớn, nhưng rất nên có hàm phụ để trả:
- top1 confidence
- avg top3 confidence
- số kết quả
- top source files

Ví dụ:

```python
@dataclass
class RetrievalDiagnostics:
    top1_confidence: float
    avg_top3_confidence: float
    num_results: int
    top_sources: list[str]
```

---

## 5. Ví dụ hành vi mong muốn của agent

## 5.1. Câu hỏi: “Điểm chuẩn ngành CNTT 2024 là bao nhiêu?”
- Router: `rag_only`
- RAG: đủ tốt
- Web: không cần
- Final answer: lấy từ tài liệu nội bộ + citation trang/file

## 5.2. Câu hỏi: “Đội ngũ giảng viên và các giảng viên nổi bật của CMCU”
- Router: `rag_then_web`
- RAG: có thể lấy profile PDF lẻ
- Web: gọi thêm để lấy trang tổng hợp giảng viên
- Final answer:
  - tóm tắt overview đội ngũ
  - liệt kê một số giảng viên tiêu biểu
  - nếu có profile PDF thì bổ sung chi tiết
  - citation tách RAG / web rõ ràng

## 5.3. Câu hỏi: “Lịch tuyển sinh mới nhất năm nay”
- Router: `rag_then_web`
- RAG: lấy khung chính sách / lịch cũ
- Web: xác minh lịch mới nhất
- Final answer: ưu tiên nguồn mới hơn

---

## 6. Anti-pattern cần tránh

Agent không nên:
- gọi cả RAG và web mọi lúc
- trả lời bằng web mà không nói rõ nguồn
- trích dẫn tất cả chunks retrieve được
- để citation không liên quan lọt vào answer cuối
- coi snippet search là đủ mà không fetch URL
- dùng knowledge nền để tự bịa số liệu tuyển sinh

---

## 7. Thứ tự triển khai khuyên dùng

### Phase 1 — orchestration
- tạo `query_router.py`
- tạo `web_search.py`
- sửa `chatbot.py`
- sửa `response_builder.py`

### Phase 2 — prompt
- nâng `SYSTEM_PROMPT`
- nâng `WEB_SEARCH_GUIDE_TEMPLATE`
- thêm topic `giang_vien`
- nâng `needs_web_search()`

### Phase 3 — tuning
- thêm retrieval diagnostics
- lọc metadata tốt hơn
- tinh chỉnh query expansion
- cải thiện source pruning

---

## 8. Checklist hoàn thành

- [ ] Có router riêng cho câu hỏi
- [ ] Có web search module riêng
- [ ] Prompt builders được gọi thực sự trong engine
- [ ] Response builder tách citation RAG và web
- [ ] Warning được hiển thị ra UI
- [ ] Query “giảng viên” trả lời đúng intent tổng quan + nhân vật nổi bật
- [ ] Query “mới nhất / hiện tại / deadline” có web augmentation
- [ ] Không còn citation nhiễu từ file không liên quan

---

## 9. Kết luận

Hệ thống phù hợp nhất với hướng:

- **RAG-first**
- **Web-augmented**
- **Grounded answer**
- **Citation sạch**
- **Routing rõ ràng**

Đây là hướng tận dụng tốt nhất kiến trúc hiện có của bạn mà không cần phá toàn bộ pipeline.
