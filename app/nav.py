"""Contadores del menú lateral y estado de los servicios para la barra inferior (solo lectura).

Privacidad: solo conteos y activo/inactivo. Nunca títulos, nombres, correos ni valores de configuración.
"""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from . import alerts, auto_assign, config, models, schedules
from .daily_summary import OPEN_STATUSES, _local
from .routers.drafts import DEFAULT_STATUSES as DRAFT_STATUSES

STAFF_ROLES = ("technician", "coordinator", "admin")
RESOLVED_STATUSES = ("resolved", "closed")


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


def _due_counts(tickets: list[models.Ticket], now: datetime) -> tuple[int, int]:
    """(por vencer, vencidos) con el mismo cálculo que las alertas."""
    states = [alerts.due_state(t, now)[0] for t in tickets]
    return states.count(alerts.DUE_SOON), states.count(alerts.OVERDUE)


def _resolved_today(db: Session, now: datetime, assigned_to: int | None = None) -> int:
    query = (
        db.query(models.History.ticket_id)
        .join(models.Ticket, models.Ticket.id == models.History.ticket_id)
        .filter(
            models.History.field == "status",
            models.History.new_value == "resolved",
            models.History.changed_at >= _today_start(now),
            models.Ticket.status.in_(RESOLVED_STATUSES),
        )
    )
    if assigned_to is not None:
        query = query.filter(models.Ticket.assigned_to == assigned_to)
    return query.distinct().count()


def _unassigned(db: Session) -> int:
    return (
        db.query(models.Ticket)
        .filter(models.Ticket.status.in_(OPEN_STATUSES), models.Ticket.assigned_to.is_(None))
        .count()
    )


def _drafts_pending(db: Session) -> int:
    # Los mismos estados que muestra la pantalla de borradores
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
            .join(models.TicketFollower, models.TicketFollower.ticket_id == models.Ticket.id)
            .filter(models.TicketFollower.user_id == user.id, models.Ticket.status.in_(OPEN_STATUSES))
            .count()
        )
        return {
            "mine_open": len(mine),
            "mine_due_soon": _due_counts(mine, now)[0],
            "following_open": following,
            "mine_resolved_today": _resolved_today(db, now, assigned_to=user.id),
            "unassigned": _unassigned(db),
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
        "unassigned": _unassigned(db),
        "due_soon": due_soon,
        "overdue": overdue,
        "open": len(tickets),
        "resolved_today": _resolved_today(db, now),
        "drafts_pending": _drafts_pending(db),
        "reports_open": db.query(models.AccountReport).filter(models.AccountReport.status == "open").count(),
        "invitations_pending": invitations.count(),
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
