"""Clientes con contrato: organizaciones, dominios, contactos y correos no reconocidos (Fase 2).

Con el interruptor client_domain_enforced encendido, solo abren tickets los contactos de una organización
con contrato vigente (o los empleados de ADN). Apagado, todo sigue como antes: cualquier correo registrado
abre tickets, aunque las organizaciones ya se pueden configurar y los clientes se ligan por dominio.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, domains, models, routing
from .database import SessionLocal
from .security import unusable_password_hash

NOT_CLIENT = "Este correo no corresponde a un cliente con contrato vigente. Contacte a ventas"
CONTRACT_NOT_CURRENT = ("El contrato de su organización no está vigente: no se pueden abrir tickets nuevos. "
                        "Los que ya tiene siguen en proceso. Contacte a ventas")
UNRECOGNIZED_DAYS = 30
PURGE_EVERY_SECONDS = 6 * 3600
STATUS_LABELS = {"current": "Vigente", "expired": "Vencido", "future": "Todavía no empieza",
                 "incomplete": "Contrato incompleto"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today() -> date:
    return datetime.now(ZoneInfo(config.OFFICE_TZ)).date()


def enforced(db: Session) -> bool:
    return routing.switch_on(db, routing.CLIENT_DOMAIN_ENFORCED)


# ----- Organizaciones -----

def contract_status(org: models.Organization, on: date | None = None) -> str:
    """current, expired, future o incomplete (falta el número o una fecha). La interna siempre current."""
    if org.kind == "internal":
        return "current"
    if not org.contract_number or org.contract_start is None or org.contract_end is None:
        return "incomplete"
    on = on or today()
    if on < org.contract_start:
        return "future"
    return "current" if on <= org.contract_end else "expired"


def is_current(org: models.Organization | None) -> bool:
    return org is not None and contract_status(org) == "current"


def organization_for_domain(db: Session, domain: str) -> models.Organization | None:
    row = db.query(models.OrganizationDomain).filter(models.OrganizationDomain.domain == domain).first()
    return db.get(models.Organization, row.organization_id) if row is not None else None


def profile(db: Session, user_id: int) -> models.RequesterProfile | None:
    return db.query(models.RequesterProfile).filter(models.RequesterProfile.user_id == user_id).first()


def organization_of(db: Session, user: models.User) -> models.Organization | None:
    row = profile(db, user.id)
    return row.organization if row is not None else None


def link_user(db: Session, user: models.User, org: models.Organization, activation: str | None = None,
              authorized: bool | None = None) -> models.RequesterProfile:
    """Liga al solicitante con la organización. No hace commit."""
    row = profile(db, user.id)
    if row is None:
        row = models.RequesterProfile(user_id=user.id, activation=activation or "active")
        db.add(row)
    elif activation is not None:
        row.activation = activation
    row.organization_id = org.id
    if authorized is not None:
        row.authorized_for_changes = authorized
    return row


def link_by_domain(db: Session, user: models.User) -> models.Organization | None:
    """Solicitante sin organización cuyo dominio es de un cliente: queda ligado. No hace commit."""
    if user.role != "requester" or organization_of(db, user) is not None:
        return None
    org = organization_for_domain(db, domains.of_email(user.email))
    if org is None or org.kind != "client":
        return None
    link_user(db, user, org)
    return org


def link_existing_clients(db: Session) -> int:
    """Migración: cada cliente ya registrado se liga por el dominio de su correo. No hace commit."""
    linked = 0
    for user in _requesters_without_organization(db):
        if link_by_domain(db, user) is not None:
            linked += 1
    db.flush()
    return linked


def _requesters_without_organization(db: Session) -> list[models.User]:
    with_org = db.query(models.RequesterProfile.user_id).filter(models.RequesterProfile.organization_id.isnot(None))
    return (
        db.query(models.User)
        .filter(models.User.role == "requester", models.User.id.notin_(with_org))
        .order_by(models.User.id)
        .all()
    )


def clients_without_organization(db: Session) -> list[models.User]:
    """Clientes activos sin organización (los empleados de ADN no cuentan): no abren tickets con el interruptor."""
    return [u for u in _requesters_without_organization(db)
            if u.is_active and not routing.is_internal_domain(u.email)]


def ticket_block(db: Session, user: models.User) -> str | None:
    """Motivo por el que el solicitante no puede abrir un ticket nuevo, o None. Los que ya tiene continúan."""
    if user.role != "requester" or not enforced(db):
        return None
    if routing.requester_kind(db, user) == "internal":
        return None
    org = organization_of(db, user)
    if org is None or org.kind != "client":
        return NOT_CLIENT
    return None if is_current(org) else CONTRACT_NOT_CURRENT


def signup_block(db: Session, email: str) -> str | None:
    """Registro en la página: con el interruptor, solo dominios de un cliente vigente."""
    if not enforced(db):
        return None
    org = organization_for_domain(db, domains.of_email(email))
    if org is None or org.kind != "client" or not is_current(org):
        return NOT_CLIENT
    return None


# ----- Contactos -----

def add_contact(db: Session, org: models.Organization, email: str, name: str,
                authorized: bool = False) -> tuple[models.User | None, str]:
    """Contacto de la organización: liga la cuenta que ya existe o crea una "pendiente de activar"
    (sin contraseña que funcione: la activa al registrarse con ese correo). Devuelve (cuenta, resultado):
    created, linked o el mensaje de por qué no. No hace commit."""
    email = email.strip().lower()
    user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
    if user is not None:
        if user.role != "requester":
            return None, "Ese correo es de una cuenta del personal"
        if routing.is_internal_domain(email):
            return None, "Los empleados de ADN no son contactos de un cliente"
        link_user(db, user, org, authorized=authorized)
        forget_unrecognized(db, email)
        return user, "linked"
    if routing.is_internal_domain(email):
        return None, "Los empleados de ADN no son contactos de un cliente"
    user = models.User(name=" ".join(name.split())[:100], email=email, role="requester",
                       password_hash=unusable_password_hash())
    db.add(user)
    db.flush()
    link_user(db, user, org, activation="pending", authorized=authorized)
    forget_unrecognized(db, email)
    return user, "created"


def is_pending_contact(db: Session, user: models.User | None) -> bool:
    row = profile(db, user.id) if user is not None else None
    return row is not None and row.activation == "pending"


# ----- No reconocidos -----

def record_unrecognized(db: Session, email: str, channel: str) -> None:
    """Correo completo (solo lo ve el administrador). No hace commit."""
    email = email.strip().lower()
    now = _now()
    row = db.query(models.UnrecognizedContact).filter(models.UnrecognizedContact.email == email).first()
    if row is None:
        db.add(models.UnrecognizedContact(email=email[:254], channel=channel, first_seen=now, last_seen=now))
        return
    row.channel = channel
    row.last_seen = now
    row.attempts += 1


def forget_unrecognized(db: Session, email: str) -> None:
    db.query(models.UnrecognizedContact).filter(models.UnrecognizedContact.email == email.strip().lower()).delete(
        synchronize_session=False)


def purge_unrecognized(db: Session, now: datetime | None = None) -> int:
    """Borra los que no se ligaron en 30 días. Hace commit."""
    limit = (now or _now()) - timedelta(days=UNRECOGNIZED_DAYS)
    count = (
        db.query(models.UnrecognizedContact)
        .filter(models.UnrecognizedContact.first_seen < limit)
        .delete(synchronize_session=False)
    )
    db.commit()
    return count


def _purge_now() -> None:
    db = SessionLocal()
    try:
        count = purge_unrecognized(db)
        if count:
            logging.info("Se borraron %s correos no reconocidos de más de %s días", count, UNRECOGNIZED_DAYS)
    finally:
        db.close()


async def purge_forever() -> None:
    """También corre en cada arranque (org_setup). No depende del correo."""
    while True:
        await asyncio.sleep(PURGE_EVERY_SECONDS)
        try:
            await asyncio.to_thread(_purge_now)
        except Exception:
            logging.exception("Falló el borrado de correos no reconocidos")


# ----- Lista de verificación del interruptor -----

def checklist(db: Session) -> list[dict]:
    clients = db.query(models.Organization).filter(models.Organization.kind == "client").all()
    current = sum(1 for org in clients if is_current(org))
    without = len(clients_without_organization(db))
    return [
        {"key": "current_organization", "label": "Al menos una organización con contrato vigente",
         "ok": current > 0, "detail": f"{current} vigente(s)" if current else "ninguna",
         "link": "admin/organizaciones", "focus": None},
        {"key": "clients_without_organization", "label": "Todos los clientes tienen organización",
         "ok": without == 0, "detail": f"{without} sin organización" if without else None,
         "link": "admin/no-reconocidos", "focus": None},
    ]
