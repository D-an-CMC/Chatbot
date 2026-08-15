# src/grades/analytics.py
# Tinh toan tong hop tu danh sach GradeRecord (da nap san trong store):
#  - Tong ket / xep loai hoc luc cua 1 hoc sinh (theo Thong tu 22/2021/TT-BGDDT).
#  - Thong ke diem cua 1 lop cho giao vien (diem TB lop, top, so HS duoi TB...).
#
# Cac ham o day THUAN TUY (khong goi DB) — nhan vao records da loc san.

from typing import Dict, List, Optional, Tuple

from src.grades.grade_store import GradeRecord

# target: "I" | "II" | "year" (ca nam)

# Chao co / Sinh hoat lop KHONG phai mon hoc duoc danh gia (chi la hoat dong) —
# loai khoi tong ket & xep loai hoc luc theo Thong tu 22.
_NON_ACADEMIC = {"Chào cờ", "Sinh hoạt lớp"}


def _num_value(r: GradeRecord, target: str) -> Optional[float]:
    """Diem trung binh mon (dang so) cua 1 ban ghi ung voi ky can xet."""
    if target == "I":
        return r.tb_hoc_ky_1 if r.semester == "I" else None
    if target == "II":
        return r.tb_hoc_ky_2 if r.semester == "II" else None
    return r.tb_ca_nam  # ca nam


def _group_by_subject(records: List[GradeRecord]) -> Dict[str, List[GradeRecord]]:
    out: Dict[str, List[GradeRecord]] = {}
    for r in records:
        out.setdefault(r.subject, []).append(r)
    return out


def classify_hoc_luc(numeric_avgs: List[float], nhanxet_results: List[str]) -> Optional[str]:
    """Xep loai ket qua hoc tap theo Thong tu 22 (THCS).

    - Tot: cac mon nhan xet deu Dat; moi mon tinh diem co DTB >= 6.5, trong do
      it nhat 6 mon >= 8.0.
    - Kha: cac mon nhan xet deu Dat; moi mon tinh diem >= 5.0, it nhat 6 mon >= 6.5.
    - Dat: nhieu nhat 1 mon nhan xet Chua dat; it nhat 6 mon tinh diem >= 5.0 va
      khong mon nao < 3.5.
    - Chua dat: cac truong hop con lai.
    Tra ve None neu khong du du lieu diem so de xep loai."""
    if not numeric_avgs:
        return None
    nx = [x for x in nhanxet_results if x]
    all_nx_dat = all(x == "Đạt" for x in nx)
    n_nx_chuadat = sum(1 for x in nx if x != "Đạt")

    def ge(th):
        return sum(1 for a in numeric_avgs if a >= th)

    def all_ge(th):
        return all(a >= th for a in numeric_avgs)

    if all_nx_dat and all_ge(6.5) and ge(8.0) >= 6:
        return "Tốt"
    if all_nx_dat and all_ge(5.0) and ge(6.5) >= 6:
        return "Khá"
    if n_nx_chuadat <= 1 and ge(5.0) >= 6 and all_ge(3.5):
        return "Đạt"
    return "Chưa đạt"


def summarize_student(records: List[GradeRecord], target: str) -> Dict:
    """Tong ket ket qua hoc tap cua 1 hoc sinh (records da loc theo hoc sinh +
    nam hoc). target: 'I' | 'II' | 'year'."""
    numeric: List[Tuple[str, float]] = []
    nhanxet: List[Tuple[str, str]] = []

    for subject, recs in _group_by_subject(records).items():
        if subject in _NON_ACADEMIC:
            continue
        # Diem so (neu la mon tinh diem)
        val = None
        for r in recs:
            v = _num_value(r, target)
            if v is not None:
                val = v
                break
        if val is not None:
            numeric.append((subject, val))
            continue
        # Mon nhan xet (Dat/Chua dat)
        ratings = [r.danh_gia for r in recs if r.danh_gia]
        if ratings:
            if target == "I":
                rr = next((r.danh_gia for r in recs if r.semester == "I" and r.danh_gia), None)
            elif target == "II":
                rr = next((r.danh_gia for r in recs if r.semester == "II" and r.danh_gia), None)
            else:
                rr = "Chưa đạt" if any(x != "Đạt" for x in ratings) else "Đạt"
            if rr:
                nhanxet.append((subject, rr))

    numeric.sort(key=lambda x: x[0])
    nhanxet.sort(key=lambda x: x[0])
    numeric_avgs = [v for _, v in numeric]
    overall_avg = round(sum(numeric_avgs) / len(numeric_avgs), 2) if numeric_avgs else None
    hoc_luc = classify_hoc_luc(numeric_avgs, [r for _, r in nhanxet])

    best = max(numeric, key=lambda x: x[1]) if numeric else None
    worst = min(numeric, key=lambda x: x[1]) if numeric else None

    return {
        "numeric": numeric,
        "nhanxet": nhanxet,
        "overall_avg": overall_avg,
        "hoc_luc": hoc_luc,
        "best": best,
        "worst": worst,
        "n_nhanxet_chuadat": sum(1 for _, r in nhanxet if r != "Đạt"),
    }


def class_stats(records: List[GradeRecord], subject: Optional[str], target: str, top_n: int = 5) -> Dict:
    """Thong ke diem cua 1 lop (records da loc theo lop + nam hoc).

    - Neu co subject: thong ke theo dung mon do (diem TB lop, top, phan bo).
    - Neu khong: tinh diem TB chung cua tung hoc sinh (trung binh cac mon tinh
      diem) roi xep hang."""
    per_student: Dict[str, Dict] = {}

    if subject:
        recs = [r for r in records if r.subject == subject]
        for r in recs:
            v = _num_value(r, target)
            if v is None:
                continue
            per_student.setdefault(r.student_id or r.name, {"name": r.name, "code": r.student_id, "vals": []})
            per_student[r.student_id or r.name]["vals"].append(v)
        scores = [(d["name"], d["code"], round(sum(d["vals"]) / len(d["vals"]), 2))
                  for d in per_student.values() if d["vals"]]
    else:
        # Diem TB chung tung hoc sinh = trung binh cac mon tinh diem
        tmp: Dict[str, Dict] = {}
        for r in records:
            v = _num_value(r, target)
            if v is None:
                continue
            key = r.student_id or r.name
            tmp.setdefault(key, {"name": r.name, "code": r.student_id, "vals": []})
            tmp[key]["vals"].append(v)
        scores = [(d["name"], d["code"], round(sum(d["vals"]) / len(d["vals"]), 2))
                  for d in tmp.values() if d["vals"]]

    scores.sort(key=lambda x: x[2], reverse=True)
    n = len(scores)
    class_avg = round(sum(s[2] for s in scores) / n, 2) if n else None

    return {
        "subject": subject,
        "num_students": n,
        "class_avg": class_avg,
        "top": scores[:top_n],
        "bottom": list(reversed(scores[-top_n:])) if n else [],
        "count_ge_8": sum(1 for s in scores if s[2] >= 8.0),
        "count_ge_6_5": sum(1 for s in scores if s[2] >= 6.5),
        "count_lt_5": sum(1 for s in scores if s[2] < 5.0),
    }
