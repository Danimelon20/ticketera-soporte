import hashlib
import re
import logging
from email.header import decode_header, make_header
from email.utils import parseaddr

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, email_attachments, email_auth, identity, mailer, models
from .email_parser import extract_sender, get_plain_body, parse_ticket_email, strip_quoted
from .security import mask_email
from .validators import is_internal_email
from .notifications import display_name, notify_followers

IGNORED_PRECEDENCE = ("bulk", "junk", "auto_reply")
AGENT_COMMENT = "Ticket creado por el Agente virtual a partir de un correo."
TICKET_REF_RE = re.compile(r"\[Ticket\s*#(\d+)\]", re.IGNORECASE)
BODY_MAX_LENGTH = 5000
# Errores que indican un correo escrito sin la plantilla
FREE_TEXT_ERRORS = ("Falta la línea Categoría", "La categoría", "Falta la línea Descripción", "La descripción")
# Sin portal: solo el personal de la empresa reporta por correo (INTERNAL_DOMAINS)
NOT_INTERNAL_SUBJECT = "No pudimos crear su ticket"
NOT_INTERNAL_BODY = ("Buen día. Recibimos su correo, pero este buzón es solo para el personal de la empresa. "
                     "Si necesita ayuda, comuníquese con su contacto habitual.")
STAFF_SENDER_BODY = ("Buen día. Recibimos su correo, pero esta dirección es de una cuenta del personal de la "
                     "Ticketera. Para reportar un problema, cree el ticket desde la Ticketera (Nuevo ticket).")
# Contraseña inutilizable: un contacto nunca inicia sesión (verify_password siempre da False)
NO_PASSWORD = "!"


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
        "Teléfono: <8 dígitos; opcional>",
        "Descripción: <qué pasa, desde cuándo y qué intentaste>",
    ])


def _save_result(db: Session, message_id: str, result: str) -> None:
    db.add(models.ProcessedEmail(message_id=message_id, result=result))


def send_ticket_created(ticket: models.Ticket, user: models.User, category: models.Category, priority: models.Priority) -> None:
    body = "\n".join([
        f"Buen día, {display_name(user)}.",
        "",
        f"Recibimos su solicitud y creamos el ticket #{ticket.id}.",
        "",
        f"Título: {ticket.title}",
        f"Categoría: {category.name}",
        f"Prioridad: {priority.name}",
        "Estado: Nuevo",
        "",
        "Le avisaremos por este medio cuando haya cambios. Puede responder este correo para agregar información.",
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
                f"Buen día, {display_name(user)}.",
                "",
                f"El ticket #{ticket.id} ya fue cerrado y no admite comentarios, por lo que su mensaje no se agregó.",
                "Si necesita más ayuda, por favor escriba un correo nuevo con \"[Soporte]\" en el asunto.",
            ]),
        )
        return "closed_ticket"

    content = strip_quoted(get_plain_body(msg))[:BODY_MAX_LENGTH]
    extracted = email_attachments.extract(msg)
    if not content and not extracted.files:
        _save_result(db, message_id, "empty_reply")
        db.commit()
        return "empty_reply"

    comment = models.Comment(ticket_id=ticket.id, user_id=user.id, content=content)
    db.add(comment)
    # Los adjuntos van al mismo comentario; lo rechazado, a una nota interna
    stored = email_attachments.save_to_comment(db, comment, user.id, extracted)
    _save_result(db, message_id, "comment_added")
    email_attachments.commit_or_cleanup(db, stored)
    lines = [content] if content else []
    if extracted.files:
        lines.append(f"Adjuntó {len(extracted.files)} archivo(s).")
    notify_followers(
        db, _RunNow(), ticket, user,
        f"[Ticket #{ticket.id}] Nuevo comentario de {display_name(user)}",
        lines,
    )
    return "comment_added"


def mark_pending(db: Session, draft: models.EmailDraft, reason: str) -> None:
    """Deja anotado por qué el borrador necesita revisión. No hace commit."""
    db.flush()
    row = db.query(models.DraftPendingReason).filter(models.DraftPendingReason.draft_id == draft.id).first()
    if row is None:
        db.add(models.DraftPendingReason(draft_id=draft.id, reason=reason))
    else:
        row.reason = reason


def _new_contact(db: Session, msg, sender: str) -> models.User:
    """Primera vez que escribe alguien de la empresa: su ficha de contacto, sin contraseña ni acceso. No hace
    commit."""
    name = " ".join(parseaddr(str(msg.get("From") or ""))[0].split())[:100]
    user = models.User(name=name, email=sender, role="requester", password_hash=NO_PASSWORD)
    db.add(user)
    db.flush()
    logging.info("Contacto nuevo %s creado desde un correo", user.id)
    return user


def process_message(db: Session, msg, verify_sender: bool = True) -> str:
    """verify_sender=False solo para scripts.simulate_email (no pasa por Exchange: no trae sus marcas)."""
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

    # El «De:» se puede falsificar: sin las marcas de autenticidad de Exchange no se usa ni se responde
    if verify_sender and not email_auth.sender_verified(msg, sender):
        _save_result(db, message_id, "unverified_sender")
        db.commit()
        logging.warning("Correo de %s sin marcas de autenticidad: no se procesó", mask_email(sender))
        return "unverified_sender"

    # Respuesta a un ticket existente (funciona aunque la IA esté apagada)
    subject = _decode_subject(msg)
    reply_result = _handle_ticket_reply(db, msg, message_id, sender, subject)
    if reply_result is not None:
        return reply_result

    # c. Solo el personal de la empresa (INTERNAL_DOMAINS): la primera vez se crea su contacto
    user = db.query(models.User).filter(func.lower(models.User.email) == sender).first()
    if user is not None and user.is_active and user.role in ("technician", "coordinator", "admin"):
        _save_result(db, message_id, "staff_sender")
        db.commit()
        mailer.send_email(sender, NOT_INTERNAL_SUBJECT, STAFF_SENDER_BODY)
        return "staff_sender"
    if user is None and is_internal_email(sender):
        user = _new_contact(db, msg, sender)
    # Un cliente archivado (otro dominio) ya no abre temas nuevos; sus respuestas a un ticket sí entran (arriba)
    if user is None or not user.is_active or user.role != "requester" or not is_internal_email(user.email):
        db.rollback()
        _save_result(db, message_id, "unregistered")
        db.commit()
        mailer.send_email(sender, NOT_INTERNAL_SUBJECT, NOT_INTERNAL_BODY)
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

    # e. Teléfono opcional (del correo o el que ya tenía); la cédula ya no se pide al personal
    phone = parsed["phone"] or user.phone
    id_number = user.id_number

    # Sin plantilla y con la IA encendida: borrador que confirma una persona (todavía no se responde)
    if errors and free_text and config.AI_ANALYSIS_ENABLED:
        draft = models.EmailDraft(
            message_id=message_id,
            requester_id=user.id,
            subject=subject[:300],
            # Sin el texto citado (por ejemplo, la respuesta a "Necesitamos más detalles")
            body=strip_quoted(body_text)[:BODY_MAX_LENGTH],
            status="analyzing",
            contact_phone=phone,
            contact_id_number=id_number,
        )
        db.add(draft)
        stored = email_attachments.save_to_draft(db, draft, email_attachments.extract(msg))
        _save_result(db, message_id, "draft_created")
        email_attachments.commit_or_cleanup(db, stored)
        # Mismo cliente con el mismo problema abierto: se agrega a ese ticket (solo con la agrupación activa)
        from . import grouping
        grouping.after_draft_created(draft.id)
        return "draft_created"

    # f. Faltan datos: responder con la plantilla
    if errors:
        _save_result(db, message_id, "incomplete")
        db.commit()
        body = "\n".join(
            [f"Buen día, {display_name(user)}.", "", "No pudimos crear su ticket porque:"]
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
    # El teléfono que trajo queda en su ficha si no tenía
    if phone and not user.phone:
        user.phone = phone
    db.add(ticket)
    db.flush()
    identity.lock(db, user)
    db.add(models.TicketFollower(ticket_id=ticket.id, user_id=user.id))
    # La IA lo analiza después, en la revisión del buzón (solo se muestra)
    db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="pending"))
    # Los adjuntos del correo, en el primer mensaje del solicitante (antes del comentario del Agente)
    stored = email_attachments.save_to_new_ticket(db, ticket, user.id, email_attachments.extract(msg))
    if agent is not None:
        db.add(models.Comment(ticket_id=ticket.id, user_id=agent.id, content=AGENT_COMMENT))
    # h. Todo en el mismo commit
    _save_result(db, message_id, "created")
    email_attachments.commit_or_cleanup(db, stored)

    # Si se fusionó en su ticket abierto, ya le llegó ese aviso: ni "Recibimos su solicitud" ni asignación
    from . import grouping
    if grouping.after_ticket_created(ticket.id) == "merged":
        return "created"
    send_ticket_created(ticket, user, category, priority)
    # Con la IA se asigna al terminar su análisis (analyze_pending); sin ella, ya mismo
    if not config.AI_ANALYSIS_ENABLED:
        from . import auto_assign
        auto_assign.assign_new(ticket.id)
    return "created"
