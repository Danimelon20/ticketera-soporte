import hashlib
import re
from email.header import decode_header, make_header

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, hacienda, mailer, models
from .email_parser import extract_sender, get_plain_body, parse_ticket_email, strip_quoted
from .notifications import notify_followers

IGNORED_PRECEDENCE = ("bulk", "junk", "auto_reply")
AGENT_COMMENT = "Ticket creado por el Agente virtual a partir de un correo."
TICKET_REF_RE = re.compile(r"\[Ticket\s*#(\d+)\]", re.IGNORECASE)
BODY_MAX_LENGTH = 5000
# Errores que indican un correo escrito sin la plantilla
FREE_TEXT_ERRORS = ("Falta la línea Categoría", "La categoría", "Falta la línea Descripción", "La descripción")


class _RunNow:
    """Reemplaza BackgroundTasks fuera de una petición: ejecuta la tarea al momento."""

    def add_task(self, func, *args, **kwargs):
        func(*args, **kwargs)


def _message_id(msg) -> str:
    message_id = "".join((msg.get("Message-ID") or "").split())
    if message_id:
        return message_id
    raw = f"{msg.get('From', '')}{msg.get('Subject', '')}{msg.get('Date', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_automatic(msg) -> bool:
    auto_submitted = msg.get("Auto-Submitted")
    if auto_submitted is not None and auto_submitted.strip().lower() != "no":
        return True
    precedence = (msg.get("Precedence") or "").strip().lower()
    return precedence in IGNORED_PRECEDENCE


def _decode_subject(msg) -> str:
    raw = msg.get("Subject") or ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _template(category_names: list[str], priority_names: list[str]) -> str:
    return "\n".join([
        'Asunto: [Soporte] <título corto del problema>',
        f"Categoría: <una de: {', '.join(category_names)}>",
        f"Prioridad: <una de: {', '.join(priority_names)}; si no la pone queda Media>",
        "Teléfono: <8 dígitos>",
        "Cédula: <9 dígitos, o 11 o 12 si es DIMEX>",
        "Descripción: <qué pasa, desde cuándo y qué intentaste>",
    ])


def _save_result(db: Session, message_id: str, result: str) -> None:
    db.add(models.ProcessedEmail(message_id=message_id, result=result))


def send_ticket_created(ticket: models.Ticket, user: models.User, category: models.Category, priority: models.Priority) -> None:
    body = "\n".join([
        f"Buen día, {user.name}.",
        "",
        f"Recibimos su solicitud y creamos el ticket #{ticket.id}.",
        "",
        f"Título: {ticket.title}",
        f"Categoría: {category.name}",
        f"Prioridad: {priority.name}",
        "Estado: Nuevo",
        "",
        "Le avisaremos por este medio cuando haya cambios.",
        f"Puede ver el ticket en {config.APP_URL}",
    ])
    mailer.send_email(user.email, f"[Ticket #{ticket.id}] Recibimos su solicitud: {ticket.title}", body)


def _handle_ticket_reply(db: Session, msg, message_id: str, sender: str, subject: str) -> str | None:
    """Respuesta a "[Ticket #N]" del creador o de un seguidor. None: seguir el flujo normal."""
    match = TICKET_REF_RE.search(subject)
    if not match:
        return None
    user = (
        db.query(models.User)
        .filter(func.lower(models.User.email) == sender, models.User.is_active.is_(True))
        .first()
    )
    ticket = db.get(models.Ticket, int(match.group(1)))
    if user is None or ticket is None:
        return None
    is_follower = (
        db.query(models.TicketFollower)
        .filter(models.TicketFollower.ticket_id == ticket.id, models.TicketFollower.user_id == user.id)
        .first()
        is not None
    )
    if ticket.created_by != user.id and not is_follower:
        return None

    if ticket.status == "closed":
        _save_result(db, message_id, "closed_ticket")
        db.commit()
        mailer.send_email(
            sender,
            f"[Ticket #{ticket.id}] El ticket está cerrado",
            "\n".join([
                f"Buen día, {user.name}.",
                "",
                f"El ticket #{ticket.id} ya fue cerrado y no admite comentarios, por lo que su mensaje no se agregó.",
                "Si necesita más ayuda, por favor escriba un correo nuevo con \"[Soporte]\" en el asunto.",
            ]),
        )
        return "closed_ticket"

    content = strip_quoted(get_plain_body(msg))[:BODY_MAX_LENGTH]
    if not content:
        _save_result(db, message_id, "empty_reply")
        db.commit()
        return "empty_reply"

    db.add(models.Comment(ticket_id=ticket.id, user_id=user.id, content=content))
    _save_result(db, message_id, "comment_added")
    db.commit()
    notify_followers(
        db, _RunNow(), ticket, user,
        f"[Ticket #{ticket.id}] Nuevo comentario de {user.name}",
        [content],
    )
    return "comment_added"


def _update_waiting_contact(db: Session, msg, message_id: str, user: models.User, subject: str,
                            category_names: list[str], priority_names: list[str]) -> str | None:
    """Completa el teléfono o la cédula de un borrador que los espera. None: seguir el flujo normal."""
    draft = (
        db.query(models.EmailDraft)
        .filter(models.EmailDraft.requester_id == user.id, models.EmailDraft.status == "waiting_contact")
        .order_by(models.EmailDraft.id.desc())
        .first()
    )
    if draft is None:
        return None
    parsed = parse_ticket_email(subject, strip_quoted(get_plain_body(msg)), category_names, priority_names)
    phone = parsed["phone"]
    id_number = parsed["id_number"]
    if id_number and hacienda.check_id_number(id_number):
        id_number = None
    if not phone and not id_number:
        return None

    if phone:
        draft.contact_phone = phone
        if not user.phone:
            user.phone = phone
    if id_number:
        draft.contact_id_number = id_number
        if not user.id_number:
            user.id_number = id_number
    missing = [name for name, value in (("Teléfono", draft.contact_phone), ("Cédula", draft.contact_id_number)) if not value]
    if not missing:
        draft.status = "pending_review"
    _save_result(db, message_id, "contact_updated")
    db.commit()

    if missing:
        mailer.send_email(
            user.email,
            "[Soporte] Falta un dato de contacto",
            "\n".join([
                f"Buen día, {user.name}.",
                "",
                "Gracias, recibimos parte de sus datos de contacto.",
                f"Todavía falta: {missing[0]}. Por favor responda este correo con la línea:",
                "",
                f"{missing[0]}: ...",
            ]),
        )
    else:
        mailer.send_email(
            user.email,
            "[Soporte] Gracias, recibimos sus datos de contacto",
            "\n".join([
                f"Buen día, {user.name}.",
                "",
                "Gracias, recibimos sus datos de contacto.",
                "Un técnico está revisando su solicitud y recibirá el número de ticket cuando se confirme.",
            ]),
        )
    return "contact_updated"


def process_message(db: Session, msg) -> str:
    # a. Duplicados
    message_id = _message_id(msg)
    if db.query(models.ProcessedEmail).filter(models.ProcessedEmail.message_id == message_id).first():
        return "duplicate"

    # b. Correos que no se responden
    sender = extract_sender(msg.get("From"))
    if not sender or sender == config.SUPPORT_MAILBOX or _is_automatic(msg):
        _save_result(db, message_id, "ignored")
        db.commit()
        return "ignored"

    # Respuesta a un ticket existente (funciona aunque la IA esté apagada)
    subject = _decode_subject(msg)
    reply_result = _handle_ticket_reply(db, msg, message_id, sender, subject)
    if reply_result is not None:
        return reply_result

    # c. Solo solicitantes registrados y activos
    user = (
        db.query(models.User)
        .filter(func.lower(models.User.email) == sender, models.User.is_active.is_(True))
        .first()
    )
    if user is None or user.role != "requester":
        _save_result(db, message_id, "unregistered")
        db.commit()
        mailer.send_email(
            sender,
            "No pudimos crear su ticket",
            "Buen día. Recibimos su correo, pero esta dirección no está registrada como cliente "
            f"en la Ticketera de Soporte. Regístrese primero en {config.APP_URL} con este mismo "
            "correo y vuelva a enviar su solicitud.",
        )
        return "unregistered"

    # d. Leer el correo
    categories = db.query(models.Category).filter(models.Category.is_active.is_(True)).order_by(models.Category.id).all()
    priorities = db.query(models.Priority).order_by(models.Priority.level).all()
    category_names = [c.name for c in categories]
    priority_names = [p.name for p in priorities]
    body_text = get_plain_body(msg)
    parsed = parse_ticket_email(subject, body_text, category_names, priority_names)
    errors = list(parsed["errors"])
    free_text = any(e.startswith(FREE_TEXT_ERRORS) for e in errors)

    # Un correo sin plantilla puede traer los datos que esperaba un borrador
    if free_text:
        contact_result = _update_waiting_contact(db, msg, message_id, user, subject, category_names, priority_names)
        if contact_result is not None:
            return contact_result

    # e. Teléfono y cédula: del correo o del perfil
    phone = parsed["phone"] or user.phone
    id_number = parsed["id_number"] or user.id_number
    if not phone and not any(e.startswith("El teléfono") for e in errors):
        errors.append("Falta la línea Teléfono")
    if not id_number and not any(e.startswith("La cédula") for e in errors):
        errors.append("Falta la línea Cédula")
    hacienda_error = None
    if parsed["id_number"]:
        hacienda_error = hacienda.check_id_number(parsed["id_number"])
        if hacienda_error:
            errors.append(hacienda_error)

    # Sin plantilla y con la IA encendida: borrador que confirma una persona (todavía no se responde)
    if errors and free_text and config.AI_ANALYSIS_ENABLED:
        db.add(models.EmailDraft(
            message_id=message_id,
            requester_id=user.id,
            subject=subject[:300],
            # Sin el texto citado (por ejemplo, la respuesta a "Necesitamos más detalles")
            body=strip_quoted(body_text)[:BODY_MAX_LENGTH],
            status="analyzing",
            contact_phone=phone,
            contact_id_number=user.id_number if hacienda_error else id_number,
        ))
        _save_result(db, message_id, "draft_created")
        db.commit()
        return "draft_created"

    # f. Faltan datos: responder con la plantilla
    if errors:
        _save_result(db, message_id, "incomplete")
        db.commit()
        body = "\n".join(
            [f"Buen día, {user.name}.", "", "No pudimos crear su ticket porque:"]
            + [f"- {e}" for e in errors]
            + [
                "",
                "Por favor responda con un correo nuevo que tenga estas 6 líneas "
                "(ponga \"[Soporte]\" en el asunto):",
                "",
                _template(category_names, priority_names),
            ]
        )
        mailer.send_email(sender, "Faltan datos para crear su ticket", body)
        return "incomplete"

    # g. Crear el ticket
    category = next(c for c in categories if c.name == parsed["category"])
    priority = next(p for p in priorities if p.name == parsed["priority"])
    agent = db.query(models.User).filter(models.User.role == "agent").first()

    ticket = models.Ticket(
        title=parsed["title"],
        description=parsed["description"],
        category_id=category.id,
        priority_id=priority.id,
        status="new",
        created_by=user.id,
        contact_phone=phone,
        contact_id_number=id_number,
        source="email",
    )
    db.add(ticket)
    db.flush()

    if not user.phone:
        user.phone = phone
    if not user.id_number:
        user.id_number = id_number
    db.add(models.TicketFollower(ticket_id=ticket.id, user_id=user.id))
    # La IA lo analiza después, en la revisión del buzón (solo se muestra)
    db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="pending"))
    if agent is not None:
        db.add(models.Comment(ticket_id=ticket.id, user_id=agent.id, content=AGENT_COMMENT))
    # h. Todo en el mismo commit
    _save_result(db, message_id, "created")
    db.commit()

    send_ticket_created(ticket, user, category, priority)
    return "created"
