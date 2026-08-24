# src/memory/memory.py
# Luu lich su hoi thoai (short-term) bang SQLite de ho tro cau hoi noi tiep
# (vd: "con hoc ky 2 thi sao?"). Khong con ho so ca nhan (khong can cho tra cuu diem).
#
# Ngoai lich su tin nhan, module nay con luu NGU CANH HOI THOAI (conversation
# context) — cac bo loc da trich xuat thanh cong o luot truoc (ten, lop, nam
# hoc, hoc ky, mon, intent). Ngu canh nay duoc truyen lai cho LLM o luot sau
# de no quyet dinh giu hay ghi de, giup xu ly cau hoi noi tiep tot hon.
#
# session_id duoc truyen theo tung loi goi (khong co dinh trong instance) vi
# ChatbotEngine la 1 singleton dung chung cho toan bo server (Streamlit
# @st.cache_resource) — neu co dinh session_id se lam lo lich su hoi thoai
# giua cac nguoi dung khac nhau dang dang nhap dong thoi.

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

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

        CREATE TABLE IF NOT EXISTS conversation_context (
            session_id TEXT PRIMARY KEY,
            intent TEXT,
            filters_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_conversations (
            conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tools_used TEXT,
            citations TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE
        );
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

    # -- Ngu canh hoi thoai (conversation context) ---------------------------

    def save_context(self, session_id: str, intent: str, filters: Dict) -> None:
        """Luu (upsert) ngu canh hoi thoai gan nhat cua 1 session.
        filters la dict {name_query, class_name, school_year, semester, subject}."""
        now = datetime.now().isoformat()
        filters_json = json.dumps(
            {k: v for k, v in filters.items() if v is not None},
            ensure_ascii=False,
        )
        self._conn.execute(
            "INSERT INTO conversation_context (session_id, intent, filters_json, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "intent=excluded.intent, filters_json=excluded.filters_json, updated_at=excluded.updated_at",
            (session_id, intent, filters_json, now),
        )
        self._conn.commit()

    def get_last_context(self, session_id: str) -> Optional[Dict]:
        """Doc ngu canh hoi thoai gan nhat. Tra ve {intent, filters} hoac None."""
        cursor = self._conn.execute(
            "SELECT intent, filters_json FROM conversation_context WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        try:
            filters = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            filters = {}
        return {"intent": row[0], "filters": filters}

    def clear_context(self, session_id: str) -> None:
        """Xoa ngu canh hoi thoai cua 1 session."""
        self._conn.execute(
            "DELETE FROM conversation_context WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()


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

    # -- Ngu canh hoi thoai -------------------------------------------------

    def save_context(self, session_id: str, intent: str, filters: Dict) -> None:
        """Luu ngu canh hoi thoai (intent + filters) cho luot sau ke thua."""
        self.short_term.save_context(session_id, intent, filters)

    def get_last_context(self, session_id: str = DEFAULT_SESSION_ID) -> Optional[Dict]:
        """Doc ngu canh hoi thoai gan nhat {intent, filters} hoac None."""
        return self.short_term.get_last_context(session_id)

    def clear_session(self, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.short_term.clear(session_id)
        self.short_term.clear_context(session_id)
        logger.info("Session moi bat dau (session_id=%s)", session_id)

    # -- AI Conversations (Next.js Client) ----------------------------------

    def get_ai_conversations(self, user_id: str) -> List[Dict]:
        cursor = self.short_term._conn.execute(
            "SELECT conversation_id, title, created_at, updated_at FROM ai_conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        )
        return [{"conversation_id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3]} for row in cursor.fetchall()]

    def create_ai_conversation(self, user_id: str, title: str = "Hội thoại mới") -> int:
        now = datetime.now().isoformat()
        cursor = self.short_term._conn.execute(
            "INSERT INTO ai_conversations (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now)
        )
        self.short_term._conn.commit()
        return cursor.lastrowid

    def update_ai_conversation_title(self, conversation_id: int, title: str) -> None:
        now = datetime.now().isoformat()
        self.short_term._conn.execute(
            "UPDATE ai_conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
            (title, now, conversation_id)
        )
        self.short_term._conn.commit()

    def get_ai_messages(self, conversation_id: int) -> List[Dict]:
        cursor = self.short_term._conn.execute(
            "SELECT message_id, role, content, tools_used, citations, created_at FROM ai_messages WHERE conversation_id = ? ORDER BY message_id ASC",
            (conversation_id,)
        )
        result = []
        for row in cursor.fetchall():
            try:
                tools_used = json.loads(row[3]) if row[3] else []
            except Exception:
                tools_used = []
            try:
                citations = json.loads(row[4]) if row[4] else []
            except Exception:
                citations = []
            result.append({
                "message_id": row[0],
                "conversation_id": conversation_id,
                "role": row[1],
                "content": row[2],
                "tools_used": tools_used,
                "citations": citations,
                "created_at": row[5]
            })
        return result

    def add_ai_message(self, conversation_id: int, role: str, content: str, tools_used: List = None, citations: List = None) -> int:
        now = datetime.now().isoformat()
        tools_str = json.dumps(tools_used, ensure_ascii=False) if tools_used else None
        citations_str = json.dumps(citations, ensure_ascii=False) if citations else None
        
        cursor = self.short_term._conn.execute(
            "INSERT INTO ai_messages (conversation_id, role, content, tools_used, citations, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, tools_str, citations_str, now)
        )
        self.short_term._conn.execute(
            "UPDATE ai_conversations SET updated_at = ? WHERE conversation_id = ?",
            (now, conversation_id)
        )
        self.short_term._conn.commit()
        return cursor.lastrowid

    def delete_ai_conversation(self, conversation_id: int) -> None:
        self.short_term._conn.execute("DELETE FROM ai_conversations WHERE conversation_id = ?", (conversation_id,))
        self.short_term._conn.commit()
