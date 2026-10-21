import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from . import availability, config, mailer, models
from .business_hours import business_hours_between
from .chatbot import _redact
from .daily_summary import (
    MAX_ITEMS, OPEN_STATUSES, _draft_line, _due_at, _level, _level_text, _local, _section, _ticket_line, recipients,
)
from .database import SessionLocal
from .email_analysis import _now
from .notifications import STATUS_LABELS, display_name

# Alertas por correo, una sola vez por ticket o borrador y tipo.
# Privacidad: solo número, título (enmascarado), nivel, estado, responsable y fecha límite.
# Nunca descripciones, comentarios, teléfonos ni cédulas, y en los logs solo tipo, id y conteos.

CRITICAL = "critical"
DUE_SOON = "due_soon"
OVERDUE = "overdue"
ON_TIME = "on_time"
RETURN_DATE = "return_date"
SUBJECT_LABELS = {CRITICAL: "CRÍTICO", DUE_SOON: "Por vencer", OVERDUE: "Vencido"}
GROUP_LABELS = {CRITICAL: "CRÍTICO", DUE_SOON: "Por vencer", OVERDUE: "Vencidos"}
INTROS = {
    CRITICAL: "Le informamos que hay una solicitud de nivel crítico que requiere atención inmediata.",
    DUE_SOON: "Le informamos que este ticket está próximo a vencer su tiempo meta.",
    OVERDUE: "Le informamos que este ticket ya superó su tiempo meta.",
}
GROUP_INTROS = {
    CRITICAL: "Le informamos que hay solicitudes de nivel crítico que requieren atención inmediata.",
    DUE_SOON: "Le informamos que estos tickets están próximos a vencer su tiempo meta.",
    OVERDUE: "Le informamos que estos tickets ya superaron su tiempo meta.",
}
SECTION_TITLES = {CRITICAL: "Críticos", DUE_SOON: "Por vencer", OVERDUE: "Vencidos"}


@dataclass
class _Alert:
    kind: str
    ticket: models.Ticket | None = None
    draft: models.EmailDraft | None = None
    to: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"Ticket #{self.ticket.id}" if self.ticket else f"Borrador #{self.draft.id}"

    @property
    def title(self) -> str:
        return _redact(self.ticket.title if self.ticket else (self.draft.title or "Sin título"))


def _active_assignee(db: Session, ticket: models.Ticket) -> str | None:
    """Correo del responsable, salvo que esté desactivado o no esté Disponible (vacaciones o ausente)."""
    user = ticket.assigned_user
    if user is None or not user.is_active or not availability.is_available(db, user):
        return None
    return user.email


def _deadline(ticket: models.Ticket) -> str:
    due = _due_at(ticket)
    return _local(due).strftime("%d/%m/%Y %H:%M") if due else "Sin fecha límite"


def _single_email(alert: _Alert) -> tuple[str, str]:
    subject = f"[Ticketera] {SUBJECT_LABELS[alert.kind]}: {alert.name} {alert.title}"
    lines = ["Buen día.", "", INTROS[alert.kind], "", f"Número: {alert.name}", f"Título: {alert.title}"]
    if alert.ticket:
        ticket = alert.ticket
        owner = ticket.assigned_user.name if ticket.assigned_user else "Sin asignar"
        lines += [
            f"Nivel: {_level_text(_level(ticket))}",
            f"Estado: {STATUS_LABELS.get(ticket.status, ticket.status)}",
            f"Responsable: {owner}",
            f"Fecha límite: {_deadline(ticket)}",
        ]
    else:
        lines += [
            f"Nivel: {_level_text(alert.draft.ai_level)}",
            "Estado: Pendiente de revisión",
            f"Solicitante: {display_name(alert.draft.requester)}",
            "Fecha límite: Sin fecha límite",
        ]
    lines += ["", f"Puede verlo en {config.APP_URL}"]
    return subject, "\n".join(lines)


def _group_email(kind: str, alerts: list[_Alert]) -> tuple[str, str]:
    noun = "tickets" if all(a.ticket for a in alerts) else "tickets o borradores"
    subject = f"[Ticketera] {GROUP_LABELS[kind]}: {len(alerts)} {noun}"
    items = [_ticket_line(a.ticket) if a.ticket else _draft_line(a.draft) for a in alerts]
    lines = ["Buen día.", "", GROUP_INTROS[kind], ""]
    lines += _section(SECTION_TITLES[kind], items)
    lines += ["", f"Puede ver el detalle en {config.APP_URL}"]
    return subject, "\n".join(lines)


def _record(db: Session, alert: _Alert, now: datetime) -> None:
    db.add(models.AlertSent(
        kind=alert.kind,
        ticket_id=alert.ticket.id if alert.ticket else None,
        draft_id=alert.draft.id if alert.draft else None,
        sent_at=now,
    ))


def _dispatch(db: Session, kind: str, alerts: list[_Alert], now: datetime) -> int:
    """Envía y registra. Más de MAX_ITEMS pendientes: un correo agrupado por destinatario."""
    if not alerts:
        return 0
    delivered: set[int] = set()
    if len(alerts) <= MAX_ITEMS:
        for i, alert in enumerate(alerts):
            subject, body = _single_email(alert)
            ok = sum(1 for to in alert.to if mailer.send_email(to, subject, body))
            if ok:
                delivered.add(i)
            target = "ticket" if alert.ticket else "borrador"
            item_id = alert.ticket.id if alert.ticket else alert.draft.id
            logging.info("Alerta %s %s %s: %s destinatarios", kind, target, item_id, ok)
    else:
        by_recipient: dict[str, list[int]] = defaultdict(list)
        for i, alert in enumerate(alerts):
            for to in alert.to:
                by_recipient[to].append(i)
        for to, indexes in by_recipient.items():
            if len(indexes) == 1:
                subject, body = _single_email(alerts[indexes[0]])
            else:
                subject, body = _group_email(kind, [alerts[i] for i in indexes])
            if mailer.send_email(to, subject, body):
                delivered.update(indexes)
        logging.info("Alertas %s agrupadas: %s de %s registradas, %s destinatarios",
                     kind, len(delivered), len(alerts), len(by_recipient))
    # Solo lo que llegó al menos a una persona: lo demás se reintenta en la siguiente revisión
    for i in delivered:
        _record(db, alerts[i], now)
    db.commit()
    return len(delivered)


def _sent_ids(db: Session, kind: str) -> tuple[set[int], set[int]]:
    rows = db.query(models.AlertSent.ticket_id, models.AlertSent.draft_id).filter(models.AlertSent.kind == kind).all()
    return {t for t, _d in rows if t is not None}, {d for _t, d in rows if d is not None}


def _open_tickets(db: Session) -> list[models.Ticket]:
    return (
        db.query(models.Ticket)
        .options(joinedload(models.Ticket.ai_analysis), joinedload(models.Ticket.assigned_user))
        .filter(models.Ticket.status.in_(OPEN_STATUSES))
        .order_by(models.Ticket.id)
        .all()
    )


def _run_critical(db: Session, now: datetime, staff: list[str]) -> int:
    sent_tickets, sent_drafts = _sent_ids(db, CRITICAL)
    # Tickets confirmados desde un borrador que ya tuvo su alerta crítica
    from_alerted_draft = dict(
        db.query(models.EmailDraft.ticket_id, models.EmailDraft.id)
        .join(models.AlertSent, models.AlertSent.draft_id == models.EmailDraft.id)
        .filter(models.AlertSent.kind == CRITICAL, models.EmailDraft.ticket_id.isnot(None))
        .all()
    )
    alerts = []
    silent = 0
    for ticket in _open_tickets(db):
        if _level(ticket) != 4 or ticket.id in sent_tickets:
            continue
        alert = _Alert(CRITICAL, ticket=ticket, to=staff)
        if ticket.id in from_alerted_draft:
            # Ya se avisó como borrador: se registra sin otro correo
            _record(db, alert, now)
            silent += 1
            logging.info("Alerta critical ticket %s: ya avisada como borrador %s", ticket.id, from_alerted_draft[ticket.id])
            continue
        alerts.append(alert)
    if silent:
        db.commit()

    drafts = (
        db.query(models.EmailDraft)
        .options(joinedload(models.EmailDraft.requester))
        .filter(models.EmailDraft.status == "pending_review", models.EmailDraft.ai_level == 4)
        .order_by(models.EmailDraft.id)
        .all()
    )
    alerts += [_Alert(CRITICAL, draft=d, to=staff) for d in drafts if d.id not in sent_drafts]
    return silent + _dispatch(db, CRITICAL, alerts, now)


def due_state(ticket: models.Ticket, now: datetime) -> tuple[str | None, int | None]:
    """Estado del vencimiento (overdue, due_soon, on_time o None sin fecha) y % de horas hábiles consumidas.

    Mismo cálculo que las alertas: vencido si la fecha límite ya pasó; por vencer si se consumió
    ALERT_WARN_PERCENT de las horas hábiles entre la creación y la fecha límite.
    """
    due = _due_at(ticket)
    if due is None:
        return None, None
    total = business_hours_between(ticket.created_at, due)
    used = business_hours_between(ticket.created_at, now) if now > ticket.created_at else 0.0
    percent = 100 if total <= 0 else min(100, round(used * 100 / total))
    if due <= now:
        # Exactamente en la fecha límite no es vencido ni por vencer (como antes)
        return (OVERDUE if due < now else ON_TIME), 100
    if total > 0 and used >= total * config.ALERT_WARN_PERCENT / 100:
        return DUE_SOON, percent
    return ON_TIME, percent


def _run_overdue(db: Session, now: datetime, staff: list[str]) -> int:
    sent_tickets, _ = _sent_ids(db, OVERDUE)
    alerts = []
    for ticket in _open_tickets(db):
        if due_state(ticket, now)[0] != OVERDUE or ticket.id in sent_tickets:
            continue
        assignee = _active_assignee(db, ticket)
        to = staff + ([assignee] if assignee and assignee not in staff else [])
        alerts.append(_Alert(OVERDUE, ticket=ticket, to=to))
    return _dispatch(db, OVERDUE, alerts, now)


def _run_due_soon(db: Session, now: datetime, staff: list[str]) -> int:
    sent_tickets, _ = _sent_ids(db, DUE_SOON)
    alerts = []
    for ticket in _open_tickets(db):
        # Uno ya vencido solo recibe la alerta de vencido
        if due_state(ticket, now)[0] != DUE_SOON or ticket.id in sent_tickets:
            continue
        assignee = _active_assignee(db, ticket)
        alerts.append(_Alert(DUE_SOON, ticket=ticket, to=[assignee] if assignee else staff))
    return _dispatch(db, DUE_SOON, alerts, now)


def _return_notice(user: models.User, return_date) -> tuple[str, str]:
    name = user.name or "Técnico sin nombre"
    subject = f"[Ticketera] Fecha de regreso: {name}"
    body = "\n".join([
        "Buen día.",
        "",
        f"La fecha de regreso de {name} era el {return_date.strftime('%d/%m/%Y')}, "
        "pero su disponibilidad sigue sin actualizarse.",
        "Si ya regresó, márquelo como Disponible; si no, actualice la fecha de regreso.",
        "",
        f"Puede actualizarla en {config.APP_URL}",
    ])
    return subject, body


def _run_return_dates(db: Session, now: datetime, staff: list[str]) -> int:
    """Aviso, una sola vez por fecha, cuando llega el regreso de un técnico. El estado no cambia solo."""
    local = _local(now)
    # Como el resumen diario: a partir de DAILY_SUMMARY_HOUR, no apenas pasa la medianoche
    if local.hour < config.DAILY_SUMMARY_HOUR:
        return 0
    rows = (
        db.query(models.TechnicianAvailability)
        .join(models.User, models.User.id == models.TechnicianAvailability.user_id)
        .filter(
            models.TechnicianAvailability.status != "available",
            models.TechnicianAvailability.return_date.isnot(None),
            models.TechnicianAvailability.return_date <= local.date(),
            models.User.role == "technician",
            models.User.is_active.is_(True),
        )
        .order_by(models.TechnicianAvailability.user_id)
        .all()
    )
    sent = 0
    for row in rows:
        if row.return_notice_for == row.return_date:
            continue
        subject, body = _return_notice(row.user, row.return_date)
        to = [row.user.email] + [email for email in staff if email != row.user.email]
        if config.EMAIL_ENABLED:
            ok = sum(1 for email in to if mailer.send_email(email, subject, body))
        else:
            # Sin correo real: queda en el log y cuenta como enviado para no repetirlo en cada revisión
            logging.warning("[SOLO DESARROLLO] Aviso de fecha de regreso del técnico %s para %s destinatarios",
                            row.user_id, len(to))
            ok = len(to)
        logging.info("Aviso de regreso técnico %s: %s destinatarios", row.user_id, ok)
        if ok:
            row.return_notice_for = row.return_date
            sent += 1
    db.commit()
    return sent


def run_alerts(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Una revisión: devuelve cuántas alertas quedaron registradas de cada tipo."""
    if not config.ALERTS_ENABLED:
        return {}
    now = now or _now()
    staff = recipients(db)
    return {
        CRITICAL: _run_critical(db, now, staff),
        OVERDUE: _run_overdue(db, now, staff),
        DUE_SOON: _run_due_soon(db, now, staff),
        RETURN_DATE: _run_return_dates(db, now, staff),
    }


def last_sent(db: Session) -> datetime | None:
    value = db.query(func.max(models.AlertSent.sent_at)).scalar()
    return value.replace(tzinfo=timezone.utc) if value else None


def _run_once() -> None:
    db = SessionLocal()
    try:
        run_alerts(db)
    finally:
        db.close()


async def alerts_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(_run_once)
        except Exception:
            logging.exception("Falló la revisión de alertas")
        await asyncio.sleep(config.ALERTS_CHECK_SECONDS)
