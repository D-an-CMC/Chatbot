# src/engine/chatbot.py
# Orchestrator chinh: cau hoi -> phan loai y dinh (diem so / danh sach lop /
# thoi khoa bieu / ...) -> trich xuat bo loc (ten/lop/nam hoc/hoc ky) ->
# tra cuu (co ap dung gioi han theo vai tro dang nhap) -> LLM dien giai ket qua.

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Generator, List, Optional, Set, Tuple

from config import (
    LOG_FORMAT, LOG_LEVEL, GRADES_DIR, DEFAULT_LLM_PROVIDER,
    USE_SUPABASE, USE_AUTH, SUPABASE_URL, SUPABASE_KEY, SUPABASE_ANON_KEY, SUPABASE_SUBJECT_NAME,
)
from src.grades.grade_store import GradeStore, GradeRecord, normalize_name
from src.memory.memory import MemoryManager, DEFAULT_SESSION_ID
from src.llm.prompt_templates import (
    build_grade_prompt, build_no_match_prompt,
    build_roster_prompt, build_no_roster_params_prompt,
    build_timetable_prompt, build_no_timetable_params_prompt,
    build_attendance_prompt, build_no_attendance_match_prompt,
    build_exam_schedule_prompt, build_no_exam_params_prompt,
    build_notifications_prompt,
    build_activities_prompt,
    build_student_info_prompt,
    build_summary_prompt,
    build_class_stats_prompt,
    build_teacher_prompt,
    build_feature_unavailable_prompt,
    build_permission_denied_prompt,
)
from src.grades.analytics import summarize_student, class_stats
from src.llm.llm_chain import call_llm, call_llm_streaming
from src.llm.response_builder import build_final_response, grade_citation_lines, format_for_display, FinalResponse
from src.observability.langfuse_tracer import create_span, create_trace, end_observation

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


import json
import re

_STUDENT_CODE_RE = re.compile(r"\bHS\d{3,}\b", re.IGNORECASE)

# Cac cum bao hieu nguoi dung muon xem diem CUA TAT CA CAC NAM HOC (khong gioi han nam hien tai).
_ALL_YEARS_KEYWORDS = [
    "qua các năm", "qua từng năm", "tất cả các năm", "toàn bộ các năm", "tất cả năm học",
    "mọi năm", "các năm học", "hết các năm", "qua các năm học", "toàn bộ điểm qua",
    "từ trước đến nay", "từ trước tới nay", "lịch sử điểm",
]

def wants_all_years(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in _ALL_YEARS_KEYWORDS)

def analyze_query_llm(question: str, history: Optional[List[dict]] = None) -> dict:
    """Su dung LLM de phan loai y dinh va trich xuat filter cung 1 luc."""
    from src.llm.llm_chain import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage
    
    context_str = ""
    if history:
        recent = [h["content"] for h in history[-4:] if h["role"] == "user"]
        if recent:
            context_str = "\nCác câu hỏi trước đó: " + " | ".join(recent)
            
    prompt = f"""Bạn là một chuyên gia phân tích ngữ nghĩa. Nhiệm vụ của bạn là phân tích câu hỏi của người dùng và trả về MỘT chuỗi JSON duy nhất chứa ý định và các thông tin cần thiết.
    
CHỈ TRẢ VỀ JSON HỢP LỆ, KHÔNG BAO GỒM BẤT KỲ VĂN BẢN NÀO KHÁC (KHÔNG DÙNG ```json ... ``` MARKDOWN).

Ý định (intent) phải là MỘT TRONG CÁC TỪ KHÓA sau:
- timetable: Hỏi về thời khóa biểu, lịch học, lịch học thêm.
- exam: Hỏi về lịch thi, lịch kiểm tra.
- attendance: Hỏi về điểm danh, đi học hay vắng học, nghỉ học.
- notification: Hỏi về thông báo.
- activity: Hỏi về hoạt động ngoại khóa, sự kiện.
- teacher: Hỏi về giáo viên (chủ nhiệm, bộ môn, ai dạy).
- class_stats: Thống kê điểm của lớp, xếp hạng của cả lớp.
- summary: Tổng kết, xếp loại học lực của cá nhân, kết quả học tập.
- student_info: Thông tin hồ sơ cá nhân, phụ huynh, liên hệ.
- roster: Danh sách lớp, sĩ số, các bạn trong lớp.
- grade: Tra cứu điểm số môn học (MẶC ĐỊNH nếu không khớp các loại trên).

Định dạng JSON yêu cầu:
{{
  "intent": "từ_khóa_intent",
  "filters": {{
    "student_name": "Tên học sinh nếu có (ví dụ: Nguyễn Văn An), hoặc mã HS nếu có",
    "class_name": "Tên lớp nếu có (ví dụ: 6A, 7B). Viết hoa chữ cái.",
    "school_year": "Năm học nếu có (ví dụ: 2023-2024, 2024-2025).",
    "semester": "Học kỳ nếu có, CHỈ GHI 'I' hoặc 'II'.",
    "subject": "Tên môn học nếu có (ví dụ: Toán, Ngữ Văn, Tiếng Anh, Khoa học tự nhiên, v.v.)"
  }}
}}
Nếu không tìm thấy thông tin cho một filter nào đó, hãy để giá trị là null.
Đối với môn học, cố gắng chuyển tên môn viết tắt hoặc không dấu (vd: khtn, ly, gdcd) về tên chuẩn (Khoa học tự nhiên, Giáo dục công dân...).
Tên học sinh: Hãy loại bỏ các đại từ xưng hô, chỉ lấy tên riêng.

{context_str}
Câu hỏi hiện tại: {question}"""

    try:
        llm = get_llm(DEFAULT_LLM_PROVIDER)
        response = llm.invoke([
            SystemMessage(content="You are a JSON data extractor."),
            HumanMessage(content=prompt)
        ])
        content = response.content
        if isinstance(content, list):
            content = " ".join([str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content])
        
        content = str(content).strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        data = json.loads(content)
        
        if "filters" not in data:
            data["filters"] = {}
        filters = data["filters"]
        
        # Normalize subject (optional) - the LLM usually gets it right
        # Provide default filter keys expected by functions
        return {
            "intent": data.get("intent", "grade"),
            "filters": {
                "name_query": filters.get("student_name"),
                "class_name": filters.get("class_name"),
                "school_year": filters.get("school_year"),
                "semester": filters.get("semester"),
                "subject": filters.get("subject")
            }
        }
    except Exception as e:
        logger.error(f"Lỗi khi dùng LLM analyze_query: {e}")
        return {
            "intent": "grade",
            "filters": {"name_query": None, "class_name": None, "school_year": None, "semester": None, "subject": None}
        }




@dataclass
class _LookupResult:
    prompt: str
    citations: List[str] = field(default_factory=list)
    has_data: bool = False
    # notice_only: cau tra loi mang tinh THONG BAO (tu choi quyen, thieu tham
    # so, tinh nang chua kha dung) — KHONG phai "khong tim thay du lieu", nen
    # khong hien canh bao "vui long cung cap them thong tin".
    notice_only: bool = False


# Gioi han so ban ghi dua vao prompt de tranh phinh to context khi hoi chung
# chung (1 hoc sinh x 16 mon x 6 hoc ky ~ 96 ban ghi; nhieu ten fuzzy co the hon).
_MAX_PROMPT_RECORDS = 250


def _limit_records(records: List[GradeRecord]) -> List[GradeRecord]:
    if len(records) <= _MAX_PROMPT_RECORDS:
        return records
    logger.info("Cat bot ban ghi dua vao prompt: %d -> %d", len(records), _MAX_PROMPT_RECORDS)
    return records[:_MAX_PROMPT_RECORDS]


# ---------------------------------------------------------------------------
# ChatbotEngine — tra cuu diem / danh sach lop / thoi khoa bieu / ...
#
# LUU Y QUAN TRONG VE DA NGUOI DUNG: instance nay la 1 singleton dung chung
# cho toan bo server (Streamlit @st.cache_resource), moi nguoi dung dang nhap
# deu goi chung 1 ChatbotEngine. Vi vay KHONG duoc luu thong tin nguoi dung
# hien tai (vai tro, student_id...) vao thuoc tinh cua self — phai truyen
# session_user theo tung loi goi chat()/chat_streaming() de tranh lo du lieu
# giua cac nguoi dung dang dang nhap dong thoi.
# ---------------------------------------------------------------------------

class ChatbotEngine:
    def __init__(self):
        self.memory = MemoryManager()
        self.school_info = None
        self.auth = None

        if USE_SUPABASE:
            from src.grades.supabase_store import SupabaseGradeStore
            from src.grades.school_info import SchoolInfoStore
            self.store = SupabaseGradeStore(SUPABASE_URL, SUPABASE_KEY, SUPABASE_SUBJECT_NAME)
            self.school_info = SchoolInfoStore(SUPABASE_URL, SUPABASE_KEY)
            logger.info("Nguon du lieu diem: Supabase")
        else:
            self.store = GradeStore(GRADES_DIR)
            logger.info("Nguon du lieu diem: Excel (%s)", GRADES_DIR)

        if USE_AUTH:
            from src.auth.auth_service import AuthService
            self.auth = AuthService(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_KEY)
            logger.info("Dang nhap/phan quyen: BAT (Supabase Auth)")
        else:
            logger.info("Dang nhap/phan quyen: TAT (khong co SUPABASE_ANON_KEY)")

        self._is_ready = False
        logger.info("ChatbotEngine da khoi tao (chua load du lieu diem)")

    def initialize(self) -> bool:
        self.store.load()
        self._is_ready = self.store.is_ready()
        if not self._is_ready:
            logger.warning("Chua co du lieu diem trong %s", GRADES_DIR)
        return self._is_ready

    def reload_index(self) -> bool:
        return self.initialize()

    def is_ready(self) -> bool:
        return self._is_ready

    def get_index_stats(self) -> dict:
        if not self._is_ready:
            return {"status": "Chua khoi tao"}
        return self.store.stats()

    # -- bo chon vai tro (demo) — tao SessionUser khong qua dang nhap -------

    def list_students_for_picker(self) -> List[dict]:
        """Danh sach (full_name, student_code) duy nhat de chon o UI demo.
        Lay tu du lieu diem da nap (khong goi them DB)."""
        seen = {}
        for r in self.store.records:
            if r.student_id and r.student_id not in seen:
                seen[r.student_id] = r.name
        return sorted(
            ({"student_code": code, "full_name": name} for code, name in seen.items()),
            key=lambda x: x["full_name"],
        )

    def make_demo_user(self, role_name: str, student_code: Optional[str] = None):
        """Tao SessionUser demo cho bo chon vai tro (khong xac thuc that)."""
        from src.auth.auth_service import SessionUser, ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT

        if role_name == ROLE_STUDENT:
            full_name = student_code or ""
            student_id = None
            if student_code and self.school_info is not None:
                stu = self.school_info.get_student_by_code(student_code)
                if stu:
                    student_id = stu.get("student_id")
                    full_name = stu.get("full_name") or full_name
            if not full_name:
                # Excel mode / khong co school_info: lay ten tu du lieu diem
                for r in self.store.records:
                    if r.student_id == student_code:
                        full_name = r.name
                        break
            return SessionUser(
                user_id=-3, email="", full_name=full_name or "Học sinh",
                role_name=ROLE_STUDENT, student_id=student_id, student_code=student_code,
            )

        if role_name == ROLE_TEACHER:
            return SessionUser(user_id=-2, email="", full_name="Giáo viên (demo)", role_name=ROLE_TEACHER)
        return SessionUser(user_id=-1, email="", full_name="Quản trị viên (demo)", role_name=ROLE_ADMIN)

    # -- tra cuu diem (mac dinh) ------------------------------------------

    def _current_school_year(self) -> Optional[str]:
        """Nam hoc HIEN TAI tu Supabase (is_current=TRUE).
        Neu khong co (che do Excel) thi lay nam moi nhat co trong du lieu diem."""
        if self.school_info is not None:
            try:
                ans = self.school_info.get_current_school_year()
                if ans:
                    return ans
            except Exception as e:
                logger.warning("Khong lay duoc nam hoc hien tai: %s", e)
        years = self.store.list_school_years()
        return years[-1] if years else None

    def _resolve_year_filter(self, question: str, explicit_year: Optional[str]) -> Optional[str]:
        """Xac dinh nam hoc can loc khi tra cuu diem:
        - Neu cau hoi neu ro nam (vd 2024-2025) -> dung nam do.
        - Neu hoi "qua cac nam / tat ca cac nam" -> None (khong loc, lay het).
        - Mac dinh (hoi chung chung) -> nam hoc HIEN TAI."""
        if explicit_year:
            return explicit_year
        if wants_all_years(question):
            return None
        return self._current_school_year()

    def _resolve_records(
        self, question: str, filters: dict, forced_student_code: Optional[str] = None,
    ) -> Tuple[List[GradeRecord], List[str]]:
        """Tra ve (danh sach ban ghi khop, danh sach ten goi y neu khong khop).

        forced_student_code: neu duoc truyen (hoc sinh dang dang nhap), bo qua
        hoan toan viec tim ten trong cau hoi va CHI loc theo ma hoc sinh nay —
        ngan hoc sinh xem duoc diem cua nguoi khac bang cach go ten khac.

        Neu cau hoi co ten mon cu cu the -> chi tra diem mon do; neu hoi chung
        chung -> tra diem TAT CA cac mon."""
        name_query = filters.get("name_query")
        subject = filters.get("subject")

        # Nam hoc: mac dinh nam hien tai; neu neu ro nam -> nam do; neu hoi
        # "qua cac nam" -> tat ca (None).
        year_filter = self._resolve_year_filter(question, filters["school_year"])

        # Xac dinh danh sach MA hoc sinh can lay diem (viec khop ten dua tren
        # chi muc ten da nap; ma/ten it thay doi). Sau do lay DIEM MOI NHAT truc
        # tiep tu nguon (real-time voi Supabase) qua fetch_for_codes().
        if forced_student_code:
            codes = [forced_student_code]
            suggestions: List[str] = []
        else:
            if not name_query:
                return [], []
            matched_names = self.store.find_matching_names(name_query)
            if not matched_names:
                return [], []
            suggestions = matched_names
            nameset = set(matched_names)
            codes = sorted({r.student_id for r in self.store.records if r.name in nameset and r.student_id})
            if not codes:
                return [], matched_names

        fresh = self.store.fetch_for_codes(codes)
        records = [
            r for r in fresh
            if (not filters["class_name"] or r.class_name.upper() == filters["class_name"].upper())
            and (not year_filter or r.school_year == year_filter)
            and (not filters["semester"] or r.semester == filters["semester"])
            and (not subject or r.subject == subject)
        ]
        # Sap xep theo (hoc sinh, mon, nam, hoc ky) de HK I luon lien truoc HK II
        # cua cung mon -> LLM trinh bay dung, khong dao HK.
        records.sort(key=lambda r: (r.name, r.subject, r.school_year, r.semester))
        return _limit_records(records), suggestions

    # -- danh sach lop / thoi khoa bieu -----------------------------------

    def _resolve_roster(self, question: str, filters: dict) -> _LookupResult:
        class_name, school_year = filters.get("class_name"), filters.get("school_year")

        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "danh sách lớp"))

        missing = []
        if not class_name:
            missing.append("tên lớp")
        if not school_year:
            missing.append("năm học")
        if missing:
            return _LookupResult(build_no_roster_params_prompt(question, missing), notice_only=True)

        roster = self.school_info.get_class_roster(class_name, school_year)
        prompt = build_roster_prompt(question, class_name, school_year, roster)
        citations = [f"Danh sách lớp {class_name} - Năm học {school_year}"] if roster else []
        return _LookupResult(prompt, citations, bool(roster))

    def _resolve_timetable(self, question: str, filters: dict, session_user=None) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "thời khóa biểu"))

        class_name, school_year, semester = filters.get("class_name"), filters.get("school_year"), filters.get("semester")

        # HOC SINH: mac dinh LUON xem TKB CUA CHINH MINH trong ky hien tai,
        # ke ca khi hoi chung chung (vd chi go "tkb") — khong can noi "cua toi".
        is_student = session_user is not None and getattr(session_user, "is_student", False)
        if is_student and session_user.student_id is not None:
            cur = self.school_info.get_current_term(date.today().isoformat())
            year_name = school_year or (cur["year_name"] if cur else None)
            if year_name:
                resolved_class = self.school_info.get_student_class(session_user.student_id, year_name)
                if resolved_class:
                    class_name, school_year = resolved_class, year_name

        missing = []
        if not class_name:
            missing.append("tên lớp")
        if not school_year:
            missing.append("năm học")
        if missing:
            return _LookupResult(build_no_timetable_params_prompt(question, missing), notice_only=True)

        # Xac dinh hoc ky: uu tien "hoc ky 1/2" neu neu ro; nguoc lai dung ky
        # hien tai (real-time) khi cung nam hoc.
        if semester:
            term_order = 2 if semester == "II" else 1
            term_label = f"Học kỳ {semester}"
        elif cur and school_year == cur.get("year_name"):
            term_order = cur.get("term_order")
            term_label = f"Học kỳ {'II' if term_order == 2 else 'I'} (hiện tại)"
        else:
            term_order = None
            term_label = None

        # TKB 1 hoc ky la CO DINH -> lay 1 tuan dai dien (tuan hien tai theo ngay
        # thuc) va loc dung hoc ky de tranh lan du lieu giua 2 ky trong cung tuan.
        week_start = self.school_info.pick_representative_week(
            class_name, school_year, date.today().isoformat()
        )
        rows = self.school_info.get_timetable(
            class_name, school_year, week_start=week_start, term_order=term_order
        )

        prompt = build_timetable_prompt(question, class_name, school_year, term_label, rows)
        cite = f"Thời khóa biểu lớp {class_name} - Năm học {school_year}"
        if term_label:
            cite += f" - {term_label}"
        citations = [cite] if rows else []
        return _LookupResult(prompt, citations, bool(rows))

    def _resolve_attendance(
        self, question: str, filters: dict, forced_student_id: Optional[int] = None, forced_full_name: Optional[str] = None,
    ) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "điểm danh"))

        if forced_student_id is not None:
            student_ids = [forced_student_id]
            display_name = forced_full_name or ""
        else:
            name_query = filters.get("name_query")
            if not name_query:
                return _LookupResult(build_no_attendance_match_prompt(question, []))

            matched_names = self.store.find_matching_names(name_query)
            if not matched_names:
                return _LookupResult(build_no_attendance_match_prompt(question, []))

            students = self.school_info.find_student_ids_by_names(matched_names)
            if not students:
                return _LookupResult(build_no_attendance_match_prompt(question, matched_names))

            student_ids = [s["student_id"] for s in students]
            display_name = students[0]["full_name"] if students else ""

        records = self.school_info.get_attendance(student_ids)
        prompt = build_attendance_prompt(question, records)
        citations = [f"Điểm danh của {display_name}"] if records else []
        return _LookupResult(prompt, citations, bool(records))

    def _resolve_exam_schedule(self, question: str, filters: dict, session_user=None) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "lịch thi"))

        class_name, school_year, semester = filters.get("class_name"), filters.get("school_year"), filters.get("semester")

        # Hoc ky HIEN TAI theo ngay thuc (co fallback ve ky gan nhat khi nghi he)
        cur = self.school_info.get_current_term(date.today().isoformat())

        # HOC SINH: mac dinh LUON xem lich thi CUA CHINH MINH trong ky hien tai,
        # ke ca khi hoi chung chung (vd chi go "lich thi") — khong can noi "cua
        # toi", va khong xem duoc lich thi lop khac. Tu suy ra lop tu ho so.
        is_student = session_user is not None and getattr(session_user, "is_student", False)
        if is_student and session_user.student_id is not None:
            year_name = school_year or (cur["year_name"] if cur else None)
            if year_name:
                resolved_class = self.school_info.get_student_class(session_user.student_id, year_name)
                if resolved_class:
                    class_name, school_year = resolved_class, year_name

        missing = []
        if not class_name:
            missing.append("tên lớp")
        if not school_year:
            missing.append("năm học")
        if missing:
            return _LookupResult(build_no_exam_params_prompt(question, missing), notice_only=True)

        # Xac dinh hoc ky: uu tien "hoc ky 1/2" neu neu ro; nguoc lai dung ky
        # hien tai (real-time) khi cung nam hoc.
        if semester:
            term_order = 2 if semester == "II" else 1
            term_label = f"Học kỳ {semester}"
        elif cur and school_year == cur.get("year_name"):
            term_order = cur.get("term_order")
            suffix = "hiện tại" if cur.get("is_current") else "gần nhất"
            term_label = f"Học kỳ {'II' if term_order == 2 else 'I'} ({suffix})"
        else:
            term_order = None
            term_label = None

        rows = self.school_info.get_exam_schedule(class_name, school_year, term_order=term_order)
        # Ky hien tai chua co lich thi va nguoi dung khong chi ro hoc ky -> hien
        # lich thi hien co cua lop (moi ky) de van tra ket qua huu ich.
        if not rows and term_order and not semester:
            rows = self.school_info.get_exam_schedule(class_name, school_year, term_order=None)
            if rows:
                term_label = "lịch thi hiện có"

        prompt = build_exam_schedule_prompt(question, class_name, school_year, term_label, rows)
        cite = f"Lịch thi lớp {class_name} - Năm học {school_year}"
        if term_label:
            cite += f" - {term_label}"
        citations = [cite] if rows else []
        return _LookupResult(prompt, citations, bool(rows))

    def _resolve_student_info(self, question: str, filters: dict) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "thông tin học sinh"))

        # Uu tien tra cuu theo ma hoc sinh neu cau hoi co (vd HS00457)
        codes = [m.group(0).upper() for m in _STUDENT_CODE_RE.finditer(question)]
        if codes:
            profiles = self.school_info.get_student_profiles(codes=codes)
            citations = [f"Thông tin học sinh {p.get('student_code')}" for p in profiles]
            return _LookupResult(build_student_info_prompt(question, profiles), citations, bool(profiles))

        name_query = filters.get("name_query")
        if not name_query:
            return _LookupResult(build_student_info_prompt(question, []))

        matched_names = self.store.find_matching_names(name_query)
        if not matched_names:
            return _LookupResult(build_student_info_prompt(question, []))

        profiles = self.school_info.get_student_profiles(names=matched_names)
        citations = [f"Thông tin học sinh: {p.get('full_name')}" for p in profiles]
        return _LookupResult(build_student_info_prompt(question, profiles), citations, bool(profiles))

    # -- tong ket / xep loai (Thong tu 22) --------------------------------

    def _resolve_summary(self, question: str, filters: dict, forced_student_code: Optional[str] = None) -> _LookupResult:
        name_query = filters.get("name_query")
        school_year = filters.get("school_year")
        semester = filters.get("semester")
        target = "I" if semester == "I" else "II" if semester == "II" else "year"

        # Xac dinh ma hoc sinh roi lay diem MOI NHAT truc tiep tu nguon (real-time)
        if forced_student_code:
            codes = [forced_student_code]
            display_name = None
        else:
            if not name_query:
                return _LookupResult(build_no_match_prompt(question, []))
            matched = self.store.find_matching_names(name_query)
            if not matched:
                return _LookupResult(build_no_match_prompt(question, []))
            names = set(matched)
            codes = sorted({r.student_id for r in self.store.records if r.name in names and r.student_id})
            display_name = matched[0] if matched else None
        recs = self.store.fetch_for_codes(codes)
        if not recs:
            return _LookupResult(build_no_match_prompt(question, []))

        # Chon nam hoc: uu tien nam duoc neu ro, nguoc lai lay nam moi nhat co du lieu
        if school_year:
            recs = [r for r in recs if r.school_year == school_year]
        else:
            years = sorted({r.school_year for r in recs})
            school_year = years[-1] if years else None
            recs = [r for r in recs if r.school_year == school_year]

        student_name = display_name or (recs[0].name if recs else "")
        if not recs:
            return _LookupResult(build_no_match_prompt(question, []))

        term_word = "Học kỳ I" if target == "I" else "Học kỳ II" if target == "II" else "cả năm"
        term_label = f"{term_word} năm học {school_year}"
        summary = summarize_student(recs, target)
        has = bool(summary.get("numeric") or summary.get("nhanxet"))
        prompt = build_summary_prompt(question, student_name, term_label, summary)
        cite = f"Tổng kết {term_label} - {student_name}"
        return _LookupResult(prompt, [cite] if has else [], has)

    # -- thong ke lop (giao vien / admin) ---------------------------------

    def _resolve_class_stats(self, question: str, filters: dict) -> _LookupResult:
        class_name = filters.get("class_name")
        school_year = filters.get("school_year")
        semester = filters.get("semester")
        subject = filters.get("subject")
        target = "I" if semester == "I" else "II" if semester == "II" else "year"

        if not class_name:
            return _LookupResult(
                f"Cau hoi hoi ve thong ke lop nhung chua ro TEN LOP.\n\n"
                f"Cau hoi cua nguoi dung: {question}\n\n"
                f"Hay lich su de nghi nguoi dung cho biet ten lop (vd 6A) va nam hoc de thong ke.",
                notice_only=True,
            )

        # Danh sach ma hoc sinh cua lop (lay tu chi muc da nap — DS lop on dinh),
        # sau do lay diem MOI NHAT truc tiep tu nguon (real-time).
        cache_recs = [r for r in self.store.records if r.class_name.upper() == class_name.upper()]
        if school_year:
            cache_recs = [r for r in cache_recs if r.school_year == school_year]
        else:
            years = sorted({r.school_year for r in cache_recs})
            school_year = years[-1] if years else None
            cache_recs = [r for r in cache_recs if r.school_year == school_year]
        codes = sorted({r.student_id for r in cache_recs if r.student_id})

        fresh = self.store.fetch_for_codes(codes)
        recs = [r for r in fresh if not school_year or r.school_year == school_year]

        term_word = "Học kỳ I" if target == "I" else "Học kỳ II" if target == "II" else "cả năm"
        term_label = f"{term_word}" if school_year else None
        stats = class_stats(recs, subject, target)
        has = bool(stats.get("num_students"))
        prompt = build_class_stats_prompt(question, class_name, school_year or "(chưa rõ)", term_label, subject, stats)
        cite = f"Thống kê lớp {class_name}"
        if school_year:
            cite += f" - {school_year}"
        if subject:
            cite += f" - {subject}"
        return _LookupResult(prompt, [cite] if has else [], has)

    # -- tra cuu giao vien ------------------------------------------------

    def _resolve_teacher(self, question: str, filters: dict, include_contact: bool = True) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "tra cứu giáo viên"))

        class_name = filters.get("class_name")
        school_year = filters.get("school_year")
        subject = filters.get("subject")
        name_query = filters.get("name_query")
        q_low = question.lower()
        wants_homeroom = any(k in q_low for k in ["chủ nhiệm", "gvcn"])

        if not school_year:
            years = self.store.list_school_years()
            school_year = years[-1] if years else None

        def _teacher_line(t: dict) -> str:
            parts = [f"{t.get('full_name', '')}"]
            if t.get("teacher_code"):
                parts.append(f"(Mã GV: {t['teacher_code']})")
            if t.get("subject_name"):
                parts.append(f"- môn: {t['subject_name']}")
            if t.get("title"):
                parts.append(f"- chức vụ: {t['title']}")
            if include_contact and t.get("phone"):
                parts.append(f"- SĐT: {t['phone']}")
            return "- " + " ".join(parts)

        # 1) Theo lop
        if class_name and school_year:
            if wants_homeroom:
                t = self.school_info.get_homeroom_teacher(class_name, school_year)
                header = f"Giáo viên chủ nhiệm lớp {class_name} năm học {school_year}:"
                lines = [_teacher_line(t)] if t else []
                cite = f"GVCN lớp {class_name} - {school_year}"
                return _LookupResult(build_teacher_prompt(question, header, lines),
                                     [cite] if lines else [], bool(lines))

            teachers = self.school_info.get_class_teachers(class_name, school_year)
            if subject:
                teachers = [t for t in teachers if t.get("subject_name") == subject]
                header = f"Giáo viên dạy môn {subject} lớp {class_name} năm học {school_year}:"
            else:
                header = f"Giáo viên bộ môn lớp {class_name} năm học {school_year}:"
            lines = [f"- {t['subject_name']}: {t['full_name']}"
                     + (f" (Mã GV: {t['teacher_code']})" if t.get("teacher_code") else "")
                     for t in teachers]
            cite = f"Giáo viên lớp {class_name} - {school_year}"
            return _LookupResult(build_teacher_prompt(question, header, lines),
                                 [cite] if lines else [], bool(lines))

        # 2) Theo ten / ma giao vien
        if name_query:
            teachers = self.school_info.find_teachers_by_name(name_query)
            if teachers:
                header = "Thông tin giáo viên:"
                lines = []
                for t in teachers:
                    lines.append(_teacher_line(t))
                    assign = self.school_info.get_teacher_assignments(t["teacher_id"], school_year)
                    for h in assign.get("homeroom", []):
                        lines.append(f"    • Chủ nhiệm lớp {h['class_name']} (năm {h['year_name']})")
                    taught = assign.get("teaching", [])
                    if taught:
                        pairs = ", ".join(
                            f"{a['class_name']}"
                            + (f"/{a['subject_name']}" if a.get("subject_name") else "")
                            for a in taught
                        )
                        lines.append(f"    • Dạy: {pairs}")
                cite = f"Thông tin giáo viên: {teachers[0].get('full_name')}"
                return _LookupResult(build_teacher_prompt(question, header, lines), [cite], True)

        return _LookupResult(build_teacher_prompt(question, "", []))

    def _resolve_notifications(self, question: str) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "thông báo"))

        notifications = self.school_info.get_recent_notifications()
        prompt = build_notifications_prompt(question, notifications)
        citations = ["Thông báo nhà trường (gần đây nhất)"] if notifications else []
        return _LookupResult(prompt, citations, bool(notifications))

    def _resolve_activities(self, question: str, filters: dict) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "hoạt động ngoại khóa"))

        school_year, semester = filters.get("school_year"), filters.get("semester")

        activities = self.school_info.get_activities(school_year, semester)
        prompt = build_activities_prompt(question, activities)
        cite = "Hoạt động ngoại khóa"
        if school_year:
            cite += f" - Năm học {school_year}"
        if semester:
            cite += f" - Học kỳ {semester}"
        citations = [cite] if activities else []
        return _LookupResult(prompt, citations, bool(activities))

    # -- phan quyen theo vai tro dang nhap ----------------------------------

    def _build_lookup(self, question: str, session_user=None) -> _LookupResult:
        session_id = self._session_id_for(session_user)
        history = self.memory.get_chat_history(session_id)
        
        analysis = analyze_query_llm(question, history)
        intent = analysis["intent"]
        filters = analysis["filters"]

        # Gan mac dinh nam hoc hien tai neu khong cung cap va khong phai "tat ca cac nam"
        if not filters.get("school_year") and not wants_all_years(question):
            filters["school_year"] = self._current_school_year()

        if session_user is not None and session_user.is_student:
            # Hoc sinh: chi duoc xem diem/diem danh CUA CHINH MINH, khong duoc
            # xem danh sach lop hay tra cuu ho so hoc sinh khac (viec cua giao
            # vien/admin).
            if intent == "roster":
                return _LookupResult(build_permission_denied_prompt(question, "danh sách lớp"), notice_only=True)
            if intent == "student_info":
                return _LookupResult(build_permission_denied_prompt(question, "tra cứu thông tin học sinh"), notice_only=True)
            if intent == "class_stats":
                return _LookupResult(build_permission_denied_prompt(question, "thống kê điểm cả lớp"), notice_only=True)
            if intent == "summary":
                return self._resolve_summary(question, filters, forced_student_code=session_user.student_code)
            if intent == "grade":
                records, _ = self._resolve_records(question, filters, forced_student_code=session_user.student_code)
                if not records:
                    return _LookupResult(build_no_match_prompt(question, []))
                return _LookupResult(build_grade_prompt(question, records), grade_citation_lines(records), True)
            if intent == "attendance":
                return self._resolve_attendance(
                    question,
                    filters,
                    forced_student_id=session_user.student_id,
                    forced_full_name=session_user.full_name,
                )
            # timetable / exam / notification / activity: khong gioi han rieng
            # cho hoc sinh (thong tin chung cua lop/truong, khong nhay cam).

        if intent == "roster":
            return self._resolve_roster(question, filters)
        if intent == "student_info":
            return self._resolve_student_info(question, filters)
        if intent == "teacher":
            include_contact = not (session_user is not None and getattr(session_user, "is_student", False))
            return self._resolve_teacher(question, filters, include_contact=include_contact)
        if intent == "class_stats":
            return self._resolve_class_stats(question, filters)
        if intent == "summary":
            return self._resolve_summary(question, filters)
        if intent == "timetable":
            return self._resolve_timetable(question, filters, session_user)
        if intent == "exam":
            return self._resolve_exam_schedule(question, filters, session_user)
        if intent == "attendance":
            return self._resolve_attendance(question, filters)
        if intent == "notification":
            return self._resolve_notifications(question)
        if intent == "activity":
            return self._resolve_activities(question, filters)

        records, suggestions = self._resolve_records(question, filters)
        if not records:
            return _LookupResult(build_no_match_prompt(question, suggestions))
        return _LookupResult(build_grade_prompt(question, records), grade_citation_lines(records), True)

    @staticmethod
    def _session_id_for(session_user) -> str:
        return session_user.session_key if session_user is not None else DEFAULT_SESSION_ID

    # -- chat (sync) ----------------------------------------------------

    def chat(self, question: str, provider: str = DEFAULT_LLM_PROVIDER, session_user=None) -> FinalResponse:
        if not self._is_ready:
            raise RuntimeError("Engine chua san sang. Chua co du lieu diem trong thu muc data/diem_khtn.")

        session_id = self._session_id_for(session_user)
        trace = create_trace(
            name="chat_request", input_data={"question": question},
            metadata={"mode": "sync", "role": getattr(session_user, "role_name", None)},
        )
        self.memory.add_user_message(question, session_id=session_id)

        lookup_span = create_span(trace, "lookup", input_data={"question": question})
        lookup = self._build_lookup(question, session_user)
        end_observation(lookup_span, output={"has_data": lookup.has_data, "num_citations": len(lookup.citations)})

        llm_answer = call_llm(
            user_prompt=lookup.prompt,
            chat_history=self.memory.get_chat_history(session_id),
            provider=provider,
            langfuse_parent=trace,
        )
        response = build_final_response(
            llm_answer=llm_answer, citations=lookup.citations, has_data=lookup.has_data, question=question,
            suppress_no_data_warning=lookup.notice_only,
        )
        self.memory.add_assistant_message(message=response.answer_text, question=question, session_id=session_id)
        return response

    # -- chat (streaming) -------------------------------------------------

    def chat_streaming(
        self, question: str, provider: str = DEFAULT_LLM_PROVIDER, session_user=None,
    ) -> Tuple[Generator[str, None, None], _LookupResult]:
        if not self._is_ready:
            raise RuntimeError("Engine chua san sang. Chua co du lieu diem trong thu muc data/diem_khtn.")

        session_id = self._session_id_for(session_user)
        trace = create_trace(
            name="chat_stream_request", input_data={"question": question},
            metadata={"mode": "stream", "role": getattr(session_user, "role_name", None)},
        )
        self.memory.add_user_message(question, session_id=session_id)

        lookup_span = create_span(trace, "lookup", input_data={"question": question})
        lookup = self._build_lookup(question, session_user)
        end_observation(lookup_span, output={"has_data": lookup.has_data, "num_citations": len(lookup.citations)})

        stream_gen = call_llm_streaming(
            user_prompt=lookup.prompt,
            chat_history=self.memory.get_chat_history(session_id),
            provider=provider,
            langfuse_parent=trace,
        )
        return stream_gen, lookup

    def finalize_streaming_response(
        self,
        full_text: str,
        lookup: _LookupResult,
        question: str,
        session_user=None,
    ) -> FinalResponse:
        response = build_final_response(
            llm_answer=full_text, citations=lookup.citations, has_data=lookup.has_data, question=question,
            suppress_no_data_warning=lookup.notice_only,
        )
        session_id = self._session_id_for(session_user)
        self.memory.add_assistant_message(message=full_text, question=question, session_id=session_id)
        return response

    def clear_session(self, session_user=None) -> None:
        self.memory.clear_session(self._session_id_for(session_user))
