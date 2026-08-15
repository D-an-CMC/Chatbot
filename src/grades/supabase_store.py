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

    def _fetch_records(self) -> List[GradeRecord]:
        client = self._get_client()

        # 1) Bang tra cuu nho — nap toan bo (co phan trang de an toan)
        classes_by_id: Dict[int, str] = {
            c["class_id"]: c["class_name"]
            for c in self._fetch_all_pages(
                lambda: client.table("classes").select("class_id, class_name")
            )
        }
        enroll_by_student_year: Dict[tuple, str] = {}
        for e in self._fetch_all_pages(
            lambda: client.table("student_enrollments").select("student_id, class_id, school_year_id")
        ):
            enroll_by_student_year[(e["student_id"], e["school_year_id"])] = classes_by_id.get(e["class_id"], "")

        # 2) Ket qua mon hoc + cac bang lien quan (embed qua PostgREST).
        #    Neu subject_name rong => lay TAT CA cac mon; nguoc lai loc 1 mon.
        def _build_results_query():
            q = client.table("subject_results").select(
                "result_id, dtb_mhk, dtb_mcn, ranking, teacher_comment,"
                "students(student_id, full_name, student_code, date_of_birth),"
                "semesters(semester_name, term_order, school_years(school_year_id, year_name)),"
                "subjects!inner(subject_name),"
                "grade_items(score, grade_types(type_code))"
            )
            if self.subject_name:
                q = q.eq("subjects.subject_name", self.subject_name)
            return q

        rows = self._fetch_all_pages(_build_results_query)

        records: List[GradeRecord] = []
        for row in rows:
            student = row.get("students") or {}
            sem = row.get("semesters") or {}
            year = sem.get("school_years") or {}
            subject_name = (row.get("subjects") or {}).get("subject_name") or self.subject_name or ""

            school_year_id = year.get("school_year_id")
            school_year = year.get("year_name") or ""
            semester = "II" if sem.get("term_order") == 2 else "I"
            student_id = student.get("student_id")

            class_name = enroll_by_student_year.get((student_id, school_year_id), "")

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
