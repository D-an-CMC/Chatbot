# api.py
# FastAPI backend cho Chatbot Tra Cuu Diem Hoc Sinh
# Chay: python api.py  hoac  uvicorn api:app --reload
# http://localhost:8000/app de vao giao dien

import json
import logging
import asyncio
import jwt
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Depends
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
from src.llm.response_builder import format_for_display, strip_think_tags
from src.observability.langfuse_tracer import flush_langfuse, get_langfuse_client

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="Cau hoi cua nguoi dung")
    provider: str = Field(
        "gemini",
        description="LLM provider",
    )

class ChatResponse(BaseModel):
    answer: str = Field(..., description="Cau tra loi day du (bao gom citations)")
    citations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    has_data: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

class StatusResponse(BaseModel):
    status: str
    engine_ready: bool
    index_stats: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str

class MessageResponse(BaseModel):
    message: str
    success: bool = True

class AiChatRequest(BaseModel):
    question: str
    conversationId: Optional[int] = None
    provider: str = "gemini"

class AiToolStepData(BaseModel):
    columns: Optional[List[str]] = None
    rows: Optional[List[List[Any]]] = None
    rowCount: Optional[int] = None
    limited: Optional[int] = None
    sql: Optional[str] = None
    error: Optional[str] = None
    subtables: Optional[List[Dict[str, Any]]] = None

class AiToolStep(BaseModel):
    tool: str
    summary: str
    data: Optional[AiToolStepData] = None

class AiCitation(BaseModel):
    source_file: str
    title: str
    page_number: Optional[int] = None
    chunk_index: Optional[int] = None

class LoginRequest(BaseModel):
    email: str
    password: str

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
    allow_origins=["*"], # Cho phep Next.js de dang connect
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: Optional[ChatbotEngine] = None

def get_engine() -> ChatbotEngine:
    global _engine
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine chua san sang. Dang khoi tao...")
    if not _engine.is_ready():
        raise HTTPException(status_code=503, detail="Chua co du lieu diem. Kiem tra Supabase hoac dat file Excel vao thu muc data/.")
    return _engine

async def get_current_user(request: Request) -> str:
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.split(" ")[1]
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            return payload.get("sub", "anonymous")
        except Exception:
            pass
    return "anonymous"

@app.on_event("startup")
async def startup_event():
    global _engine
    logger.info("Dang khoi tao ChatbotEngine...")
    _engine = ChatbotEngine()
    _engine.initialize()
    if _engine.is_ready():
        logger.info("Engine da san sang!")
    else:
        logger.warning("Engine khoi tao nhung chua co du lieu diem.")
    get_langfuse_client()

@app.on_event("shutdown")
async def shutdown_event():
    flush_langfuse()

# ---------------------------------------------------------------------------
# Original Chatbot Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_model=MessageResponse, tags=["Health"])
async def health_check():
    return MessageResponse(message=f"{APP_ICON} {APP_TITLE} API dang hoat dong!", success=True)

@app.get("/status", response_model=StatusResponse, tags=["System"])
async def system_status():
    global _engine
    engine_ready = _engine is not None and _engine.is_ready()
    index_stats = _engine.get_index_stats() if _engine is not None and _engine.is_ready() else {}
    return StatusResponse(
        status="San sang" if engine_ready else "Chua san sang",
        engine_ready=engine_ready,
        index_stats=index_stats,
        timestamp=datetime.now().isoformat(),
    )

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    engine = get_engine()
    try:
        response = engine.chat(request.question, provider=request.provider)
        return ChatResponse(
            answer=format_for_display(response),
            citations=response.citations,
            warnings=response.warnings,
            has_data=response.metadata.get("has_data", False),
            metadata=response.metadata,
        )
    except Exception as e:
        logger.error(f"Loi chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Loi xu ly cau hoi: {e}")

@app.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest):
    engine = get_engine()
    try:
        stream_gen, lookup = engine.chat_streaming(request.question, provider=request.provider)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    async def event_generator():
        full_text = ""
        try:
            for token in stream_gen:
                full_text += token
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)

            full_text = strip_think_tags(full_text)
            response = engine.finalize_streaming_response(full_text=full_text, lookup=lookup, question=request.question)
            done_payload = {
                "done": True,
                "full_answer": format_for_display(response),
                "citations": response.citations,
                "warnings": response.warnings,
                "metadata": response.metadata,
            }
            yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/session/clear", response_model=MessageResponse, tags=["Session"])
async def clear_session():
    engine = get_engine()
    engine.clear_session()
    return MessageResponse(message="Da xoa session thanh cong.")

# ---------------------------------------------------------------------------
# Next.js AI Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/ai/chat/stream", tags=["NextJS AI"])
async def ai_chat_stream_nextjs(request: AiChatRequest, user_id: str = Depends(get_current_user)):
    engine = get_engine()
    conv_id = request.conversationId
    if not conv_id:
        title = request.question[:50] + "..." if len(request.question) > 50 else request.question
        conv_id = engine.memory.create_ai_conversation(user_id=user_id, title=title)
    
    msgs = engine.memory.get_ai_messages(conv_id)
    session_id = f"nextjs_{conv_id}"
    engine.memory.clear_session(session_id)
    for m in msgs:
        engine.memory.short_term.add_turn(session_id, m["role"], m["content"])

    try:
        stream_gen, lookup = engine.chat_streaming(request.question, provider=request.provider, session_user=None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    async def event_generator():
        yield f"event: thought\ndata: {json.dumps({'summary': 'Đang phân tích câu hỏi...'})}\n\n"
        await asyncio.sleep(0.1)

        tools_used = []
        if lookup.has_data:
            tool_data = AiToolStepData(columns=["Kết quả tra cứu"], rows=[["Đã tìm thấy dữ liệu"]])
            tool_step = AiToolStep(tool="database_lookup", summary="Tra cứu thông tin", data=tool_data)
            tools_used.append(tool_step.dict())
            yield f"event: tool\ndata: {json.dumps(tool_step.dict())}\n\n"
            await asyncio.sleep(0.1)

        full_text = ""
        for token in stream_gen:
            full_text += token
            await asyncio.sleep(0)
        
        full_text = strip_think_tags(full_text)
        cites = [AiCitation(source_file=c, title=c).dict() for c in lookup.citations]

        engine.memory.add_ai_message(conv_id, "user", request.question)
        engine.memory.add_ai_message(conv_id, "assistant", full_text, tools_used=tools_used, citations=cites)

        done_payload = {
            "answer": full_text,
            "steps": tools_used,
            "citations": cites,
            "warnings": [],
            "conversationId": conv_id
        }
        yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/ai/chat", tags=["NextJS AI"])
async def ai_chat_sync_nextjs(request: AiChatRequest, user_id: str = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Vui lòng dùng stream")

@app.get("/api/ai/conversations", tags=["NextJS AI"])
async def get_conversations(user_id: str = Depends(get_current_user)):
    engine = get_engine()
    convs = engine.memory.get_ai_conversations(user_id)
    return {"success": True, "data": convs}

@app.get("/api/ai/conversations/{conv_id}", tags=["NextJS AI"])
async def get_conversation_messages(conv_id: int, user_id: str = Depends(get_current_user)):
    engine = get_engine()
    msgs = engine.memory.get_ai_messages(conv_id)
    return {"success": True, "data": {"conversationId": conv_id, "messages": msgs}}

@app.delete("/api/ai/conversations/{conv_id}", tags=["NextJS AI"])
async def delete_conversation(conv_id: int, user_id: str = Depends(get_current_user)):
    engine = get_engine()
    engine.memory.delete_ai_conversation(conv_id)
    return {"success": True}



# ---------------------------------------------------------------------------
# Frontend static files
# ---------------------------------------------------------------------------

_frontend_dir = Path(__file__).parent / "frontend"
if _frontend_dir.exists():
    @app.get("/app", include_in_schema=False)
    async def redirect_to_app():
        return RedirectResponse(url="/app/")
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    # Dùng port 3001 thay vì API_PORT để không làm gián đoạn cấu hình hiện tại của Next.js Client
    print(f"\n  Frontend: http://localhost:3001/app")
    print(f"  API Docs: http://localhost:3001/docs\n")
    uvicorn.run(
        "api:app",
        host=API_HOST,
        port=3001,
        reload=False,
        log_level="info",
    )
