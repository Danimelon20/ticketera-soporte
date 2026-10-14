from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import config, mailer, models

STATUS_LABELS = {
    "new": "Nuevo",
    "in_progress": "En proceso",
    "resolved": "Resuelto",
    "closed": "Cerrado",
}

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
) -> None:
    # Solo título y las líneas del cambio: nunca teléfono ni cédula del ticket
    followers = (
        db.query(models.User)
        .join(models.TicketFollower, models.TicketFollower.user_id == models.User.id)
        .filter(
            models.TicketFollower.ticket_id == ticket.id,
            models.User.id != actor.id,
            models.User.is_active.is_(True),
        )
        .all()
    )
    for user in followers:
        body = "\n".join(
            [f"Hola {user.name}.", ""]
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
