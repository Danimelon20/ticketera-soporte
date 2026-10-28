"""Similitud entre textos de tickets con embeddings de Ollama (modelo pequeño multilingüe).

El vector de cada ticket se guarda en ticket_embeddings y solo se recalcula si cambia el texto o el modelo.
Nunca registrar en logs los textos.
"""
import hashlib
import json
import math
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from . import chatbot, config, models

# Los textos comparados son cortos (~500 caracteres): con menos contexto el modelo ocupa menos RAM
EMBED_NUM_CTX = 512


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("Se acabó el tiempo de la agrupación")
    return left


def embed(texts: list[str], deadline: float) -> list[list[float]]:
    response = httpx.post(
        f"{config.LLM_BASE_URL}/api/embed",
        json={
            "model": config.EMBEDDING_MODEL,
            "input": texts,
            "keep_alive": chatbot.KEEP_ALIVE,
            "options": {"num_ctx": EMBED_NUM_CTX},
        },
        timeout=remaining(deadline),
    )
    if response.status_code != 200:
        raise RuntimeError(f"Ollama respondió {response.status_code} a los embeddings")
    data = response.json()
    chatbot.log_timing("embed", data)
    vectors = data["embeddings"]
    if len(vectors) != len(texts):
        raise RuntimeError("Ollama devolvió otra cantidad de embeddings")
    return vectors


def cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


def _hash(text: str) -> str:
    return hashlib.sha256(f"{config.EMBEDDING_MODEL}\n{text}".encode("utf-8")).hexdigest()


def score(db: Session, ticket_id: int | None, text: str, candidates: list[tuple[int, str]],
          deadline: float) -> list[tuple[int, float]]:
    """Similitud del texto nuevo con cada candidato (id, texto), de mayor a menor. Guarda los vectores nuevos."""
    items = [(ticket_id, text)] + list(candidates)
    ids = [i for i, _ in items if i is not None]
    cached = {
        row.ticket_id: row
        for row in db.query(models.TicketEmbedding).filter(models.TicketEmbedding.ticket_id.in_(ids)).all()
    } if ids else {}
    vectors: dict[int, list[float]] = {}
    missing = []
    for index, (item_id, item_text) in enumerate(items):
        row = cached.get(item_id)
        if row is not None and row.model == config.EMBEDDING_MODEL and row.text_hash == _hash(item_text):
            vectors[index] = json.loads(row.vector)
        else:
            missing.append(index)
    if missing:
        for index, vector in zip(missing, embed([items[i][1] for i in missing], deadline)):
            vectors[index] = vector
            item_id = items[index][0]
            if item_id is None:
                continue
            row = cached.get(item_id) or models.TicketEmbedding(ticket_id=item_id)
            row.model = config.EMBEDDING_MODEL
            row.text_hash = _hash(items[index][1])
            row.vector = json.dumps(vector)
            row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.add(row)
        db.commit()
    result = [(items[i][0], cosine(vectors[0], vectors[i])) for i in range(1, len(items))]
    return sorted(result, key=lambda pair: pair[1], reverse=True)
