import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import chatbot, config
from ..schemas import ChatRequest, ChatResponse
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])

CHAT_ROLES = ("requester", "technician", "coordinator", "admin")
UNAVAILABLE = "El asistente no está disponible en este momento"
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 10 * 60

# Límite en memoria por usuario (se reinicia al reiniciar el servidor)
_recent: dict[int, deque] = defaultdict(deque)
_lock = threading.Lock()


def _allow(user_id: int) -> bool:
    now = time.monotonic()
    with _lock:
        stamps = _recent[user_id]
        while stamps and now - stamps[0] > RATE_WINDOW_SECONDS:
            stamps.popleft()
        if len(stamps) >= RATE_LIMIT:
            return False
        stamps.append(now)
        return True


def reset_rate_limit() -> None:
    with _lock:
        _recent.clear()


def _prepare(data: ChatRequest, db: Session, current_user) -> str:
    """Permisos, límite y prompt del sistema (lo mismo para la respuesta completa y por partes)."""
    if current_user.role not in CHAT_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para usar el asistente")
    if not config.CHATBOT_ENABLED:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    if not _allow(current_user.id):
        raise HTTPException(status_code=429, detail="Has enviado muchos mensajes. Espera unos minutos.")

    # Solo el largo: nunca el texto del mensaje ni la respuesta
    logging.info("Chat: usuario %s, %s caracteres", current_user.id, len(data.message))

    context = chatbot.build_context(db, current_user)
    return chatbot.build_system_prompt(current_user, context)


@router.post("/chat", response_model=ChatResponse)
def chat(data: ChatRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    system_prompt = _prepare(data, db, current_user)
    try:
        reply = chatbot.call_llm(system_prompt, data.history, data.message)
    except chatbot.ChatbotUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    return ChatResponse(reply=reply)


@router.post("/chat/stream")
def chat_stream(data: ChatRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """La respuesta llega en texto plano por partes, mientras el modelo la genera."""
    system_prompt = _prepare(data, db, current_user)
    chunks = chatbot.stream_llm(system_prompt, data.history, data.message)
    # El primer trozo se espera aquí: si Ollama no responde, todavía se puede devolver 503
    try:
        first = next(chunks)
    except chatbot.ChatbotUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    def body():
        yield first
        try:
            yield from chunks
        except chatbot.ChatbotUnavailable:
            yield "\n\n(El asistente no pudo terminar la respuesta.)"

    return StreamingResponse(
        body(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )
