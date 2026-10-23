"""Cancelar y reabrir tickets. Un ticket cancelado queda "closed" con una fila en ticket_cancellations:
sale de todas las colas y contadores de trabajo, pero se conserva con su historial."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import auto_assign, models

# Única fuente del permiso: cancelar y reabrir
CANCEL_ROLES = ("coordinator",)
NO_PERMISSION = "No tienes permiso para cancelar tickets"
MAX_TICKETS = 50


def require_can_cancel(user: models.User) -> None:
    if user.role not in CANCEL_ROLES:
        raise HTTPException(status_code=403, detail=NO_PERMISSION)


def cancelled_ids(db: Session) -> set[int]:
    return {row[0] for row in db.query(models.TicketCancellation.ticket_id).all()}


def count(db: Session) -> int:
    return db.query(models.TicketCancellation).count()


def check_can_be_cancelled(ticket: models.Ticket) -> None:
    if ticket.status == "closed":
        detail = "ya está cancelado" if ticket.cancellation_row else "está cerrado"
        raise HTTPException(status_code=400, detail=f"El ticket #{ticket.id} {detail}")


def cancel(db: Session, ticket: models.Ticket, actor: models.User, reason: str, note: str | None) -> None:
    """No hace commit. El responsable se conserva: el ticket queda tal como estaba al cancelarlo."""
    check_can_be_cancelled(ticket)
    old_status = ticket.status
    ticket.status = "closed"
    auto_assign.cancel_waiting(db, ticket.id)
    db.add(models.TicketCancellation(
        ticket_id=ticket.id, reason=reason, note=note or None, cancelled_by=actor.id,
        cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.add(models.History(
        ticket_id=ticket.id, field="cancelled", old_value=old_status, new_value=reason, changed_by=actor.id,
    ))


def reopen(db: Session, ticket: models.Ticket, actor: models.User) -> None:
    """Vuelve a la cola como Nuevo y sin responsable. No hace commit."""
    row = db.query(models.TicketCancellation).filter(models.TicketCancellation.ticket_id == ticket.id).first()
    if row is None:
        raise HTTPException(status_code=400, detail="El ticket no está cancelado")
    db.delete(row)
    db.add(models.History(ticket_id=ticket.id, field="reopened", old_value=row.reason, new_value=None, changed_by=actor.id))
    db.add(models.History(ticket_id=ticket.id, field="status", old_value="closed", new_value="new", changed_by=actor.id))
    if ticket.assigned_to is not None:
        db.add(models.History(
            ticket_id=ticket.id, field="assigned_to", old_value=str(ticket.assigned_to), new_value=None,
            changed_by=actor.id,
        ))
    ticket.status = "new"
    ticket.assigned_to = None
