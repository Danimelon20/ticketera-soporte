"""Registro de cambios de cuentas: quién, a qué cuenta, qué acción y cuándo.

Los valores se guardan ya enmascarados (teléfono y cédula con los últimos 4 dígitos, correo como
mask_email). Nunca se guardan contraseñas ni códigos.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models
from .security import mask_email
from .validators import mask_last4

ROLE_NAMES = {
    "requester": "Solicitante",
    "technician": "Técnico",
    "coordinator": "Coordinador",
    "admin": "Administrador",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def masked_email(value: str | None) -> str | None:
    return mask_email(value) if value else None


def masked_digits(value: str | None) -> str | None:
    return mask_last4(value)


def role_name(role: str | None) -> str | None:
    return ROLE_NAMES.get(role, role) if role else None


def agent_id(db: Session) -> int | None:
    agent = db.query(models.User.id).filter(models.User.role == "agent").first()
    return agent[0] if agent else None


def record(
    db: Session,
    actor_id: int | None,
    action: str,
    target: models.User | None = None,
    target_label: str | None = None,
    old: str | None = None,
    new: str | None = None,
) -> None:
    """Agrega una fila al registro. No hace commit."""
    db.add(models.AccountChange(
        actor_id=actor_id,
        target_user_id=target.id if target is not None else None,
        target_label=target_label,
        action=action,
        old_value=old,
        new_value=new,
        created_at=_now(),
    ))
