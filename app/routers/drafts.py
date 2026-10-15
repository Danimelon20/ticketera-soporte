from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import config, email_analysis, models
from ..chatbot import _redact
from ..email_processing import send_ticket_created
from ..schemas import DraftConfirm, DraftRead
from ..security import get_current_user
from ..validators import mask_id_number, normalize_id_number, normalize_phone
from .tickets import verify_id_number

router = APIRouter(prefix="/api", tags=["drafts"])

REVIEW_ROLES = ("technician", "coordinator", "admin")
# Los poco claros y los que no son soporte no se revisan: no se listan ni se cuentan
DEFAULT_STATUSES = ("pending_review", "waiting_contact", "error")
DraftStatus = Literal[
    "analyzing", "pending_review", "waiting_contact", "unclear", "not_support",
    "status_reply", "confirmed", "discarded", "error",
]


def require_reviewer(current_user=Depends(get_current_user)) -> models.User:
    if current_user.role not in REVIEW_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para revisar borradores")
    return current_user


def _stored_contact(draft: models.EmailDraft) -> tuple[str | None, str | None]:
    # Los del correo o, si faltan, los del perfil
    requester = draft.requester
    return draft.contact_phone or requester.phone, draft.contact_id_number or requester.id_number


def _draft_read(draft: models.EmailDraft, viewer: models.User) -> DraftRead:
    requester = draft.requester
    phone, id_number = _stored_contact(draft)
    data = DraftRead(
        id=draft.id,
        status=draft.status,
        created_at=draft.created_at,
        requester_id=draft.requester_id,
        requester_name=requester.name,
        subject=draft.subject,
        body=draft.body,
        title=draft.title,
        category_id=draft.category_id,
        priority_id=draft.priority_id,
        description=draft.description,
        contact_phone=phone,
        contact_id_number=id_number,
        ai_level=draft.ai_level,
        summary=draft.summary,
        needs_review=draft.needs_review,
        ticket_id=draft.ticket_id,
    )
    if viewer.role == "technician":
        # El técnico no ve datos personales completos (como en el detalle del ticket)
        data.body = _redact(data.body)
        data.summary = _redact(data.summary) if data.summary else None
        data.title = _redact(data.title) if data.title else None
        data.description = _redact(data.description) if data.description else None
        data.contact_phone = mask_id_number(phone)
        data.contact_id_number = mask_id_number(id_number)
    return data


def _final_contact(value: str | None, stored: str | None, normalize, missing: str) -> str:
    """Lo que escribió quien confirma; vacío o enmascarado: el dato guardado."""
    value = (value or "").strip()
    if not value or "*" in value:
        if not stored:
            raise HTTPException(status_code=422, detail=missing)
        return stored
    try:
        return normalize(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _open_draft(db: Session, draft_id: int) -> models.EmailDraft:
    draft = db.get(models.EmailDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="El borrador no existe")
    if draft.status in ("confirmed", "discarded"):
        raise HTTPException(status_code=409, detail="Este borrador ya fue confirmado o descartado")
    return draft


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/drafts", response_model=list[DraftRead])
def list_drafts(status: Optional[DraftStatus] = None, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    statuses = (status,) if status else DEFAULT_STATUSES
    drafts = (
        db.query(models.EmailDraft)
        .filter(models.EmailDraft.status.in_(statuses))
        .order_by(models.EmailDraft.created_at.asc(), models.EmailDraft.id.asc())
        .all()
    )
    return [_draft_read(d, reviewer) for d in drafts]


@router.post("/drafts/{draft_id}/confirm", response_model=DraftRead)
def confirm_draft(draft_id: int, data: DraftConfirm, background_tasks: BackgroundTasks,
                  db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    draft = _open_draft(db, draft_id)
    category = db.get(models.Category, data.category_id)
    if not category:
        raise HTTPException(status_code=400, detail="La categoría no existe")
    priority = db.get(models.Priority, data.priority_id)
    if not priority:
        raise HTTPException(status_code=400, detail="La prioridad no existe")
    stored_phone, stored_id = _stored_contact(draft)
    contact_phone = _final_contact(data.contact_phone, stored_phone, normalize_phone, "Falta el teléfono")
    contact_id_number = _final_contact(data.contact_id_number, stored_id, normalize_id_number, "Falta la cédula")
    # Hacienda solo para una cédula nueva escrita por quien confirma
    if contact_id_number != stored_id:
        verify_id_number(contact_id_number)

    requester = draft.requester
    ticket = models.Ticket(
        title=data.title,
        description=data.description,
        category_id=category.id,
        priority_id=priority.id,
        status="new",
        created_by=requester.id,
        contact_phone=contact_phone,
        contact_id_number=contact_id_number,
        source="email",
    )
    db.add(ticket)
    db.flush()

    db.add(models.TicketFollower(ticket_id=ticket.id, user_id=requester.id))
    agent = db.query(models.User).filter(models.User.role == "agent").first()
    if agent is not None:
        db.add(models.Comment(
            ticket_id=ticket.id,
            user_id=agent.id,
            content=f"Ticket creado a partir de un correo y confirmado por {reviewer.name}.",
        ))
    # El análisis de la IA ya está hecho: el del borrador
    db.add(models.TicketAIAnalysis(
        ticket_id=ticket.id,
        status="done",
        ai_level=draft.ai_level,
        ai_category_id=draft.category_id,
        summary=draft.summary,
        needs_review=draft.needs_review,
        model=config.LLM_MODEL,
        analyzed_at=_now().replace(tzinfo=None),
        due_at=email_analysis.compute_due_at(ticket.created_at, draft.ai_level),
    ))
    if not requester.phone:
        requester.phone = contact_phone
    if not requester.id_number:
        requester.id_number = contact_id_number

    draft.status = "confirmed"
    draft.reviewed_by = reviewer.id
    draft.reviewed_at = _now()
    draft.ticket_id = ticket.id
    db.commit()
    # Cargados antes de cerrar la sesión: el correo se envía después de responder
    for obj in (draft, ticket, requester, category, priority):
        db.refresh(obj)

    background_tasks.add_task(send_ticket_created, ticket, requester, category, priority)
    return _draft_read(draft, reviewer)


@router.post("/drafts/{draft_id}/discard", response_model=DraftRead)
def discard_draft(draft_id: int, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    draft = _open_draft(db, draft_id)
    draft.status = "discarded"
    draft.reviewed_by = reviewer.id
    draft.reviewed_at = _now()
    db.commit()
    db.refresh(draft)
    return _draft_read(draft, reviewer)
