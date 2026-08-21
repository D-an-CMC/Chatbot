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


# ---------------------------------------------------------------------------
# Trich xuat bo loc (ten / lop / nam hoc / hoc ky) tu cau hoi tu do
# ---------------------------------------------------------------------------

_STUDENT_CODE_RE = re.compile(r"\bHS\d{3,}\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\s*[-–]\s*(20\d{2})\b")
_CLASS_RE = re.compile(r"\blớp\s*([6-9])\s*([A-Ca-c])\b", re.IGNORECASE)
_BARE_CLASS_RE = re.compile(r"\b([6-9])\s*([A-Ca-c])\b")
_SEMESTER_RE = re.compile(r"(học\s*k[ỳì]|k[ỳì]|hk)\s*([I1]{1,2}|2)\b", re.IGNORECASE)

_STOPWORD_PHRASES = [
    "tất cả các môn", "tất cả các", "tất cả", "các môn", "mọi môn", "toàn bộ", "các",
    "điểm số", "điểm của", "điểm trung bình", "điểm giữa kỳ", "điểm cuối kỳ",
    "điểm cả năm", "điểm", "cho tôi biết", "cho em biết", "cho mình biết",
    "làm ơn", "vui lòng", "học sinh", "bạn học", "bạn", "em ơi", "của em", "của bạn",
    "của", "là bao nhiêu", "là gì", "như thế nào", "thế nào", "ra sao",
    "môn khoa học tự nhiên", "khoa học tự nhiên", "môn vật lý", "vật lý", "môn",
    "cả năm", "giữa kỳ", "cuối kỳ", "trung bình", "tổng kết",
    "nhận xét", "tra cứu điểm", "tra cứu", "xem điểm", "xem", "hãy cho", "hãy",
    "cho", "tôi", "mình", "hộ", "giúp", "với", "nhé",
    # cum kich hoat tra cuu thong tin ho so hoc sinh (loai khoi phan tim ten)
    "thông tin học sinh", "thông tin liên hệ", "thông tin", "hồ sơ học sinh", "hồ sơ",
    "phụ huynh", "địa chỉ", "ngày sinh", "số điện thoại", "sđt", "liên hệ",
    "mã học sinh", "mã hs", "giới tính",
    # cum kich hoat tra cuu giao vien / tong ket (loai khoi phan tim ten)
    "giáo viên chủ nhiệm", "giáo viên bộ môn", "giáo viên dạy", "giáo viên", "gvcn",
    "chủ nhiệm", "dạy môn", "dạy lớp", "dạy", "thầy giáo", "cô giáo", "thầy", "cô", "ai",
    "xếp loại", "học lực", "kết quả học tập", "tổng kết", "kết quả", "danh hiệu",
    "lớp", "nào", "gì", "những", "thống kê", "top",
]


def _strip_stopwords(question: str) -> str:
    # Loai bo cac cum gan voi so/ky hieu truoc (nam hoc, lop, hoc ky) de tranh
    # con sot lai chu so/so La Ma le loi khi phan cum bi cat rieng le.
    text = _YEAR_RE.sub(" ", question)
    text = _CLASS_RE.sub(" ", text)
    text = _BARE_CLASS_RE.sub(" ", text)
    text = _SEMESTER_RE.sub(" ", text)
    text = re.sub(r"\bnăm\s*học\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnăm\b", " ", text, flags=re.IGNORECASE)

    for phrase in _STOPWORD_PHRASES:
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", text, flags=re.IGNORECASE)

    text = re.sub(r"[?.,!:;]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Nhan dien MON HOC trong cau hoi
# ---------------------------------------------------------------------------
# Chia 2 nhom:
#  - _SUBJECT_ANY: alias an toan (nhieu tu / viet tat dac thu) -> match bat ky dau.
#  - _SUBJECT_AFTER_MARK: alias ngan de trung ten nguoi (toan, van, ly, hoa,
#    su, dia, tin, nhac...) -> CHI nhan dien khi dung ngay sau "diem"/"mon",
#    tranh hieu nham ten hoc sinh (vd "Nhat Anh", "Toan", "Van") thanh mon.

_SUBJECT_ANY = {
    "khoa hoc tu nhien": "Khoa học tự nhiên",
    "khtn": "Khoa học tự nhiên",
    "vat ly": "Khoa học tự nhiên",
    "hoa hoc": "Khoa học tự nhiên",
    "sinh hoc": "Khoa học tự nhiên",
    "ngu van": "Ngữ Văn",
    "tieng anh": "Tiếng Anh",
    "anh van": "Tiếng Anh",
    "lich su": "Lịch sử",
    "dia ly": "Địa Lý",
    "tin hoc": "Tin học",
    "cong nghe": "Công nghệ",
    "the duc": "Thể dục",
    "am nhac": "Âm nhạc",
    "my thuat": "Mỹ thuật",
    "giao duc cong dan": "Giáo dục công dân",
    "gdcd": "Giáo dục công dân",
    "chao co": "Chào cờ",
    "sinh hoat lop": "Sinh hoạt lớp",
    "hoat dong trai nghiem huong nghiep": "Hoạt động trải nghiệm hướng nghiệp",
    "hoat dong trai nghiem": "Hoạt động trải nghiệm hướng nghiệp",
    "trai nghiem huong nghiep": "Hoạt động trải nghiệm hướng nghiệp",
    "noi dung giao duc dia phuong": "Nội dung giáo dục địa phương",
    "giao duc dia phuong": "Nội dung giáo dục địa phương",
}
_SUBJECT_AFTER_MARK = {
    "toan": "Toán",
    "van": "Ngữ Văn",
    "anh": "Tiếng Anh",
    "su": "Lịch sử",
    "dia": "Địa Lý",
    "tin": "Tin học",
    "nhac": "Âm nhạc",
    "ly": "Khoa học tự nhiên",
    "hoa": "Khoa học tự nhiên",
    "sinh": "Khoa học tự nhiên",
}
_SUBJECT_MARKS = {"diem", "mon", "day"}  # "day" = "dạy" (vd: ai dạy Toán)


def _contains_phrase(tokens: List[str], phrase: str) -> bool:
    parts = phrase.split()
    n = len(parts)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == parts:
            return True
    return False


def detect_subject(question: str, known_subjects: Optional[Set[str]] = None):
    """Nhan dien ten mon trong cau hoi.

    Tra ve (subject_name | None, alias_da_match | None). alias tra ve de loai
    khoi phan tim ten hoc sinh. known_subjects (neu co) gioi han chi nhan dien
    cac mon that su ton tai trong du lieu."""
    nq = normalize_name(question)
    tokens = nq.split()

    def _ok(subj):
        return known_subjects is None or subj in known_subjects

    # 1) alias an toan — uu tien cum dai truoc de tranh khop mot phan
    for alias in sorted(_SUBJECT_ANY, key=lambda a: -len(a)):
        if _contains_phrase(tokens, alias) and _ok(_SUBJECT_ANY[alias]):
            return _SUBJECT_ANY[alias], alias

    # 2) alias ngan — chi khi dung ngay sau "diem"/"mon"
    for i, tok in enumerate(tokens):
        if tok in _SUBJECT_MARKS and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if nxt in _SUBJECT_AFTER_MARK and _ok(_SUBJECT_AFTER_MARK[nxt]):
                return _SUBJECT_AFTER_MARK[nxt], nxt
    return None, None


def _strip_alias_tokens(name_query: str, alias: str) -> str:
    """Loai cac tu thuoc alias mon khoi chuoi tim ten (lam viec tren dang
    khong dau — find_matching_names se tu chuan hoa lai nen khong anh huong)."""
    alias_toks = set(alias.split())
    kept = [t for t in normalize_name(name_query).split() if t not in alias_toks]
    return " ".join(kept).strip()


def extract_query_filters(question: str, known_subjects: Optional[Set[str]] = None) -> dict:
    filters: dict = {"school_year": None, "semester": None, "class_name": None, "subject": None}

    year_match = _YEAR_RE.search(question)
    if year_match:
        filters["school_year"] = f"{year_match.group(1)}-{year_match.group(2)}"

    class_match = _CLASS_RE.search(question) or _BARE_CLASS_RE.search(question)
    if class_match:
        filters["class_name"] = f"{class_match.group(1)}{class_match.group(2)}".upper()

    sem_match = _SEMESTER_RE.search(question)
    if sem_match:
        g = sem_match.group(2).lower()
        filters["semester"] = "II" if g in ("ii", "2") else "I"

    subject, alias = detect_subject(question, known_subjects)
    filters["subject"] = subject

    name = _strip_stopwords(question)
    if alias:
        name = _strip_alias_tokens(name, alias)
    filters["name_query"] = name
    return filters


# ---------------------------------------------------------------------------
# Phan loai y dinh cua cau hoi
# ---------------------------------------------------------------------------

_TIMETABLE_KEYWORDS = [
    "thời khóa biểu", "tkb", "lịch học", "lịch dạy", "học vào thứ", "tiết mấy", "lịch dạy học",
]
_EXAM_KEYWORDS = [
    "lịch thi", "lịch kiểm tra", "thi cuối kỳ", "thi giữa kỳ", "ngày thi", "thi khi nào",
    "thi môn gì", "thi những môn", "thi hôm nào", "khi nào thi", "bao giờ thi",
    "sắp thi", "sắp tới thi", "có thi không", "lịch thi cử",
]
_ATTENDANCE_KEYWORDS = [
    "điểm danh", "vắng học", "nghỉ học", "đi học đầy đủ", "có mặt",
    "vắng mấy buổi", "nghỉ mấy buổi", "nghỉ buổi", "vắng buổi",
    "nghỉ những buổi", "vắng những buổi", "buổi học nào", "nghỉ hôm nào",
    "vắng hôm nào", "nghỉ ngày nào", "vắng ngày nào", "đã nghỉ", "đi muộn", "đi trễ",
]
_NOTIFICATION_KEYWORDS = [
    "thông báo",
]
_ACTIVITY_KEYWORDS = [
    "hoạt động ngoại khóa", "hoạt động ngoài giờ", "sự kiện", "sinh hoạt tập thể", "hoạt động",
]
_ROSTER_KEYWORDS = [
    "danh sách lớp", "danh sách học sinh", "sĩ số", "những học sinh nào",
    "có bao nhiêu học sinh", "lớp có ai", "ai trong lớp", "các bạn trong lớp",
]
_STUDENT_INFO_KEYWORDS = [
    "thông tin học sinh", "thông tin của học sinh", "hồ sơ học sinh", "hồ sơ của",
    "thông tin liên hệ", "thông tin về", "thông tin của em", "thông tin em",
    "tra cứu học sinh", "tra cứu thông tin", "phụ huynh của", "phụ huynh em",
    "địa chỉ của", "ngày sinh của", "sđt phụ huynh", "số điện thoại phụ huynh",
    "mã học sinh của",
]
_TEACHER_KEYWORDS = [
    "giáo viên", "gvcn", "chủ nhiệm", "ai dạy", "ai là giáo viên", "dạy môn",
    "dạy lớp", "dạy những", "giáo viên bộ môn", "giáo viên dạy", "cô nào", "thầy nào",
    "dạy", "cô ", "thầy ",  # "cô "/"thầy " co dau cach nen khong trung "công"/...
]
# Thong ke lop (giao vien/admin) — cac cum gan voi "lop" hoac xep hang.
_CLASS_STATS_KEYWORDS = [
    "trung bình lớp", "tb lớp", "điểm trung bình lớp", "top", "cao nhất lớp",
    "thấp nhất lớp", "giỏi nhất lớp", "kém nhất lớp", "dưới trung bình",
    "trên trung bình", "xếp hạng", "hạng nhất", "thống kê lớp", "thống kê", "phổ điểm",
    "bao nhiêu học sinh giỏi", "số học sinh giỏi", "bao nhiêu em đạt",
]
# Tong ket / xep loai ca nhan (khong dung "tong ket" tran de tranh nham "tong ket mon X")
_SUMMARY_KEYWORDS = [
    "xếp loại", "học lực", "kết quả học tập", "tổng kết học kỳ", "tổng kết cả năm",
    "tổng kết năm", "kết quả học kỳ", "kết quả cả năm", "danh hiệu", "được học sinh giỏi",
    "lên lớp",
]
# Cac cum bao hieu nguoi dung muon xem diem CUA TAT CA CAC NAM HOC (khong gioi
# han nam hien tai). Vd: "toan bo diem qua cac nam", "tat ca cac nam".
_ALL_YEARS_KEYWORDS = [
    "qua các năm", "qua từng năm", "tất cả các năm", "toàn bộ các năm", "tất cả năm học",
    "mọi năm", "các năm học", "hết các năm", "qua các năm học", "toàn bộ điểm qua",
    "từ trước đến nay", "từ trước tới nay", "lịch sử điểm",
]


def wants_all_years(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in _ALL_YEARS_KEYWORDS)


def classify_intent(question: str) -> str:
    q = question.lower()
    if any(k in q for k in _TIMETABLE_KEYWORDS):
        return "timetable"
    if any(k in q for k in _EXAM_KEYWORDS):
        return "exam"
    if any(k in q for k in _ATTENDANCE_KEYWORDS):
        return "attendance"
    if any(k in q for k in _NOTIFICATION_KEYWORDS):
        return "notification"
    if any(k in q for k in _ACTIVITY_KEYWORDS):
        return "activity"
    if any(k in q for k in _TEACHER_KEYWORDS):
        return "teacher"
    if any(k in q for k in _CLASS_STATS_KEYWORDS):
        return "class_stats"
    if any(k in q for k in _SUMMARY_KEYWORDS):
        return "summary"
    if any(k in q for k in _STUDENT_INFO_KEYWORDS):
        return "student_info"
    if any(k in q for k in _ROSTER_KEYWORDS):
        return "roster"
    return "grade"


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
        """Nam hoc HIEN TAI theo ngay thuc (tu Supabase, co fallback ve nam gan
        nhat khi nghi he). Neu khong co school_info (che do Excel) thi lay nam
        moi nhat co trong du lieu diem."""
        if self.school_info is not None:
            try:
                cur = self.school_info.get_current_term(date.today().isoformat())
                if cur and cur.get("year_name"):
                    return cur["year_name"]
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
        self, question: str, forced_student_code: Optional[str] = None,
    ) -> Tuple[List[GradeRecord], List[str]]:
        """Tra ve (danh sach ban ghi khop, danh sach ten goi y neu khong khop).

        forced_student_code: neu duoc truyen (hoc sinh dang dang nhap), bo qua
        hoan toan viec tim ten trong cau hoi va CHI loc theo ma hoc sinh nay —
        ngan hoc sinh xem duoc diem cua nguoi khac bang cach go ten khac.

        Neu cau hoi co ten mon cu the -> chi tra diem mon do; neu hoi chung
        chung -> tra diem TAT CA cac mon."""
        known_subjects = set(self.store.list_subjects())
        filters = extract_query_filters(question, known_subjects)
        name_query = filters.pop("name_query")
        subject = filters["subject"]

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

    def _resolve_roster(self, question: str) -> _LookupResult:
        filters = extract_query_filters(question)
        class_name, school_year = filters["class_name"], filters["school_year"]

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

    def _resolve_timetable(self, question: str, session_user=None) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "thời khóa biểu"))

        filters = extract_query_filters(question)
        class_name, school_year, semester = filters["class_name"], filters["school_year"], filters["semester"]

        q_low = question.lower()
        wants_self = any(k in q_low for k in ["của tôi", "của em", "của mình", "của con", "của cháu"])

        # Xac dinh hoc ky HIEN TAI theo ngay thuc (real-time)
        cur = self.school_info.get_current_term(date.today().isoformat())

        # "cua toi" cho hoc sinh: tu suy ra lop + nam hoc tu ho so + ky hien tai
        if wants_self and session_user is not None and getattr(session_user, "is_student", False):
            year_name = school_year or (cur["year_name"] if cur else None)
            if not year_name:
                return _LookupResult(build_timetable_prompt(
                    question, "(của bạn)", "(chưa xác định)", None, []))
            resolved_class = self.school_info.get_student_class(session_user.student_id, year_name)
            if not resolved_class:
                return _LookupResult(build_timetable_prompt(
                    question, "(của bạn)", year_name, None, []))
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
        self, question: str, forced_student_id: Optional[int] = None, forced_full_name: Optional[str] = None,
    ) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "điểm danh"))

        if forced_student_id is not None:
            student_ids = [forced_student_id]
            display_name = forced_full_name or ""
        else:
            filters = extract_query_filters(question)
            name_query = filters["name_query"]
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

    def _resolve_exam_schedule(self, question: str, session_user=None) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "lịch thi"))

        filters = extract_query_filters(question)
        class_name, school_year, semester = filters["class_name"], filters["school_year"], filters["semester"]

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

    def _resolve_student_info(self, question: str) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "thông tin học sinh"))

        # Uu tien tra cuu theo ma hoc sinh neu cau hoi co (vd HS00457)
        codes = [m.group(0).upper() for m in _STUDENT_CODE_RE.finditer(question)]
        if codes:
            profiles = self.school_info.get_student_profiles(codes=codes)
            citations = [f"Thông tin học sinh {p.get('student_code')}" for p in profiles]
            return _LookupResult(build_student_info_prompt(question, profiles), citations, bool(profiles))

        filters = extract_query_filters(question)
        name_query = filters["name_query"]
        if not name_query:
            return _LookupResult(build_student_info_prompt(question, []))

        matched_names = self.store.find_matching_names(name_query)
        if not matched_names:
            return _LookupResult(build_student_info_prompt(question, []))

        profiles = self.school_info.get_student_profiles(names=matched_names)
        citations = [f"Thông tin học sinh: {p.get('full_name')}" for p in profiles]
        return _LookupResult(build_student_info_prompt(question, profiles), citations, bool(profiles))

    # -- tong ket / xep loai (Thong tu 22) --------------------------------

    def _resolve_summary(self, question: str, forced_student_code: Optional[str] = None) -> _LookupResult:
        known_subjects = set(self.store.list_subjects())
        filters = extract_query_filters(question, known_subjects)
        name_query = filters.pop("name_query")
        school_year = filters["school_year"]
        semester = filters["semester"]
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

    def _resolve_class_stats(self, question: str) -> _LookupResult:
        known_subjects = set(self.store.list_subjects())
        filters = extract_query_filters(question, known_subjects)
        class_name = filters["class_name"]
        school_year = filters["school_year"]
        semester = filters["semester"]
        subject = filters["subject"]
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

    def _resolve_teacher(self, question: str, include_contact: bool = True) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "tra cứu giáo viên"))

        known_subjects = set(self.store.list_subjects())
        filters = extract_query_filters(question, known_subjects)
        class_name = filters["class_name"]
        school_year = filters["school_year"]
        subject = filters["subject"]
        name_query = filters["name_query"]
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

    def _resolve_activities(self, question: str) -> _LookupResult:
        if self.school_info is None:
            return _LookupResult(build_feature_unavailable_prompt(question, "hoạt động ngoại khóa"))

        filters = extract_query_filters(question)
        school_year, semester = filters["school_year"], filters["semester"]

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
        intent = classify_intent(question)

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
                return self._resolve_summary(question, forced_student_code=session_user.student_code)
            if intent == "grade":
                records, _ = self._resolve_records(question, forced_student_code=session_user.student_code)
                if not records:
                    return _LookupResult(build_no_match_prompt(question, []))
                return _LookupResult(build_grade_prompt(question, records), grade_citation_lines(records), True)
            if intent == "attendance":
                return self._resolve_attendance(
                    question,
                    forced_student_id=session_user.student_id,
                    forced_full_name=session_user.full_name,
                )
            # timetable / exam / notification / activity: khong gioi han rieng
            # cho hoc sinh (thong tin chung cua lop/truong, khong nhay cam).

        if intent == "roster":
            return self._resolve_roster(question)
        if intent == "student_info":
            return self._resolve_student_info(question)
        if intent == "teacher":
            include_contact = not (session_user is not None and getattr(session_user, "is_student", False))
            return self._resolve_teacher(question, include_contact=include_contact)
        if intent == "class_stats":
            return self._resolve_class_stats(question)
        if intent == "summary":
            return self._resolve_summary(question)
        if intent == "timetable":
            return self._resolve_timetable(question, session_user)
        if intent == "exam":
            return self._resolve_exam_schedule(question, session_user)
        if intent == "attendance":
            return self._resolve_attendance(question)
        if intent == "notification":
            return self._resolve_notifications(question)
        if intent == "activity":
            return self._resolve_activities(question)

        records, suggestions = self._resolve_records(question)
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
