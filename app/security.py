import hashlib
import hmac
import secrets
from fastapi import Header, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession
from .database import get_db
from . import models


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


def generate_invite_code(role: str) -> str:
    prefix = "TEC-" if role == "technician" else "COO-"
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{prefix}{part1}-{part2}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: DBSession = Depends(get_db)
) -> models.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar")
    token = authorization[7:]
    db_session = db.query(models.Session).filter(models.Session.token == token).first()
    if not db_session:
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar")
    user = db.query(models.User).filter(models.User.id == db_session.user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar")
    return user
