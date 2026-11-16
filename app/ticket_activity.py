"""Última actividad visible de un ticket (para el reporte de vencimiento): lo más reciente entre su último cambio y
su último comentario público. Antes vivía en el portal del solicitante, que ya no existe."""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import comments, models


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
