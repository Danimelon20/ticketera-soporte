"""Portal del solicitante: avance de 4 pasos derivado de los estados reales y última actualización visible."""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import comments, models

# 1 Recibida, 2 Asignada, 3 En atención, 4 Resuelta
RECEIVED, ASSIGNED, IN_PROGRESS, RESOLVED = 1, 2, 3, 4


def progress_step(ticket: models.Ticket) -> int:
    if ticket.status in ("resolved", "closed"):
        return RESOLVED
    if ticket.status == "in_progress":
        return IN_PROGRESS
    return ASSIGNED if ticket.assigned_to is not None else RECEIVED


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None and value.tzinfo else value


def last_activity(db: Session, tickets: list[models.Ticket]) -> dict[int, datetime]:
    """Lo más reciente entre el último cambio del ticket y su último comentario público (nunca los internos)."""
    ids = [t.id for t in tickets]
    latest: dict[int, datetime] = {}
    if ids:
        query = (
            db.query(models.Comment.ticket_id, func.max(models.Comment.created_at))
            .filter(models.Comment.ticket_id.in_(ids))
        )
        latest = dict(comments.not_internal(query).group_by(models.Comment.ticket_id).all())
    result = {}
    for t in tickets:
        candidates = [d for d in (_naive(t.updated_at), _naive(latest.get(t.id))) if d is not None]
        result[t.id] = max(candidates)
    return result
