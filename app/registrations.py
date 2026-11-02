"""Tickets creados por el personal (técnico o coordinador) a nombre de un cliente que llamó o escribió.

Una sola lógica para los dos roles:
- El cliente se encuentra solo por su correo exacto: nunca listados ni búsquedas parciales (Ley 8968).
- Por teléfono hay que indicar cómo se verificó la identidad. Los últimos 4 dígitos de la cédula los compara
  el servidor (la cédula no sale al navegador). Cada fallo queda en el Registro de cambios y, con
  MAX_FAILURES fallos para el mismo cliente en FAILURE_WINDOW (sumando a todo el personal), esa vía se
  bloquea: queda el correo registrado.
"""
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import audit, config, models

STAFF_ROLES = ("technician", "coordinator")
CHANNELS = ("email", "phone")
IDENTITY_CHECKS = ("email", "id_last4")
CHANNEL_LABELS = {"email": "correo", "phone": "teléfono"}
FAILED_ACTION = "identity_check_failed"
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
OPEN_TICKETS_SHOWN = 6

NOT_REGISTERED = "El cliente debe registrarse primero en la página"
NO_PERMISSION = "No tiene permiso para crear tickets a nombre de un cliente"
METHOD_REQUIRED = "Indique cómo verificó la identidad del cliente"
LAST4_FORMAT = "Escriba los últimos 4 dígitos de la cédula"
LAST4_MISMATCH = "Los dígitos no coinciden con la cédula registrada"
NO_ID_NUMBER = "El cliente no tiene cédula registrada: use el correo registrado del cliente"
BLOCKED = "Verificación bloqueada: use el correo registrado del cliente"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def register_url() -> str:
    return f"{config.APP_URL.rstrip('/')}/#crear-cuenta"


def not_registered_detail() -> str:
    return f"{NOT_REGISTERED}. Dirección para registrarse: {register_url()}"


def require_staff(user: models.User) -> None:
    if user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail=NO_PERMISSION)


def find_client(db: Session, email: str) -> models.User | None:
    """Solicitante activo con ese correo exacto (sin importar mayúsculas ni espacios a los lados)."""
    value = (email or "").strip().lower()
    if not value:
        return None
    return (
        db.query(models.User)
        .filter(
            func.lower(models.User.email) == value,
            models.User.role == "requester",
            models.User.is_active.is_(True),
        )
        .first()
    )


def get_client(db: Session, email: str) -> models.User:
    client = find_client(db, email)
    if client is None:
        raise HTTPException(status_code=404, detail=not_registered_detail())
    return client


def open_tickets(db: Session, client: models.User) -> list[models.Ticket]:
    """Tickets abiertos del cliente, para no duplicar casos (solo para el coordinador, que ya ve todos)."""
    return (
        db.query(models.Ticket)
        .filter(models.Ticket.created_by == client.id, models.Ticket.status.in_(("new", "in_progress")))
        .order_by(models.Ticket.created_at.desc())
        .limit(OPEN_TICKETS_SHOWN)
        .all()
    )


def record_creation(db: Session, ticket: models.Ticket, actor: models.User, channel: str,
                    method: str | None) -> None:
    """Fila de registro e historial "Creado por X en nombre de Y vía Z" (new_value: el canal). No hace commit."""
    db.add(models.TicketRegistration(
        ticket_id=ticket.id, registered_by=actor.id, channel=channel, identity_check=method, created_at=_now(),
    ))
    db.add(models.History(
        ticket_id=ticket.id, field="created_on_behalf", old_value=None, new_value=channel, changed_by=actor.id,
    ))


def registration_of(db: Session, ticket_id: int) -> models.TicketRegistration | None:
    return db.query(models.TicketRegistration).filter(models.TicketRegistration.ticket_id == ticket_id).first()


def recent_failures(db: Session, client: models.User, now: datetime | None = None) -> int:
    since = (now or _now()) - FAILURE_WINDOW
    return (
        db.query(func.count(models.AccountChange.id))
        .filter(
            models.AccountChange.action == FAILED_ACTION,
            models.AccountChange.target_user_id == client.id,
            models.AccountChange.created_at >= since,
        )
        .scalar()
    )


def check_identity(db: Session, actor: models.User, client: models.User, channel: str,
                   method: str | None, last4: str | None) -> str | None:
    """Devuelve el método que queda guardado (None por correo). Un fallo hace commit para que cuente."""
    if channel != "phone":
        return None
    if method not in IDENTITY_CHECKS:
        raise HTTPException(status_code=422, detail=METHOD_REQUIRED)
    if method == "email":
        # El cliente dictó su correo registrado: es el que se buscó
        return method
    if recent_failures(db, client) >= MAX_FAILURES:
        raise HTTPException(status_code=429, detail=BLOCKED)
    if last4 is None or len(last4) != 4 or not last4.isdigit():
        raise HTTPException(status_code=422, detail=LAST4_FORMAT)
    digits = "".join(c for c in (client.id_number or "") if c.isdigit())
    if len(digits) < 4:
        raise HTTPException(status_code=400, detail=NO_ID_NUMBER)
    if hmac.compare_digest(digits[-4:], last4):
        return method
    # Persona, cliente y hora; nunca los dígitos
    audit.record(db, actor.id, FAILED_ACTION, target=client, new="últimos 4 de la cédula")
    db.commit()
    if recent_failures(db, client) >= MAX_FAILURES:
        raise HTTPException(status_code=429, detail=BLOCKED)
    raise HTTPException(status_code=400, detail=LAST4_MISMATCH)
