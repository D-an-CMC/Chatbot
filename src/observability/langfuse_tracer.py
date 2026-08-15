import logging
from typing import Any, Dict, Optional

from config import (
    LANGFUSE_BASE_URL,
    LANGFUSE_ENABLED,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LOG_FORMAT,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

_langfuse_client = None


def _safe_call(obj: Any, method: str, **kwargs):
    if obj is None or not hasattr(obj, method):
        return None
    try:
        return getattr(obj, method)(**kwargs)
    except Exception:
        return None


def get_langfuse_client():
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    if not LANGFUSE_ENABLED:
        return None
    if not LANGFUSE_SECRET_KEY or not LANGFUSE_PUBLIC_KEY:
        logger.warning("Langfuse disabled: missing key(s).")
        return None

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            secret_key=LANGFUSE_SECRET_KEY,
            public_key=LANGFUSE_PUBLIC_KEY,
            host=LANGFUSE_BASE_URL,
        )
        logger.info("Langfuse initialized successfully.")
    except Exception as exc:
        logger.warning("Langfuse init failed: %s", exc)
        _langfuse_client = None
    return _langfuse_client


def create_trace(name: str, input_data: Any = None, metadata: Optional[Dict[str, Any]] = None):
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return client.trace(name=name, input=input_data, metadata=metadata or {})
    except Exception as exc:
        logger.debug("Langfuse create_trace failed: %s", exc)
        return None


def create_span(parent: Any, name: str, input_data: Any = None, metadata: Optional[Dict[str, Any]] = None):
    if parent is None:
        return None
    span = _safe_call(parent, "span", name=name, input=input_data, metadata=metadata or {})
    if span is not None:
        return span
    # fallback for direct client usage
    return _safe_call(parent, "create_span", name=name, input=input_data, metadata=metadata or {})


def create_generation(
    parent: Any,
    name: str,
    model: str,
    input_data: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    if parent is None:
        return None
    gen = _safe_call(
        parent,
        "generation",
        name=name,
        model=model,
        input=input_data,
        metadata=metadata or {},
    )
    if gen is not None:
        return gen
    return _safe_call(
        parent,
        "create_generation",
        name=name,
        model=model,
        input=input_data,
        metadata=metadata or {},
    )


def end_observation(obs: Any, output: Any = None, metadata: Optional[Dict[str, Any]] = None):
    if obs is None:
        return
    if _safe_call(obs, "end", output=output, metadata=metadata or {}) is not None:
        return
    _safe_call(obs, "update", output=output, metadata=metadata or {})


def flush_langfuse():
    client = get_langfuse_client()
    if client is None:
        return
    _safe_call(client, "flush")
