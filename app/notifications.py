from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import audit, config, mailer, models
from .database import SessionLocal

STATUS_LABELS = {
    "new": "Nuevo",
    "in_progress": "En proceso",
    "resolved": "Resuelto",
    "closed": "Cerrado",
}

def display_name(user: models.User) -> str:
    """Nombre del perfil o, si la cuenta todavía no lo completó, su correo."""
    return user.name or user.email


FOLLOW_NOTICE = (
    "Recibes este correo porque das seguimiento a este ticket. "
    "Puedes desactivarlo desde la página del ticket."
)


def notify_followers(
    db: Session,
    background_tasks: BackgroundTasks,
    ticket: models.Ticket,
    actor: models.User,
    subject: str,
    lines: list[str],
    exclude_ids: tuple[int, ...] = (),
    files=(),
) -> None:
    # Solo título y las líneas del cambio: nunca teléfono ni cédula del ticket.
    # exclude_ids: quien ya recibe otro correo por el mismo cambio (un solo correo por persona)
    # files: adjuntos de una respuesta al cliente (nombre, contenido, tipo). Solo el solicitante los recibe;
    # los demás seguidores ven "Adjuntó N archivo(s)." entre las líneas.
    followers = (
        db.query(models.User)
        .join(models.TicketFollower, models.TicketFollower.user_id == models.User.id)
        .filter(
            models.TicketFollower.ticket_id == ticket.id,
            models.User.id != actor.id,
            models.User.id.notin_(exclude_ids),
            models.User.is_active.is_(True),
        )
        .all()
    )
    # Solo quien puede ver el ticket: nunca el administrador ni un técnico al que ya no le corresponde
    from . import ticket_access
    visible = ticket_access.visible_user_ids(db, ticket, [u.id for u in followers])
    followers = [u for u in followers if u.id in visible]
    for user in followers:
        with_files = bool(files) and user.id == ticket.created_by
        # Más de 20 MB no sale por correo: va el enlace al ticket en el portal
        linked = with_files and sum(len(data) for _, data, _ in files) > mailer.MAX_ATTACHMENTS_BYTES
        portal_lines = [
            "Los archivos adjuntos están disponibles en el portal de soporte.",
            ticket_link(ticket.id),
        ] if linked else []
        body = "\n".join(
            [f"Hola {display_name(user)}.", ""]
            + lines
            + portal_lines
            + [
                "",
                f"Título: {ticket.title}",
                f"Puedes ver el ticket en {config.APP_URL}",
                "",
                FOLLOW_NOTICE,
            ]
        )
        if with_files:
            background_tasks.add_task(
                send_with_files, user.email, subject, body, [] if linked else list(files), ticket.id, actor.id, len(files)
            )
        else:
            background_tasks.add_task(mailer.send_email, user.email, subject, body)


def ticket_link(ticket_id: int) -> str:
    return f"{config.APP_URL}/#ticket/{ticket_id}"


def send_with_files(to: str, subject: str, body: str, files: list, ticket_id: int, actor_id: int, count: int) -> None:
    """Correo al solicitante con los adjuntos (o con el enlace, si files viene vacío). Si salió, queda en el registro."""
    if not mailer.send_email(to, subject, body, attachments=files or None):
        return
    db = SessionLocal()
    try:
        value = f"{count} adjunto(s)" if files else f"enlace al portal ({count} adjunto(s))"
        audit.record(db, actor_id, "email_attachments_sent", target_label=f"Ticket #{ticket_id}", new=value)
        db.commit()
    finally:
        db.close()
