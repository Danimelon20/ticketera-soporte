import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from . import config, mailer, models
from .business_hours import is_business_day
from .chatbot import _redact
from .database import SessionLocal
from .email_analysis import LEVEL_NAMES, _now, effective_level
from .notifications import STATUS_LABELS

# Resumen diario para coordinadores y administradores.
# Privacidad: solo número, título (enmascarado), nivel, estado y responsable.
# Nunca descripciones, comentarios, teléfonos ni cédulas, y nada del contenido en los logs.

CHECK_SECONDS = 300
MAX_ITEMS = 10
OPEN_STATUSES = ("new", "in_progress")
RECIPIENT_ROLES = ("coordinator", "admin")
LEVEL_ORDER = (4, 3, 2, 1, 0, None)
NONE_TEXT = "Ninguno"


def _local(now: datetime) -> datetime:
    """now en UTC sin zona (como en la base) -> hora local de la oficina."""
    return now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(config.OFFICE_TZ))


def _level(ticket: models.Ticket) -> int | None:
    row = ticket.ai_analysis
    return effective_level(row) if row else None


def _level_text(level: int | None) -> str:
    return "Sin nivel" if level is None else f"Nivel {level} ({LEVEL_NAMES.get(level, '')})"


def _due_at(ticket: models.Ticket) -> datetime | None:
    return ticket.ai_analysis.due_at if ticket.ai_analysis else None


def _ticket_line(ticket: models.Ticket) -> str:
    owner = ticket.assigned_user.name if ticket.assigned_user else "Sin asignar"
    status = STATUS_LABELS.get(ticket.status, ticket.status)
    return f"#{ticket.id} | {_redact(ticket.title)} | {_level_text(_level(ticket))} | {status} | {owner}"


def _draft_line(draft: models.EmailDraft) -> str:
    return (
        f"Borrador #{draft.id} | {_redact(draft.title or 'Sin título')} | {_level_text(draft.ai_level)} "
        f"| Solicitante: {draft.requester.name}"
    )


def _section(title: str, lines: list[str], extra: list[str] | None = None) -> list[str]:
    out = [f"{title}: {len(lines)}"]
    out += extra or []
    if not lines:
        out.append(f"  {NONE_TEXT}")
    else:
        out += [f"  - {line}" for line in lines[:MAX_ITEMS]]
        if len(lines) > MAX_ITEMS:
            out.append(f"  - … y {len(lines) - MAX_ITEMS} más")
    return out


def build_summary(db: Session, now: datetime | None = None) -> tuple[str, str]:
    now = now or _now()
    local = _local(now)
    end_of_day = (
        datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=local.tzinfo)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )

    open_tickets = (
        db.query(models.Ticket)
        .options(joinedload(models.Ticket.ai_analysis), joinedload(models.Ticket.assigned_user))
        .filter(models.Ticket.status.in_(OPEN_STATUSES))
        .order_by(models.Ticket.id)
        .all()
    )
    critical = [t for t in open_tickets if _level(t) == 4]
    overdue = [t for t in open_tickets if _due_at(t) is not None and _due_at(t) < now]
    due_today = [t for t in open_tickets if _due_at(t) is not None and now <= _due_at(t) < end_of_day]
    unassigned = [t for t in open_tickets if t.assigned_to is None]

    pending_drafts = (
        db.query(models.EmailDraft)
        .options(joinedload(models.EmailDraft.requester))
        .filter(models.EmailDraft.status == "pending_review")
        .order_by(models.EmailDraft.id)
        .all()
    )
    waiting_contact = db.query(models.EmailDraft).filter(models.EmailDraft.status == "waiting_contact").count()

    new_tickets = (
        db.query(models.Ticket)
        .options(joinedload(models.Ticket.ai_analysis), joinedload(models.Ticket.assigned_user))
        .filter(models.Ticket.created_at >= now - timedelta(hours=24))
        .all()
    )
    new_tickets.sort(key=lambda t: (LEVEL_ORDER.index(_level(t)) if _level(t) in LEVEL_ORDER else len(LEVEL_ORDER), t.id))
    by_level = [
        f"  {_level_text(level)}: {count}"
        for level in LEVEL_ORDER
        if (count := sum(1 for t in new_tickets if _level(t) == level))
    ]

    date_text = local.strftime("%d/%m/%Y")
    lines = [
        "Buen día.",
        "",
        f"Le compartimos el resumen de la Ticketera de Soporte del {date_text}, "
        f"generado a las {local.strftime('%H:%M')}.",
        "",
    ]
    for section in (
        _section("Tickets críticos (nivel 4) abiertos", [_ticket_line(t) for t in critical]),
        _section("Tickets vencidos", [_ticket_line(t) for t in overdue]),
        _section("Tickets que vencen hoy", [_ticket_line(t) for t in due_today]),
        _section("Tickets sin asignar", [_ticket_line(t) for t in unassigned]),
        _section(
            "Borradores pendientes de revisión",
            [_draft_line(d) for d in pending_drafts],
            [f"  Esperan datos de contacto: {waiting_contact}"],
        ),
        _section("Tickets nuevos en las últimas 24 horas", [_ticket_line(t) for t in new_tickets], by_level),
    ):
        lines += section + [""]
    lines.append(f"Puede ver el detalle en {config.APP_URL}")
    return f"[Ticketera] Resumen del día {date_text}", "\n".join(lines)


def recipients(db: Session) -> list[str]:
    users = (
        db.query(models.User)
        .filter(models.User.role.in_(RECIPIENT_ROLES), models.User.is_active.is_(True))
        .order_by(models.User.id)
        .all()
    )
    return [u.email for u in users]


def send_summary(db: Session, now: datetime | None = None) -> tuple[int, int]:
    """Envía el resumen a cada destinatario. Devuelve (enviados, destinatarios)."""
    subject, body = build_summary(db, now)
    to = recipients(db)
    sent = sum(1 for address in to if mailer.send_email(address, subject, body))
    return sent, len(to)


def maybe_send_daily_summary(db: Session, now: datetime | None = None) -> bool:
    """El resumen automático: una vez por día hábil, a partir de DAILY_SUMMARY_HOUR."""
    if not config.DAILY_SUMMARY_ENABLED:
        return False
    now = now or _now()
    local = _local(now)
    if not is_business_day(local.date()) or local.hour < config.DAILY_SUMMARY_HOUR:
        return False
    if db.query(models.DailySummary).filter(models.DailySummary.day == local.date()).first():
        return False

    sent, total = send_summary(db, now)
    if total and not sent:
        # No se marca el día: se reintenta en la siguiente revisión
        logging.warning("No se pudo enviar el resumen diario; se reintentará")
        return False
    db.add(models.DailySummary(day=local.date(), sent_at=now))
    db.commit()
    logging.info("Resumen diario enviado a %s destinatarios", sent)
    return True


def last_automatic(db: Session) -> datetime | None:
    row = db.query(models.DailySummary).order_by(models.DailySummary.day.desc()).first()
    return row.sent_at.replace(tzinfo=timezone.utc) if row else None


def _run_once() -> None:
    db = SessionLocal()
    try:
        maybe_send_daily_summary(db)
    finally:
        db.close()


async def summary_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(_run_once)
        except Exception:
            logging.exception("Falló el resumen diario")
        await asyncio.sleep(CHECK_SECONDS)
