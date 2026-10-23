"""Adjuntos de los correos que llegan: qué se toma del MIME y dónde queda.

Misma validación que la web (attachments.validate). Nunca registrar en logs el nombre original ni el
contenido de un adjunto.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import attachments, audit, comments as internal_comments, models

# Imágenes incrustadas de este tamaño o menos: logos de firma, no se toman
INLINE_MIN_BYTES = 30 * 1024
CLOUD_RE = re.compile(r"https?://[^\s\"'<>]*(?:sharepoint\.com|onedrive\.live\.com|1drv\.ms)", re.IGNORECASE)
CLOUD_NOTE = "El correo traía un enlace a un archivo en la nube"
# Correos adjuntos dentro del correo: se descartan sin nota y no se recorren
ATTACHED_EMAIL_TYPES = ("message/rfc822", "application/vnd.ms-outlook")
ATTACHED_EMAIL_EXTENSIONS = (".eml", ".msg")
# Borradores que nadie confirma ni descarta a mano: sus archivos se borran después de STALE_DAYS
STALE_DRAFT_STATUSES = ("not_support", "status_reply", "unclear")
STALE_DAYS = 30
NOTE_MAX_LENGTH = 300


@dataclass
class Extracted:
    files: list = field(default_factory=list)  # (nombre, contenido, Content-Type), ya validados
    notes: list = field(default_factory=list)
    rejected: int = 0


def _is_attached_email(part) -> bool:
    name = (part.get_filename() or "").lower()
    return part.get_content_type() in ATTACHED_EMAIL_TYPES or name.endswith(ATTACHED_EMAIL_EXTENSIONS)


def _leaves(part):
    """Partes finales del correo, sin entrar en los correos adjuntos."""
    if _is_attached_email(part):
        return
    if part.is_multipart():
        for sub in part.get_payload():
            yield from _leaves(sub)
    else:
        yield part


def _text(part) -> str:
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def extract(msg) -> Extracted:
    result = Extracted()
    valid = []
    cloud = False
    unnamed = 0
    for part in _leaves(msg):
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if part.get_content_type() in ("text/plain", "text/html") and not filename and disposition != "attachment":
            # Cuerpo del correo: no es adjunto, pero puede traer enlaces a OneDrive o SharePoint
            cloud = cloud or bool(CLOUD_RE.search(_text(part)))
            continue
        is_image = part.get_content_maintype() == "image"
        embedded = is_image and disposition != "attachment" and (disposition == "inline" or part.get("Content-ID"))
        data = part.get_payload(decode=True) or b""
        if embedded:
            if len(data) <= INLINE_MIN_BYTES:
                continue
        elif disposition != "attachment" and not filename:
            continue
        if not filename:
            unnamed += 1
            subtype = part.get_content_subtype()
            filename = f"imagen-{unnamed}.{'jpg' if subtype == 'jpeg' else subtype}" if is_image else f"archivo-{unnamed}"
        name = attachments.clean_name(filename)
        try:
            content_type = attachments.validate(name, data)
        except attachments.Rejected as exc:
            result.rejected += 1
            result.notes.append(f"No se adjuntó «{name}»: {exc.reason}.")
            continue
        valid.append((name, data, content_type))
    result.files = valid[:attachments.MAX_FILES]
    left_out = len(valid) - attachments.MAX_FILES
    if left_out > 0:
        result.notes.append(
            f"Quedaron fuera {left_out} archivo(s): se toman como máximo {attachments.MAX_FILES} por correo."
        )
    if cloud:
        result.notes.append(CLOUD_NOTE)
    return result


def _record_received(db: Session, label: str, extracted: Extracted, saved: int) -> None:
    if not saved and not extracted.rejected:
        return
    value = f"{saved} adjunto(s)"
    if extracted.rejected:
        value += f", {extracted.rejected} rechazado(s)"
    audit.record(db, audit.agent_id(db), "email_attachments_received", target_label=label, new=value)


def _internal_note(db: Session, ticket_id: int, notes: list[str]) -> None:
    agent_id = audit.agent_id(db)
    if notes and agent_id is not None:
        internal_comments.add_internal(db, ticket_id, agent_id, "\n".join(["Adjuntos del correo:"] + notes))


def save_to_draft(db: Session, draft: models.EmailDraft, extracted: Extracted) -> list[str]:
    """Archivos y notas al borrador. No hace commit. Devuelve los nombres en disco (para limpiar si falla)."""
    db.flush()
    stored = []
    try:
        for name, data, content_type in extracted.files:
            stored_name, sha256 = attachments.store_file(data)
            stored.append(stored_name)
            db.add(models.DraftAttachment(
                draft_id=draft.id, original_name=name, content_type=content_type,
                size=len(data), sha256=sha256, stored_name=stored_name,
                received_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
    except Exception:
        attachments.remove_files(stored)
        raise
    for note in extracted.notes:
        db.add(models.DraftAttachmentNote(draft_id=draft.id, text=note[:NOTE_MAX_LENGTH]))
    _record_received(db, f"Borrador #{draft.id}", extracted, len(stored))
    return stored


def save_to_comment(db: Session, comment: models.Comment, user_id: int, extracted: Extracted) -> list[str]:
    """Archivos al comentario; lo rechazado, en una nota interna del Agente virtual. No hace commit."""
    db.flush()
    rows = attachments.save(db, comment, user_id, extracted.files) if extracted.files else []
    _internal_note(db, comment.ticket_id, extracted.notes)
    _record_received(db, f"Ticket #{comment.ticket_id}", extracted, len(rows))
    return [row.stored_name for row in rows]


def first_message(db: Session, ticket: models.Ticket, requester_id: int) -> models.Comment:
    """Primer mensaje del solicitante, sin texto: lleva los adjuntos del correo original."""
    comment = models.Comment(ticket_id=ticket.id, user_id=requester_id, content="")
    db.add(comment)
    db.flush()
    return comment


def save_to_new_ticket(db: Session, ticket: models.Ticket, requester_id: int, extracted: Extracted) -> list[str]:
    """Ticket creado directo desde un correo. No hace commit."""
    if extracted.files:
        return save_to_comment(db, first_message(db, ticket, requester_id), requester_id, extracted)
    _internal_note(db, ticket.id, extracted.notes)
    _record_received(db, f"Ticket #{ticket.id}", extracted, 0)
    return []


def commit_or_cleanup(db: Session, stored: list[str]) -> None:
    try:
        db.commit()
    except Exception:
        attachments.remove_files(stored)
        raise


def move_to_ticket(db: Session, draft: models.EmailDraft, ticket: models.Ticket,
                   comment: models.Comment | None = None) -> None:
    """Al confirmar: los archivos del borrador pasan al primer mensaje del ticket (mismo archivo en disco).

    comment: otro comentario que los lleva (un borrador agregado a un ticket abierto).
    Las notas quedan en una nota interna del Agente virtual. No hace commit.
    """
    rows = db.query(models.DraftAttachment).filter(models.DraftAttachment.draft_id == draft.id).order_by(models.DraftAttachment.id).all()
    if rows:
        comment = comment or first_message(db, ticket, draft.requester_id)
        for row in rows:
            db.add(models.Attachment(
                ticket_id=ticket.id, comment_id=comment.id, original_name=row.original_name,
                content_type=row.content_type, size=row.size, sha256=row.sha256,
                stored_name=row.stored_name, uploaded_by=draft.requester_id, uploaded_at=row.received_at,
            ))
            db.delete(row)
        db.flush()
    notes = db.query(models.DraftAttachmentNote).filter(models.DraftAttachmentNote.draft_id == draft.id).order_by(models.DraftAttachmentNote.id).all()
    _internal_note(db, ticket.id, [note.text for note in notes])


def remove_from_draft(db: Session, draft_id: int) -> list[str]:
    """Quita las filas de los archivos del borrador y devuelve sus nombres en disco. No hace commit ni borra archivos."""
    rows = db.query(models.DraftAttachment).filter(models.DraftAttachment.draft_id == draft_id).all()
    for row in rows:
        db.delete(row)
    return [row.stored_name for row in rows]


def purge_stale_drafts(db: Session, now: datetime | None = None) -> int:
    """Borra del disco los archivos de los borradores que nadie revisa, STALE_DAYS después de recibidos."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    drafts = (
        db.query(models.EmailDraft.id)
        .join(models.DraftAttachment, models.DraftAttachment.draft_id == models.EmailDraft.id)
        .filter(
            models.EmailDraft.status.in_(STALE_DRAFT_STATUSES),
            models.EmailDraft.created_at < now - timedelta(days=STALE_DAYS),
        )
        .distinct()
        .all()
    )
    stored = []
    for (draft_id,) in drafts:
        stored += remove_from_draft(db, draft_id)
    db.commit()
    attachments.remove_files(stored)
    return len(stored)
