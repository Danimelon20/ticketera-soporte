"""Gestión de cuentas por rol: quién puede desactivar, restablecer o desbloquear a quién."""
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from . import audit, comments, config, daily_summary, mailer, models

NO_PERMISSION = "No tienes permiso para gestionar esta cuenta"
SELF_CHANGE = "No puedes cambiar tu propio rol ni desactivarte"
AGENT_LOCKED = "El Agente virtual no se puede modificar"
LAST_ADMIN = "Debe quedar al menos un administrador activo"
RELEASED_COMMENT = "Técnico desactivado; ticket liberado"
RELEASED_LIST_MAX = 10
OPEN_STATUSES = ("new", "in_progress")

# Roles de las cuentas que cada rol puede gestionar, por acción
PERMISSIONS = {
    "admin": {
        "active": ("coordinator", "technician", "requester"),
        "reset": ("coordinator", "technician"),
        "unlock": ("admin", "coordinator", "technician", "requester"),
    },
    "coordinator": {
        "active": ("technician", "requester"),
        "reset": ("technician",),
        "unlock": ("technician", "requester"),
    },
}


def check_can_manage(actor: models.User, target: models.User, action: str) -> None:
    """403 si el actor no puede hacer esa acción sobre la cuenta (nunca sobre sí mismo)."""
    if target.role == "agent":
        raise HTTPException(status_code=403, detail=AGENT_LOCKED)
    if target.id == actor.id:
        raise HTTPException(status_code=403, detail=SELF_CHANGE)
    if target.role not in PERMISSIONS.get(actor.role, {}).get(action, ()):
        raise HTTPException(status_code=403, detail=NO_PERMISSION)


def close_sessions(db: Session, user_id: int) -> None:
    """Cierra todas las sesiones de la cuenta en todos sus dispositivos. No hace commit."""
    session_ids = [s.id for s in db.query(models.Session.id).filter(models.Session.user_id == user_id)]
    if session_ids:
        db.query(models.SessionActivity).filter(models.SessionActivity.session_id.in_(session_ids)).delete(
            synchronize_session=False
        )
    db.query(models.Session).filter(models.Session.user_id == user_id).delete(synchronize_session=False)


def _release_tickets(db: Session, actor: models.User, user: models.User) -> list[models.Ticket]:
    tickets = (
        db.query(models.Ticket)
        .filter(models.Ticket.assigned_to == user.id, models.Ticket.status.in_(OPEN_STATUSES))
        .order_by(models.Ticket.id)
        .all()
    )
    for ticket in tickets:
        ticket.assigned_to = None
        db.add(models.History(
            ticket_id=ticket.id, field="assigned_to", old_value=str(user.id), new_value=None, changed_by=actor.id,
        ))
        # Interno: no llega al solicitante ni a los seguidores
        comments.add_internal(db, ticket.id, actor.id, RELEASED_COMMENT)
    if tickets:
        audit.record(db, actor.id, "tickets_released", target=user, new=str(len(tickets)))
    return tickets


def released_notice(user: models.User, tickets: list[models.Ticket]) -> tuple[str, str]:
    lines = [
        f"Se desactivó la cuenta del técnico {user.name or audit.masked_email(user.email)}.",
        f"Estos {len(tickets)} tickets abiertos quedaron sin asignar:",
        "",
    ]
    lines += [f"- #{t.id} {t.title}" for t in tickets[:RELEASED_LIST_MAX]]
    if len(tickets) > RELEASED_LIST_MAX:
        lines.append(f"… y {len(tickets) - RELEASED_LIST_MAX} más")
    lines += ["", f"Asígnelos de nuevo en {config.APP_URL}"]
    return "[Ticketera] Tickets liberados por desactivación", "\n".join(lines)


def set_active(
    db: Session, actor: models.User, user: models.User, active: bool, background_tasks: BackgroundTasks | None = None
) -> None:
    """Desactiva o reactiva (nunca se borra). No hace commit; el correo sale después de responder."""
    if user.is_active == active:
        return
    user.is_active = active
    audit.record(db, actor.id, "account_activated" if active else "account_deactivated", target=user)
    if active:
        return
    close_sessions(db, user.id)
    if user.role != "technician":
        return
    tickets = _release_tickets(db, actor, user)
    if tickets:
        subject, body = released_notice(user, tickets)
        send = background_tasks.add_task if background_tasks is not None else (lambda f, *a: f(*a))
        for email in daily_summary.recipients(db):
            send(mailer.send_email, email, subject, body)


def active_admins(db: Session, excluding: int | None = None) -> int:
    query = db.query(models.User).filter(models.User.role == "admin", models.User.is_active.is_(True))
    if excluding is not None:
        query = query.filter(models.User.id != excluding)
    return query.count()


def change_role(db: Session, actor: models.User, user: models.User, role: str) -> None:
    """Cambia el rol y cierra sus sesiones. No hace commit."""
    if role == user.role:
        return
    if user.role == "admin" and active_admins(db, excluding=user.id) == 0:
        raise HTTPException(status_code=400, detail=LAST_ADMIN)
    audit.record(db, actor.id, "role_changed", target=user, old=audit.role_name(user.role), new=audit.role_name(role))
    user.role = role
    close_sessions(db, user.id)
