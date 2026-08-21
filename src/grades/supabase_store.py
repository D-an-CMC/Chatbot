# src/grades/supabase_store.py
# Nguon du lieu diem thay the cho Excel: doc truc tiep tu Supabase (PostgreSQL).
#
# Ke thua toan bo logic tra cuu (search, find_matching_names, stats...) tu
# GradeStore — chi ghi de load() de nap du lieu tu Supabase thay vi tu file
# .xlsx. Nho vay ChatbotEngine va cac phan con lai cua he thong khong can sua.
#
# Schema Supabase (khac voi file Excel):
#   subject_results(result_id, student_id, subject_id, semester_id, teacher_id,
#                    dtb_mhk, dtb_mcn, ranking, teacher_comment)
#   grade_items(result_id, grade_type_id, score)  -- grade_types.type_code:
#                    DDGtx (thuong xuyen) | DDGgk (giua ky) | DDGck (cuoi ky)
#   students(student_id, full_name, student_code, date_of_birth, class_id)
#   student_enrollments(student_id, class_id, school_year_id) -- lop theo TUNG
#                    nam hoc (students.class_id chi la lop HIEN TAI)
#   classes(class_id, class_name, school_year_id)
#   semesters(semester_id, semester_name, term_order, school_year_id)
#   school_years(school_year_id, year_name)
#   subjects(subject_id, subject_name)
#
# Luu y bao mat: SUPABASE_KEY o day PHAI la service_role key (bypass RLS) vi
# moi bang deu bat Row Level Security va chi cho phep role "authenticated"
# (gan voi tai khoan dang nhap) doc du lieu — khong co policy nao cho anon.
# service_role key CHI duoc dung o backend (server chay app.py), khong duoc
# nhung vao bat ky noi nao co the chay tren trinh duyet.

import logging
from typing import Dict, List, Optional

from src.grades.grade_store import GradeRecord, GradeStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_TX_CODE = "DDGtx"
_GK_CODE = "DDGgk"
_CK_CODE = "DDGck"


class SupabaseGradeStore(GradeStore):
    def __init__(self, url: str, key: str, subject_name: str = ""):
        super().__init__(data_dir=".")  # data_dir khong dung toi (load() bi ghi de)
        self.url = url
        self.key = key
        # subject_name rong => nap TAT CA cac mon; co gia tri => chi 1 mon do.
        self.subject_name = (subject_name or "").strip()
        self.client = None
        # Bang tra cuu on dinh (lop / phan lop theo nam) — cache sau lan nap dau
        # de fetch_for_codes() chi phai query lai bang diem (real-time), khong
        # phai keo lai classes/enrollments moi lan.
        self._classes_by_id: Optional[Dict[int, str]] = None
        self._student_id_by_code: Optional[Dict[str, int]] = None

    def _get_client(self):
        if self.client is None:
            from supabase import create_client
            self.client = create_client(self.url, self.key)
        return self.client

    def load(self) -> None:
        try:
            self.records = self._fetch_records()
        except Exception as e:
            logger.error("Loi khi nap du lieu tu Supabase: %s", e)
            self.records = []

        self._build_indexes()
        self._ready = bool(self.records)
        scope = f"mon '{self.subject_name}'" if self.subject_name else "TAT CA cac mon"
        logger.info(
            "SupabaseGradeStore da nap %d ban ghi (%s) tu Supabase",
            len(self.records), scope,
        )

    reload = load

    @staticmethod
    def _fetch_all_pages(build_query, page_size: int = 1000) -> list:
        """PostgREST gioi han mac dinh 1000 dong/request — phai phan trang de
        lay het, neu khong cac bang lon (vd student_enrollments) se bi cat mat
        cac dong cuoi mot cach am tham (khong loi, chi thieu du lieu)."""
        rows = []
        offset = 0
        while True:
            resp = build_query().range(offset, offset + page_size - 1).execute()
            rows.extend(resp.data)
            if len(resp.data) < page_size:
                break
            offset += page_size
        return rows

    def _ensure_lookup_maps(self) -> None:
        """Nap (mot lan) cac bang tra cuu ON DINH trong nam hoc: lop, phan lop
        theo nam (suy ra class_name), va anh xa ma hoc sinh -> student_id (de loc
        bang diem theo cot truc tiep). Cache lai de fetch_for_codes() chi phai
        query lai bang diem (real-time)."""
        if (self._classes_by_id is not None and self._student_id_by_code is not None):
            return
        client = self._get_client()
        self._classes_by_id = {
            c["class_id"]: c["class_name"]
            for c in self._fetch_all_pages(
                lambda: client.table("classes").select("class_id, class_name")
            )
        }
        self._student_id_by_code = {
            s["student_code"]: s["student_id"]
            for s in self._fetch_all_pages(
                lambda: client.table("students").select("student_id, student_code")
            )
            if s.get("student_code")
        }

    def _codes_to_ids(self, codes: List[str]) -> List[int]:
        """Doi ma hoc sinh -> student_id (so). Neu co ma chua co trong cache
        (hoc sinh moi them sau khi khoi dong) thi tra cuu bo sung truc tiep."""
        self._ensure_lookup_maps()
        mp = self._student_id_by_code or {}
        ids = [mp[c] for c in codes if c in mp]
        missing = [c for c in codes if c not in mp]
        if missing:
            client = self._get_client()
            resp = client.table("students").select("student_id, student_code").in_("student_code", missing).execute()
            for s in resp.data or []:
                mp[s["student_code"]] = s["student_id"]
                ids.append(s["student_id"])
        return ids

    def _build_results_query(self, student_ids: Optional[List[int]] = None):
        """Query bang diem (subject_results) + cac bang lien quan. Neu truyen
        student_ids -> CHI lay diem cua nhung hoc sinh do (loc theo cot truc tiep
        subject_results.student_id — nhanh, dung cho tra cuu real-time).
        subject_name (neu co) van gioi han 1 mon."""
        client = self._get_client()
        q = client.table("subject_results").select(
            "result_id, dtb_mhk, dtb_mcn, ranking, teacher_comment, student_id,"
            "students(student_id, full_name, student_code, date_of_birth, class_id),"
            "semesters(semester_name, term_order, school_years(school_year_id, year_name)),"
            "subjects!inner(subject_name),"
            "grade_items(score, grade_types(type_code))"
        )
        if self.subject_name:
            q = q.eq("subjects.subject_name", self.subject_name)
        if student_ids:
            q = q.in_("student_id", list(student_ids))
        return q

    def _fetch_records(self, student_ids: Optional[List[int]] = None) -> List[GradeRecord]:
        self._ensure_lookup_maps()
        rows = self._fetch_all_pages(lambda: self._build_results_query(student_ids))
        return self._rows_to_records(rows)

    def fetch_for_codes(self, codes) -> List[GradeRecord]:
        """Lay diem MOI NHAT (query truc tiep Supabase) cho mot nhom hoc sinh
        theo ma. Dung khi tra cuu diem/xep loai/thong ke de luon phan anh diem
        vua duoc cap nhat — khong dung snapshot cache. Neu loi -> fallback ve
        du lieu da nap trong bo nho."""
        codes = [c for c in (codes or []) if c]
        if not codes:
            return []
        try:
            ids = self._codes_to_ids(codes)
            if not ids:
                return []
            return self._fetch_records(student_ids=ids)
        except Exception as e:
            logger.error("Loi khi lay diem real-time (%s) — dung cache: %s", codes, e)
            codeset = set(codes)
            return [r for r in self.records if r.student_id in codeset]

    def _rows_to_records(self, rows: list) -> List[GradeRecord]:
        records: List[GradeRecord] = []
        for row in rows:
            student = row.get("students") or {}
            sem = row.get("semesters") or {}
            year = sem.get("school_years") or {}
            subject_name = (row.get("subjects") or {}).get("subject_name") or self.subject_name or ""

            school_year = year.get("year_name") or ""
            semester = "II" if sem.get("term_order") == 2 else "I"
            
            class_id = student.get("class_id")
            class_name = self._classes_by_id.get(class_id, "") if self._classes_by_id and class_id else ""

            tx_scores: List[float] = []
            giua_ky: Optional[float] = None
            cuoi_ky: Optional[float] = None
            for gi in row.get("grade_items") or []:
                score = gi.get("score")
                if score is None:
                    continue
                score = round(float(score), 2)
                code = (gi.get("grade_types") or {}).get("type_code")
                if code == _TX_CODE:
                    tx_scores.append(score)
                elif code == _GK_CODE:
                    giua_ky = score
                elif code == _CK_CODE:
                    cuoi_ky = score

            dtb_mhk = row.get("dtb_mhk")
            dtb_mcn = row.get("dtb_mcn")
            dob = student.get("date_of_birth")

            records.append(GradeRecord(
                school_year=school_year,
                source_file="supabase",
                sheet_name="",
                class_name=class_name,
                subject=subject_name,
                semester=semester,
                name=student.get("full_name") or "",
                student_id=student.get("student_code"),
                dob=str(dob) if dob else None,
                tx_scores=tx_scores,
                giua_ky=giua_ky,
                cuoi_ky=cuoi_ky,
                tb_hoc_ky_1=round(float(dtb_mhk), 2) if semester == "I" and dtb_mhk is not None else None,
                tb_hoc_ky_2=round(float(dtb_mhk), 2) if semester == "II" and dtb_mhk is not None else None,
                tb_ca_nam=round(float(dtb_mcn), 2) if dtb_mcn is not None else None,
                nhan_xet=row.get("teacher_comment") or "",
                danh_gia=(row.get("ranking") or None),
            ))

        return records
