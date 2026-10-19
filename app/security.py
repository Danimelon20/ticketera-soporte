import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import Header, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession
from .database import get_db
from . import config, models

SESSION_EXPIRED = "Su sesión venció. Inicie sesión de nuevo."
LOGIN_REQUIRED = "Inicia sesión para continuar"
# Cada cuánto se anota el último uso de una sesión (no en cada petición)
ACTIVITY_SECONDS = 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}${pwd_hash.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(pwd_hash, expected_hash)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """La tabla sessions guarda este hash; el token en claro solo lo tiene el navegador."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_min(role: str) -> int:
    return 8 if role == "requester" else 12


def generate_invite_code(role: str) -> str:
    prefix = "TEC-" if role == "technician" else "COO-"
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{prefix}{part1}-{part2}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def generate_login_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


PASSWORD_LENGTH = 20
_PASSWORD_GROUPS = ("ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnopqrstuvwxyz", "23456789", "!#$%&*+-=?@_")


def generate_password() -> str:
    """Contraseña aleatoria fuerte con al menos una letra mayúscula, minúscula, dígito y símbolo."""
    alphabet = "".join(_PASSWORD_GROUPS)
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))
        if all(any(c in group for c in password) for group in _PASSWORD_GROUPS):
            return password


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def get_current_session(
    authorization: str | None = Header(default=None),
    db: DBSession = Depends(get_db)
) -> models.Session:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED)
    db_session = db.query(models.Session).filter(models.Session.token == hash_token(authorization[7:])).first()
    if not db_session:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED)

    now = _now()
    activity = db.query(models.SessionActivity).filter(models.SessionActivity.session_id == db_session.id).first()
    created_at = _naive(db_session.created_at)
    last_used = activity.last_used_at if activity else created_at
    if (
        now - created_at > timedelta(hours=config.SESSION_MAX_HOURS)
        or now - last_used > timedelta(minutes=config.SESSION_IDLE_MINUTES)
    ):
        db.query(models.SessionActivity).filter(models.SessionActivity.session_id == db_session.id).delete()
        db.delete(db_session)
        db.commit()
        raise HTTPException(status_code=401, detail=SESSION_EXPIRED)

    if activity is None:
        db.add(models.SessionActivity(session_id=db_session.id, last_used_at=now))
        db.commit()
    elif (now - activity.last_used_at).total_seconds() >= ACTIVITY_SECONDS:
        activity.last_used_at = now
        db.commit()
    return db_session


def get_current_user(
    db_session: models.Session = Depends(get_current_session),
    db: DBSession = Depends(get_db)
) -> models.User:
    user = db.query(models.User).filter(models.User.id == db_session.user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED)
    return user
