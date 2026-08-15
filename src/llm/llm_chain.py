# src/llm/llm_chain.py
# Khoi tao LLM qua OpenRouter + LangChain

import logging
from typing import Dict, Generator, List, Optional

from config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODEL,
    GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL,
    LLM_TEMPERATURE, LLM_MAX_TOKENS, LOG_FORMAT, LOG_LEVEL,
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TEMPERATURE, GEMINI_MAX_TOKENS
)
from src.llm.prompt_templates import SYSTEM_PROMPT
from src.observability.langfuse_tracer import create_generation, end_observation

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

_llms = {}
_GEMINI_PROVIDER_MODEL_MAP = {
    "gemini": GEMINI_MODEL,
    "gemini_2_5_flash": "gemini-2.5-flash",
    "gemini_2_0_flash": "gemini-2.0-flash",
    "gemini_1_5_flash": "gemini-1.5-flash",
    "gemini_1_5_flash_8b": "gemini-1.5-flash-8b",
}

def get_llm(provider: str = "openrouter"):
    """Tạo và cache LLM instance theo provider."""
    global _llms
    if provider in _llms:
        return _llms[provider]

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise ImportError("Thiếu langchain-openai: pip install langchain-openai")

    if provider == "groq":
        _llms[provider] = ChatOpenAI(
            model=GROQ_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            openai_api_key=GROQ_API_KEY,
            openai_api_base=GROQ_BASE_URL,
        )
        logger.info(f"LLM đã khởi tạo (Groq): {GROQ_MODEL}")

    elif provider == "openrouter":
        _llms[provider] = ChatOpenAI(
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            openai_api_key=OPENROUTER_API_KEY,
            openai_api_base=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://tra-cuu-diem.local",
                "X-Title": "Tra Cuu Diem Chatbot",
            },
        )
        logger.info(f"LLM đã khởi tạo (OpenRouter): {LLM_MODEL}")

    # ==================== GEMINI ====================
    elif provider in _GEMINI_PROVIDER_MODEL_MAP:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "Thiếu langchain-google-genai: pip install langchain-google-genai"
            )

        selected_model = _GEMINI_PROVIDER_MODEL_MAP.get(provider, GEMINI_MODEL)

        _llms[provider] = ChatGoogleGenerativeAI(
            model=selected_model,
            temperature=GEMINI_TEMPERATURE,
            max_tokens=GEMINI_MAX_TOKENS,
            google_api_key=GEMINI_API_KEY,
        )
        logger.info(f"LLM đã khởi tạo (Gemini): {selected_model}")
    # ===============================================

    else:
        raise ValueError(
            f"Provider không hỗ trợ: {provider}. "
            f"Hỗ trợ hiện tại: openrouter, groq, gemini, gemini_2_5_flash, gemini_2_0_flash, gemini_1_5_flash, gemini_1_5_flash_8b"
        )

    return _llms[provider]


def _content_to_text(content) -> str:
    """Chuan hoa response.content ve chuoi text.

    Mot so provider (vd Gemini/Gemma qua langchain-google-genai) tra ve content
    dang list cac block (str hoac dict {"type": "text", "text": ...}) thay vi
    mot chuoi don gian nhu OpenAI-compatible API."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "".join(parts)
    return str(content) if content is not None else ""


def build_messages(user_prompt: str, chat_history: Optional[List[Dict[str, str]]] = None) -> List:
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    if chat_history:
        history_to_include = chat_history[:-1] if chat_history else []
        for turn in history_to_include:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=turn["content"]))
            elif turn["role"] == "assistant":
                messages.append(AIMessage(content=turn["content"]))
    messages.append(HumanMessage(content=user_prompt))
    return messages


def call_llm(
    user_prompt: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
    provider: str = "openrouter",
    langfuse_parent=None,
) -> str:
    """Goi LLM va tra ve phan tra loi day du."""
    llm = get_llm(provider)
    messages = build_messages(user_prompt, chat_history)
    logger.info(f"Dang goi LLM (Provider: {provider})...")

    generation = create_generation(
        langfuse_parent,
        name="llm_call",
        model=provider,
        input_data={"user_prompt": user_prompt[:2000], "history_turns": len(chat_history or [])},
        metadata={"provider": provider},
    )

    try:
        response = llm.invoke(messages)
        answer = _content_to_text(response.content)
        end_observation(generation, output={"answer_preview": answer[:2000], "answer_len": len(answer)})
        logger.info(f"LLM tra loi: {len(answer)} ky tu")
        return answer
    except Exception as e:
        end_observation(generation, output={"error": str(e)})
        logger.error(f"Loi khi goi LLM: {e}")
        raise RuntimeError(f"Khong the ket noi den LLM. Chi tiet: {e}")


def call_llm_streaming(
    user_prompt: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
    provider: str = "openrouter",
    langfuse_parent=None,
) -> Generator[str, None, None]:
    """Goi LLM voi che do streaming."""
    llm = get_llm(provider)
    messages = build_messages(user_prompt, chat_history)
    logger.info(f"Dang streaming LLM (Provider: {provider})...")

    generation = create_generation(
        langfuse_parent,
        name="llm_streaming_call",
        model=provider,
        input_data={"user_prompt": user_prompt[:2000], "history_turns": len(chat_history or [])},
        metadata={"provider": provider, "streaming": True},
    )
    total_chars = 0

    try:
        for chunk in llm.stream(messages):
            text = _content_to_text(chunk.content)
            if text:
                total_chars += len(text)
                yield text
        end_observation(generation, output={"total_streamed_chars": total_chars})
    except Exception as e:
        end_observation(generation, output={"error": str(e), "total_streamed_chars": total_chars})
        logger.error(f"Loi khi streaming LLM: {e}")
        yield f"\n[LOI KET NOI: {e}]"


def check_llm_connection() -> bool:
    try:
        response = call_llm("Tra loi voi dung 2 tu: 'San sang'")
        return len(response.strip()) > 0
    except Exception:
        return False
