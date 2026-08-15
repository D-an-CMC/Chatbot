# src/memory/memory.py
# Luu lich su hoi thoai (short-term) bang SQLite de ho tro cau hoi noi tiep
# (vd: "con hoc ky 2 thi sao?"). Khong con ho so ca nhan (khong can cho tra cuu diem).
#
# session_id duoc truyen theo tung loi goi (khong co dinh trong instance) vi
# ChatbotEngine la 1 singleton dung chung cho toan bo server (Streamlit
# @st.cache_resource) — neu co dinh session_id se lam lo lich su hoi thoai
# giua cac nguoi dung khac nhau dang dang nhap dong thoi.

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

from config import MEMORY_DIR, MEMORY_DB_PATH, SHORT_TERM_MAX_TURNS, LOG_FORMAT, LOG_LEVEL

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

DEFAULT_SESSION_ID = "default"


@dataclass
class Turn:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


def _get_connection() -> sqlite3.Connection:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS short_term (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT 'default',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_short_term_session
            ON short_term(session_id, id);
    """)
    conn.commit()
    return conn


class ShortTermMemory:
    def __init__(self, max_turns: int = SHORT_TERM_MAX_TURNS):
        self.max_turns = max_turns
        self._conn = _get_connection()

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        timestamp = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO short_term (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, role, content, timestamp),
        )
        self._conn.execute("""
            DELETE FROM short_term
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM short_term
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
        """, (session_id, session_id, self.max_turns))
        self._conn.commit()

    def get_history_for_llm(self, session_id: str) -> List[Dict[str, str]]:
        cursor = self._conn.execute(
            "SELECT role, content FROM short_term WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        return [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]

    def clear(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM short_term WHERE session_id = ?", (session_id,))
        self._conn.commit()

    def count(self, session_id: str) -> int:
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM short_term WHERE session_id = ?", (session_id,),
        )
        return cursor.fetchone()[0]


class MemoryManager:
    def __init__(self):
        self.short_term = ShortTermMemory()
        logger.info("MemoryManager da khoi tao (SQLite backend: %s)", MEMORY_DB_PATH)

    def add_user_message(self, message: str, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.short_term.add_turn(session_id, "user", message)

    def add_assistant_message(self, message: str, question: str = "", session_id: str = DEFAULT_SESSION_ID) -> None:
        self.short_term.add_turn(session_id, "assistant", message)

    def get_chat_history(self, session_id: str = DEFAULT_SESSION_ID) -> List[Dict[str, str]]:
        return self.short_term.get_history_for_llm(session_id)

    def clear_session(self, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.short_term.clear(session_id)
        logger.info("Session moi bat dau (session_id=%s)", session_id)
