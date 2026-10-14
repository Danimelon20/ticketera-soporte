import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/chat", response_model=ChatResponse)
def chat(data: ChatRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in CHAT_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para usar el asistente")
    if not config.CHATBOT_ENABLED:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    if not _allow(current_user.id):
        raise HTTPException(status_code=429, detail="Has enviado muchos mensajes. Espera unos minutos.")

    # Solo el largo: nunca el texto del mensaje ni la respuesta
    logging.info("Chat: usuario %s, %s caracteres", current_user.id, len(data.message))

    context = chatbot.build_context(db, current_user)
    system_prompt = chatbot.build_system_prompt(current_user, context)
    try:
        reply = chatbot.call_llm(system_prompt, data.history, data.message)
    except chatbot.ChatbotUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    return ChatResponse(reply=reply)
