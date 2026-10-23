"""Quién ve qué ticket. Única fuente para el detalle, la lista, la cola, los adjuntos y los avisos.

- Coordinador: todos, completos.
- Técnico: el que tiene asignado, completo; los que tuvo asignados antes, solo lectura; los demás, nada.
- Solicitante: los que creó.
- Administrador: ninguno. Solo con "Ver conversación" (con motivo, queda en el Registro de cambios), que da
  una lectura puntual del detalle y, durante una hora, los adjuntos de ese ticket.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from . import models

FULL = "full"
READ = "read"
CONVERSATION_ACTION = "conversation_viewed"
ADMIN_FILES_WINDOW = timedelta(hours=1)
NO_PERMISSION = "No tienes permiso para ver este ticket"
READ_ONLY = "Este ticket ya no está asignado a ti: solo puedes verlo"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _past_assignments(db: Session, user_id: int):
    """Tickets que la persona tuvo asignados alguna vez (según el historial)."""
    value = str(user_id)
    return db.query(models.History.ticket_id).filter(
        models.History.field == "assigned_to",
        or_(models.History.old_value == value, models.History.new_value == value),
    )


def access(db: Session, user: models.User, ticket: models.Ticket) -> str | None:
    if user.role == "coordinator":
        return FULL
    if user.role == "requester":
        return FULL if ticket.created_by == user.id else None
    if user.role == "technician":
        if ticket.assigned_to == user.id:
            return FULL
        if _past_assignments(db, user.id).filter(models.History.ticket_id == ticket.id).first() is not None:
            return READ
    return None


def filter_visible(db: Session, user: models.User, query: Query) -> Query:
    """Mismo alcance que access(), para listas (el administrador no recibe ninguno)."""
    if user.role == "coordinator":
        return query
    if user.role == "requester":
        return query.filter(models.Ticket.created_by == user.id)
    if user.role == "technician":
        return query.filter(
            (models.Ticket.assigned_to == user.id) | models.Ticket.id.in_(_past_assignments(db, user.id))
        )
    return query.filter(False)


def visible_user_ids(db: Session, ticket: models.Ticket, user_ids: list[int]) -> set[int]:
    """De esas personas, las que pueden ver el ticket (para avisos a seguidores)."""
    users = db.query(models.User).filter(models.User.id.in_(user_ids)).all() if user_ids else []
    return {u.id for u in users if access(db, u, ticket) is not None}


def conversation_label(ticket_id: int) -> str:
    return f"Ticket #{ticket_id}"


def admin_files_open(db: Session, admin: models.User, ticket_id: int, now: datetime | None = None) -> bool:
    """El administrador usó "Ver conversación" en ese ticket hace menos de una hora."""
    if admin.role != "admin":
        return False
    since = (now or _now()) - ADMIN_FILES_WINDOW
    return (
        db.query(models.AccountChange.id)
        .filter(
            models.AccountChange.actor_id == admin.id,
            models.AccountChange.action == CONVERSATION_ACTION,
            models.AccountChange.target_label == conversation_label(ticket_id),
            models.AccountChange.created_at >= since,
        )
        .first()
        is not None
    )
