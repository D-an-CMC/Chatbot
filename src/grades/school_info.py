# src/grades/school_info.py
# Tra cuu danh sach lop (cho giao vien) va thoi khoa bieu (cho hoc sinh) tu Supabase.
# Khac voi GradeStore/SupabaseGradeStore (chi xoay quanh diem so 1 mon), module nay
# doc truc tiep cac bang hanh chinh (classes, students, student_enrollments, timetables)
# nen chi hoat dong khi he thong dang dung Supabase (khong co du lieu tuong duong trong Excel).

import logging
from datetime import date as _date, timedelta as _timedelta
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SchoolInfoStore:
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        self.client = None

    def _get_client(self):
        if self.client is None:
            from supabase import create_client
            self.client = create_client(self.url, self.key)
        return self.client

    @staticmethod
    def _fetch_all_pages(build_query, page_size: int = 1000) -> list:
        rows = []
        offset = 0
        while True:
            resp = build_query().range(offset, offset + page_size - 1).execute()
            rows.extend(resp.data)
            if len(resp.data) < page_size:
                break
            offset += page_size
        return rows

    def _find_class_id(self, class_name: str, school_year: str) -> Optional[int]:
        client = self._get_client()
        resp = (
            client.table("classes")
            .select("class_id, school_years!inner(year_name)")
            .eq("class_name", class_name.upper())
            .eq("school_years.year_name", school_year)
            .execute()
        )
        if not resp.data:
            return None
        return resp.data[0]["class_id"]

    # -- danh sach lop -------------------------------------------------------

    def get_class_roster(self, class_name: str, school_year: str) -> List[Dict]:
        class_id = self._find_class_id(class_name, school_year)
        if class_id is None:
            return []

        client = self._get_client()
        rows = self._fetch_all_pages(
            lambda: client.table("students")
            .select("full_name, student_code, gender, date_of_birth")
            .eq("class_id", class_id)
        )

        roster = []
        for s in rows:
            roster.append({
                "roll_number": None,
                "full_name": s.get("full_name"),
                "student_code": s.get("student_code"),
                "gender": s.get("gender"),
                "status": None,
            })
        roster.sort(key=lambda r: (r["full_name"] or ""))
        return roster

    # -- hoc ky hien tai / lop cua hoc sinh --------------------------------

    def get_current_school_year(self) -> Optional[str]:
        """Lay nam hoc dang duoc danh dau la is_current=TRUE trong bang school_years."""
        client = self._get_client()
        resp = (
            client.table("school_years")
            .select("year_name")
            .eq("is_current", True)
            .execute()
        )
        if resp.data:
            return resp.data[0]["year_name"]
        return None

    def get_current_term(self, today_iso: str) -> Optional[Dict]:
        """Xac dinh hoc ky "hien tai" theo NGAY THUC.

        - Uu tien hoc ky dang dien ra (start_date <= today <= end_date).
        - Neu hom nay roi vao ky nghi (khong nam trong ky nao, vd he) thi lay
          ky GAN NHAT vua ket thuc (end_date <= today, lon nhat) de van tra
          duoc TKB/lich thi cua ky vua qua.
        - Neu hom nay truoc moi ky (dau nam) thi lay ky sap toi (som nhat).

        Tra ve {semester_id, term_order, year_name, is_current}."""
        client = self._get_client()

        def _pack(row, is_current):
            return {
                "semester_id": row["semester_id"],
                "term_order": row["term_order"],
                "year_name": (row.get("school_years") or {}).get("year_name"),
                "is_current": is_current,
            }

        sel = "semester_id, term_order, start_date, end_date, school_years(year_name)"

        # 1) Ky dang dien ra
        resp = (
            client.table("semesters").select(sel)
            .lte("start_date", today_iso).gte("end_date", today_iso).execute()
        )
        if resp.data:
            return _pack(resp.data[0], True)

        # 2) Ky vua ket thuc gan nhat
        resp = (
            client.table("semesters").select(sel)
            .lte("end_date", today_iso).order("end_date", desc=True).limit(1).execute()
        )
        if resp.data:
            return _pack(resp.data[0], False)

        # 3) Ky sap toi (som nhat)
        resp = (
            client.table("semesters").select(sel)
            .order("start_date", desc=False).limit(1).execute()
        )
        if resp.data:
            return _pack(resp.data[0], False)
        return None

    def get_student_class(self, student_id: int, year_name: str) -> Optional[str]:
        if student_id is None or not year_name:
            return None
        client = self._get_client()
        
        resp2 = (
            client.table("students")
            .select("classes(class_name, school_years(year_name))")
            .eq("student_id", student_id)
            .execute()
        )
        if resp2.data:
            c = resp2.data[0].get("classes") or {}
            cls = c.get("class_name")
            cyear = (c.get("school_years") or {}).get("year_name")
            if cls and cyear == year_name:
                return cls
        return None

    def pick_representative_week(self, class_name: str, school_year: str, today_iso: str) -> Optional[str]:
        """Chon 1 tuan dai dien cho lop: tuan co week_start GAN NHAT <= hom nay
        (real-time). TKB 1 hoc ky la co dinh nen 1 tuan la du dai dien; neu hom
        nay truoc moi tuan da nhap thi lay tuan som nhat."""
        class_id = self._find_class_id(class_name, school_year)
        if class_id is None:
            return None
        client = self._get_client()
        resp = (
            client.table("timetables")
            .select("week_start")
            .eq("class_id", class_id)
            .lte("week_start", today_iso)
            .order("week_start", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["week_start"]
        # Hom nay truoc moi tuan da nhap -> lay tuan som nhat co san
        resp2 = (
            client.table("timetables")
            .select("week_start")
            .eq("class_id", class_id)
            .order("week_start", desc=False)
            .limit(1)
            .execute()
        )
        return resp2.data[0]["week_start"] if resp2.data else None

    # -- thoi khoa bieu -------------------------------------------------------

    def get_timetable(
        self,
        class_name: str,
        school_year: str,
        week_start: Optional[str] = None,
        term_order: Optional[int] = None,
    ) -> List[Dict]:
        """Lay thoi khoa bieu cua 1 lop.

        - week_start (YYYY-MM-DD): chi lay TKB cua dung tuan bat dau ngay do.
        - term_order (1|2): lay TKB ca hoc ky (gop tat ca tuan thuoc ky, khu
          trung thanh 1 tuan dai dien).
        - khong truyen gi: lay tat ca (khu trung theo mau tuan)."""
        class_id = self._find_class_id(class_name, school_year)
        if class_id is None:
            return []

        client = self._get_client()

        def _build():
            # teachers(...) can chi ro FK vi bang teachers gio co 2 quan he voi
            # timetables (them qua exam_proctors) -> PostgREST bao mo ho neu khong.
            q = client.table("timetables").select(
                "day_of_week, period_no, start_time, end_time, room, week_start,"
                "custom_subject_name, custom_teacher_name,"
                "subjects(subject_name), teachers!timetables_teacher_id_fkey(full_name),"
                "semesters!inner(term_order)"
            ).eq("class_id", class_id).eq("timetable_type_id", 1)  # 1 = lich hoc (loai tru lich thi)
            if week_start:
                q = q.eq("week_start", week_start)
            if term_order:
                q = q.eq("semesters.term_order", term_order)
            return q

        rows = self._fetch_all_pages(_build)

        # Khu trung lap: du lieu bi nhan ban nhieu lan (cung thu/tiet/mon). Khi
        # lay theo tuan cu the thi khu theo (thu, tiet, mon, GV, phong); khi lay
        # ca ky cung khu tuong tu de ra 1 mau tuan dai dien.
        result = []
        seen = set()
        for r in rows:
            subject = r.get("custom_subject_name") or (r.get("subjects") or {}).get("subject_name")
            teacher = r.get("custom_teacher_name") or (r.get("teachers") or {}).get("full_name")
            item = {
                "day_of_week": r.get("day_of_week"),
                "period_no": r.get("period_no"),
                "start_time": r.get("start_time"),
                "end_time": r.get("end_time"),
                "room": r.get("room"),
                "subject_name": subject,
                "teacher_name": teacher,
            }
            key = (item["day_of_week"], item["period_no"], item["subject_name"],
                   item["teacher_name"], item["room"])
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    # -- tra cuu hoc sinh (cho bo chon vai tro demo) ------------------------

    def get_student_by_code(self, student_code: str) -> Optional[Dict]:
        if not student_code:
            return None
        client = self._get_client()
        resp = (
            client.table("students")
            .select("student_id, full_name, student_code")
            .eq("student_code", student_code)
            .execute()
        )
        return resp.data[0] if resp.data else None

    # -- ho so thong tin hoc sinh (cho giao vien / admin) ------------------

    def _latest_class(self, student_id: int):
        client = self._get_client()
        resp = (
            client.table("students")
            .select("classes(class_name, school_years(year_name))")
            .eq("student_id", student_id)
            .execute()
        )
        best = {"class_name": None, "year_name": None, "roll_number": None}
        if resp.data:
            c = resp.data[0].get("classes") or {}
            yr = (c.get("school_years") or {}).get("year_name") or ""
            cls = c.get("class_name")
            if cls:
                best = {"class_name": cls, "year_name": yr, "roll_number": None}
        return best

    def get_student_profiles(
        self, codes: Optional[List[str]] = None, names: Optional[List[str]] = None, limit: int = 10,
    ) -> List[Dict]:
        """Ho so day du cua hoc sinh (ma, gioi tinh, ngay sinh, dia chi, phu
        huynh, lop hien tai). Tra cuu theo ma hoc sinh HOAC theo ho ten."""
        client = self._get_client()
        q = client.table("students").select(
            "student_id, student_code, full_name, gender, date_of_birth, address,"
            "enrollment_date, status, parent_full_name, parent_phone, parent_email,"
            "parent_relationship"
        )
        if codes:
            q = q.in_("student_code", codes)
        elif names:
            q = q.in_("full_name", names)
        else:
            return []

        rows = q.limit(limit).execute().data or []
        profiles = []
        for s in rows:
            info = self._latest_class(s["student_id"])
            p = dict(s)
            p["class_name"] = info["class_name"]
            p["class_year"] = info["year_name"]
            p["roll_number"] = info["roll_number"]
            profiles.append(p)
        profiles.sort(key=lambda x: x.get("full_name") or "")
        return profiles

    # -- tra cuu giao vien -------------------------------------------------

    def get_homeroom_teacher(self, class_name: str, school_year: str) -> Optional[Dict]:
        """Giao vien chu nhiem (GVCN) cua 1 lop trong 1 nam hoc."""
        client = self._get_client()
        resp = (
            client.table("classes")
            .select(
                "class_name, school_years!inner(year_name),"
                "teachers(full_name, teacher_code, phone, gender, title, subjects(subject_name))"
            )
            .eq("class_name", class_name.upper())
            .eq("school_years.year_name", school_year)
            .execute()
        )
        if not resp.data:
            return None
        t = resp.data[0].get("teachers")
        if not t:
            return None
        return {
            "full_name": t.get("full_name"),
            "teacher_code": t.get("teacher_code"),
            "phone": t.get("phone"),
            "gender": t.get("gender"),
            "title": t.get("title"),
            "subject_name": (t.get("subjects") or {}).get("subject_name"),
        }

    def get_class_teachers(self, class_name: str, school_year: str) -> List[Dict]:
        """Danh sach giao vien bo mon cua 1 lop (mon -> giao vien), lay tu TKB
        (timetables type 1). Khu trung theo (mon, giao vien)."""
        class_id = self._find_class_id(class_name, school_year)
        if class_id is None:
            return []
        client = self._get_client()
        rows = self._fetch_all_pages(
            lambda: client.table("timetables").select(
                "custom_subject_name, custom_teacher_name, subjects(subject_name),"
                "teachers!timetables_teacher_id_fkey(full_name, teacher_code)"
            ).eq("class_id", class_id).eq("timetable_type_id", 1)
        )
        seen = set()
        result = []
        for r in rows:
            subject = r.get("custom_subject_name") or (r.get("subjects") or {}).get("subject_name")
            teacher = (r.get("teachers") or {}).get("full_name") or r.get("custom_teacher_name")
            code = (r.get("teachers") or {}).get("teacher_code")
            if not subject or not teacher:
                continue
            key = (subject, teacher)
            if key in seen:
                continue
            seen.add(key)
            result.append({"subject_name": subject, "full_name": teacher, "teacher_code": code})
        result.sort(key=lambda x: x["subject_name"])
        return result

    def find_teachers_by_name(self, name_query: str) -> List[Dict]:
        """Tim giao vien theo ho ten (chua dau) hoac ma giao vien (GVxx)."""
        if not name_query:
            return []
        client = self._get_client()
        q = name_query.strip()
        if q.upper().startswith("GV") and any(ch.isdigit() for ch in q):
            resp = client.table("teachers").select(
                "teacher_id, full_name, teacher_code, phone, gender, title, subjects(subject_name)"
            ).ilike("teacher_code", q).execute()
        else:
            resp = client.table("teachers").select(
                "teacher_id, full_name, teacher_code, phone, gender, title, subjects(subject_name)"
            ).ilike("full_name", f"%{q}%").execute()
        out = []
        for t in resp.data or []:
            out.append({
                "teacher_id": t.get("teacher_id"),
                "full_name": t.get("full_name"),
                "teacher_code": t.get("teacher_code"),
                "phone": t.get("phone"),
                "gender": t.get("gender"),
                "title": t.get("title"),
                "subject_name": (t.get("subjects") or {}).get("subject_name"),
            })
        return out

    def get_teacher_assignments(self, teacher_id: int, school_year: Optional[str] = None) -> Dict:
        """Cac lop giao vien nay day (mon + lop, tu TKB) va cac lop lam chu nhiem."""
        client = self._get_client()

        # Lop day (tu timetables): lay class_id + subject
        tt_rows = self._fetch_all_pages(
            lambda: client.table("timetables").select(
                "class_id, subjects(subject_name), classes(class_name, school_years(year_name))"
            ).eq("teacher_id", teacher_id).eq("timetable_type_id", 1)
        )
        teaching = set()
        for r in tt_rows:
            cls = (r.get("classes") or {})
            year = (cls.get("school_years") or {}).get("year_name")
            if school_year and year != school_year:
                continue
            subject = (r.get("subjects") or {}).get("subject_name")
            teaching.add((cls.get("class_name"), subject, year))

        # Lop chu nhiem
        hr = (
            client.table("classes")
            .select("class_name, school_years(year_name)")
            .eq("homeroom_teacher_id", teacher_id)
            .execute()
        )
        homeroom = []
        for c in hr.data or []:
            year = (c.get("school_years") or {}).get("year_name")
            if school_year and year != school_year:
                continue
            homeroom.append({"class_name": c.get("class_name"), "year_name": year})

        teaching_list = [
            {"class_name": c, "subject_name": s, "year_name": y}
            for (c, s, y) in sorted(teaching, key=lambda x: (x[2] or "", x[0] or "", x[1] or ""))
        ]
        return {"teaching": teaching_list, "homeroom": homeroom}

    # -- diem danh -------------------------------------------------------

    def find_student_ids_by_names(self, names: List[str]) -> List[Dict]:
        if not names:
            return []
        client = self._get_client()
        resp = (
            client.table("students")
            .select("student_id, full_name, student_code")
            .in_("full_name", names)
            .execute()
        )
        return resp.data

    def get_attendance(self, student_ids: List[int], limit: int = 50) -> List[Dict]:
        if not student_ids:
            return []
        client = self._get_client()
        rows = self._fetch_all_pages(
            lambda: client.table("attendances")
            .select("status, note, students(full_name), "
                    "attendance_sessions(session_date, session)")
            .in_("student_id", student_ids)
        )
        result = []
        for r in rows:
            ses = r.get("attendance_sessions") or {}
            result.append({
                "full_name": (r.get("students") or {}).get("full_name"),
                "session_date": ses.get("session_date"),
                "session": ses.get("session"),
                "status": r.get("status"),
                "note": r.get("note"),
            })
        result.sort(key=lambda x: (x.get("session_date") or "", x.get("session") or ""), reverse=True)
        return result[:limit]

    # -- lich thi -------------------------------------------------------

    @staticmethod
    def _exam_date(week_start, day_of_week) -> Optional[str]:
        """Suy ra ngay thi thuc te = week_start (thu Hai) + (thu - 2) ngay.
        day_of_week co the la '2'..'8' hoac 'Thu 2'... Tra ve ISO yyyy-mm-dd."""
        if not week_start:
            return None
        s = str(day_of_week or "").strip()
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            return None
        try:
            n = int(digits)
            base = _date.fromisoformat(str(week_start))
            return (base + _timedelta(days=n - 2)).isoformat()
        except Exception:
            return None

    def get_exam_schedule(
        self, class_name: str, school_year: str, term_order: Optional[int] = None,
    ) -> List[Dict]:
        """Lich thi cua 1 lop — luu trong bang timetables voi timetable_type_id=2
        (khac lich hoc type=1). Moi dong co exam_name (vd 'Giua ki 1'), mon hoc,
        thu, tiet, tuan. term_order (1|2) loc theo hoc ky."""
        class_id = self._find_class_id(class_name, school_year)
        if class_id is None:
            return []

        client = self._get_client()

        def _build():
            q = client.table("timetables").select(
                "day_of_week, period_no, start_time, room, week_start, exam_name,"
                "custom_subject_name, subjects(subject_name), semesters!inner(term_order)"
            ).eq("class_id", class_id).eq("timetable_type_id", 2)
            if term_order:
                q = q.eq("semesters.term_order", term_order)
            return q

        rows = self._fetch_all_pages(_build)

        result = []
        seen = set()
        for r in rows:
            subject = r.get("custom_subject_name") or (r.get("subjects") or {}).get("subject_name")
            # bo tien to "THI - " neu co de hien ten mon gon
            if subject and subject.upper().startswith("THI -"):
                subject = subject[5:].strip()
            item = {
                "exam_name": r.get("exam_name"),
                "subject_name": subject,
                "day_of_week": r.get("day_of_week"),
                "period_no": r.get("period_no"),
                "start_time": r.get("start_time"),
                "room": r.get("room"),
                "exam_date": self._exam_date(r.get("week_start"), r.get("day_of_week")),
            }
            key = (item["exam_name"], item["subject_name"], item["exam_date"], item["period_no"])
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        result.sort(key=lambda x: (x.get("exam_date") or "", x.get("period_no") or 0))
        return result

    # -- thong bao -------------------------------------------------------

    def get_recent_notifications(self, limit: int = 10) -> List[Dict]:
        client = self._get_client()
        resp = (
            client.table("notifications")
            .select("title, content, target_type, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data

    # -- hoat dong ngoai khoa ---------------------------------------------

    def get_activities(
        self, school_year: Optional[str] = None, semester: Optional[str] = None, limit: int = 20,
    ) -> List[Dict]:
        client = self._get_client()
        rows = self._fetch_all_pages(
            lambda: client.table("activities").select(
                "activity_name, activity_type, description, location, start_datetime, end_datetime,"
                "teachers(full_name),"
                "semesters(term_order, school_years(year_name))"
            )
        )

        term_order = (2 if semester == "II" else 1) if semester else None
        result = []
        for r in rows:
            sem = r.get("semesters") or {}
            year = sem.get("school_years") or {}
            if school_year and year.get("year_name") != school_year:
                continue
            if term_order is not None and sem.get("term_order") != term_order:
                continue
            result.append({
                "activity_name": r.get("activity_name"),
                "activity_type": r.get("activity_type"),
                "description": r.get("description"),
                "location": r.get("location"),
                "start_datetime": r.get("start_datetime"),
                "end_datetime": r.get("end_datetime"),
                "organizer": (r.get("teachers") or {}).get("full_name"),
            })
        result.sort(key=lambda x: x.get("start_datetime") or "")
        return result[:limit]
