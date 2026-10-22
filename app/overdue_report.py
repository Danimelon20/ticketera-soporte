"""Reporte de vencimiento de un ticket: datos para la pantalla y el PDF.

Permisos: coordinador y administrador, cualquier ticket; técnico, solo el que tiene asignado.
En la pantalla, teléfono y cédula con el mismo enmascarado del detalle; en el PDF, siempre
enmascarados (el archivo puede salir del sistema). Nunca comentarios: solo eventos.
"""
import os
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import comments, models, nav, portal
from .chatbot import _redact
from .daily_summary import _local
from .notifications import STATUS_LABELS
from .validators import mask_id_number, mask_last4

CAUSE_ROLES = ("coordinator", "admin")
NOT_ALLOWED = "No tienes permiso para ver el reporte de este ticket"
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "img", "american-data.png")
LOGO_TEXT = "American Data Networks · Ticketera de soporte"
SOURCE_NAMES = {"web": "Web", "email": "Correo"}
RESPONDER_ROLES = ("technician", "coordinator", "admin")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _name(user: models.User | None) -> str | None:
    # Nunca el correo: el reporte puede verlo otra persona
    return (user.name or "Sin nombre") if user else None


def check_can_view(user: models.User, ticket: models.Ticket) -> None:
    if user.role in CAUSE_ROLES:
        return
    if user.role == "technician" and ticket.assigned_to == user.id:
        return
    raise HTTPException(status_code=403, detail=NOT_ALLOWED)


def _closed_at(db: Session, ticket: models.Ticket) -> datetime | None:
    """Cuándo dejó de estar abierto por primera vez (resuelto, cerrado o cancelado)."""
    row = (
        db.query(models.History.changed_at)
        .filter(
            models.History.ticket_id == ticket.id,
            (models.History.field == "cancelled")
            | ((models.History.field == "status") & models.History.new_value.in_(("resolved", "closed"))),
        )
        .order_by(models.History.changed_at)
        .first()
    )
    return row[0] if row else None


def was_overdue(db: Session, ticket: models.Ticket, now: datetime) -> bool:
    due = nav._due_at(ticket)
    if due is None:
        return False
    if nav.is_overdue(ticket, now):
        return True
    notice = db.query(models.TicketDueNotice.id).filter(
        models.TicketDueNotice.ticket_id == ticket.id, models.TicketDueNotice.kind == "overdue"
    ).first()
    if notice:
        return True
    closed = _closed_at(db, ticket)
    return closed is not None and closed > due


def _first_response(db: Session, ticket: models.Ticket) -> models.Comment | None:
    """Primer comentario público del personal (no del cliente, del Agente virtual ni notas internas)."""
    query = (
        db.query(models.Comment)
        .join(models.User, models.User.id == models.Comment.user_id)
        .filter(models.Comment.ticket_id == ticket.id, models.User.role.in_(RESPONDER_ROLES))
    )
    return comments.not_internal(query).order_by(models.Comment.created_at, models.Comment.id).first()


def _timeline(db: Session, ticket: models.Ticket, first: models.Comment | None, due: datetime) -> list[dict]:
    users = {}

    def who(user_id):
        if user_id not in users:
            users[user_id] = db.get(models.User, user_id)
        return users[user_id]

    auto_ids = {
        row[0] for row in db.query(models.TicketAutoAssignment.history_id)
        .filter(models.TicketAutoAssignment.ticket_id == ticket.id, models.TicketAutoAssignment.history_id.isnot(None))
    }
    creator = who(ticket.created_by)
    events = [{"at": ticket.created_at, "kind": "created", "actor": _name(creator), "text": "Ticket creado"}]
    history = (
        db.query(models.History).filter(models.History.ticket_id == ticket.id)
        .order_by(models.History.changed_at, models.History.id).all()
    )
    for entry in history:
        actor = _name(who(entry.changed_by))
        if entry.field == "assigned_to":
            auto = entry.id in auto_ids
            if entry.new_value:
                target = _name(who(int(entry.new_value)))
                verb = "Asignado" if entry.old_value is None else "Reasignado"
                text = f"{verb} a {target}" + (" (automático)" if auto else "")
            else:
                text = "Quedó sin responsable"
            events.append({"at": entry.changed_at, "kind": "assigned", "actor": actor, "text": text, "auto": auto})
        elif entry.field == "status":
            old = STATUS_LABELS.get(entry.old_value, entry.old_value or "")
            new = STATUS_LABELS.get(entry.new_value, entry.new_value or "")
            events.append({"at": entry.changed_at, "kind": "status", "actor": actor, "text": f"Estado: {old} → {new}"})
        elif entry.field == "cancelled":
            reason = models.CANCEL_REASONS.get(entry.new_value, entry.new_value)
            events.append({"at": entry.changed_at, "kind": "cancelled", "actor": actor, "text": f"Cancelado · {reason}"})
        elif entry.field == "reopened":
            events.append({"at": entry.changed_at, "kind": "reopened", "actor": actor, "text": "Reabierto"})
    if first is not None:
        events.append({"at": first.created_at, "kind": "first_response", "actor": _name(who(first.user_id)),
                       "text": "Primera respuesta al cliente"})
    events.append({"at": due, "kind": "overdue", "actor": None, "text": "Venció el tiempo meta"})
    events.sort(key=lambda e: e["at"])
    for event in events:
        event.setdefault("auto", False)
    return events


def build(db: Session, ticket: models.Ticket, viewer: models.User, contact: tuple, now: datetime | None = None) -> dict:
    """contact: (teléfono, cédula) ya decididos para quien lo ve."""
    now = now or _now()
    if not was_overdue(db, ticket, now):
        raise HTTPException(status_code=400, detail="El ticket no está vencido")
    due = nav._due_at(ticket)
    first = _first_response(db, ticket)
    cause = db.query(models.OverdueCause).filter(models.OverdueCause.ticket_id == ticket.id).first()
    level = nav._level(ticket)
    return {
        "id": ticket.id,
        "title": ticket.title,
        "status": ticket.status,
        "cancelled": ticket.cancellation_row is not None,
        "level": level,
        "source": ticket.source,
        "priority": ticket.priority.name if ticket.priority else None,
        "category": ticket.category.name if ticket.category else None,
        "requester_name": _name(db.get(models.User, ticket.created_by)),
        "assigned_to": ticket.assigned_to,
        "assigned_name": _name(ticket.assigned_user),
        "created_at": ticket.created_at,
        "due_at": due,
        "overdue_seconds": max(0, int((now - due).total_seconds())),
        "first_response_at": first.created_at if first else None,
        "without_response_seconds": max(0, int(((first.created_at if first else now) - ticket.created_at).total_seconds())),
        "last_activity_at": portal.last_activity(db, [ticket])[ticket.id],
        "contact_phone": contact[0],
        "contact_id_number": contact[1],
        "timeline": _timeline(db, ticket, first, due),
        "cause": {
            "cause": cause.cause,
            "label": models.OVERDUE_CAUSES.get(cause.cause, cause.cause),
            "note": cause.note,
            "recorded_by_name": _name(cause.user),
            "recorded_at": cause.recorded_at,
        } if cause else None,
        "can_record_cause": viewer.role in CAUSE_ROLES,
    }


def pdf_contact(ticket: models.Ticket) -> tuple:
    """En el PDF siempre enmascarados, sin importar el rol."""
    return mask_last4(ticket.contact_phone), mask_id_number(ticket.contact_id_number)


def duration_text(seconds: int) -> str:
    minutes = seconds // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins:02d} min"
    return f"{mins} min"


def _when(value: datetime | None) -> str:
    return _local(value).strftime("%d/%m/%Y %H:%M") if value else "—"


def filename(ticket_id: int, now: datetime) -> str:
    return f"vencimiento-ticket-{ticket_id}-{_local(now).strftime('%Y-%m-%d')}.pdf"


def render_pdf(report: dict, generated_by: str, now: datetime) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = styles["BodyText"].clone("small", fontSize=8, leading=10, textColor=colors.HexColor("#5B6078"))

    def p(text, style=body):
        return Paragraph(escape(str(text)), style)

    buffer = BytesIO()
    # Sin comprimir: es corto y así el contenido se puede revisar (también en las pruebas)
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"Reporte de vencimiento · Ticket #{report['id']}", author="Ticketera de soporte", pageCompression=0,
    )
    story = []
    if os.path.exists(LOGO_PATH):
        story.append(Image(LOGO_PATH, width=4.2 * cm, height=2.2 * cm, hAlign="LEFT"))
    else:
        story.append(p(LOGO_TEXT, small))
    story.append(Paragraph(escape(f"Reporte de vencimiento · Ticket #{report['id']}"), styles["Title"]))
    story.append(p(f"Generado el {_when(now)} por {generated_by}", small))
    story.append(Spacer(1, 10))

    level = "Sin nivel" if report["level"] is None else f"Nivel {report['level']}"
    rows = [
        # Título y nota pasan por _redact: un teléfono o cédula escrito ahí tampoco sale completo
        ("Ticket", f"#{report['id']} · {_redact(report['title'])}"),
        ("Nivel · canal", f"{level} · {SOURCE_NAMES.get(report['source'], report['source'])}"),
        ("Prioridad · categoría", f"{report['priority'] or '—'} · {report['category'] or '—'}"),
        ("Cliente", report["requester_name"] or "—"),
        ("Teléfono · cédula", f"{report['contact_phone'] or '—'} · {report['contact_id_number'] or '—'}"),
        ("Responsable", report["assigned_name"] or "Sin asignar"),
        ("Creación", _when(report["created_at"])),
        ("Vencimiento", _when(report["due_at"])),
        ("Tiempo vencido", duration_text(report["overdue_seconds"])),
        ("Primera respuesta", _when(report["first_response_at"]) if report["first_response_at"] else "Sin respuesta"),
        ("Tiempo sin primera respuesta", duration_text(report["without_response_seconds"])),
        ("Última actividad", _when(report["last_activity_at"])),
    ]
    data = Table([[p(k, small), p(v)] for k, v in rows], colWidths=[5 * cm, 12 * cm])
    data.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E6E8EE")),
    ]))
    story += [data, Spacer(1, 12), Paragraph("Línea de tiempo", styles["Heading3"])]
    events = [[p(_when(e["at"]), small), p(e["text"] + (f" · {e['actor']}" if e["actor"] else ""))] for e in report["timeline"]]
    timeline = Table(events, colWidths=[3.5 * cm, 13.5 * cm])
    timeline.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [timeline, Spacer(1, 12), Paragraph("Causa del vencimiento", styles["Heading3"])]
    cause = report["cause"]
    if cause:
        story.append(p(f"{cause['label']} · registrada por {cause['recorded_by_name']} el {_when(cause['recorded_at'])}"))
        if cause["note"]:
            story.append(p(f"Nota: {_redact(cause['note'])}"))
    else:
        story.append(p("Causa pendiente de registrar"))
    doc.build(story)
    return buffer.getvalue()
