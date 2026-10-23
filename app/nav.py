"""Contadores del menú lateral y estado de los servicios para la barra inferior (solo lectura).

Privacidad: solo conteos y activo/inactivo. Nunca títulos, nombres, correos ni valores de configuración.
"""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from . import account_reports, alerts, auto_assign, availability, cancellations, config, grouping, models, schedules
from .daily_summary import OPEN_STATUSES, _due_at, _level, _local

STAFF_ROLES = ("technician", "coordinator", "admin")
RESOLVED_STATUSES = ("resolved", "closed")
PAGE_SIZE = 25
QUEUE_VIEWS = (
    "all", "open", "unassigned", "due_soon", "overdue", "resolved_today",
    "mine_open", "mine_due_soon", "following", "mine_resolved_today", "cancelled",
)
_OPEN_VIEWS = ("open", "unassigned", "due_soon", "overdue", "mine_open", "mine_due_soon", "following")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_start(now: datetime) -> datetime:
    """Medianoche de hoy en la hora de la oficina, en UTC sin zona (como en la base)."""
    local_midnight = datetime.combine(_local(now).date(), time(0, 0), tzinfo=ZoneInfo(config.OFFICE_TZ))
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _open_tickets(db: Session, assigned_to: int | None = None) -> list[models.Ticket]:
    query = (
        db.query(models.Ticket)
        .options(joinedload(models.Ticket.ai_analysis))
        .filter(models.Ticket.status.in_(OPEN_STATUSES))
    )
    if assigned_to is not None:
        query = query.filter(models.Ticket.assigned_to == assigned_to)
    return query.all()


def is_overdue(ticket: models.Ticket, now: datetime) -> bool:
    """Abierto y con la fecha límite pasada: solo aparece en la vista Vencidos."""
    return ticket.status in OPEN_STATUSES and alerts.due_state(ticket, now)[0] == alerts.OVERDUE


def _not_overdue(tickets: list[models.Ticket], now: datetime) -> list[models.Ticket]:
    return [t for t in tickets if not is_overdue(t, now)]


def _due_counts(tickets: list[models.Ticket], now: datetime) -> tuple[int, int]:
    """(por vencer, vencidos) con el mismo cálculo que las alertas."""
    states = [alerts.due_state(t, now)[0] for t in tickets]
    return states.count(alerts.DUE_SOON), states.count(alerts.OVERDUE)


def _resolved_today(db: Session, now: datetime, assigned_to: int | None = None) -> int:
    return len(_resolved_today_ids(db, now, assigned_to))


def _resolved_today_ids(db: Session, now: datetime, assigned_to: int | None = None) -> set[int]:
    """Tickets que pasaron a Resuelto hoy (hora de la oficina) y siguen resueltos o cerrados (sin cancelados ni fusionados)."""
    query = (
        db.query(models.History.ticket_id)
        .join(models.Ticket, models.Ticket.id == models.History.ticket_id)
        .filter(
            models.History.field == "status",
            models.History.new_value == "resolved",
            models.History.changed_at >= _today_start(now),
            models.Ticket.status.in_(RESOLVED_STATUSES),
            models.Ticket.id.notin_(db.query(models.TicketCancellation.ticket_id)),
            models.Ticket.id.notin_(grouping.merged_source_ids(db)),
        )
    )
    if assigned_to is not None:
        query = query.filter(models.Ticket.assigned_to == assigned_to)
    return {row[0] for row in query.distinct().all()}


def _unassigned(db: Session, now: datetime) -> int:
    # Sin los vencidos: esos se cuentan en Vencidos
    return sum(1 for t in _not_overdue(_open_tickets(db), now) if t.assigned_to is None)


def _drafts_pending(db: Session) -> int:
    # Los mismos estados que muestra la pantalla de borradores (aquí: drafts importa el router de tickets)
    from .routers.drafts import DEFAULT_STATUSES as DRAFT_STATUSES
    return db.query(models.EmailDraft).filter(models.EmailDraft.status.in_(DRAFT_STATUSES)).count()


def _shift(db: Session, user: models.User, now: datetime) -> dict:
    rows = schedules.get(db, user.id)
    today = schedules.today_range(rows, now)
    return {
        "start": today[0] if today else None,
        "end": today[1] if today else None,
        "on_shift": schedules.on_shift(rows, now),
        "load": auto_assign.loads(db, [user.id])[user.id],
    }


def counts(db: Session, user: models.User, now: datetime | None = None) -> dict:
    """Contadores según el rol. El técnico nunca recibe reportes, aprobaciones ni invitaciones."""
    now = now or _now()
    if user.role == "technician":
        mine = _open_tickets(db, assigned_to=user.id)
        following = (
            db.query(models.Ticket)
            .options(joinedload(models.Ticket.ai_analysis))
            .join(models.TicketFollower, models.TicketFollower.ticket_id == models.Ticket.id)
            .filter(models.TicketFollower.user_id == user.id, models.Ticket.status.in_(OPEN_STATUSES))
            .all()
        )
        mine_due_soon, mine_overdue = _due_counts(mine, now)
        return {
            "mine_open": len(mine) - mine_overdue,
            "mine_due_soon": mine_due_soon,
            # Solo sus vencidos
            "overdue": mine_overdue,
            "following_open": len(_not_overdue(following, now)),
            "mine_resolved_today": _resolved_today(db, now, assigned_to=user.id),
            "unassigned": _unassigned(db, now),
            "drafts_pending": _drafts_pending(db),
            "shift": _shift(db, user, now),
        }

    tickets = _open_tickets(db)
    due_soon, overdue = _due_counts(tickets, now)
    invitations = db.query(models.StaffInvitation).filter(
        models.StaffInvitation.used_by.is_(None), models.StaffInvitation.expires_at > now
    )
    if user.role == "coordinator":
        # Como la lista de invitaciones: el coordinador solo ve las suyas
        invitations = invitations.filter(models.StaffInvitation.created_by == user.id)
    result = {
        "unassigned": _unassigned(db, now),
        "due_soon": due_soon,
        "overdue": overdue,
        # Los vencidos van aparte, en Vencidos
        "open": len(tickets) - overdue,
        "resolved_today": _resolved_today(db, now),
        "drafts_pending": _drafts_pending(db),
        "reports_open": db.query(models.AccountReport).filter(models.AccountReport.status == "open").count(),
        "invitations_pending": invitations.count(),
        # Aparte: los cancelados no cuentan como trabajo, pero no se esconden
        "cancelled": cancellations.count(db),
    }
    if user.role == "admin":
        result["approvals_pending"] = (
            db.query(models.AccountRequest).filter(models.AccountRequest.status == "pending").count()
        )
    return result


def system_status(db: Session, now: datetime | None = None) -> dict:
    """Solo activo/inactivo y conteos: nunca buzones, URLs, modelos ni horas configuradas."""
    now = now or _now()
    return {
        "email": bool(config.EMAIL_ENABLED),
        "ai_analysis": bool(config.AI_ANALYSIS_ENABLED),
        "auto_assign": bool(config.AUTO_ASSIGN_ENABLED),
        "auto_assign_mode": auto_assign.load_mode() if config.AUTO_ASSIGN_ENABLED else None,
        # Disponibles y en turno: los mismos que puede elegir la asignación automática
        "technicians_on_shift": len(auto_assign.candidates(db, now)),
    }


def _assignee_note(ticket: models.Ticket, statuses: dict, shifts: dict, now: datetime) -> str | None:
    """Aviso sobre el responsable: de vacaciones, con incapacidad o ausente (fuera de su horario),
    o el ticket vence después de su turno de hoy."""
    user = ticket.assigned_user
    if user is None or user.role not in availability.ROLES or ticket.status not in OPEN_STATUSES:
        return None
    status = statuses.get(user.id, ("available", None))[0]
    if status != "available":
        return status
    due = _due_at(ticket)
    today = schedules.today_range(shifts.get(user.id, []), now)
    if due is None or today is None or due <= now:
        return None
    hour, minute = (int(part) for part in today[1].split(":"))
    shift_end = _local(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return "after_shift" if _local(due) > shift_end else None


def queue(
    db: Session,
    user: models.User,
    *,
    view: str = "open",
    status: str | None = None,
    level: str | None = None,
    assigned_to: int | None = None,
    source: str | None = None,
    search: str | None = None,
    requester_id: int | None = None,
    page: int = 1,
    now: datetime | None = None,
) -> dict:
    """Cola de tickets por vista, ordenada por vencimiento (los que no tienen fecha al final), de 25 en 25.

    Mismo alcance que GET /tickets para el personal. El técnico no filtra por responsable: assigned_to se ignora.
    Solo datos para la lista: nunca descripción, teléfono, cédula ni correo.
    """
    now = now or _now()
    query = db.query(models.Ticket).options(
        joinedload(models.Ticket.ai_analysis), joinedload(models.Ticket.assigned_user)
    ).filter(models.Ticket.id.notin_(grouping.merged_source_ids(db)))  # Los fusionados se ven desde su destino
    if view in _OPEN_VIEWS:
        query = query.filter(models.Ticket.status.in_(OPEN_STATUSES))
    if view == "unassigned":
        query = query.filter(models.Ticket.assigned_to.is_(None))
    if view in ("mine_open", "mine_due_soon"):
        query = query.filter(models.Ticket.assigned_to == user.id)
    if view == "overdue" and user.role == "technician":
        # El técnico solo ve sus vencidos
        query = query.filter(models.Ticket.assigned_to == user.id)
    if view == "following":
        query = query.join(models.TicketFollower, models.TicketFollower.ticket_id == models.Ticket.id).filter(
            models.TicketFollower.user_id == user.id
        )
    if view == "cancelled":
        query = query.filter(models.Ticket.id.in_(db.query(models.TicketCancellation.ticket_id)))
    if view in ("resolved_today", "mine_resolved_today"):
        ids = _resolved_today_ids(db, now, assigned_to=user.id if view == "mine_resolved_today" else None)
        query = query.filter(models.Ticket.id.in_(ids))
    if status:
        query = query.filter(models.Ticket.status == status)
    if source:
        query = query.filter(models.Ticket.source == source)
    if requester_id is not None:
        # "Ver sus tickets" desde Cuentas
        query = query.filter(models.Ticket.created_by == requester_id)
    if assigned_to is not None and user.role != "technician":
        # 0: sin asignar
        query = query.filter(
            models.Ticket.assigned_to.is_(None) if assigned_to == 0 else models.Ticket.assigned_to == assigned_to
        )
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(models.Ticket.title.ilike(term) | models.Ticket.description.ilike(term))

    # Buscar o ver los tickets de un cliente encuentra también los vencidos
    keep_overdue = view == "overdue" or bool(search and search.strip()) or requester_id is not None
    rows = []
    for ticket in query.all():
        state, percent = alerts.due_state(ticket, now)
        if view in ("due_soon", "mine_due_soon") and state != alerts.DUE_SOON:
            continue
        if view == "overdue" and state != alerts.OVERDUE:
            continue
        # Los vencidos abiertos solo salen en la vista Vencidos
        if not keep_overdue and state == alerts.OVERDUE and ticket.status in OPEN_STATUSES:
            continue
        ticket_level = _level(ticket)
        if level == "none" and ticket_level is not None:
            continue
        if level not in (None, "none") and ticket_level != int(level):
            continue
        rows.append((ticket, state, percent, ticket_level))
    rows.sort(key=lambda r: (_due_at(r[0]) is None, _due_at(r[0]) or datetime.max, r[0].id))

    total = len(rows)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    page_rows = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]

    tickets = [r[0] for r in page_rows]
    creator_ids = list({t.created_by for t in tickets})
    creators = {u.id: u for u in db.query(models.User).filter(models.User.id.in_(creator_ids)).all()} if creator_ids else {}
    reported = account_reports.reported_ids(db, creator_ids)
    cancelled = cancellations.cancelled_ids(db) if tickets else set()
    incidents = grouping.incident_ids(db) if tickets else set()
    staff_ids = list({t.assigned_to for t in tickets if t.assigned_user and t.assigned_user.role in availability.ROLES})
    statuses = availability.statuses_for(db, staff_ids, now)
    shifts = schedules.for_users(db, staff_ids)

    items = []
    for ticket, state, percent, ticket_level in page_rows:
        due = _due_at(ticket)
        creator = creators.get(ticket.created_by)
        items.append({
            "id": ticket.id,
            "title": ticket.title,
            "requester_name": (creator.name or "Sin nombre") if creator else "Sin nombre",
            "level": ticket_level,
            "source": ticket.source,
            "status": ticket.status,
            "assigned_to": ticket.assigned_to,
            "assigned_name": (ticket.assigned_user.name or "Sin nombre") if ticket.assigned_user else None,
            "reported": ticket.created_by in reported,
            "auto_assigned": auto_assign.is_auto_assigned(db, ticket),
            "cancelled": ticket.id in cancelled,
            "incident": ticket.id in incidents,
            "due_at": due,
            "due_state": state,
            "remaining_seconds": int((due - now).total_seconds()) if due else None,
            "consumed_pct": percent,
            "assignee_note": _assignee_note(ticket, statuses, shifts, now),
        })
    return {"items": items, "total": total, "page": page, "pages": pages}


def suggestion(db: Session, ticket: models.Ticket, now: datetime | None = None) -> dict | None:
    """Mismo candidato que elegiría la asignación automática, sin asignar nada.

    Solo para tickets abiertos sin responsable o con un responsable que no está Disponible.
    """
    if ticket.status not in OPEN_STATUSES:
        return None
    now = now or _now()
    current = ticket.assigned_user
    if current is not None and availability.is_available(db, current, now):
        return None
    user = auto_assign.choose(db, now)
    if user is None or user.id == ticket.assigned_to:
        return None
    return {"user_id": user.id, "name": user.name or "Sin nombre", "load": auto_assign.loads(db, [user.id])[user.id]}
