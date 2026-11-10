"""Gestión de cuentas por rol: quién puede desactivar, restablecer o desbloquear a quién."""
import logging

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from . import audit, comments, config, daily_summary, mailer, models

NO_PERMISSION = "No tienes permiso para gestionar esta cuenta"
SELF_CHANGE = "No puedes cambiar tu propio rol ni desactivarte"
AGENT_LOCKED = "El Agente virtual no se puede modificar"
LAST_ADMIN = "Debe quedar al menos un administrador activo"
STAFF_USE_MICROSOFT = "El personal entra con su cuenta Microsoft"
NO_ADMIN_ROLE = "El rol de administrador no se asigna desde la página: solo con python -m app.create_admin"
RELEASED_COMMENT = "Técnico desactivado; ticket liberado"
RELEASED_LIST_MAX = 10
OPEN_STATUSES = ("new", "in_progress")
# Los únicos roles entre los que el administrador cambia a alguien (técnico ↔ coordinador)
STAFF_ROLE_CHOICES = ("technician", "coordinator")

# Roles de las cuentas que cada rol puede gestionar, por acción. Ningún administrador actúa sobre otro
# administrador (el primero y los siguientes se crean con app.create_admin)
PERMISSIONS = {
    "admin": {
        "active": ("coordinator", "technician", "requester"),
        "reset": ("coordinator", "technician"),
        "unlock": ("coordinator", "technician", "requester"),
        # Vacaciones e incapacidades (la disponibilidad en sí es automática)
        "availability": ("technician", "coordinator"),
        # Horario del coordinador: informativo (la asignación automática solo usa técnicos)
        "schedule": ("technician", "coordinator"),
        "sessions": ("coordinator", "technician", "requester"),
        # Cambio directo de rol: técnico ↔ coordinador (nunca a administrador)
        "role": STAFF_ROLE_CHOICES,
    },
    # Desactivar, restablecer, desbloquear y cerrar sesiones: solo el administrador (cuida las cuentas).
    # El coordinador maneja turnos y disponibilidad de los técnicos
    "coordinator": {
        "availability": ("technician",),
        "schedule": ("technician",),
        # Solo como solicitud con aprobación del administrador
        "role": ("technician", "requester"),
    },
}


def check_can_manage(actor: models.User, target: models.User, action: str, new_role: str | None = None) -> None:
    """403 si el actor no puede hacer esa acción sobre la cuenta (nunca sobre sí mismo).
    new_role: con action="role", el rol pedido; ninguna pantalla asigna el de administrador."""
    if action == "role" and new_role == "admin":
        raise HTTPException(status_code=403, detail=NO_ADMIN_ROLE)
    if target.role == "agent":
        raise HTTPException(status_code=403, detail=AGENT_LOCKED)
    if target.id == actor.id:
        raise HTTPException(status_code=403, detail=SELF_CHANGE)
    if target.role not in PERMISSIONS.get(actor.role, {}).get(action, ()):
        raise HTTPException(status_code=403, detail=NO_PERMISSION)
    # Con Microsoft, técnicos y coordinadores no tienen contraseña que restablecer
    if action == "reset" and config.MS_LOGIN_ENABLED and target.role in ("technician", "coordinator"):
        raise HTTPException(status_code=403, detail=STAFF_USE_MICROSOFT)


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


MANY_ADMINS = "Hay %d administradores activos y solo debe haber uno: corríjalo con python -m scripts.admin_tools list-admins y demote"


def warn_if_many_admins(db: Session) -> int:
    """Bases anteriores a la regla de un solo administrador: avisa en el log, sin cambiar a nadie."""
    count = active_admins(db)
    if count > 1:
        logging.warning(MANY_ADMINS, count)
    return count


def change_role(db: Session, actor: models.User, user: models.User, role: str) -> None:
    """Cambia el rol y cierra sus sesiones. No hace commit."""
    if role == user.role:
        return
    # Segunda defensa: el administrador solo se crea con app.create_admin
    if role == "admin":
        raise HTTPException(status_code=403, detail=NO_ADMIN_ROLE)
    if user.role == "admin" and active_admins(db, excluding=user.id) == 0:
        raise HTTPException(status_code=400, detail=LAST_ADMIN)
    audit.record(db, actor.id, "role_changed", target=user, old=audit.role_name(user.role), new=audit.role_name(role))
    user.role = role
    close_sessions(db, user.id)
