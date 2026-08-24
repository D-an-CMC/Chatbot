# config.py
# Cau hinh chung cho chatbot tra cuu diem hoc sinh (Khoa hoc tu nhien / Vat ly)

import os
import logging
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# ---------------------------------------------------------------------------
# Duong dan
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


# Thu muc chua cac file Excel so diem (moi file = 1 nam hoc)
DATA_DIR = BASE_DIR / "data"
GRADES_DIR = DATA_DIR / "diem_khtn"

# Thu muc luu memory (lich su hoi thoai)
MEMORY_DIR = BASE_DIR / "memory"

for _dir in [DATA_DIR, GRADES_DIR, MEMORY_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
GEMINI_API_KEY = _env_str("GEMINI_API_KEY", "")
GEMINI_MODEL = _env_str("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TEMPERATURE = _env_float("GEMINI_TEMPERATURE", 0.1)
GEMINI_MAX_TOKENS = _env_int("GEMINI_MAX_TOKENS", 8192)

# ---------------------------------------------------------------------------
# OpenRouter / LLM
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY = _env_str("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = _env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

LLM_MODEL = _env_str("LLM_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.1)
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 8192)

# Provider mac dinh dung cho chat (openrouter | groq | gemini | ...)
DEFAULT_LLM_PROVIDER = _env_str("DEFAULT_LLM_PROVIDER", "gemini")

# ---------------------------------------------------------------------------
# Groq (LLM thay the)
# ---------------------------------------------------------------------------
GROQ_API_KEY = _env_str("GROQ_API_KEY", "")
GROQ_BASE_URL = _env_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = _env_str("GROQ_MODEL", "qwen/qwen3-32b")

# ---------------------------------------------------------------------------
# Supabase (nguon du lieu diem thay the cho file Excel)
# ---------------------------------------------------------------------------
SUPABASE_URL = _env_str("SUPABASE_URL", "")
SUPABASE_KEY = _env_str("SUPABASE_KEY", "")  # service_role key — chi dung o backend
SUPABASE_ANON_KEY = _env_str("SUPABASE_ANON_KEY", "")  # dung cho dang nhap (Supabase Auth)
# De TRONG ("") de nap TAT CA cac mon. Dat ten 1 mon (vd "Khoa học tự nhiên")
# neu chi muon gioi han dung 1 mon.
SUPABASE_SUBJECT_NAME = _env_str("SUPABASE_SUBJECT_NAME", "")
# Bat khi da co du SUPABASE_URL + SUPABASE_KEY (service_role key)
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

# ---------------------------------------------------------------------------
# Che do xac thuc / phan quyen (AUTH_MODE)
# ---------------------------------------------------------------------------
#   "select" - CHON VAI TRO thu cong (demo/dev, khong can dang nhap) — MAC DINH.
#              Dung khi chua ghep dang nhap that. Logic phan quyen van hoat dong
#              day du; chi khac la SessionUser lay tu bo chon vai tro thay vi login.
#   "login"  - Man hinh dang nhap Supabase Auth (yeu cau tai khoan that + anon key).
#   "off"    - Khong phan quyen (moi nguoi xem duoc tat ca — nhu truoc khi lam auth).
AUTH_MODE = _env_str("AUTH_MODE", "select").strip().lower()

# Bat man hinh dang nhap that khi AUTH_MODE=login va co du anon key
USE_AUTH = (AUTH_MODE == "login") and USE_SUPABASE and bool(SUPABASE_ANON_KEY)
# Bat bo chon vai tro (demo) khi AUTH_MODE=select
USE_ROLE_SELECT = (AUTH_MODE == "select")

# ---------------------------------------------------------------------------
# Observability (Langfuse)
# ---------------------------------------------------------------------------
LANGFUSE_ENABLED = _env_str("LANGFUSE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LANGFUSE_SECRET_KEY = _env_str("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY = _env_str("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_BASE_URL = _env_str("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

# ---------------------------------------------------------------------------
# Memory (lich su hoi thoai)
# ---------------------------------------------------------------------------

MEMORY_DB_PATH = MEMORY_DIR / "memory.db"
SHORT_TERM_MAX_TURNS = _env_int("SHORT_TERM_MAX_TURNS", 30)

# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

APP_TITLE = _env_str("APP_TITLE", "Tra Cuu Diem Khoa Hoc Tu Nhien")
APP_ICON = _env_str("APP_ICON", "📊")
APP_DESCRIPTION = _env_str(
    "APP_DESCRIPTION",
    "Chatbot tra cuu diem so va nhan xet mon Khoa hoc tu nhien / Vat ly tu so diem Excel.",
)

# ---------------------------------------------------------------------------
# FastAPI (api.py)
# ---------------------------------------------------------------------------

API_HOST = _env_str("API_HOST", "0.0.0.0")
API_PORT = _env_int("API_PORT", 8000)
API_CORS_ORIGINS = [
    o.strip()
    for o in _env_str("API_CORS_ORIGINS", "*").split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = getattr(logging, _env_str("LOG_LEVEL", "INFO").upper(), logging.INFO)
LOG_FORMAT = _env_str("LOG_FORMAT", "%(asctime)s | %(levelname)s | %(name)s | %(message)s")

