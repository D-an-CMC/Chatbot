# src/grades/grade_store.py
# Doc va chuan hoa du lieu diem so tu cac file so diem Excel (Khoa hoc tu nhien / Vat ly)
# Cac file co dinh dang tieu de khong dong nhat giua cac nam / hoc ky, nen viec doc cot
# dua tren tim kiem nhan (label) trong dong tieu de thay vi hardcode vi tri cot.

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GradeRecord — 1 ban ghi diem cua 1 hoc sinh trong 1 so diem (lop/mon/hoc ky/nam)
# ---------------------------------------------------------------------------

@dataclass
class GradeRecord:
    school_year: str
    source_file: str
    sheet_name: str
    class_name: str
    subject: str
    semester: str  # "I" hoac "II" — hoc ky cua so diem nguon
    name: str
    stt: Optional[int] = None
    student_id: Optional[str] = None
    dob: Optional[str] = None
    tx_scores: List[float] = field(default_factory=list)
    giua_ky: Optional[float] = None
    cuoi_ky: Optional[float] = None
    tb_hoc_ky_1: Optional[float] = None
    tb_hoc_ky_2: Optional[float] = None
    tb_ca_nam: Optional[float] = None
    nhan_xet: str = ""
    # Voi cac mon danh gia bang NHAN XET (Am nhac, My thuat, The duc, Hoat dong
    # trai nghiem, Noi dung giao duc dia phuong): khong co diem so, chi co ket
    # qua "Dat" / "Chua dat". Cac mon nay dtb/diem thanh phan deu rong.
    danh_gia: Optional[str] = None

    def to_context_block(self, index: int) -> str:
        lines = [f"[{index}] Hoc sinh: {self.name}"]
        if self.student_id:
            lines.append(f"  Ma hoc sinh: {self.student_id}")
        lines.append(
            f"  Lop: {self.class_name} | Mon: {self.subject} | "
            f"Nam hoc: {self.school_year} | So diem hoc ky: {self.semester}"
        )
        if self.danh_gia:
            # Mon nhan xet: dua ket qua Dat/Chua dat len lam noi dung chinh de
            # LLM khong bo sot gia tri (chi mon nay moi co, mon tinh diem thi None).
            lines.append(f"  Ket qua danh gia mon hoc: {self.danh_gia} (mon nay danh gia bang NHAN XET Dat/Chua dat, khong cham diem)")
        if self.tx_scores:
            lines.append(f"  Diem danh gia thuong xuyen: {', '.join(str(x) for x in self.tx_scores)}")
        if self.giua_ky is not None:
            lines.append(f"  Diem giua ky: {self.giua_ky}")
        if self.cuoi_ky is not None:
            lines.append(f"  Diem cuoi ky: {self.cuoi_ky}")
        if self.tb_hoc_ky_1 is not None:
            lines.append(f"  Diem trung binh hoc ky I: {self.tb_hoc_ky_1}")
        if self.tb_hoc_ky_2 is not None:
            lines.append(f"  Diem trung binh hoc ky II: {self.tb_hoc_ky_2}")
        if self.tb_ca_nam is not None:
            lines.append(f"  Diem trung binh ca nam: {self.tb_ca_nam}")
        if self.nhan_xet:
            lines.append(f"  Nhan xet: {self.nhan_xet}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers chuan hoa text / so
# ---------------------------------------------------------------------------

def _normalize_label(v) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).replace("\n", " ").replace("\r", " ").strip().lower()
    return re.sub(r"\s+", " ", s)


def _clean_text(v) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


def _format_date(v) -> Optional[str]:
    text = _clean_text(v)
    if not text:
        return None
    try:
        ts = pd.Timestamp(v)
        if not pd.isna(ts):
            return ts.strftime("%d/%m/%Y")
    except Exception:
        pass
    return text


def _strip_diacritics(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_name(s: str) -> str:
    """Chuan hoa ten de so sanh: bo dau, lowercase, gom khoang trang."""
    s = _strip_diacritics(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Suy luan lop / mon / hoc ky tu ten sheet
# ---------------------------------------------------------------------------

def _infer_sheet_context(sheet_name: str) -> tuple:
    s = sheet_name.strip()
    m = re.match(r"^(\d{1,2}[A-Za-z])", s)
    class_name = m.group(1).upper() if m else s.split()[0]

    low = s.lower()
    subject = "Vật lý" if re.search(r"vật\s*l[ýy]", low) else "Khoa học tự nhiên"

    sem_match = re.search(r"k[ỳì]\s*(i{1,2}|1|2)\b", low)
    semester = "I"
    if sem_match:
        g = sem_match.group(1)
        semester = "II" if g in ("ii", "2") else "I"
    return class_name, subject, semester


# ---------------------------------------------------------------------------
# Tim va phan loai cac cot trong 1 sheet (header khong co vi tri co dinh)
# ---------------------------------------------------------------------------

def _find_header_row(raw: pd.DataFrame) -> Optional[tuple]:
    max_scan = min(6, len(raw))
    for r in range(max_scan):
        for c in range(raw.shape[1]):
            if "họ và tên" in _normalize_label(raw.iat[r, c]):
                return r, c
    return None


def _classify_columns(raw: pd.DataFrame, header_row: int, name_col: int) -> Dict:
    ncols = raw.shape[1]
    row1 = [_normalize_label(raw.iat[header_row, c]) for c in range(ncols)]

    # Dong ngay duoi header chi duoc coi la "sub-header" (vd ĐTB mhk/mcn cua khoi
    # "Danh gia lai") neu o cot ten hoc sinh no dang trong — neu khong, do la
    # dong du lieu hoc sinh dau tien va khong duoc gop vao nhan cot.
    sub_header_present = False
    if header_row + 1 < len(raw):
        if not _normalize_label(raw.iat[header_row + 1, name_col]):
            sub_header_present = True
    row2 = (
        [_normalize_label(raw.iat[header_row + 1, c]) for c in range(ncols)]
        if sub_header_present else [""] * ncols
    )
    combined = [(row1[c] + " " + row2[c]).strip() for c in range(ncols)]

    cols: Dict = {
        "stt": None, "student_id": None, "dob": None,
        "tx_start": None, "gk": None, "ck": None,
        "mhk1": None, "mhk2": None, "mhk_generic": None, "mcn": None,
        "nhan_xet": None,
    }

    retake_start = None
    for c in range(ncols):
        if "đánh giá lại" in row1[c]:
            retake_start = c
            break

    for c in range(ncols):
        if c == name_col:
            continue
        lbl = combined[c]
        compact = lbl.replace(" ", "")
        if not lbl:
            continue

        if "mã học sinh" in lbl or compact in {"mãhs"}:
            cols["student_id"] = c
        elif "ngày sinh" in lbl:
            cols["dob"] = c
        elif compact in {"stt", "số", "sốtt"}:
            cols["stt"] = c
        elif "đgtx" in compact and cols["tx_start"] is None:
            cols["tx_start"] = c
        elif "nhận xét" in lbl:
            cols["nhan_xet"] = c
        elif retake_start is not None and c >= retake_start:
            # thuoc khoi "Danh gia lai" (diem thi lai) — bo qua
            continue
        elif "đtb" in lbl and compact.endswith("mhkii"):
            cols["mhk2"] = c
        elif "đtb" in lbl and compact.endswith("mhki"):
            cols["mhk1"] = c
        elif "đtb" in lbl and compact.endswith("mhk"):
            cols["mhk_generic"] = c
        elif "đtb" in lbl and "mcn" in lbl:
            cols["mcn"] = c
        elif re.search(r"\bgk\b", lbl):
            cols["gk"] = c
        elif re.search(r"\bck\b", lbl):
            cols["ck"] = c

    tx_cols: List[int] = []
    if cols["tx_start"] is not None:
        end = cols["gk"] if cols["gk"] is not None else cols["tx_start"] + 1
        tx_cols = list(range(cols["tx_start"], max(end, cols["tx_start"] + 1)))
    cols["tx_cols"] = tx_cols
    return cols


def _parse_sheet(raw: pd.DataFrame, sheet_name: str, school_year: str, source_file: str) -> List[GradeRecord]:
    header = _find_header_row(raw)
    if header is None:
        logger.warning("Khong tim thay dong tieu de trong sheet '%s' (%s)", sheet_name, source_file)
        return []
    header_row, name_col = header
    cols = _classify_columns(raw, header_row, name_col)
    class_name, subject, semester = _infer_sheet_context(sheet_name)

    data_start = header_row + 1
    if data_start < len(raw):
        row_vals = [_normalize_label(raw.iat[data_start, c]) for c in range(raw.shape[1])]
        name_cell = _normalize_label(raw.iat[data_start, name_col])
        if not name_cell and any("đtb" in v for v in row_vals):
            data_start += 1

    records: List[GradeRecord] = []
    for r in range(data_start, len(raw)):
        name = _clean_text(raw.iat[r, name_col]) if name_col < raw.shape[1] else ""
        if not name:
            continue

        tx_scores = []
        for c in cols["tx_cols"]:
            val = _to_float(raw.iat[r, c])
            if val is not None:
                tx_scores.append(val)

        mhk1 = _to_float(raw.iat[r, cols["mhk1"]]) if cols["mhk1"] is not None else None
        mhk2 = _to_float(raw.iat[r, cols["mhk2"]]) if cols["mhk2"] is not None else None
        if cols["mhk_generic"] is not None:
            generic_val = _to_float(raw.iat[r, cols["mhk_generic"]])
            if generic_val is not None:
                if semester == "I" and mhk1 is None:
                    mhk1 = generic_val
                elif semester == "II" and mhk2 is None:
                    mhk2 = generic_val

        record = GradeRecord(
            school_year=school_year,
            source_file=source_file,
            sheet_name=sheet_name,
            class_name=class_name,
            subject=subject,
            semester=semester,
            name=name,
            stt=_to_int(raw.iat[r, cols["stt"]]) if cols["stt"] is not None else None,
            student_id=_clean_text(raw.iat[r, cols["student_id"]]) or None if cols["student_id"] is not None else None,
            dob=_format_date(raw.iat[r, cols["dob"]]) if cols["dob"] is not None else None,
            tx_scores=tx_scores,
            giua_ky=_to_float(raw.iat[r, cols["gk"]]) if cols["gk"] is not None else None,
            cuoi_ky=_to_float(raw.iat[r, cols["ck"]]) if cols["ck"] is not None else None,
            tb_hoc_ky_1=mhk1,
            tb_hoc_ky_2=mhk2,
            tb_ca_nam=_to_float(raw.iat[r, cols["mcn"]]) if cols["mcn"] is not None else None,
            nhan_xet=_clean_text(raw.iat[r, cols["nhan_xet"]]).replace("\n", " ") if cols["nhan_xet"] is not None else "",
        )
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# GradeStore — nap toan bo cac file .xlsx trong thu muc va cho phep tra cuu
# ---------------------------------------------------------------------------

class GradeStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.records: List[GradeRecord] = []
        self._name_index: Dict[str, Set[str]] = {}
        self._ready = False

    def load(self) -> None:
        self.records = []
        files = sorted(self.data_dir.glob("*.xlsx"))
        for f in files:
            year_match = re.search(r"(20\d{2}-20\d{2})", f.stem)
            school_year = year_match.group(1) if year_match else f.stem
            try:
                xl = pd.ExcelFile(f, engine="openpyxl")
            except Exception as e:
                logger.warning("Khong doc duoc file %s: %s", f, e)
                continue
            for sheet_name in xl.sheet_names:
                try:
                    raw = xl.parse(sheet_name, header=None)
                except Exception as e:
                    logger.warning("Loi doc sheet '%s' trong %s: %s", sheet_name, f.name, e)
                    continue
                self.records.extend(_parse_sheet(raw, sheet_name, school_year, f.name))

        self._build_indexes()
        self._ready = bool(self.records)
        logger.info(
            "GradeStore da nap %d ban ghi tu %d file (%s)",
            len(self.records), len(files), self.data_dir,
        )

    reload = load

    def is_ready(self) -> bool:
        return self._ready

    def _build_indexes(self) -> None:
        self._name_index = {}
        for r in self.records:
            norm = normalize_name(r.name)
            self._name_index.setdefault(norm, set()).add(r.name)

    # -- tra cuu ten (fuzzy, bo dau) -----------------------------------

    def find_matching_names(self, query: str, limit: int = 8) -> List[str]:
        nq = normalize_name(query)
        if not nq:
            return []

        if nq in self._name_index:
            return sorted(self._name_index[nq])

        all_norms = list(self._name_index.keys())

        substr = [n for n in all_norms if nq in n or n in nq]
        if substr:
            result: Set[str] = set()
            for n in substr:
                result |= self._name_index[n]
            return sorted(result)[:limit]

        q_tokens = set(nq.split())
        if q_tokens:
            token_matches = [n for n in all_norms if q_tokens.issubset(set(n.split()))]
            if token_matches:
                result = set()
                for n in token_matches:
                    result |= self._name_index[n]
                return sorted(result)[:limit]

        close = difflib.get_close_matches(nq, all_norms, n=limit, cutoff=0.72)
        result = set()
        for n in close:
            result |= self._name_index[n]
        return sorted(result)[:limit]

    # -- tra cuu ban ghi --------------------------------------------------

    def fetch_for_codes(self, codes) -> List["GradeRecord"]:
        """Lay ban ghi diem cho mot nhom hoc sinh theo ma. Ban co so (Excel)
        doc tu du lieu da nap; SupabaseGradeStore ghi de de query truc tiep DB
        (real-time). Nho vay engine dung chung 1 API cho ca 2 che do."""
        codeset = {c for c in (codes or []) if c}
        if not codeset:
            return []
        return [r for r in self.records if r.student_id in codeset]

    def search(
        self,
        name: Optional[str] = None,
        class_name: Optional[str] = None,
        school_year: Optional[str] = None,
        semester: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> List[GradeRecord]:
        matched_names: Optional[Set[str]] = None
        if name:
            matched_names = set(self.find_matching_names(name))
            if not matched_names:
                return []

        results = []
        for r in self.records:
            if matched_names is not None and r.name not in matched_names:
                continue
            if class_name and r.class_name.upper() != class_name.upper():
                continue
            if school_year and r.school_year != school_year:
                continue
            if semester and r.semester != semester:
                continue
            if subject and r.subject != subject:
                continue
            results.append(r)
        return results

    # -- thong ke / danh sach ----------------------------------------------

    def list_school_years(self) -> List[str]:
        return sorted({r.school_year for r in self.records})

    def list_classes(self, school_year: Optional[str] = None) -> List[str]:
        return sorted({
            r.class_name for r in self.records
            if not school_year or r.school_year == school_year
        })

    def list_subjects(self) -> List[str]:
        return sorted({r.subject for r in self.records if r.subject})

    def stats(self) -> dict:
        try:
            num_files = len(list(self.data_dir.glob("*.xlsx")))
        except Exception:
            num_files = 0
        return {
            "total_records": len(self.records),
            "num_students": len(self._name_index),
            "school_years": self.list_school_years(),
            "num_classes": len(self.list_classes()),
            "num_subjects": len(self.list_subjects()),
            "num_files": num_files,
        }
