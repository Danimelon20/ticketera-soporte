"""Aviso de tratamiento de datos (Ley 8968, art. 5): aceptado una sola vez por versión.

Los textos están versionados en app/legal/ (la primera línea dice "<!-- version: N -->"). Una versión nueva
obliga a aceptar de nuevo. El personal, en su rol de personal, no pasa por este flujo.
"""
import os
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models, routing

LEGAL_DIR = os.path.join(os.path.dirname(__file__), "legal")
NOTICE_FILES = {"internal": "aviso_interno.md", "external": "aviso_privacidad.md"}
REQUIRED = "privacy_required"
REQUIRED_MESSAGE = "Debe aceptar el aviso de tratamiento de datos para continuar"
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
    return routing.requester_kind(db, user)


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
