from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import config, mailer, models

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
) -> None:
    # Solo título y las líneas del cambio: nunca teléfono ni cédula del ticket.
    # exclude_ids: quien ya recibe otro correo por el mismo cambio (un solo correo por persona)
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
    for user in followers:
        body = "\n".join(
            [f"Hola {display_name(user)}.", ""]
            + lines
            + [
                "",
                f"Título: {ticket.title}",
                f"Puedes ver el ticket en {config.APP_URL}",
                "",
                FOLLOW_NOTICE,
            ]
        )
        background_tasks.add_task(mailer.send_email, user.email, subject, body)
