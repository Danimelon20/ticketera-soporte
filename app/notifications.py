from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import audit, mailer, models
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


FOLLOW_NOTICE = "Recibe este correo porque reportó este problema. Puede responderlo para agregar información."


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
            # Solo solicitantes: las filas antiguas del personal ("Dar seguimiento") ya no envían correo
            models.User.role == "requester",
        )
        .all()
    )
    # Solo quien reportó el ticket: los contactos ya no inician sesión, así que «puede verlo» es «es suyo».
    # Nunca el contacto de otro ticket (por ejemplo, de un incidente ligado)
    followers = [u for u in followers if u.id == ticket.created_by]
    for user in followers:
        with_files = bool(files) and user.id == ticket.created_by
        # Más de 20 MB no sale por correo: se le pide que los solicite (ya no hay portal donde verlos)
        linked = with_files and sum(len(data) for _, data, _ in files) > mailer.MAX_ATTACHMENTS_BYTES
        portal_lines = [
            "Los archivos adjuntos pesan más de 20 MB y no caben en el correo: responda este correo para pedirlos.",
        ] if linked else []
        body = "\n".join(
            [f"Hola {display_name(user)}.", ""]
            + lines
            + portal_lines
            + [
                "",
                f"Título: {ticket.title}",
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


def send_with_files(to: str, subject: str, body: str, files: list, ticket_id: int, actor_id: int, count: int) -> None:
    """Correo al solicitante con los adjuntos (o con el enlace, si files viene vacío). Si salió, queda en el registro."""
    if not mailer.send_email(to, subject, body, attachments=files or None):
        return
    db = SessionLocal()
    try:
        value = f"{count} adjunto(s)" if files else f"aviso de adjuntos de más de 20 MB ({count} adjunto(s))"
        audit.record(db, actor_id, "email_attachments_sent", target_label=f"Ticket #{ticket_id}", new=value)
        db.commit()
    finally:
        db.close()
