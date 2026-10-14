import hashlib
from email.header import decode_header, make_header

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, hacienda, mailer, models
from .email_parser import extract_sender, get_plain_body, parse_ticket_email

IGNORED_PRECEDENCE = ("bulk", "junk", "auto_reply")
AGENT_COMMENT = "Ticket creado por el Agente virtual a partir de un correo."


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
        f"Prioridad: <una de: {', '.join(priority_names)}; si no la pones queda Media>",
        "Teléfono: <8 dígitos>",
        "Cédula: <9 dígitos, o 11 o 12 si es DIMEX>",
        "Descripción: <qué pasa, desde cuándo y qué intentaste>",
    ])


def _save_result(db: Session, message_id: str, result: str) -> None:
    db.add(models.ProcessedEmail(message_id=message_id, result=result))


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
            "No pudimos crear tu ticket",
            "Hola. Recibimos tu correo, pero esta dirección no está registrada como cliente "
            f"en la Ticketera de Soporte. Regístrate primero en {config.APP_URL} con este mismo "
            "correo y vuelve a enviar tu solicitud.",
        )
        return "unregistered"

    # d. Leer el correo
    categories = db.query(models.Category).filter(models.Category.is_active.is_(True)).order_by(models.Category.id).all()
    priorities = db.query(models.Priority).order_by(models.Priority.level).all()
    category_names = [c.name for c in categories]
    priority_names = [p.name for p in priorities]
    parsed = parse_ticket_email(_decode_subject(msg), get_plain_body(msg), category_names, priority_names)
    errors = list(parsed["errors"])

    # e. Teléfono y cédula: del correo o del perfil
    phone = parsed["phone"] or user.phone
    id_number = parsed["id_number"] or user.id_number
    if not phone and not any(e.startswith("El teléfono") for e in errors):
        errors.append("Falta la línea Teléfono")
    if not id_number and not any(e.startswith("La cédula") for e in errors):
        errors.append("Falta la línea Cédula")
    if parsed["id_number"]:
        hacienda_error = hacienda.check_id_number(parsed["id_number"])
        if hacienda_error:
            errors.append(hacienda_error)

    # f. Faltan datos: responder con la plantilla
    if errors:
        _save_result(db, message_id, "incomplete")
        db.commit()
        body = "\n".join(
            [f"Hola, {user.name}.", "", "No pudimos crear tu ticket porque:"]
            + [f"- {e}" for e in errors]
            + [
                "",
                "Responde con un correo nuevo que tenga estas 6 líneas "
                "(pon \"[Soporte]\" en el asunto):",
                "",
                _template(category_names, priority_names),
            ]
        )
        mailer.send_email(sender, "Faltan datos para crear tu ticket", body)
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

    body = "\n".join([
        f"Hola, {user.name}.",
        "",
        f"Recibimos tu solicitud y creamos el ticket #{ticket.id}.",
        "",
        f"Título: {ticket.title}",
        f"Categoría: {category.name}",
        f"Prioridad: {priority.name}",
        "Estado: Nuevo",
        "",
        "Te avisaremos por este medio cuando haya cambios.",
        f"Puedes ver el ticket en {config.APP_URL}",
    ])
    mailer.send_email(sender, f"[Ticket #{ticket.id}] Recibimos tu solicitud: {ticket.title}", body)
    return "created"
