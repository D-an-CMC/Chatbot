# Kiến trúc hệ thống — Chatbot Tra Cứu Điểm & Thông Tin Học Đường (THCS)

> Streamlit + Supabase • Phân quyền 3 cấp • 12 nhóm chức năng • LLM: Gemini/Groq/OpenRouter

## 1. Tổng quan

Chatbot hội thoại (Streamlit) giúp Học sinh/Phụ huynh, Giáo viên và Quản trị viên tra
cứu điểm số cùng nhiều thông tin học đường của một trường THCS. **Nguồn dữ liệu chính là
Supabase (PostgreSQL)**; vẫn giữ khả năng chạy dự phòng bằng file Excel.

Nguyên tắc thiết kế:

- **Dữ liệu có cấu trúc → truy vấn trực tiếp**, KHÔNG dùng RAG/vector search.
- **Grounded**: chỉ trả lời dựa trên dữ liệu lấy được, không bịa.
- **Phân quyền theo vai trò** ngay tại tầng điều phối.
- **Quan sát được** (Langfuse) và có **bộ nhớ hội thoại** (SQLite).

**Công nghệ:** Python 3 + Streamlit · Supabase (supabase-py, PostgREST) · pandas/openpyxl
(dự phòng Excel) · LangChain (openai/google-genai) · LLM OpenRouter/Groq/Gemini (mặc định
Gemini) · SQLite (memory) · Langfuse.

## 2. Luồng xử lý một câu hỏi

1. Câu hỏi được lưu vào memory (theo từng người dùng) và gửi tới `ChatbotEngine`.
2. `classify_intent()` phân loại vào ~12 nhóm ý định (điểm, tổng kết/xếp loại, thống kê
   lớp, giáo viên, thông tin HS, danh sách lớp, TKB, lịch thi, điểm danh, thông báo, hoạt động).
3. `extract_query_filters()` bóc năm học/lớp/học kỳ (regex) + `detect_subject()` nhận diện
   môn; phần còn lại coi là tên (đối chiếu fuzzy, không dấu).
4. **Kiểm tra phân quyền** theo `SessionUser`: học sinh chỉ được truy vấn dữ liệu của chính mình.
5. Resolver tương ứng truy vấn nguồn: `SupabaseGradeStore` / `SchoolInfoStore` / `analytics`.
6. Dựng prompt theo đúng intent (`prompt_templates.py`) — chỉ nhồi dữ liệu thực tế.
7. LLM (`llm_chain.py`) sinh câu trả lời tiếng Việt, có thể streaming.
8. `response_builder.py` hậu xử lý: bỏ `<think>`, gắn “📎 Nguồn”, tạo cảnh báo (cờ
   `notice_only` bỏ cảnh báo với câu thông báo/từ chối quyền).
9. Hiển thị + lưu vào lịch sử hội thoại.

## 3. Phân quyền theo vai trò

Ba vai trò (`src/auth/auth_service.py`):

- **Admin / Giáo viên (GiaoVien):** tra cứu điểm, hồ sơ, thống kê, danh sách toàn trường.
- **Học sinh / Phụ huynh (HocSinh-PhuHuynh):** CHỈ xem điểm / điểm danh / tổng kết của
  chính mình; bị chặn danh sách lớp, thống kê cả lớp, thông tin học sinh khác.

Engine là **singleton dùng chung** (Streamlit `@st.cache_resource`) → `SessionUser` được
truyền theo từng lời gọi `chat()`, không lưu trên `self`, tránh lẫn dữ liệu giữa người dùng.
Hiện chạy `AUTH_MODE=select` (chọn vai trò demo); đã sẵn `AuthService` cho đăng nhập thật.

## 4. Các nhóm chức năng (intent)

| Nhóm chức năng | Resolver / xử lý | Nguồn dữ liệu |
|---|---|---|
| Tra cứu điểm (grade) | `_resolve_records` + `build_grade_prompt` | `SupabaseGradeStore` (subject_results, grade_items) |
| Tổng kết & xếp loại (summary) | `analytics.summarize_student` (Thông tư 22) | records đã nạp |
| Thống kê lớp (class_stats) | `analytics.class_stats` | records đã nạp |
| Tra cứu giáo viên (teacher) | `SchoolInfoStore.get_homeroom/class_teachers/…` | classes, timetables, teachers |
| Thông tin học sinh (student_info) | `SchoolInfoStore.get_student_profiles` | students, student_enrollments |
| Danh sách lớp (roster) | `SchoolInfoStore.get_class_roster` | student_enrollments, students |
| Thời khóa biểu (timetable) | `SchoolInfoStore.get_timetable` | timetables (type 1) |
| Lịch thi (exam) | `SchoolInfoStore.get_exam_schedule` | timetables (type 2, `exam_name`) |
| Điểm danh (attendance) | `SchoolInfoStore.get_attendance` | attendances, attendance_sessions |
| Thông báo (notification) | `SchoolInfoStore.get_recent_notifications` | notifications |
| Hoạt động (activity) | `SchoolInfoStore.get_activities` | activities |

## 5. Breakdown các thành phần

| Lớp | Thành phần | File | Vai trò |
|---|---|---|---|
| Giao diện | Streamlit UI | `app.py` | Chat, sidebar, chọn vai trò, gợi ý |
| Cấu hình | Config | `config.py` | Đọc `.env`: Supabase, LLM, AUTH_MODE, Langfuse |
| Điều phối | ChatbotEngine | `src/engine/chatbot.py` | Intent, bóc lọc, phân quyền, định tuyến |
| Dữ liệu điểm | GradeStore | `src/grades/grade_store.py` | GradeRecord + tra cứu tên fuzzy (nền tảng) |
| Dữ liệu điểm | SupabaseGradeStore | `src/grades/supabase_store.py` | Nạp điểm từ Supabase (kế thừa GradeStore) |
| Dữ liệu học đường | SchoolInfoStore | `src/grades/school_info.py` | Lớp, TKB, lịch thi, điểm danh, GV, hồ sơ |
| Tính toán | Analytics | `src/grades/analytics.py` | Xếp loại (TT22) + thống kê lớp |
| Phân quyền | Auth Service | `src/auth/auth_service.py` | SessionUser, vai trò, Supabase Auth |
| Prompt | Prompt Templates | `src/llm/prompt_templates.py` | SYSTEM_PROMPT + dựng ngữ cảnh theo intent |
| LLM | LLM Chain | `src/llm/llm_chain.py` | Gọi mô hình, streaming |
| Hậu xử lý | Response Builder | `src/llm/response_builder.py` | Nguồn, cảnh báo, cờ `notice_only` |
| Bộ nhớ | Memory | `src/memory/memory.py` | Hội thoại SQLite theo người dùng |
| Quan sát | Langfuse Tracer | `src/observability/langfuse_tracer.py` | Trace/span từng bước |

## 6. Chi tiết các thành phần chính

- **GradeStore / GradeRecord** — bản ghi điểm (điểm TX/GK/CK, TB học kỳ/cả năm, nhận xét,
  và `danh_gia` = Đạt/Chưa đạt cho môn nhận xét); tra cứu tên fuzzy 4 mức. Lớp nền dùng chung.
- **SupabaseGradeStore** — ghi đè `load()` nạp điểm từ Supabase (phân trang 1000 dòng), đọc
  cột `ranking` cho môn đánh giá nhận xét. Engine không cần sửa nhờ kế thừa.
- **SchoolInfoStore** — lớp, TKB (`timetables` type 1), lịch thi (type 2, tự tính ngày từ
  `week_start` + thứ), điểm danh (ánh xạ Anh→Việt, tổng hợp buổi vắng), giáo viên (GVCN/bộ
  môn), hồ sơ HS, xác định học kỳ hiện tại theo ngày (fallback kỳ gần nhất khi nghỉ hè).
- **Analytics** — `summarize_student()` xếp loại học lực Tốt/Khá/Đạt/Chưa đạt theo Thông tư
  22 (loại trừ Chào cờ & Sinh hoạt lớp); `class_stats()` điểm TB lớp, top/bottom, phân bố.
- **ChatbotEngine** — `classify_intent` + `extract_query_filters`/`detect_subject` + resolver
  có phân quyền. Singleton → truyền `SessionUser` theo lời gọi.
- **Prompt / LLM / Response Builder** — mỗi intent một hàm dựng prompt; gọi LLM qua LangChain
  (streaming); hậu xử lý nguồn + cảnh báo (`notice_only`).
- **Memory / Langfuse / Streamlit UI / Config** — hội thoại SQLite; trace an toàn; giao diện
  chat + chọn vai trò; đọc cấu hình từ `.env`.

## 7. Mô hình dữ liệu Supabase (bảng chính)

- `students`, `student_enrollments` — hồ sơ HS; lớp theo TỪNG năm học (có HS thiếu enrollment).
- `classes` (`homeroom_teacher_id` = GVCN), `school_years`, `semesters` (`term_order` 1/2).
- `subjects`, `subject_results` — `dtb_mhk`/`dtb_mcn` (điểm) hoặc `ranking` (Đạt/Chưa đạt).
- `grade_items`, `grade_types` — `DDGtx` (thường xuyên), `DDGgk` (giữa kỳ), `DDGck` (cuối kỳ).
- `timetables` — `type_id=1` lịch học, `type_id=2` lịch thi (`exam_name`); embed `teachers`
  cần chỉ FK rõ (`teachers!timetables_teacher_id_fkey`) do có bảng `exam_proctors`.
- `attendances`, `attendance_sessions` — `status` (PRESENT/ABSENT_UNEXCUSED/…), `session` (MORNING/AFTERNOON).
- `teachers` (`subject_id`, `teacher_code`, `phone`), `notifications`, `activities`.

> **Lưu ý:** Chào cờ & Sinh hoạt lớp lưu như `subject_results` nhưng KHÔNG phải môn đánh giá
> → bị loại khỏi xếp loại. Xếp loại học lực tính trong code, không có cột lưu sẵn.

## 8. Cấu trúc thư mục

```text
files/
├── app.py                     # Giao diện Streamlit
├── config.py                  # Cấu hình (.env)
├── .env / .env.example        # Biến môi trường (secret bị .gitignore)
├── requirements.txt
├── data/diem_khtn/            # Excel sổ điểm (dự phòng)
├── src/
│   ├── engine/chatbot.py      # Điều phối + phân quyền
│   ├── grades/
│   │   ├── grade_store.py     # GradeRecord + tra cứu nền tảng
│   │   ├── supabase_store.py  # Nạp điểm từ Supabase
│   │   ├── school_info.py     # Lớp/TKB/lịch thi/điểm danh/GV/hồ sơ
│   │   └── analytics.py       # Xếp loại (TT22) + thống kê lớp
│   ├── auth/auth_service.py   # SessionUser + vai trò
│   ├── llm/                   # prompt_templates, llm_chain, response_builder
│   ├── memory/memory.py       # Hội thoại (SQLite)
│   └── observability/langfuse_tracer.py
└── (api.py, frontend/ — di sản kiến trúc cũ, không dùng)
```

## 9. Cấu hình (.env)

- `SUPABASE_URL` / `SUPABASE_KEY` (service_role) / `SUPABASE_ANON_KEY`.
- `SUPABASE_SUBJECT_NAME` — trống = tất cả môn; điền = 1 môn.
- `DEFAULT_LLM_PROVIDER` / `GEMINI_API_KEY` / `GEMINI_MODEL` (mặc định Gemini).
- `AUTH_MODE` — `select` (demo) hoặc `login` (Supabase Auth).
- `LANGFUSE_*` — observability. File `.env` bị `.gitignore` loại; dùng `.env.example` làm mẫu.

## 10. Hạn chế & lưu ý

- Một số HS thiếu `student_enrollments` → không suy ra lớp cho truy vấn “của tôi”.
- `exam_schedules` (cũ) và `activities` hiện rỗng; lịch thi đã chuyển vào `timetables` (type 2).
- `api.py` (FastAPI) và `frontend/` là di sản kiến trúc cũ, KHÔNG còn dùng (chỉ giữ tham khảo).
- Trích tên/môn dựa heuristic từ khóa, không dùng NER.
- Engine dùng chung mọi phiên → bắt buộc truyền `SessionUser` theo lời gọi.
