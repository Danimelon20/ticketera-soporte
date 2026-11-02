"""Aviso por correo al técnico cuando se le asigna un ticket (a mano o automáticamente).

Solo número, título (enmascarado), nivel y el enlace a la página: nunca cédula, teléfono, descripción
ni comentarios internos.
"""
import logging

from . import config, mailer, models
from .chatbot import _redact
from .daily_summary import _level, _level_text
from .security import mask_email


def should_notify(assignee: models.User | None, actor: models.User | None) -> bool:
    """Solo técnicos activos, y nunca a quien se asignó a sí mismo."""
    return (
        assignee is not None
        and assignee.role == "technician"
        and assignee.is_active
        and (actor is None or actor.id != assignee.id)
    )


PENDING_LEVEL = "pendiente de análisis"


def level_line(ticket: models.Ticket) -> str:
    """Asignado antes de que la IA termine (p. ej. a nombre de un cliente): nunca vacío."""
    level = _level(ticket)
    row = ticket.ai_analysis
    if level is None and config.AI_ANALYSIS_ENABLED and (row is None or row.status == "pending"):
        return f"Nivel: {PENDING_LEVEL}"
    return f"Nivel: {_level_text(level)}"


def build(ticket: models.Ticket, assignee: models.User) -> tuple[str, str]:
    title = _redact(ticket.title)
    subject = f"[Ticket #{ticket.id}] Se le asignó: {title}"
    body = "\n".join([
        f"Buen día, {assignee.name or 'técnico'}.",
        "",
        "Se le asignó este ticket:",
        "",
        f"Número: #{ticket.id}",
        f"Título: {title}",
        level_line(ticket),
        "",
        f"Puede verlo en {config.APP_URL}",
    ])
    return subject, body


def deliver(to: str, subject: str, body: str, ticket_id: int) -> bool:
    """Envía un aviso ya armado (puede correr después de cerrar la sesión de la base)."""
    sent = mailer.send_email(to, subject, body)
    if config.MAIL_TRANSPORT == "log":
        # Sin correo real: solo número y destinatario enmascarado, nunca el contenido
        logging.warning("[SOLO DESARROLLO] Aviso de asignación del ticket #%s para %s", ticket_id, mask_email(to))
    return sent


def schedule(background_tasks, ticket: models.Ticket, assignee: models.User) -> None:
    """Arma el aviso ahora (con la sesión abierta) y lo envía con background_tasks."""
    subject, body = build(ticket, assignee)
    background_tasks.add_task(deliver, assignee.email, subject, body, ticket.id)
