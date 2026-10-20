"""Asignación automática de tickets nuevos al técnico disponible, en turno y con menos carga.

Solo con AUTO_ASSIGN_ENABLED. Corre después del análisis de la IA (el nivel ya existe) y nunca cambia
una asignación existente. Sin candidatos, el ticket queda en espera y lo reintenta auto_assign_forever,
que no depende del correo.
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import func, update
from sqlalchemy.orm import Session, joinedload

from . import assignment_notice, audit, availability, config, email_analysis, models, schedules
from .database import SessionLocal
from .email_processing import _RunNow
from .notifications import notify_followers

# Abierto: cualquier estado que no sea resuelto o cerrado
OPEN_STATUSES = ("new", "in_progress")
LOAD_MODES = ("levels", "count")
NO_LEVEL = 2
# Una asignación a la vez en todo el proceso; el UPDATE condicionado cubre lo demás
AUTO_ASSIGN_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_mode() -> str:
    return config.AUTO_ASSIGN_LOAD_MODE if config.AUTO_ASSIGN_LOAD_MODE in LOAD_MODES else "levels"


def _row(db: Session, ticket_id: int) -> models.TicketAutoAssignment | None:
    return db.query(models.TicketAutoAssignment).filter(models.TicketAutoAssignment.ticket_id == ticket_id).first()


def candidates(db: Session, now: datetime) -> list[models.User]:
    """Técnicos activos, Disponibles y en turno en este momento (sin horario: nunca)."""
    technicians = (
        db.query(models.User)
        .filter(models.User.role == "technician", models.User.is_active.is_(True))
        .order_by(models.User.id)
        .all()
    )
    by_user = schedules.for_users(db, [u.id for u in technicians])
    return [
        u for u in technicians
        if by_user[u.id] and availability.is_available(db, u) and schedules.on_shift(by_user[u.id], now)
    ]


def loads(db: Session, user_ids: list[int]) -> dict[int, int]:
    """Carga de cada técnico: suma de niveles (sin nivel cuenta 2) o cantidad de tickets abiertos."""
    result = {uid: 0 for uid in user_ids}
    if not user_ids:
        return result
    tickets = (
        db.query(models.Ticket)
        .options(joinedload(models.Ticket.ai_analysis))
        .filter(models.Ticket.assigned_to.in_(user_ids), models.Ticket.status.in_(OPEN_STATUSES))
        .all()
    )
    by_levels = load_mode() == "levels"
    for ticket in tickets:
        level = email_analysis.effective_level(ticket.ai_analysis) if ticket.ai_analysis else None
        result[ticket.assigned_to] += (level if level is not None else NO_LEVEL) if by_levels else 1
    return result


def choose(db: Session, now: datetime) -> models.User | None:
    """Menor carga; empate: la asignación automática más antigua (o ninguna) y luego el id."""
    users = candidates(db, now)
    if not users:
        return None
    ids = [u.id for u in users]
    load = loads(db, ids)
    last_auto = dict(
        db.query(models.TicketAutoAssignment.user_id, func.max(models.TicketAutoAssignment.assigned_at))
        .filter(models.TicketAutoAssignment.status == "assigned", models.TicketAutoAssignment.user_id.in_(ids))
        .group_by(models.TicketAutoAssignment.user_id)
        .all()
    )
    return min(users, key=lambda u: (load[u.id], last_auto.get(u.id) or datetime.min, u.id))


def _assign(db: Session, ticket: models.Ticket, row: models.TicketAutoAssignment | None, now: datetime) -> bool:
    """Asigna si hay candidato. Sin candidato deja la fila en espera. No hace commit."""
    if row is None:
        row = models.TicketAutoAssignment(ticket_id=ticket.id, status="waiting", created_at=now)
        db.add(row)
    user = choose(db, now)
    agent_id = audit.agent_id(db)
    if user is None or agent_id is None:
        return False
    # Solo si sigue sin asignar: si otro proceso ganó, no se toca
    result = db.execute(
        update(models.Ticket)
        .where(models.Ticket.id == ticket.id, models.Ticket.assigned_to.is_(None))
        .values(assigned_to=user.id)
    )
    if result.rowcount == 0:
        row.status = "cancelled"
        return False
    history = models.History(
        ticket_id=ticket.id, field="assigned_to", old_value=None, new_value=str(user.id), changed_by=agent_id,
    )
    db.add(history)
    db.flush()
    row.status = "assigned"
    row.user_id = user.id
    row.assigned_at = now
    row.history_id = history.id
    db.refresh(ticket)
    logging.info("Ticket %s asignado automáticamente al técnico %s", ticket.id, user.id)
    agent = db.get(models.User, agent_id)
    # Al técnico su aviso; a los demás seguidores el de siempre (un solo correo por persona)
    assignment_notice.schedule(_RunNow(), ticket, user)
    notify_followers(
        db, _RunNow(), ticket, agent,
        f"[Ticket #{ticket.id}] Nueva asignación",
        [f"El ticket se asignó automáticamente a {user.name or 'un técnico'}."],
        exclude_ids=(user.id,),
    )
    return True


def _still_open_and_free(ticket: models.Ticket | None) -> bool:
    return ticket is not None and ticket.assigned_to is None and ticket.status in OPEN_STATUSES


def assign_new(ticket_id: int, now: datetime | None = None) -> str:
    """Primer intento, al terminar el análisis. Nunca lanza excepciones (corre en segundo plano)."""
    if not config.AUTO_ASSIGN_ENABLED:
        return "disabled"
    now = now or _now()
    with AUTO_ASSIGN_LOCK:
        db = SessionLocal()
        try:
            ticket = db.get(models.Ticket, ticket_id)
            row = _row(db, ticket_id)
            if row is not None and row.status != "waiting":
                return "skipped"
            if not _still_open_and_free(ticket):
                # Asignado a mano, resuelto o cerrado antes: ya no le toca al sistema
                if row is not None:
                    row.status = "cancelled"
                    db.commit()
                return "skipped"
            assigned = _assign(db, ticket, row, now)
            db.commit()
            if assigned:
                return "assigned"
            # Otro proceso lo asignó entre la elección y el UPDATE
            return "skipped" if _row(db, ticket_id).status == "cancelled" else "waiting"
        except Exception:
            db.rollback()
            logging.exception("Falló la asignación automática del ticket %s", ticket_id)
            return "error"
        finally:
            db.close()


def run_pending(now: datetime | None = None) -> int:
    """Reintenta los tickets en espera, del más antiguo al más nuevo. Devuelve cuántos asignó."""
    if not config.AUTO_ASSIGN_ENABLED:
        return 0
    now = now or _now()
    assigned = 0
    with AUTO_ASSIGN_LOCK:
        db = SessionLocal()
        try:
            rows = (
                db.query(models.TicketAutoAssignment)
                .join(models.Ticket, models.Ticket.id == models.TicketAutoAssignment.ticket_id)
                .filter(models.TicketAutoAssignment.status == "waiting")
                .order_by(models.Ticket.created_at, models.Ticket.id)
                .all()
            )
            for row in rows:
                if not _still_open_and_free(row.ticket):
                    row.status = "cancelled"
                elif _assign(db, row.ticket, row, now):
                    assigned += 1
                # Uno por uno: la carga se recalcula con lo recién asignado
                db.commit()
        except Exception:
            db.rollback()
            logging.exception("Falló la revisión de asignaciones pendientes")
        finally:
            db.close()
    if assigned:
        logging.info("Asignación automática: %s tickets pendientes asignados", assigned)
    return assigned


def analyze_then_assign(ticket_id: int) -> None:
    """Ticket de la página: primero el análisis (si está activo) y después la asignación."""
    if config.AI_ANALYSIS_ENABLED:
        email_analysis.analyze_ticket_by_id(ticket_id)
    assign_new(ticket_id)


def cancel_waiting(db: Session, ticket_id: int) -> None:
    """Asignación a mano: el ticket sale de la espera. No hace commit."""
    row = _row(db, ticket_id)
    if row is not None and row.status == "waiting":
        row.status = "cancelled"


def is_auto_assigned(db: Session, ticket: models.Ticket) -> bool:
    """Lo asignó el sistema y nadie lo reasignó después."""
    row = _row(db, ticket.id)
    if row is None or row.status != "assigned" or row.user_id != ticket.assigned_to:
        return False
    last = (
        db.query(models.History.id)
        .filter(models.History.ticket_id == ticket.id, models.History.field == "assigned_to")
        .order_by(models.History.id.desc())
        .first()
    )
    return last is not None and last[0] == row.history_id


async def auto_assign_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(run_pending)
        except Exception:
            logging.exception("Falló la revisión de asignaciones pendientes")
        await asyncio.sleep(config.AUTO_ASSIGN_CHECK_SECONDS)
