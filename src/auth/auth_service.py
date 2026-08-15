# src/auth/auth_service.py
# Dang nhap qua Supabase Auth (email/mat khau) va phan giai vai tro
# (Admin / GiaoVien / HocSinh-PhuHuynh) de gioi han quyen truy cap du lieu.

import logging
from dataclasses import dataclass
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROLE_ADMIN = "Admin"
ROLE_TEACHER = "GiaoVien"
ROLE_STUDENT = "HocSinh-PhuHuynh"


@dataclass
class SessionUser:
    user_id: int
    email: str
    full_name: str
    role_name: str  # ROLE_ADMIN | ROLE_TEACHER | ROLE_STUDENT
    student_id: Optional[int] = None
    student_code: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role_name == ROLE_ADMIN

    @property
    def is_teacher(self) -> bool:
        return self.role_name == ROLE_TEACHER

    @property
    def is_student(self) -> bool:
        return self.role_name == ROLE_STUDENT

    @property
    def session_key(self) -> str:
        """Dung lam session_id rieng cho lich su hoi thoai cua tung nguoi dung.
        Uu tien student_code de moi hoc sinh (ke ca o che do chon vai tro demo,
        khi user_id co the trung nhau) co lich su rieng biet."""
        if self.student_code:
            return f"student_{self.student_code}"
        return f"user_{self.user_id}"


class AuthService:
    """Xac thuc bang Supabase Auth (anon key), sau do tra cuu vai tro va
    student_id (neu la hoc sinh) bang service_role key — buoc tra cuu noi bo
    cua backend, khong phai truy cap du lieu cua nguoi dung khac."""

    def __init__(self, url: str, anon_key: str, service_key: str):
        self.url = url
        self.anon_key = anon_key
        self.service_key = service_key
        self._admin_client = None

    def _get_admin_client(self):
        if self._admin_client is None:
            from supabase import create_client
            self._admin_client = create_client(self.url, self.service_key)
        return self._admin_client

    def sign_in(self, email: str, password: str) -> SessionUser:
        """Raise ValueError voi thong bao than thien neu dang nhap that bai."""
        email = (email or "").strip()
        if not email or not password:
            raise ValueError("Vui lòng nhập đầy đủ email và mật khẩu.")

        from supabase import create_client
        auth_client = create_client(self.url, self.anon_key)
        try:
            result = auth_client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as e:
            logger.warning("Dang nhap that bai cho %s: %s", email, e)
            raise ValueError("Email hoặc mật khẩu không đúng.")

        auth_user = getattr(result, "user", None)
        if auth_user is None:
            raise ValueError("Email hoặc mật khẩu không đúng.")

        client = self._get_admin_client()
        resp = (
            client.table("users")
            .select("user_id, email, username, role_id, roles(role_name)")
            .eq("auth_id", auth_user.id)
            .execute()
        )
        if not resp.data:
            raise ValueError("Tài khoản chưa được gán vai trò trong hệ thống. Vui lòng liên hệ quản trị viên.")

        row = resp.data[0]
        role_name = (row.get("roles") or {}).get("role_name", "")
        full_name = row.get("username") or row.get("email") or email

        student_id = None
        student_code = None
        if role_name == ROLE_STUDENT:
            s_resp = (
                client.table("students")
                .select("student_id, student_code, full_name")
                .eq("user_id", row["user_id"])
                .execute()
            )
            if not s_resp.data:
                raise ValueError(
                    "Tài khoản này chưa liên kết với hồ sơ học sinh nào. Vui lòng liên hệ quản trị viên."
                )
            student_id = s_resp.data[0]["student_id"]
            student_code = s_resp.data[0]["student_code"]
            full_name = s_resp.data[0]["full_name"] or full_name

        return SessionUser(
            user_id=row["user_id"],
            email=row.get("email") or email,
            full_name=full_name,
            role_name=role_name,
            student_id=student_id,
            student_code=student_code,
        )
