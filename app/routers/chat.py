import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import assistant_queries, chatbot, config
from ..schemas import ChatRequest, ChatResponse
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])

CHAT_ROLES = ("technician", "coordinator", "admin")
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


@dataclass
class Prepared:
    """Qué contestar: ayuda (streaming, sin datos), texto fijo (ninguna) o datos del catálogo."""
    kind: str  # "help", "text" o "data"
    system_prompt: str | None = None
    text: str | None = None
    result: assistant_queries.Result | None = None


def _prepare(data: ChatRequest, db: Session, current_user) -> Prepared:
    """Permisos, límite y elección de la consulta (lo mismo para la respuesta completa y por partes).
    Los números se calculan aquí, antes de responder; la IA solo redacta después."""
    if current_user.role not in CHAT_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para usar el asistente")
    if not config.CHATBOT_ENABLED:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    if not _allow(current_user.id):
        raise HTTPException(status_code=429, detail="Has enviado muchos mensajes. Espera unos minutos.")

    # Solo el largo: nunca el texto del mensaje ni la respuesta
    logging.info("Chat: usuario %s, %s caracteres", current_user.id, len(data.message))

    try:
        query_id, period = chatbot.select_query(
            data.message, assistant_queries.options_for(current_user), assistant_queries.PERIODS)
    except chatbot.ChatbotUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    logging.info("Asistente: consulta elegida %s", query_id)
    if query_id == chatbot.HELP:
        context = chatbot.build_context(db, current_user)
        return Prepared("help", system_prompt=chatbot.build_system_prompt(current_user, context))
    query = assistant_queries.resolve(current_user, query_id)
    if query is None:
        return Prepared("text", text=assistant_queries.no_match_text(current_user))
    return Prepared("data", result=assistant_queries.run(db, current_user, query, period))


@router.post("/chat", response_model=ChatResponse)
def chat(data: ChatRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    prepared = _prepare(data, db, current_user)
    if prepared.kind == "text":
        return ChatResponse(reply=prepared.text)
    if prepared.kind == "data":
        return ChatResponse(reply=assistant_queries.answer(prepared.result, data.message))
    try:
        reply = chatbot.call_llm(prepared.system_prompt, data.history, data.message)
    except chatbot.ChatbotUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    return ChatResponse(reply=reply)


STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


@router.post("/chat/stream")
def chat_stream(data: ChatRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """La respuesta llega en texto plano por partes, mientras el modelo la genera. Las de datos llegan completas
    (se verifican antes de mostrarlas) y con X-Chat-Kind: data, para que la página diga «Consultando datos…»."""
    prepared = _prepare(data, db, current_user)
    if prepared.kind != "help":
        result, message, text = prepared.result, data.message, prepared.text

        def single():
            yield text if result is None else assistant_queries.answer(result, message)

        return StreamingResponse(single(), media_type="text/plain; charset=utf-8",
                                 headers={**STREAM_HEADERS, "X-Chat-Kind": prepared.kind})

    chunks = chatbot.stream_llm(prepared.system_prompt, data.history, data.message)
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
        body(), media_type="text/plain; charset=utf-8", headers={**STREAM_HEADERS, "X-Chat-Kind": "help"}
    )
