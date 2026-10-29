"""Aviso de tratamiento de datos (Ley 8968, art. 5): aceptado una sola vez por versión.

Los textos están versionados en app/legal/ (la primera línea dice "<!-- version: N -->"). Una versión nueva
obliga a aceptar de nuevo. El personal, en su rol de personal, no pasa por este flujo.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import clients, config, models, routing
from .security import hash_token, mask_email, new_token

LEGAL_DIR = os.path.join(os.path.dirname(__file__), "legal")
NOTICE_FILES = {"internal": "aviso_interno.md", "external": "aviso_privacidad.md"}
REQUIRED = "privacy_required"
REQUIRED_MESSAGE = "Debe aceptar el aviso de tratamiento de datos para continuar"
LINK_DAYS = 30
LINK_INVALID = "El enlace no es válido, ya se usó o venció"
_VERSION_RE = re.compile(r"<!--\s*version:\s*([\w.\-]+)\s*-->")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def read_notice(kind: str) -> tuple[str, str] | None:
    """(versión, texto) del aviso, o None si todavía no existe ese archivo."""
    name = NOTICE_FILES.get(kind)
    path = os.path.join(LEGAL_DIR, name) if name else None
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = _VERSION_RE.search(text.splitlines()[0] if text else "")
    version = match.group(1) if match else "1"
    return version, _VERSION_RE.sub("", text, count=1).lstrip()


def notice_kind(db: Session, user: models.User) -> str | None:
    """Aviso que le toca: solo a los solicitantes (interno o externo)."""
    if user.role != "requester":
        return None
    kind = routing.requester_kind(db, user)
    # El de clientes solo con el interruptor de clientes con contrato (apagado, todo sigue como antes)
    if kind == "external" and not clients.enforced(db):
        return None
    return kind


def pending(db: Session, user: models.User) -> dict | None:
    """Aviso que falta aceptar (kind, version, text) o None."""
    kind = notice_kind(db, user)
    notice = read_notice(kind) if kind else None
    if notice is None:
        return None
    version, text = notice
    accepted = db.query(models.PrivacyAcceptance.id).filter(
        models.PrivacyAcceptance.user_id == user.id,
        models.PrivacyAcceptance.notice == kind,
        models.PrivacyAcceptance.version == version,
    ).first()
    return None if accepted else {"kind": kind, "version": version, "text": text}


def check_accepted(db: Session, user: models.User) -> None:
    """403 privacy_required mientras falte aceptar (validado en el servidor, no solo en la página)."""
    if pending(db, user) is not None:
        raise HTTPException(status_code=403, detail=REQUIRED_MESSAGE, headers={"X-Error-Code": REQUIRED})


def accept(db: Session, user: models.User, version: str, ip: str | None, via: str = "login") -> bool:
    """Guarda la aceptación de la versión vigente. False si la versión no es la que se mostró. No hace commit."""
    current = pending(db, user)
    if current is None:
        return True
    if version != current["version"]:
        return False
    db.add(models.PrivacyAcceptance(
        user_id=user.id, notice=current["kind"], version=version, accepted_at=_now(), ip=(ip or "")[:45] or None, via=via,
    ))
    return True


# ----- Enlace "Acepto" por correo (contactos que todavía no entran a la página) -----

def email_link(db: Session, user: models.User) -> str | None:
    """Enlace de un solo uso si al contacto le falta aceptar; reemplaza los anteriores. No hace commit."""
    current = pending(db, user)
    if current is None:
        return None
    now = _now()
    db.query(models.PrivacyEmailToken).filter(
        models.PrivacyEmailToken.user_id == user.id, models.PrivacyEmailToken.used_at.is_(None),
    ).update({models.PrivacyEmailToken.used_at: now}, synchronize_session=False)
    token = new_token()
    db.add(models.PrivacyEmailToken(user_id=user.id, token_hash=hash_token(token), version=current["version"],
                                    created_at=now, expires_at=now + timedelta(days=LINK_DAYS)))
    link = f"{config.APP_URL.rstrip('/')}/#aviso/{token}"
    # Solo sin correo real, para probar en desarrollo (como los códigos)
    if not config.EMAIL_ENABLED:
        logging.warning("[SOLO DESARROLLO] Enlace del aviso para %s: %s", mask_email(user.email), link)
    return link


def _token_row(db: Session, token: str) -> models.PrivacyEmailToken | None:
    row = db.query(models.PrivacyEmailToken).filter(
        models.PrivacyEmailToken.token_hash == hash_token(token or "")).first()
    if row is None or row.used_at is not None or row.expires_at <= _now():
        return None
    return row


def notice_for_token(db: Session, token: str) -> dict | None:
    """El aviso que muestra el enlace, o None si el enlace no sirve."""
    row = _token_row(db, token)
    user = db.get(models.User, row.user_id) if row is not None else None
    if user is None or not user.is_active:
        return None
    current = pending(db, user)
    if current is not None:
        return current
    # Ya lo aceptó (por ejemplo, al entrar a la página): el enlace solo lo confirma
    return {"kind": notice_kind(db, user) or "external", "version": row.version, "text": "", "accepted": True}


def accept_by_token(db: Session, token: str, version: str, ip: str | None) -> str | None:
    """Acepta con el enlace (un solo uso). Devuelve el error o None. No hace commit."""
    row = _token_row(db, token)
    user = db.get(models.User, row.user_id) if row is not None else None
    if user is None or not user.is_active:
        return LINK_INVALID
    if not accept(db, user, version, ip, via="email_link"):
        return "El aviso cambió: léalo de nuevo"
    row.used_at = _now()
    return None
