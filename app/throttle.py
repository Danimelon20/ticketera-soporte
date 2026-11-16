"""Bloqueo por intentos fallidos: se cuenta por correo, exista o no la cuenta."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import audit, models
from .security import mask_email

MAX_FAILURES = 5
LOCK_MINUTES = 15
LOCK_MESSAGE = "Demasiados intentos. Intente de nuevo en unos minutos."


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(db: Session, email: str) -> models.LoginThrottle | None:
    return db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).first()


def is_locked(db: Session, email: str) -> bool:
    row = _row(db, email)
    return row is not None and row.locked_until is not None and row.locked_until > _now()


def register_failure(db: Session, email: str, user: models.User | None = None) -> bool:
    """Suma un fallo (contraseña o código). Devuelve True si con este el correo quedó bloqueado. Hace commit."""
    now = _now()
    row = _row(db, email)
    if row is None:
        row = models.LoginThrottle(email=email, failures=0, updated_at=now)
        db.add(row)
    if row.locked_until is not None and row.locked_until <= now:
        # El bloqueo anterior terminó: se empieza a contar de nuevo
        row.locked_until = None
    row.failures += 1
    row.updated_at = now
    if user is not None:
        audit.record(db, None, "login_failed", target=user)
    locked = row.failures >= MAX_FAILURES
    if locked:
        row.failures = 0
        row.locked_until = now + timedelta(minutes=LOCK_MINUTES)
        audit.record(
            db, None, "login_locked",
            target=user,
            target_label=None if user is not None else mask_email(email),
            new=f"{LOCK_MINUTES} minutos",
        )
    db.commit()
    return locked


def reset(db: Session, email: str) -> None:
    """Inicio de sesión correcto: el contador vuelve a cero. No hace commit."""
    row = _row(db, email)
    if row is not None:
        row.failures = 0
        row.locked_until = None
