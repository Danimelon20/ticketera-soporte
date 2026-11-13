import logging
import os
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import account_reports, assignment_notice, attachments, audit, auto_assign, availability, config, draft_classification, email_analysis, email_attachments, grouping, identity, models, routing, suspicion
from ..chatbot import _redact
from ..email_processing import send_ticket_created
from ..schemas import (
    AttachmentRead, DraftAssign, DraftConfirm, DraftRead, DraftTechnician, DraftTransferOption, DraftTransferRequest,
    DraftTransferResult,
)
from ..security import get_current_user, mask_email
from ..validators import mask_id_number, normalize_id_number, normalize_phone
from .tickets import person_name, unavailable_message, verify_id_number

router = APIRouter(prefix="/api", tags=["drafts"])

REVIEW_ROLES = ("technician", "coordinator")
# Ven todos los borradores, los reasignan y confirman cualquiera
MANAGER_ROLES = ("coordinator",)
NOT_YOURS = "Este borrador está asignado a otra persona"
# Tickets por departamento: los borradores son del coordinador del departamento; los técnicos ya no los ven
OTHER_DEPARTMENT = "Este borrador es de otro departamento"
TECHNICIANS_NO_DRAFTS = "Con tickets por departamento, los borradores los revisa el coordinador del departamento"
NO_DRAFT_ASSIGNMENT = "Con tickets por departamento, los borradores no se asignan a técnicos"
TECHNICIAN_REQUIRED = "Elija el técnico de su departamento que atenderá el ticket"
TECHNICIAN_NOT_IN_DEPARTMENT = "Solo puede elegir un técnico activo de su departamento"
CLASSIFIED = ("routed", "classified")
TRANSFER_OFF = "Encienda tickets por departamento para transferir borradores (Administración › Activación)"
TRANSFER_SAME = "El borrador ya es de ese departamento"
TRANSFER_NO_COORDINATOR = "Ese departamento no tiene coordinador activo"
TRANSFER_NOT_PENDING = "Este borrador ya no está pendiente"
# Los poco claros y los que no son soporte no se revisan: no se listan ni se cuentan
DEFAULT_STATUSES = auto_assign.DRAFT_PENDING_STATUSES
DraftStatus = Literal[
    "analyzing", "pending_review", "waiting_contact", "unclear", "not_support",
    "status_reply", "confirmed", "discarded", "error",
]


def require_reviewer(current_user=Depends(get_current_user), db: Session = Depends(get_db)) -> models.User:
    if current_user.role not in REVIEW_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para revisar borradores")
    if current_user.role == "technician" and routing.enforced(db):
        raise HTTPException(status_code=403, detail=TECHNICIANS_NO_DRAFTS)
    return current_user


def draft_department_id(db: Session, draft_id: int) -> int | None:
    """Departamento del borrador (de la IA o del administrador). None mientras está «Por clasificar»."""
    row = draft_classification.row_of(db, draft_id)
    return row.department_id if row is not None and row.status in CLASSIFIED else None


def coordinator_department_id(db: Session, user: models.User) -> int | None:
    row = routing.coordinator_row(db, user.id) if user.role == "coordinator" else None
    return row.department_id if row is not None else None


def _stored_contact(draft: models.EmailDraft) -> tuple[str | None, str | None]:
    # Los del correo o, si faltan, los del perfil
    requester = draft.requester
    return draft.contact_phone or requester.phone, draft.contact_id_number or requester.id_number


def _pending_reason(db: Session, draft: models.EmailDraft) -> str | None:
    row = db.query(models.DraftPendingReason).filter(models.DraftPendingReason.draft_id == draft.id).first()
    return row.reason if row else None


def _draft_files(db: Session, draft_id: int) -> tuple[list[AttachmentRead], list[str]]:
    rows = db.query(models.DraftAttachment).filter(models.DraftAttachment.draft_id == draft_id).order_by(models.DraftAttachment.id).all()
    notes = db.query(models.DraftAttachmentNote).filter(models.DraftAttachmentNote.draft_id == draft_id).order_by(models.DraftAttachmentNote.id).all()
    files = []
    for row in rows:
        item = AttachmentRead.model_validate(row)
        item.inline = attachments.is_inline(row)
        files.append(item)
    return files, [note.text for note in notes]


def _reviewer_id(db: Session, draft_id: int) -> int | None:
    row = auto_assign.draft_row(db, draft_id)
    return row.user_id if row is not None and row.status in ("assigned", "done") else None


def can_review(db: Session, draft: models.EmailDraft, user: models.User) -> bool:
    """El coordinador, o el técnico que es su revisor. Con tickets por departamento: solo el coordinador del
    departamento del borrador."""
    if routing.enforced(db):
        dept_id = coordinator_department_id(db, user)
        return dept_id is not None and draft_department_id(db, draft.id) == dept_id
    if user.role in MANAGER_ROLES:
        return True
    return user.role == "technician" and _reviewer_id(db, draft.id) == user.id


def _check_can_review(db: Session, draft: models.EmailDraft, user: models.User) -> None:
    if not can_review(db, draft, user):
        raise HTTPException(status_code=403, detail=OTHER_DEPARTMENT if routing.enforced(db) else NOT_YOURS)


def _draft_read(db: Session, draft: models.EmailDraft, viewer: models.User) -> DraftRead:
    requester = draft.requester
    phone, id_number = _stored_contact(draft)
    files, notes = _draft_files(db, draft.id)
    data = DraftRead(
        id=draft.id,
        status=draft.status,
        created_at=draft.created_at,
        requester_id=draft.requester_id,
        # Cuenta nueva sin perfil completo: se muestra el correo
        requester_name=requester.name or requester.email,
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
        pending_reason=_pending_reason(db, draft),
        # Los borradores solo los ve el personal
        requester_flags=account_reports.staff_flags(db, requester),
        attachments=files,
        attachment_notes=notes,
    )
    merge = grouping.active_draft_merge(db, draft.id)
    if merge is not None:
        data.merged_into = merge.target_ticket_id
    enforced = routing.enforced(db)
    # Con tickets por departamento no hay revisor: el técnico se elige al confirmar
    reviewer_id = None if enforced else _reviewer_id(db, draft.id)
    if reviewer_id is not None:
        reviewer = db.get(models.User, reviewer_id)
        data.assigned_to = reviewer_id
        data.assigned_name = person_name(reviewer) if reviewer else None
    classification = draft_classification.row_of(db, draft.id) if enforced else None
    if classification is not None:
        data.classification = classification.status
        if classification.department_id is not None:
            data.department_name = routing.path_label(db.get(models.Department, classification.department_id),
                                                      routing.departments_by_id(db))
    pending = draft.status in DEFAULT_STATUSES
    data.can_review = pending and can_review(db, draft, viewer)
    data.can_reassign = pending and viewer.role in MANAGER_ROLES and not enforced
    data.can_transfer = enforced and data.can_review
    last = _last_transfer(db, draft.id) if enforced else None
    if last is not None:
        by_id = routing.departments_by_id(db)
        actor = db.get(models.User, last.transferred_by)
        data.transfer_note = (f"Transferido desde {routing.path_label(by_id.get(last.from_department_id), by_id)} "
                              f"por {person_name(actor) if actor else 'Sin nombre'}: {last.reason}")
    if viewer.role == "technician":
        # El técnico no ve datos personales completos (como en el detalle del ticket)
        data.body = _redact(data.body)
        data.summary = _redact(data.summary) if data.summary else None
        data.title = _redact(data.title) if data.title else None
        data.description = _redact(data.description) if data.description else None
        data.contact_phone = mask_id_number(phone)
        data.contact_id_number = mask_id_number(id_number)
        # Sin nombre en el perfil: el correo enmascarado, nunca el completo
        data.requester_name = requester.name or mask_email(requester.email)
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
def list_drafts(
    status: Optional[DraftStatus] = None,
    # Solo el coordinador filtra: mine (los que revisa él), none (Sin revisor) o all. El técnico ve solo los suyos
    assigned: Literal["all", "mine", "none"] = "all",
    db: Session = Depends(get_db),
    reviewer=Depends(require_reviewer),
):
    statuses = (status,) if status else DEFAULT_STATUSES
    if routing.enforced(db):
        # Una sola lista: la del departamento del coordinador (sin revisores ni filtros)
        dept_id = coordinator_department_id(db, reviewer)
        if dept_id is None:
            return []
        drafts = (
            db.query(models.EmailDraft)
            .join(models.DraftClassification, models.DraftClassification.draft_id == models.EmailDraft.id)
            .filter(models.EmailDraft.status.in_(statuses),
                    models.DraftClassification.status.in_(CLASSIFIED),
                    models.DraftClassification.department_id == dept_id)
            .order_by(models.EmailDraft.created_at.asc(), models.EmailDraft.id.asc()).all()
        )
        return [_draft_read(db, d, reviewer) for d in drafts]
    query = (
        db.query(models.EmailDraft)
        .outerjoin(models.DraftAssignment, models.DraftAssignment.draft_id == models.EmailDraft.id)
        .filter(models.EmailDraft.status.in_(statuses))
    )
    if reviewer.role not in MANAGER_ROLES or assigned == "mine":
        query = query.filter(models.DraftAssignment.user_id == reviewer.id)
    elif assigned == "none":
        query = query.filter(models.DraftAssignment.user_id.is_(None))
    drafts = query.order_by(models.EmailDraft.created_at.asc(), models.EmailDraft.id.asc()).all()
    return [_draft_read(db, d, reviewer) for d in drafts]


def _last_transfer(db: Session, draft_id: int) -> models.DraftTransfer | None:
    return (db.query(models.DraftTransfer).filter(models.DraftTransfer.draft_id == draft_id)
            .order_by(models.DraftTransfer.id.desc()).first())


@router.get("/drafts/transfer-options", response_model=list[DraftTransferOption])
def draft_transfer_options(db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    """Destinos para transferir un borrador: los departamentos activos que atienden tickets. Su departamento y los
    que no tienen coordinador activo salen deshabilitados, con el motivo."""
    if not routing.enforced(db):
        raise HTTPException(status_code=409, detail=TRANSFER_OFF)
    own = coordinator_department_id(db, reviewer)
    by_id = routing.departments_by_id(db)
    options = []
    for dept in by_id.values():
        if not routing.serves_tickets(dept, by_id):
            continue
        reason = None
        if dept.id == own:
            reason = "Es su departamento"
        elif not draft_classification.has_coordinator(db, dept.id):
            reason = "Sin coordinador activo"
        options.append(DraftTransferOption(id=dept.id, path=routing.path_label(dept, by_id), enabled=reason is None,
                                           disabled_reason=reason))
    return sorted(options, key=lambda o: o.path.casefold())


@router.get("/drafts/technicians", response_model=list[DraftTechnician])
def draft_technicians(db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    """Para confirmar un borrador: los técnicos activos del departamento del coordinador, con su carga (la de la
    asignación automática) y su disponibilidad. Sin tickets por departamento, lista vacía."""
    dept_id = coordinator_department_id(db, reviewer) if routing.enforced(db) else None
    if dept_id is None:
        return []
    technicians = routing.department_technicians(db, dept_id)
    loads = auto_assign.loads(db, [u.id for u in technicians])
    result = []
    for user in technicians:
        status_, return_date = availability.get(db, user)
        result.append(DraftTechnician(id=user.id, name=person_name(user), load=loads[user.id],
                                      availability=status_, availability_text=availability.describe(status_, return_date)))
    return result


def _chosen_technician(db: Session, draft: models.EmailDraft, data: DraftConfirm) -> tuple[models.User, int]:
    """Tickets por departamento: el técnico que eligió el coordinador. 422 si falta o no es un técnico activo del
    departamento del borrador; 409 si no está disponible y no se confirmó. Devuelve (técnico, departamento)."""
    dept_id = draft_department_id(db, draft.id)
    if data.assigned_to is None:
        raise HTTPException(status_code=422, detail=TECHNICIAN_REQUIRED)
    user = db.get(models.User, data.assigned_to)
    if (user is None or not user.is_active or user.role != "technician" or dept_id is None
            or not routing.belongs_to(db, user, dept_id)):
        raise HTTPException(status_code=422, detail=TECHNICIAN_NOT_IN_DEPARTMENT)
    if not data.confirm_unavailable and not availability.is_available(db, user):
        status_, return_date = availability.get(db, user)
        raise HTTPException(status_code=409, detail=unavailable_message(user, status_, return_date))
    return user, dept_id


@router.post("/drafts/{draft_id}/confirm", response_model=DraftRead)
def confirm_draft(draft_id: int, data: DraftConfirm, background_tasks: BackgroundTasks,
                  db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    draft = _open_draft(db, draft_id)
    _check_can_review(db, draft, reviewer)
    chosen, department_id = _chosen_technician(db, draft, data) if routing.enforced(db) else (None, None)
    category = db.get(models.Category, data.category_id)
    if not category:
        raise HTTPException(status_code=400, detail="La categoría no existe")
    priority = db.get(models.Priority, data.priority_id)
    if not priority:
        raise HTTPException(status_code=400, detail="La prioridad no existe")
    stored_phone, stored_id = _stored_contact(draft)
    contact_phone = _final_contact(data.contact_phone, stored_phone, normalize_phone, "Falta el teléfono")
    contact_id_number = _final_contact(data.contact_id_number, stored_id, normalize_id_number, "Falta la cédula")
    requester = draft.requester
    # Hacienda solo para una cédula nueva escrita por quien confirma
    if contact_id_number != stored_id:
        if identity.id_number_taken(db, contact_id_number, requester.id):
            # La escribió quien confirma: solo queda en el registro
            suspicion.id_number_conflict(db, requester, contact_id_number, "staff", actor_id=reviewer.id, report=False)
            db.commit()
            raise HTTPException(status_code=400, detail=identity.ID_IN_USE)
        verify_id_number(contact_id_number)
    elif contact_id_number != requester.id_number and identity.id_number_taken(db, contact_id_number, requester.id):
        # El intento del cliente ya se contó cuando llegó su correo: aquí solo queda en el registro
        suspicion.id_number_conflict(db, requester, contact_id_number, "draft", actor_id=reviewer.id, report=False)
        db.commit()
        raise HTTPException(status_code=400, detail=identity.ID_IN_USE)

    # Responsable: el técnico que confirma; si confirma un coordinador, el revisor del borrador.
    # Sin ninguno de los dos, lo decide la asignación automática como siempre
    # Con tickets por departamento: el técnico que eligió el coordinador
    owner_id = reviewer.id if reviewer.role == "technician" else _reviewer_id(db, draft.id)
    owner = db.get(models.User, owner_id) if owner_id is not None else None
    if owner is not None and (not owner.is_active or owner.role != "technician"):
        owner = None
    if chosen is not None:
        owner = chosen
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
        assigned_to=owner.id if owner else None,
    )
    db.add(ticket)
    db.flush()
    if department_id is not None:
        # El ticket nace con el departamento del borrador
        routing.set_ticket_department(db, ticket, department_id)
    if owner is not None:
        db.add(models.History(
            ticket_id=ticket.id, field="assigned_to", old_value=None, new_value=str(owner.id), changed_by=reviewer.id,
        ))

    db.add(models.TicketFollower(ticket_id=ticket.id, user_id=requester.id))
    # Los adjuntos del correo pasan al primer mensaje del ticket (antes del comentario del Agente)
    email_attachments.move_to_ticket(db, draft, ticket)
    agent = db.query(models.User).filter(models.User.role == "agent").first()
    if agent is not None:
        db.add(models.Comment(
            ticket_id=ticket.id,
            user_id=agent.id,
            content=f"Ticket creado a partir de un correo y confirmado por {reviewer.name}.",
        ))
    # El análisis de la IA ya está hecho: el del borrador
    analysis = models.TicketAIAnalysis(
        ticket_id=ticket.id,
        status="done",
        ai_level=draft.ai_level,
        ai_category_id=draft.category_id,
        summary=draft.summary,
        needs_review=draft.needs_review,
        model=config.LLM_MODEL,
        analyzed_at=_now().replace(tzinfo=None),
        due_at=email_analysis.compute_due_at(ticket.created_at, draft.ai_level),
    )
    db.add(analysis)
    # Sin nivel del borrador (o "No es soporte"): vence como Estándar
    email_analysis.apply_default_level(db, ticket, analysis)
    # Al perfil con las mismas reglas que "Complete su perfil"; desde aquí queda fijo
    identity.apply_contact_from_email(
        db, requester, requester.phone or contact_phone, contact_id_number, actor_id=reviewer.id
    )
    identity.lock(db, requester)

    draft.status = "confirmed"
    draft.reviewed_by = reviewer.id
    draft.reviewed_at = _now()
    draft.ticket_id = ticket.id
    auto_assign.finish_draft(db, draft.id)
    db.commit()
    # Cargados antes de cerrar la sesión: el correo se envía después de responder
    for obj in (draft, ticket, requester, category, priority):
        db.refresh(obj)
    if owner is not None:
        db.refresh(owner)
        # Confirmó un coordinador: el revisor recibe el aviso de siempre ("Asignarme a mí" no se avisa)
        if assignment_notice.should_notify(owner, reviewer):
            assignment_notice.schedule(background_tasks, ticket, owner)

    background_tasks.add_task(send_ticket_created, ticket, requester, category, priority)
    # Solo el caso B (otros clientes): el mismo cliente ya se revisó al llegar el correo.
    # Antes de asignar, para que herede el responsable del principal
    if config.GROUPING_ENABLED:
        background_tasks.add_task(grouping.after_ticket_created, ticket.id, ("link",))
    # El nivel ya existe (viene del análisis del borrador). Con responsable, assign_new no lo cambia
    if owner is None:
        background_tasks.add_task(auto_assign.assign_new, ticket.id)
    return _draft_read(db, draft, reviewer)


@router.get("/drafts/{draft_id}", response_model=DraftRead)
def get_draft(draft_id: int, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    """Un borrador. Con tickets por departamento, solo el coordinador de su departamento (403 a los demás, también
    al que lo transfirió)."""
    draft = db.get(models.EmailDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="El borrador no existe")
    _check_can_review(db, draft, reviewer)
    return _draft_read(db, draft, reviewer)


@router.post("/drafts/{draft_id}/transfer", response_model=DraftTransferResult)
def transfer_draft(draft_id: int, data: DraftTransferRequest, db: Session = Depends(get_db),
                   reviewer=Depends(require_reviewer)):
    """El coordinador transfiere un borrador de su departamento a otro que atiende tickets y tiene coordinador
    activo. El borrador llega completo; el coordinador del destino recibe el aviso en la campana y el anterior
    deja de verlo. Queda como corrección de la IA (draft_transfers)."""
    if not routing.enforced(db):
        raise HTTPException(status_code=409, detail=TRANSFER_OFF)
    draft = _open_draft(db, draft_id)
    _check_can_review(db, draft, reviewer)
    if draft.status not in DEFAULT_STATUSES:
        raise HTTPException(status_code=409, detail=TRANSFER_NOT_PENDING)
    row = draft_classification.row_of(db, draft.id)
    source_id = row.department_id
    target = routing.serving_department(db, data.department_id)
    if target.id == source_id:
        raise HTTPException(status_code=400, detail=TRANSFER_SAME)
    coordinator = routing.coordinator_of(db, target.id)
    if not draft_classification.has_coordinator(db, target.id):
        raise HTTPException(status_code=409, detail=TRANSFER_NO_COORDINATOR)
    origin = "transfer" if _last_transfer(db, draft.id) is not None else ("ai" if row.status == "routed" else "admin")
    db.add(models.DraftTransfer(
        draft_id=draft.id, ai_department_id=row.ai_department_id, origin=origin, from_department_id=source_id,
        to_department_id=target.id, reason=data.reason, transferred_by=reviewer.id,
        transferred_at=_now().replace(tzinfo=None), notify_user_id=coordinator.user_id,
    ))
    row.department_id = target.id
    db.commit()
    # Sin el texto del correo ni el motivo en el log
    logging.info("Borrador %s transferido del departamento %s al %s por el usuario %s",
                 draft.id, source_id, target.id, reviewer.id)
    by_id = routing.departments_by_id(db)
    return DraftTransferResult(draft_id=draft.id, department_name=routing.path_label(target, by_id))


@router.post("/drafts/{draft_id}/request-details")
def request_details(draft_id: int, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    draft = _open_draft(db, draft_id)
    _check_can_review(db, draft, reviewer)
    if draft.status not in ("pending_review", "waiting_contact"):
        raise HTTPException(status_code=409, detail="Este borrador ya no está pendiente")
    # Lo decide una persona: se envía aunque el antibucle de 24 horas ya haya avisado
    result = email_analysis._handle_unclear(db, draft, draft.requester, force=True)
    if result == "send_failed":
        raise HTTPException(status_code=502, detail="No se pudo enviar el correo al cliente. El borrador no se modificó.")
    logging.info("Borrador %s: más detalles pedidos por el usuario %s", draft_id, reviewer.id)
    return {"result": result}


@router.post("/drafts/{draft_id}/discard", response_model=DraftRead)
def discard_draft(draft_id: int, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    draft = _open_draft(db, draft_id)
    _check_can_review(db, draft, reviewer)
    draft.status = "discarded"
    draft.reviewed_by = reviewer.id
    draft.reviewed_at = _now()
    auto_assign.finish_draft(db, draft.id)
    stored = email_attachments.remove_from_draft(db, draft.id)
    db.commit()
    # Descartado: sus archivos se borran del disco
    attachments.remove_files(stored)
    db.refresh(draft)
    return _draft_read(db, draft, reviewer)


@router.get("/drafts/attachments/{attachment_id}")
def download_draft_attachment(
    attachment_id: int, preview: bool = False, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)
):
    """Adjunto de un borrador: solo quien revisa borradores. preview=1 (miniaturas) no queda en el registro."""
    row = db.get(models.DraftAttachment, attachment_id)
    if row is None or not os.path.isfile(attachments.path_of(row)):
        raise HTTPException(status_code=404, detail="El adjunto no existe")
    _check_can_review(db, db.get(models.EmailDraft, row.draft_id), reviewer)
    if not preview:
        audit.record(
            db, reviewer.id, "attachment_downloaded",
            target_label=f"Borrador #{row.draft_id}", new=f"Adjunto de borrador #{row.id}",
        )
        db.commit()
    return attachments.file_response(row)


@router.post("/drafts/{draft_id}/assign", response_model=DraftRead)
def assign_draft(draft_id: int, data: DraftAssign, db: Session = Depends(get_db), reviewer=Depends(require_reviewer)):
    """Solo el coordinador: otro técnico revisa el borrador. Le llega el aviso "Nuevo borrador asignado"."""
    if reviewer.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Solo un coordinador puede reasignar borradores")
    if routing.enforced(db):
        raise HTTPException(status_code=403, detail=NO_DRAFT_ASSIGNMENT)
    draft = _open_draft(db, draft_id)
    if draft.status not in DEFAULT_STATUSES:
        raise HTTPException(status_code=409, detail="Este borrador ya no está pendiente")
    user = db.get(models.User, data.user_id)
    if user is None or not user.is_active or user.role != "technician":
        raise HTTPException(status_code=400, detail="Solo se puede asignar a técnicos activos")
    row = auto_assign.draft_row(db, draft.id)
    if row is not None and row.user_id == user.id and row.status == "assigned":
        return _draft_read(db, draft, reviewer)
    # Igual que con los tickets: a alguien no disponible, solo con confirmación
    if not data.confirm_unavailable and not availability.is_available(db, user):
        status_, return_date = availability.get(db, user)
        raise HTTPException(status_code=409, detail=unavailable_message(user, status_, return_date))
    now = _now().replace(tzinfo=None)
    if row is None:
        row = models.DraftAssignment(draft_id=draft.id, created_at=now)
        db.add(row)
    row.status = "assigned"
    row.user_id = user.id
    row.assigned_at = now
    row.assigned_by = reviewer.id
    row.notify = True
    row.seen_at = None
    db.commit()
    logging.info("Borrador %s reasignado al técnico %s por el usuario %s", draft.id, user.id, reviewer.id)
    db.refresh(draft)
    return _draft_read(db, draft, reviewer)
