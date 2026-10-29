"""Quién ve qué ticket. Única fuente para el detalle, la lista, la cola, los adjuntos y los avisos.

- Coordinador: todos, completos. Con el enrutamiento encendido, solo los de su área (sin área, ninguno).
- Técnico: el que tiene asignado, completo; los que tuvo asignados antes, solo lectura; los demás, nada.
- Solicitante: los que creó.
- Técnico o coordinador que abrió un ticket interno para sí: lo ve como solicitante (OWN), sin poder cambiarlo.
- Administrador: ninguno. Solo con "Ver conversación" (con motivo, queda en el Registro de cambios), que da
  una lectura puntual del detalle y, durante una hora, los adjuntos de ese ticket.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from . import models, routing

FULL = "full"
READ = "read"
# El personal en su propio ticket: como solicitante (comenta, no cambia estado ni asignación)
OWN = "own"
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


def _area_ticket_ids(db: Session, user: models.User):
    """Tickets del área que dirige el coordinador (ninguno si no tiene área)."""
    area = routing.coordinator_area(db, user)
    area_id = area.id if area is not None else -1
    return db.query(models.TicketRouting.ticket_id).filter(models.TicketRouting.area_id == area_id)


def _staff_access(db: Session, user: models.User, ticket: models.Ticket) -> str | None:
    if user.role == "coordinator":
        if not routing.enforced(db):
            return FULL
        row = routing.routing_of(db, ticket.id)
        area = routing.coordinator_area(db, user)
        return FULL if row is not None and area is not None and row.area_id == area.id else None
    if user.role == "technician":
        if ticket.assigned_to == user.id:
            return FULL
        if _past_assignments(db, user.id).filter(models.History.ticket_id == ticket.id).first() is not None:
            return READ
    return None


def access(db: Session, user: models.User, ticket: models.Ticket) -> str | None:
    if user.role == "requester":
        return FULL if ticket.created_by == user.id else None
    if user.role in ("coordinator", "technician"):
        level = _staff_access(db, user, ticket)
        if level is None and ticket.created_by == user.id:
            return OWN
        return level
    return None


def as_requester(db: Session, user: models.User, ticket: models.Ticket) -> bool:
    """Lo ve como solicitante: sin notas internas ni datos del personal."""
    return user.role == "requester" or access(db, user, ticket) == OWN


def filter_visible(db: Session, user: models.User, query: Query) -> Query:
    """Mismo alcance que access(), para listas (el administrador no recibe ninguno)."""
    if user.role == "coordinator":
        if not routing.enforced(db):
            return query
        return query.filter(models.Ticket.id.in_(_area_ticket_ids(db, user)) | (models.Ticket.created_by == user.id))
    if user.role == "requester":
        return query.filter(models.Ticket.created_by == user.id)
    if user.role == "technician":
        return query.filter(
            (models.Ticket.assigned_to == user.id)
            | models.Ticket.id.in_(_past_assignments(db, user.id))
            | (models.Ticket.created_by == user.id)
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
