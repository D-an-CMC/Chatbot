# api.py
# FastAPI backend cho Chatbot Tuyen Sinh CMC University
# Chay: python api.py  hoac  uvicorn api:app --reload
# http://localhost:8000/app để vào giao diện

import json
import logging
import asyncio
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import (
    APP_TITLE, APP_DESCRIPTION, APP_ICON,
    API_HOST, API_PORT, API_CORS_ORIGINS,
    LOG_FORMAT, LOG_LEVEL,
)
from src.engine.chatbot import ChatbotEngine
from src.llm.response_builder import format_for_display
from src.observability.langfuse_tracer import flush_langfuse, get_langfuse_client
from src.rag.ingest import get_ingest_status, run_ingest

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="Cau hoi cua nguoi dung")
    provider: str = Field(
        "openrouter",
        description="LLM provider: openrouter, groq, gemini, gemini_2_5_flash, gemini_2_0_flash, gemini_1_5_flash, gemini_1_5_flash_8b",
    )


class CitationItem(BaseModel):
    index: int
    source_file: str
    page_number: Optional[int] = None
    admission_cycle: Optional[str] = None
    document_type: Optional[str] = None
    confidence: float = 1.0


class WebCitationItem(BaseModel):
    index: int
    title: str
    url: str
    domain: str
    is_official: bool = False


class ChatResponse(BaseModel):
    answer: str = Field(..., description="Cau tra loi day du (bao gom citations)")
    rag_citations: List[CitationItem] = Field(default_factory=list)
    web_citations: List[WebCitationItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    has_low_confidence: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StatusResponse(BaseModel):
    status: str
    engine_ready: bool
    index_stats: Dict[str, Any] = Field(default_factory=dict)
    ingest_status: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class ProfileResponse(BaseModel):
    summary: str
    raw: Dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    force: bool = Field(False, description="Force rebuild toan bo index")


class MessageResponse(BaseModel):
    message: str
    success: bool = True


# ---------------------------------------------------------------------------
# App & Engine
# ---------------------------------------------------------------------------

app = FastAPI(
    title=f"{APP_ICON} {APP_TITLE} API",
    description=f"{APP_DESCRIPTION} — RESTful API backend.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Singleton engine
_engine: Optional[ChatbotEngine] = None
_ingest_running = False


def get_engine() -> ChatbotEngine:
    global _engine
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine chua san sang. Dang khoi tao...")
    if not _engine.is_ready():
        raise HTTPException(status_code=503, detail="Index chua duoc load. Chay `python ingest.py` truoc.")
    return _engine


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _engine
    logger.info("Dang khoi tao ChatbotEngine...")
    _engine = ChatbotEngine()
    _engine.initialize()
    if _engine.is_ready():
        logger.info("Engine da san sang!")
    else:
        logger.warning("Engine khoi tao nhung index chua co. Chay `python ingest.py`.")
    get_langfuse_client()


@app.on_event("shutdown")
async def shutdown_event():
    flush_langfuse()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_model=MessageResponse, tags=["Health"])
async def health_check():
    """Health check — kiem tra server con song."""
    return MessageResponse(
        message=f"{APP_ICON} {APP_TITLE} API dang hoat dong!",
        success=True,
    )


@app.get("/status", response_model=StatusResponse, tags=["System"])
async def system_status():
    """Trang thai tong quan cua he thong."""
    global _engine
    engine_ready = _engine is not None and _engine.is_ready()
    index_stats = _engine.get_index_stats() if _engine is not None and _engine.is_ready() else {}
    ingest = get_ingest_status()

    return StatusResponse(
        status="San sang" if engine_ready else "Chua san sang",
        engine_ready=engine_ready,
        index_stats=index_stats,
        ingest_status=ingest,
        timestamp=datetime.now().isoformat(),
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    """Chat dong bo — gui cau hoi, nhan toan bo cau tra loi."""
    engine = get_engine()

    try:
        response = engine.chat(request.question, provider=request.provider)
        citations = [
            CitationItem(
                index=c.index,
                source_file=c.source_file,
                page_number=c.page_number,
                admission_cycle=c.admission_cycle,
                document_type=c.document_type,
                confidence=c.confidence,
            )
            for c in response.rag_citations
        ]
        web_citations = [
            WebCitationItem(
                index=c.index,
                title=c.title,
                url=c.url,
                domain=c.domain,
                is_official=c.is_official,
            )
            for c in response.web_citations
        ]
        return ChatResponse(
            answer=format_for_display(response),
            rag_citations=citations,
            web_citations=web_citations,
            warnings=response.warnings,
            has_low_confidence=response.has_low_confidence,
            metadata=response.metadata,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Loi chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Loi xu ly cau hoi: {e}")


@app.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest):
    """Chat streaming — tra ve tung token qua SSE (Server-Sent Events).

    Moi event co dang:
    - `data: {"token": "..."}` — 1 token moi
    - `data: {"done": true, "citations": [...], "warnings": [...]}` — ket thuc
    """
    engine = get_engine()

    try:
        stream_gen, retrieved_chunks = engine.chat_streaming(request.question, provider=request.provider)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    async def event_generator():
        full_text = ""
        try:
            for token in stream_gen:
                full_text += token
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)  # yield control to event loop

            # Finalize response
            response = engine.finalize_streaming_response(
                full_text=full_text,
                retrieved_chunks=retrieved_chunks,
                question=request.question,
            )
            citations = [
                {
                    "index": c.index,
                    "source_file": c.source_file,
                    "page_number": c.page_number,
                    "admission_cycle": c.admission_cycle,
                    "document_type": c.document_type,
                    "confidence": c.confidence,
                }
                for c in response.rag_citations
            ]
            web_citations = [
                {
                    "index": c.index,
                    "title": c.title,
                    "url": c.url,
                    "domain": c.domain,
                    "is_official": c.is_official,
                }
                for c in response.web_citations
            ]
            done_payload = {
                "done": True,
                "full_answer": format_for_display(response),
                "rag_citations": citations,
                "web_citations": web_citations,
                "warnings": response.warnings,
                "metadata": response.metadata,
            }
            yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"Loi streaming: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat/stream/image", tags=["Chat"])
async def chat_stream_image(
    image: UploadFile = File(..., description="Image file (jpg/png/webp)"),
    question: str = Form("", description="Optional question about the image"),
    provider: str = Form("openrouter", description="LLM provider"),
):
    """Upload image + OCR + chat streaming.

    Accepts multipart/form-data with an image file.
    Runs OCR, then streams the LLM answer via SSE.
    """
    engine = get_engine()

    # Save uploaded file to temp dir
    _upload_dir = Path(__file__).parent / "_uploads"
    _upload_dir.mkdir(exist_ok=True)
    suffix = Path(image.filename or "img.png").suffix or ".png"
    tmp_path = _upload_dir / f"ocr_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Loi luu file: {e}")

    try:
        ocr_result, stream_gen = engine.chat_with_image_streaming(
            image_path=str(tmp_path),
            question=question or "",
            provider=provider
        )
    except RuntimeError as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))

    async def event_generator():
        full_text = ""
        try:
            # Send OCR result first so frontend can show it
            ocr_event = {
                "ocr_done": True,
                "ocr_result": ocr_result,
            }
            yield f"data: {json.dumps(ocr_event, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)

            for token in stream_gen:
                full_text += token
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)

            done_payload = {
                "done": True,
                "full_answer": full_text,
                "citations": [],
                "warnings": [],
                "metadata": {"ocr_result": ocr_result},
            }
            yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"Loi streaming (image): {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/session/clear", response_model=MessageResponse, tags=["Session"])
async def clear_session():
    """Xoa session hien tai (xoa short-term memory)."""
    engine = get_engine()
    engine.clear_session()
    return MessageResponse(message="Da xoa session thanh cong.")


@app.get("/profile", response_model=ProfileResponse, tags=["Profile"])
async def get_profile():
    """Xem ho so nguoi dung (tu dong nhan dien tu hoi thoai)."""
    engine = get_engine()
    from dataclasses import asdict
    return ProfileResponse(
        summary=engine.get_profile_summary(),
        raw=asdict(engine.memory.profile.profile),
    )


@app.delete("/profile", response_model=MessageResponse, tags=["Profile"])
async def reset_profile():
    """Reset ho so nguoi dung."""
    engine = get_engine()
    engine.reset_profile()
    return MessageResponse(message="Da reset ho so nguoi dung.")


@app.get("/ingest/status", tags=["Ingest"])
async def ingest_status():
    """Xem trang thai index hien tai."""
    return get_ingest_status()


@app.post("/ingest", response_model=MessageResponse, tags=["Ingest"])
async def trigger_ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    """Trigger ingest tai lieu moi (chay background)."""
    global _ingest_running, _engine

    if _ingest_running:
        raise HTTPException(status_code=409, detail="Ingest dang chay. Vui long doi.")

    def _run_ingest():
        global _ingest_running, _engine
        _ingest_running = True
        try:
            success = run_ingest(force_rebuild=request.force)
            if success and _engine is not None:
                _engine.reload_index()
                logger.info("Da reload index sau ingest.")
        except Exception as e:
            logger.error(f"Ingest that bai: {e}")
        finally:
            _ingest_running = False

    background_tasks.add_task(_run_ingest)

    mode = "Force rebuild" if request.force else "Incremental"
    return MessageResponse(message=f"Da bat dau ingest ({mode}). Theo doi tai /ingest/status.")


@app.get("/tools", tags=["System"])
async def list_tools():
    """Danh sach tools ma Agent ho tro."""
    engine = get_engine()
    return engine.get_available_tools()


# ---------------------------------------------------------------------------
# Frontend static files
# ---------------------------------------------------------------------------

_frontend_dir = Path(__file__).parent / "frontend"
if _frontend_dir.exists():
    @app.get("/app", include_in_schema=False)
    async def redirect_to_app():
        return RedirectResponse(url="/app/")

    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print(f"\n  Frontend: http://localhost:{API_PORT}/app")
    print(f"  API Docs: http://localhost:{API_PORT}/docs\n")
    uvicorn.run(
        "api:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info",
    )
