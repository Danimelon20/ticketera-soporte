"""Avisos de vencimiento de la campana (aparte de los correos de app/alerts.py, que no cambian).

- Al 50 % y al 80 % del plazo (horas hábiles, con los plazos de siempre) y al vencer: al responsable.
- Al 80 %, si no tiene responsable o el responsable no está disponible: también a los coordinadores,
  con la sugerencia de a quién reasignar.
- Al vencer, el reporte de vencimiento: a coordinadores y administradores, pendiente de revisar
  hasta que se registre la causa.
Cada aviso se genera una sola vez por ticket (ticket_due_notices). La primera revisión después de
instalar esto solo registra lo que ya correspondía (baseline) y no lo muestra.
Privacidad: solo número, título enmascarado y nombres; nunca descripción, teléfono ni cédula.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from . import alerts, availability, models, nav
from .chatbot import _redact

ALERT_KINDS = ("due_50", "due_80", "overdue")  # De menos a más avanzado
REPORT_ROLES = ("coordinator", "admin")
NOTICE_ROLES = ("technician", "coordinator", "admin")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _kinds_for(state: str | None, percent: int | None) -> list[str]:
    if state is None:
        return []
    if state == alerts.OVERDUE:
        return ["due_50", "due_80", "overdue", "report"]
    kinds = []
    if percent >= 50:
        kinds.append("due_50")
    if percent >= 80:
        kinds.append("due_80")
    return kinds


def sync(db: Session, now: datetime | None = None) -> None:
    """Genera los avisos que falten. Hace commit."""
    now = now or _now()
    first = db.query(models.DueNoticesInstall).first() is None
    if first:
        db.add(models.DueNoticesInstall(installed_at=now))
    existing = {(t, k) for t, k in db.query(models.TicketDueNotice.ticket_id, models.TicketDueNotice.kind)}
    for ticket in nav._open_tickets(db):
        for kind in _kinds_for(*alerts.due_state(ticket, now)):
            if (ticket.id, kind) in existing:
                continue
            notice = models.TicketDueNotice(ticket_id=ticket.id, kind=kind, created_at=now, baseline=first)
            if kind == "due_80" and not first:
                assignee = ticket.assigned_user
                if assignee is None or not availability.is_available(db, assignee, now):
                    notice.escalated = True
                    suggestion = nav.suggestion(db, ticket, now)
                    notice.suggested_user_id = suggestion["user_id"] if suggestion else None
            db.add(notice)
    db.commit()


def _visible(notice: models.TicketDueNotice, user: models.User, open_now: bool, cause_ids: set[int],
             cancelled: set[int]) -> bool:
    ticket = notice.ticket
    if notice.baseline:
        return False
    if notice.kind == "report":
        # Sigue pendiente de revisar mientras no tenga causa, aunque ya esté resuelto
        return user.role in REPORT_ROLES and ticket.id not in cancelled and (open_now or ticket.id not in cause_ids)
    if not open_now:
        return False
    if ticket.assigned_to == user.id:
        return True
    return notice.kind == "due_80" and notice.escalated and user.role == "coordinator"


def items_for(db: Session, user: models.User, now: datetime | None = None) -> list[dict]:
    """Avisos que ve la persona, el más reciente arriba: a lo sumo uno de vencimiento por ticket
    (el más avanzado) y, para coordinadores y administradores, el del reporte."""
    now = now or _now()
    notices = (
        db.query(models.TicketDueNotice)
        .options(
            joinedload(models.TicketDueNotice.ticket).joinedload(models.Ticket.ai_analysis),
            joinedload(models.TicketDueNotice.suggested_user),
        )
        .filter(models.TicketDueNotice.baseline.is_(False))
        .all()
    )
    cause_ids = {row[0] for row in db.query(models.OverdueCause.ticket_id)}
    cancelled = {row[0] for row in db.query(models.TicketCancellation.ticket_id)}
    seen = {
        (t, k) for t, k in db.query(models.OverdueNoticeSeen.ticket_id, models.OverdueNoticeSeen.kind)
        .filter(models.OverdueNoticeSeen.user_id == user.id)
    }
    best: dict[int, models.TicketDueNotice] = {}
    reports: list[models.TicketDueNotice] = []
    for notice in notices:
        if not _visible(notice, user, notice.ticket.status in nav.OPEN_STATUSES, cause_ids, cancelled):
            continue
        if notice.kind == "report":
            reports.append(notice)
            continue
        current = best.get(notice.ticket_id)
        if current is None or ALERT_KINDS.index(notice.kind) > ALERT_KINDS.index(current.kind):
            best[notice.ticket_id] = notice

    items = []
    for notice in list(best.values()) + reports:
        ticket = notice.ticket
        suggestion = None
        # La sugerencia es para quien puede reasignar
        if notice.kind == "due_80" and notice.suggested_user and user.role in REPORT_ROLES:
            suggestion = {"user_id": notice.suggested_user.id, "name": notice.suggested_user.name or "Sin nombre"}
        items.append({
            "ticket_id": ticket.id,
            "title": _redact(ticket.title),
            "kind": notice.kind,
            "created_at": notice.created_at,
            "due_at": nav._due_at(ticket),
            "read": (ticket.id, notice.kind) in seen,
            "needs_review": notice.kind == "report" and ticket.id not in cause_ids,
            "suggestion": suggestion,
        })
    items.sort(key=lambda i: (i["created_at"], i["ticket_id"]), reverse=True)
    return items


def mark_read(db: Session, user: models.User, ticket_id: int | None = None, now: datetime | None = None) -> int:
    """Marca como leídos los avisos que la persona ve (de un ticket o todos). Hace commit."""
    now = now or _now()
    count = 0
    for item in items_for(db, user, now):
        if item["read"] or (ticket_id is not None and item["ticket_id"] != ticket_id):
            continue
        db.add(models.OverdueNoticeSeen(user_id=user.id, ticket_id=item["ticket_id"], kind=item["kind"], seen_at=now))
        count += 1
    db.commit()
    return count
