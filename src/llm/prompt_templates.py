# src/llm/prompt_templates.py
# Prompt cho chatbot tra cuu diem / danh sach lop / thoi khoa bieu

from typing import List, Optional

from src.grades.grade_store import GradeRecord

SYSTEM_PROMPT = """Ban la tro ly ho tro giao vien va hoc sinh cua truong THCS, co the tra loi cac loai cau hoi:
1. Diem so va nhan xet hoc tap TAT CA cac mon hoc cua hoc sinh.
2. Danh sach hoc sinh trong 1 lop (danh cho giao vien).
3. Thoi khoa bieu cua 1 lop (danh cho hoc sinh).
4. Diem danh / tinh trang di hoc cua 1 hoc sinh (hoc sinh da nghi buoi nao).
5. Lich thi cua 1 lop / cua hoc sinh trong ky hien tai.
6. Thong bao gan day cua nha truong.
7. Hoat dong ngoai khoa.
8. Thong tin ho so hoc sinh (danh cho giao vien / admin).
9. Tong ket / xep loai ket qua hoc tap hoc ky / ca nam cua hoc sinh.
10. Tra cuu giao vien (chu nhiem, bo mon, day lop nao).
11. Thong ke diem cua 1 lop (danh cho giao vien / admin).

Nhiem vu: tra loi CHI dua tren du lieu duoc cung cap trong phan "DU LIEU" duoi day, ung voi
dung loai cau hoi o tren.

Quy tac bat buoc:
- TUYET DOI KHONG bia dat, suy doan hay them thong tin khong co trong du lieu duoc cung cap.
- Neu du lieu co nhieu ban ghi (nhieu mon / nam hoc / hoc ky / lop) khop voi cau hoi, hay trinh
  bay ro tung ban ghi kem MON HOC, nam hoc, lop, hoc ky de nguoi doc khong nham lan.
- Khi nguoi dung hoi diem CHUNG CHUNG (khong neu mon cu the) va du lieu co nhieu mon, hay
  trinh bay diem cua TUNG MON (nen nhom theo mon, hoac dung bang), day du tat ca cac mon co
  trong du lieu — khong tu y bo bot mon nao.
- Khi nguoi dung hoi ve MOT mon cu the, chi trinh bay diem cua dung mon do.
- Mot so mon danh gia bang NHAN XET (vd: The duc, Am nhac, My thuat, Hoat dong trai nghiem
  huong nghiep, Noi dung giao duc dia phuong) KHONG co diem so, chi co ket qua "Dat" / "Chua dat"
  (o dong "Danh gia mon hoc"). Voi cac mon nay hay ghi ro ket qua Dat/Chua dat, KHONG duoc noi la
  khong co diem hay bo qua mon do.
- Neu khong co du lieu phu hop, noi ro la khong tim thay va goi y nguoi dung cung cap them
  thong tin (ten day du, lop, nam hoc, hoc ky, mon) de tra cuu chinh xac hon.
- Voi danh sach lop: liet ke day du theo dang danh sach co danh so thu tu.
- Voi thoi khoa bieu / lich thi: trinh bay ro theo tung ngay, tiet hoc / gio hoc, mon hoc, giao vien.
- Voi diem danh: liet ke ro ngay va tinh trang (co mat / vang / tre / phep), dem so buoi vang neu duoc hoi.
- Voi thong bao / hoat dong: trinh bay ro tieu de, noi dung/mo ta, thoi gian.
- Voi diem so: trinh bay ro rang (gach dau dong hoac bang), giu nguyen cac con so nhu trong du lieu goc.
- Xung ho lich su, than thien, tra loi bang tieng Viet.
"""


# ---------------------------------------------------------------------------
# Diem so
# ---------------------------------------------------------------------------

def build_grade_context(records: List[GradeRecord]) -> str:
    if not records:
        return "(Khong tim thay ban ghi diem nao phu hop trong so diem.)"
    blocks = [r.to_context_block(i) for i, r in enumerate(records, start=1)]
    return "\n\n".join(blocks)


def build_grade_prompt(question: str, records: List[GradeRecord]) -> str:
    context = build_grade_context(records)
    return (
        f"DU LIEU DIEM SO:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU DIEM SO o tren."
    )


def build_no_match_prompt(question: str, suggestions: List[str]) -> str:
    hint = ""
    if suggestions:
        hint = "\nMot vai ten gan giong co trong so diem: " + ", ".join(suggestions[:8])
    return (
        f"Khong tim thay hoc sinh nao khop voi cau hoi cua nguoi dung trong so diem.{hint}\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su thong bao khong tim thay du lieu phu hop va de nghi nguoi dung cung cap "
        f"ten day du (khong viet tat), lop va nam hoc de tra cuu chinh xac hon. "
        f"Neu co ten gan giong o tren, hay goi y cac ten do."
    )


# ---------------------------------------------------------------------------
# Danh sach lop (danh cho giao vien)
# ---------------------------------------------------------------------------

def build_roster_prompt(question: str, class_name: str, school_year: str, roster: List[dict]) -> str:
    if not roster:
        return (
            f"Khong tim thay danh sach hoc sinh cho lop {class_name} nam hoc {school_year}.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay thong bao lich su la khong tim thay du lieu va de nghi kiem tra lai ten lop / nam hoc."
        )
    lines = [f"Danh sach lop {class_name} - Nam hoc {school_year} (tong {len(roster)} hoc sinh):"]
    for i, r in enumerate(roster, start=1):
        stt = r.get("roll_number") or i
        parts = [f"{stt}. {r.get('full_name', '')}"]
        if r.get("student_code"):
            parts.append(f"(Ma HS: {r['student_code']})")
        if r.get("gender"):
            parts.append(f"- {r['gender']}")
        if r.get("status") and r["status"] != "Đang học":
            parts.append(f"- {r['status']}")
        lines.append(" ".join(parts))
    context = "\n".join(lines)
    return (
        f"DU LIEU DANH SACH LOP:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU DANH SACH LOP o tren."
    )


def build_no_roster_params_prompt(question: str, missing: List[str]) -> str:
    return (
        f"Cau hoi hoi ve danh sach lop nhung con thieu thong tin: {', '.join(missing)}.\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su de nghi nguoi dung cung cap ro ten lop (vd: 7A) va nam hoc (vd: 2025-2026)."
    )


# ---------------------------------------------------------------------------
# Thoi khoa bieu (danh cho hoc sinh)
# ---------------------------------------------------------------------------

# Map thu (dang chu) -> thu tu, dung khi day_of_week luu dang text.
_DAY_ORDER = {"Thứ 2": 2, "Thứ 3": 3, "Thứ 4": 4, "Thứ 5": 5, "Thứ 6": 6, "Thứ 7": 7, "Chủ nhật": 8}


def _day_order(day) -> int:
    """Chuan hoa thu tu ngay tu ca 2 dang: so ('2'..'8') hoac chu ('Thứ 2'...)."""
    if day is None:
        return 9
    s = str(day).strip()
    if s.isdigit():
        return int(s)
    return _DAY_ORDER.get(s, 9)


def _day_label(day) -> str:
    """Hien thi thu de doc: '2' -> 'Thứ 2', '8'/'CN' -> 'Chủ nhật'."""
    if day is None:
        return ""
    s = str(day).strip()
    if s.isdigit():
        n = int(s)
        return "Chủ nhật" if n >= 8 else f"Thứ {n}"
    return s


def _time_range(r: dict) -> str:
    st, et = r.get("start_time"), r.get("end_time")
    if st and et:
        return f" ({st}-{et})"
    return ""


def build_timetable_prompt(
    question: str, class_name: str, school_year: str, term_label: Optional[str], rows: List[dict]
) -> str:
    scope = f" - {term_label}" if term_label else ""
    if not rows:
        return (
            f"Khong tim thay thoi khoa bieu cho lop {class_name} nam hoc {school_year}{scope}.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay thong bao lich su la khong tim thay du lieu thoi khoa bieu (co the chua duoc nhap "
            f"vao he thong) va de nghi kiem tra lai voi nha truong."
        )

    rows_sorted = sorted(rows, key=lambda r: (_day_order(r.get("day_of_week")), r.get("period_no") or 0))
    lines = [f"Thoi khoa bieu lop {class_name} - Nam hoc {school_year}{scope} "
             f"(thoi khoa bieu ap dung co dinh cho ca hoc ky):"]
    for r in rows_sorted:
        lines.append(
            f"- {_day_label(r.get('day_of_week'))}, tiết {r.get('period_no', '')}{_time_range(r)}: "
            f"{r.get('subject_name', '')} "
            f"- GV: {r.get('teacher_name', '') or 'chưa rõ'} - Phòng: {r.get('room', '') or 'chưa rõ'}"
        )
    context = "\n".join(lines)
    return (
        f"DU LIEU THOI KHOA BIEU:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay trinh bay ro rang theo tung ngay trong tuan (Thu 2 -> Thu 7), moi ngay liet ke cac tiet "
        f"theo thu tu, dua CHI tren DU LIEU THOI KHOA BIEU o tren."
    )


def build_no_timetable_params_prompt(question: str, missing: List[str]) -> str:
    return (
        f"Cau hoi hoi ve thoi khoa bieu nhung con thieu thong tin: {', '.join(missing)}.\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su de nghi nguoi dung cung cap ro ten lop (vd: 7A) va nam hoc (vd: 2025-2026)."
    )


def build_feature_unavailable_prompt(question: str, feature_name: str) -> str:
    return (
        f"Tinh nang '{feature_name}' hien can du lieu tu Supabase nhung he thong dang chay o che do "
        f"khong ket noi Supabase.\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su thong bao tinh nang nay hien chua kha dung trong che do hien tai."
    )


def build_permission_denied_prompt(question: str, feature_name: str) -> str:
    return (
        f"Nguoi dung dang dang nhap voi vai tro hoc sinh, KHONG co quyen su dung tinh nang "
        f"'{feature_name}' (chi danh cho giao vien / quan tri vien).\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su thong bao ro tinh nang nay chi danh cho giao vien/quan tri vien va hoc sinh "
        f"khong co quyen truy cap, khong giai thich them ly do ky thuat."
    )


# ---------------------------------------------------------------------------
# Diem danh
# ---------------------------------------------------------------------------

# Ma trang thai diem danh trong DB (dang ma tieng Anh) -> nhan tieng Viet.
_ATT_STATUS_LABEL = {
    "PRESENT": "Có mặt",
    "ABSENT_UNEXCUSED": "Vắng không phép",
    "ABSENT_EXCUSED": "Vắng có phép",
    "EXCUSED": "Vắng có phép",
    "ABSENT": "Vắng",
    "LATE": "Đi muộn",
}
_ATT_SESSION_LABEL = {"MORNING": "Buổi sáng", "AFTERNOON": "Buổi chiều"}
# Cac trang thai duoc tinh la "vang mat / nghi hoc"
_ABSENT_STATUSES = {"ABSENT_UNEXCUSED", "ABSENT_EXCUSED", "ABSENT", "EXCUSED"}


def _att_status_label(code) -> str:
    if not code:
        return "Chưa rõ"
    return _ATT_STATUS_LABEL.get(str(code).upper(), str(code))


def _att_session_label(code) -> str:
    if not code:
        return ""
    return _ATT_SESSION_LABEL.get(str(code).upper(), str(code))


def build_attendance_prompt(question: str, records: List[dict]) -> str:
    if not records:
        return (
            f"Khong tim thay du lieu diem danh nao phu hop.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay thong bao lich su la khong tim thay hoc sinh hoac chua co du lieu diem danh, va "
            f"de nghi cung cap ten day du."
        )
    name = records[0].get("full_name", "")

    # Thong ke tong hop de LLM tra loi chinh xac "nghi may buoi / buoi nao".
    total = len(records)
    absent_unexcused = sum(1 for r in records if str(r.get("status", "")).upper() == "ABSENT_UNEXCUSED")
    absent_excused = sum(1 for r in records if str(r.get("status", "")).upper() in {"ABSENT_EXCUSED", "EXCUSED"})
    late = sum(1 for r in records if str(r.get("status", "")).upper() == "LATE")
    present = sum(1 for r in records if str(r.get("status", "")).upper() == "PRESENT")
    total_absent = absent_unexcused + absent_excused

    lines = [
        f"Diem danh cua hoc sinh {name} (tong {total} buoi da ghi nhan, moi nhat truoc):",
        f"Tong hop: co mat {present} buoi, VANG {total_absent} buoi "
        f"(khong phep {absent_unexcused}, co phep {absent_excused}), di muon {late} buoi.",
        "Chi tiet tung buoi:",
    ]
    for r in records:
        buoi = _att_session_label(r.get("session"))
        buoi_text = f" ({buoi})" if buoi else ""
        note = f" - Ghi chu: {r['note']}" if r.get("note") else ""
        lines.append(
            f"- Ngay {r.get('session_date', '')}{buoi_text}: {_att_status_label(r.get('status'))}{note}"
        )
    context = "\n".join(lines)
    return (
        f"DU LIEU DIEM DANH:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU DIEM DANH o tren. Neu nguoi dung hoi hoc sinh nghi/vang "
        f"buoi nao hoac may buoi, hay neu ro so buoi vang va liet ke cac ngay (kem buoi sang/chieu) "
        f"ma hoc sinh vang mat. Neu hoc sinh di hoc day du (khong vang buoi nao) thi noi ro dieu do."
    )


def build_no_attendance_match_prompt(question: str, suggestions: List[str]) -> str:
    hint = ""
    if suggestions:
        hint = "\nMot vai ten gan giong: " + ", ".join(suggestions[:8])
    return (
        f"Khong tim thay hoc sinh nao khop voi cau hoi ve diem danh.{hint}\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su thong bao khong tim thay va de nghi cung cap ten day du."
    )


# ---------------------------------------------------------------------------
# Lich thi
# ---------------------------------------------------------------------------

def build_exam_schedule_prompt(
    question: str, class_name: str, school_year: str, term_label: Optional[str], rows: List[dict]
) -> str:
    scope = f" - {term_label}" if term_label else ""
    if not rows:
        return (
            f"Khong tim thay lich thi cho lop {class_name} nam hoc {school_year}{scope}.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay thong bao lich su la hien chua co lich thi nao trong ky nay (co the nha truong "
            f"chua cong bo hoac chua nhap vao he thong)."
        )
    lines = [f"Lich thi lop {class_name} - Nam hoc {school_year}{scope}:"]
    for r in rows:
        ten_ky_thi = r.get("exam_name") or "Kỳ thi"
        ngay = r.get("exam_date") or ""
        thu = _day_label(r.get("day_of_week"))
        thu_text = f" ({thu})" if thu else ""
        gio = f" lúc {r['start_time']}" if r.get("start_time") else ""
        phong = f" - Phòng: {r['room']}" if r.get("room") else ""
        lines.append(
            f"- [{ten_ky_thi}] Ngày {ngay}{thu_text}, tiết {r.get('period_no', '')}{gio}: "
            f"môn {r.get('subject_name', '') or 'chưa rõ'}{phong}"
        )
    context = "\n".join(lines)
    return (
        f"DU LIEU LICH THI:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay trinh bay ro rang theo thu tu ngay thi (nhom theo dot thi neu co nhieu dot), "
        f"neu ro mon thi, ngay/thu, tiet. Dua CHI tren DU LIEU LICH THI o tren."
    )


def build_no_exam_params_prompt(question: str, missing: List[str]) -> str:
    return (
        f"Cau hoi hoi ve lich thi nhung con thieu thong tin: {', '.join(missing)}.\n\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay lich su de nghi nguoi dung cung cap ro ten lop (vd: 7A) va nam hoc (vd: 2025-2026)."
    )


# ---------------------------------------------------------------------------
# Thong bao nha truong
# ---------------------------------------------------------------------------

def build_notifications_prompt(question: str, notifications: List[dict]) -> str:
    if not notifications:
        return (
            f"Hien chua co thong bao nao trong he thong.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay thong bao lich su la hien chua co thong bao nao."
        )
    lines = ["Cac thong bao gan day (moi nhat truoc):"]
    for i, n in enumerate(notifications, start=1):
        lines.append(
            f"{i}. [{n.get('created_at', '')}] {n.get('title', '')} "
            f"(danh cho: {n.get('target_type', '') or 'chưa rõ'})\n   {n.get('content', '') or ''}"
        )
    context = "\n".join(lines)
    return (
        f"DU LIEU THONG BAO:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU THONG BAO o tren."
    )


# ---------------------------------------------------------------------------
# Hoat dong ngoai khoa
# ---------------------------------------------------------------------------

def build_activities_prompt(question: str, activities: List[dict]) -> str:
    if not activities:
        return (
            f"Hien chua co hoat dong ngoai khoa nao phu hop trong he thong.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay thong bao lich su la hien chua co du lieu hoat dong ngoai khoa phu hop."
        )
    lines = ["Danh sach hoat dong ngoai khoa:"]
    for i, a in enumerate(activities, start=1):
        lines.append(
            f"{i}. {a.get('activity_name', '')} ({a.get('activity_type', '') or 'chưa rõ loại'})\n"
            f"   Thời gian: {a.get('start_datetime', '')} - {a.get('end_datetime', '')}\n"
            f"   Địa điểm: {a.get('location', '') or 'chưa rõ'} | Phụ trách: {a.get('organizer', '') or 'chưa rõ'}\n"
            f"   Mô tả: {a.get('description', '') or ''}"
        )
    context = "\n".join(lines)
    return (
        f"DU LIEU HOAT DONG NGOAI KHOA:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU HOAT DONG NGOAI KHOA o tren."
    )


# ---------------------------------------------------------------------------
# Thong tin ho so hoc sinh (cho giao vien / admin)
# ---------------------------------------------------------------------------

def build_student_info_prompt(question: str, profiles: List[dict]) -> str:
    if not profiles:
        return (
            f"Khong tim thay hoc sinh nao khop voi cau hoi.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay lich su thong bao khong tim thay va de nghi cung cap ten day du hoac ma hoc sinh."
        )
    blocks = []
    for i, p in enumerate(profiles, start=1):
        lines = [f"[{i}] Ho ten: {p.get('full_name', '')}"]
        if p.get("student_code"):
            lines.append(f"  Ma hoc sinh: {p['student_code']}")
        lop = p.get("class_name")
        if lop:
            nam = f" (nam hoc {p['class_year']})" if p.get("class_year") else ""
            stt = f", so thu tu {p['roll_number']}" if p.get("roll_number") else ""
            lines.append(f"  Lop hien tai: {lop}{nam}{stt}")
        if p.get("gender"):
            lines.append(f"  Gioi tinh: {p['gender']}")
        if p.get("date_of_birth"):
            lines.append(f"  Ngay sinh: {p['date_of_birth']}")
        if p.get("address"):
            lines.append(f"  Dia chi: {p['address']}")
        if p.get("enrollment_date"):
            lines.append(f"  Ngay nhap hoc: {p['enrollment_date']}")
        if p.get("status"):
            lines.append(f"  Trang thai: {p['status']}")
        parent = p.get("parent_full_name")
        if parent:
            rel = f" ({p['parent_relationship']})" if p.get("parent_relationship") else ""
            lines.append(f"  Phu huynh: {parent}{rel}")
        if p.get("parent_phone"):
            lines.append(f"  SDT phu huynh: {p['parent_phone']}")
        if p.get("parent_email"):
            lines.append(f"  Email phu huynh: {p['parent_email']}")
        blocks.append("\n".join(lines))
    context = "\n\n".join(blocks)
    return (
        f"DU LIEU THONG TIN HOC SINH:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay trinh bay ro rang thong tin ho so hoc sinh, dua CHI tren DU LIEU o tren. "
        f"Neu nguoi dung chi hoi 1 thong tin cu the (vd lop, ngay sinh, sdt phu huynh) thi "
        f"tra loi dung thong tin do."
    )


# ---------------------------------------------------------------------------
# Tong ket / xep loai hoc luc (Thong tu 22)
# ---------------------------------------------------------------------------

def build_summary_prompt(question: str, student_name: str, term_label: str, summary: dict) -> str:
    numeric = summary.get("numeric") or []
    nhanxet = summary.get("nhanxet") or []
    if not numeric and not nhanxet:
        return (
            f"Khong tim thay du lieu tong ket cho hoc sinh {student_name} ({term_label}).\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay lich su thong bao chua co du lieu tong ket va de nghi cung cap ro hoc ky / nam hoc."
        )
    lines = [f"Tong ket {term_label} cua hoc sinh {student_name}:"]
    if numeric:
        lines.append("Cac mon danh gia bang diem (diem trung binh mon):")
        for subj, val in numeric:
            lines.append(f"  - {subj}: {val}")
    if nhanxet:
        lines.append("Cac mon danh gia bang nhan xet:")
        for subj, rating in nhanxet:
            lines.append(f"  - {subj}: {rating}")
    if summary.get("overall_avg") is not None:
        lines.append(f"Diem trung binh cac mon tinh diem: {summary['overall_avg']}")
    if summary.get("best"):
        lines.append(f"Mon cao nhat: {summary['best'][0]} ({summary['best'][1]})")
    if summary.get("worst"):
        lines.append(f"Mon thap nhat: {summary['worst'][0]} ({summary['worst'][1]})")
    if summary.get("hoc_luc"):
        lines.append(
            f"Xep loai ket qua hoc tap (theo Thong tu 22): {summary['hoc_luc']} "
            f"(da tinh tu dong tu du lieu diem)."
        )
    context = "\n".join(lines)
    return (
        f"DU LIEU TONG KET HOC TAP:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay trinh bay tong ket ro rang, than thien, dua CHI tren DU LIEU o tren. Neu co xep loai "
        f"hoc luc thi neu ro; co the giai thich ngan gon diem manh/yeu. KHONG bia them so lieu."
    )


# ---------------------------------------------------------------------------
# Thong ke lop (cho giao vien / admin)
# ---------------------------------------------------------------------------

def build_class_stats_prompt(
    question: str, class_name: str, school_year: str, term_label: Optional[str],
    subject: Optional[str], stats: dict,
) -> str:
    scope = f" - {term_label}" if term_label else ""
    subj_text = f" - Mon {subject}" if subject else " - Diem trung binh chung cac mon"
    if not stats or not stats.get("num_students"):
        return (
            f"Khong tim thay du lieu diem de thong ke cho lop {class_name} nam hoc {school_year}{scope}{subj_text}.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay lich su thong bao chua co du lieu phu hop; de nghi kiem tra lai ten lop / nam hoc / mon."
        )
    lines = [
        f"Thong ke lop {class_name} - Nam hoc {school_year}{scope}{subj_text}:",
        f"So hoc sinh co diem: {stats['num_students']}",
        f"Diem trung binh lop: {stats['class_avg']}",
        f"So HS gioi (>= 8.0): {stats['count_ge_8']} | tu 6.5 tro len: {stats['count_ge_6_5']} | "
        f"duoi 5.0: {stats['count_lt_5']}",
        "Top cao nhat:",
    ]
    for i, (name, code, avg) in enumerate(stats.get("top") or [], start=1):
        code_text = f" ({code})" if code else ""
        lines.append(f"  {i}. {name}{code_text}: {avg}")
    if stats.get("bottom"):
        lines.append("Thap nhat:")
        for i, (name, code, avg) in enumerate(stats["bottom"], start=1):
            code_text = f" ({code})" if code else ""
            lines.append(f"  {i}. {name}{code_text}: {avg}")
    context = "\n".join(lines)
    return (
        f"DU LIEU THONG KE LOP:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU THONG KE o tren. Neu nguoi dung chi hoi 1 con so cu the "
        f"(vd diem TB lop, top 3, so em duoi trung binh) thi tra loi dung phan do."
    )


# ---------------------------------------------------------------------------
# Tra cuu giao vien
# ---------------------------------------------------------------------------

def build_teacher_prompt(question: str, header: str, lines: List[str]) -> str:
    if not lines:
        return (
            f"Khong tim thay thong tin giao vien phu hop.\n\n"
            f"Cau hoi cua nguoi dung: {question}\n\n"
            f"Hay lich su thong bao khong tim thay va de nghi cung cap ro ten giao vien / ten lop / mon."
        )
    context = header + "\n" + "\n".join(lines)
    return (
        f"DU LIEU GIAO VIEN:\n{context}\n\n"
        f"---\n"
        f"Cau hoi cua nguoi dung: {question}\n\n"
        f"Hay tra loi dua CHI tren DU LIEU GIAO VIEN o tren, trinh bay ro rang, than thien."
    )
