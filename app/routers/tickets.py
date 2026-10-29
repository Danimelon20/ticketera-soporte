from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from sqlalchemy.orm import Session
from typing import Literal, Optional
from ..database import get_db
from .. import models
from ..schemas import AttachmentRead, CancellationRead, ConversationViewRequest, TicketCancelRequest, TicketCreate, TicketRead, TicketUpdate, TicketDetail, HistoryRead, CommentCreate, CommentRead, AIAnalysisRead, AILevelUpdate, PersonRead, SuggestionRead
from ..security import get_current_user
from ..validators import mask_id_number
from ..chatbot import _redact
from .. import cancellations, clients, config, hacienda, mailer, privacy
from ..notifications import STATUS_LABELS, display_name, notify_followers
from .. import account_reports, alerts, attachments, assignment_notice, audit, auto_assign, availability, comments as internal_comments, email_analysis, grouping, identity, nav, portal, routing, ticket_access

AI_ROLES = ("technician", "coordinator")
# Cambios de área y de departamento: internos, el solicitante no los ve
ROUTE_FIELDS = ("area", "department")
# Dirigir la operación: crear a nombre de un cliente, sugerencias y reasignar
OPERATION_ROLES = ("coordinator",)

router = APIRouter(prefix="/api", tags=["tickets"])


def verify_id_number(id_number: str) -> None:
    """Verifica en Hacienda que la cédula (ya normalizada) sea física o DIMEX."""
    error = hacienda.check_id_number(id_number)
    if error:
        raise HTTPException(status_code=400, detail=error)


OWN_TICKET = "Abrió este ticket como solicitante: no puede cambiar su estado ni su asignación"


def check_access(db: Session, db_ticket: models.Ticket, current_user, full: bool = False, allow_own: bool = False) -> str:
    """Reglas de ticket_access: 403 si no puede verlo; con full, también si solo puede leerlo.

    allow_own: el personal en su propio ticket cuenta como solicitante (por ejemplo, para comentar).
    """
    level = ticket_access.access(db, current_user, db_ticket)
    if level is None:
        raise HTTPException(status_code=403, detail=ticket_access.NO_PERMISSION)
    if full and level == ticket_access.OWN and not allow_own:
        raise HTTPException(status_code=403, detail=OWN_TICKET)
    if full and level not in (ticket_access.FULL, ticket_access.OWN):
        raise HTTPException(status_code=403, detail=ticket_access.READ_ONLY)
    return level


def get_visible_ticket(db: Session, ticket_id: int, current_user, full: bool = False) -> models.Ticket:
    """Mismas reglas que el detalle: 404 si no existe, 403 si no puede verlo (o cambiarlo, con full)."""
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    check_access(db, db_ticket, current_user, full)
    return db_ticket


def contact_fields(db_ticket: models.Ticket, current_user) -> tuple[Optional[str], Optional[str]]:
    """Teléfono y cédula del ticket según quién lo ve.

    Coordinador, administrador o el creador: completos. Técnico asignado: teléfono completo y cédula
    enmascarada. Técnico no asignado o rol desconocido: nada.
    """
    is_creator = current_user.role != "requester" or db_ticket.created_by == current_user.id
    is_coordinator_or_admin = current_user.role in ("coordinator", "admin")
    is_assigned_technician = (
        current_user.role == "technician"
        and db_ticket.assigned_to is not None
        and db_ticket.assigned_to == current_user.id
    )
    if is_coordinator_or_admin or (is_creator and current_user.role != "technician"):
        return db_ticket.contact_phone, db_ticket.contact_id_number
    if is_assigned_technician:
        return db_ticket.contact_phone, mask_id_number(db_ticket.contact_id_number)
    return None, None


def unavailable_message(user: models.User, status: str, return_date) -> str:
    states = {
        "vacation": "está de vacaciones",
        "incapacity": "tiene una ausencia",
        "away": "está fuera de su horario",
    }
    state = states.get(status, f"está {availability.LABELS.get(status, status).lower()}")
    back = f" (regresa el {return_date.strftime('%d/%m/%Y')})" if return_date else ""
    return f"{person_name(user)} {state}{back}. ¿Asignar de todas formas?"


def hides_personal_data(ticket: models.Ticket, current_user) -> bool:
    """Técnico no asignado: el teléfono o la cédula escritos en el texto se ocultan al responder."""
    return (
        current_user.role == "technician"
        and ticket.assigned_to != current_user.id
        and ticket.created_by != current_user.id
    )


def ticket_read(ticket: models.Ticket, current_user, last_activity_at=None) -> TicketRead:
    # En la base queda el texto original; solo se enmascara la respuesta
    data = TicketRead.model_validate(ticket)
    data.assigned_name = person_name(ticket.assigned_user) if ticket.assigned_user else None
    data.progress_step = portal.progress_step(ticket)
    data.last_activity_at = last_activity_at or ticket.updated_at
    if hides_personal_data(ticket, current_user):
        data.description = _redact(data.description)
    route = ticket.routing_row
    if route is not None:
        data.area_id = route.area_id
        data.area_name = route.area.name if route.area else None
        # El departamento es estructura interna: el solicitante no lo recibe
        if current_user.role != "requester" and route.department is not None:
            data.department_id = route.department_id
            data.department_name = route.department.name
    row = ticket.cancellation_row
    # El solicitante ve su ticket "Cerrado", sin el motivo interno
    if row is not None and current_user.role != "requester":
        data.cancellation = CancellationRead(
            reason=row.reason,
            reason_label=models.CANCEL_REASONS.get(row.reason, row.reason),
            note=row.note,
            cancelled_by_name=person_name(row.user) if row.user else None,
            cancelled_at=row.cancelled_at,
        )
    return data


def person_name(user: models.User) -> str:
    # Nunca el correo como respaldo: puede verlo otra persona
    return user.name or "Sin nombre"


def ticket_people(db: Session, ticket: models.Ticket, comments: list, history: list) -> list[PersonRead]:
    """Nombre y rol de quienes aparecen en el ticket (sin correos ni datos personales)."""
    ids = {ticket.created_by, ticket.assigned_to}
    ids |= {c.user_id for c in comments}
    ids |= {h.changed_by for h in history}
    ids |= {int(v) for h in history if h.field == "assigned_to" for v in (h.old_value, h.new_value) if v and v.isdigit()}
    ids.discard(None)
    users = db.query(models.User).filter(models.User.id.in_(ids)).all() if ids else []
    return [PersonRead(id=u.id, name=person_name(u), role=u.role) for u in users]


def attachment_read(row: models.Attachment) -> AttachmentRead:
    data = AttachmentRead.model_validate(row)
    data.inline = attachments.is_inline(row)
    return data


def comment_read(comment: models.Comment, ticket: models.Ticket, current_user, internal: bool = False, files=()) -> CommentRead:
    data = CommentRead.model_validate(comment)
    data.internal = internal
    data.attachments = [attachment_read(row) for row in files]
    if hides_personal_data(ticket, current_user):
        data.content = _redact(data.content)
    return data


def ai_analysis_read(row: models.TicketAIAnalysis, ticket: models.Ticket) -> AIAnalysisRead:
    overdue = (
        row.due_at is not None
        and row.due_at < email_analysis._now()
        and ticket.status not in ("resolved", "closed")
    )
    return AIAnalysisRead(
        status=row.status,
        ai_level=row.ai_level,
        ai_category_id=row.ai_category_id,
        ai_category_name=row.ai_category.name if row.ai_category else None,
        summary=row.summary,
        needs_review=row.needs_review,
        human_level=row.human_level,
        effective_level=email_analysis.effective_level(row),
        due_at=row.due_at,
        overdue=overdue,
    )


def _is_internal_area(db: Session, area_id: int | None) -> bool:
    area = db.get(models.ServiceArea, area_id) if area_id is not None else None
    return area is not None and area.audience == "internal"


def ticket_route(db: Session, owner: models.User, area_id: int | None, unsure: bool) -> tuple[int | None, int | None]:
    """Área y departamento del ticket nuevo según lo que eligió el solicitante.

    Solo áreas activas de su tipo (externa o interna). "No estoy seguro" (solo externos) va al departamento
    de entrada. Sin elegir: con el enrutamiento encendido es obligatorio; apagado, como "No estoy seguro".
    """
    kind = routing.requester_kind(db, owner)
    if area_id is not None:
        area = db.get(models.ServiceArea, area_id)
        if area is None or not area.is_active or area.audience != kind:
            raise HTTPException(status_code=400, detail=routing.AREA_NOT_ALLOWED)
        return routing.route_for_area(db, area)
    if unsure and kind == "internal":
        raise HTTPException(status_code=400, detail=routing.AREA_REQUIRED)
    if not unsure and routing.enforced(db):
        raise HTTPException(status_code=400, detail=routing.AREA_REQUIRED)
    if kind == "internal":
        # Apagado y sin elegir: la primera área interna
        first = next(iter(routing.allowed_areas(db, owner)), None)
        return (first.id, first.entry_department_id) if first is not None else (None, None)
    return routing.unsure_route(db)


@router.post("/tickets", response_model=TicketRead)
def create_ticket(ticket: TicketCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role == "admin":
        raise HTTPException(status_code=403, detail="El administrador no crea tickets")
    # El técnico solo abre tickets internos para sí mismo (como solicitante)
    if current_user.role == "technician" and (ticket.requester_id is not None or not _is_internal_area(db, ticket.area_id)):
        raise HTTPException(status_code=403, detail="Los técnicos no pueden crear tickets")
    privacy.check_accepted(db, current_user)

    if ticket.requester_id is not None:
        if current_user.role not in OPERATION_ROLES:
            raise HTTPException(status_code=403, detail="No puedes crear tickets a nombre de otra persona")
        owner = db.query(models.User).filter(models.User.id == ticket.requester_id).first()
        if not owner or owner.role != "requester":
            raise HTTPException(status_code=400, detail="El solicitante no existe")
    else:
        owner = current_user
    # Clientes sin organización vigente no abren tickets nuevos (los que tienen siguen en proceso)
    blocked = clients.ticket_block(db, owner)
    if blocked:
        raise HTTPException(status_code=403, detail=blocked)

    # El ticket queda a nombre del perfil: sin nombre, cédula y teléfono no se crea
    if not identity.profile_complete(owner):
        detail = identity.INCOMPLETE_OWN if owner.id == current_user.id else identity.INCOMPLETE_OTHER
        raise HTTPException(status_code=400, detail=detail)

    categoria = db.query(models.Category).filter(models.Category.id == ticket.category_id).first()
    if not categoria:
        raise HTTPException(status_code=400, detail="La categoría no existe")
    prioridad = db.query(models.Priority).filter(models.Priority.id == ticket.priority_id).first()
    if not prioridad:
        raise HTTPException(status_code=400, detail="La prioridad no existe")
    area_id, department_id = ticket_route(db, owner, ticket.area_id, ticket.unsure)

    db_ticket = models.Ticket(
        title=ticket.title,
        description=ticket.description,
        category_id=ticket.category_id,
        priority_id=ticket.priority_id,
        status="new",
        created_by=owner.id,
        contact_phone=ticket.contact_phone or owner.phone,
        contact_id_number=owner.id_number,
        source="web",
    )
    db.add(db_ticket)
    db.flush()
    routing.set_route(db, db_ticket.id, area_id, department_id)
    # Desde el primer ticket, nombre y cédula quedan fijos
    identity.lock(db, owner)
    db.commit()
    db.refresh(db_ticket)

    # Primero la agrupación: si se fusiona, el ticket queda cerrado y el análisis y la asignación lo saltan
    if config.GROUPING_ENABLED:
        background_tasks.add_task(grouping.after_ticket_created, db_ticket.id)
    # Mismo análisis que los tickets por correo, después de responder: no espera al modelo.
    # La asignación automática va después del análisis, para que el nivel ya exista.
    if config.AUTO_ASSIGN_ENABLED:
        background_tasks.add_task(auto_assign.analyze_then_assign, db_ticket.id)
    elif config.AI_ANALYSIS_ENABLED:
        background_tasks.add_task(email_analysis.analyze_ticket_by_id, db_ticket.id)
    return ticket_read(db_ticket, current_user)


@router.get("/tickets", response_model=list[TicketRead])
def list_tickets(
    status: Optional[Literal["new", "in_progress", "resolved", "closed"]] = None,
    priority_id: Optional[int] = None,
    category_id: Optional[int] = None,
    assigned_to: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    # Solicitante: los suyos; técnico: asignados y los que tuvo; administrador: ninguno
    if current_user.role == "admin":
        raise HTTPException(status_code=403, detail=ticket_access.NO_PERMISSION)
    privacy.check_accepted(db, current_user)
    query = ticket_access.filter_visible(db, current_user, db.query(models.Ticket))

    if status:
        query = query.filter(models.Ticket.status == status)

    if priority_id:
        query = query.filter(models.Ticket.priority_id == priority_id)

    if category_id:
        query = query.filter(models.Ticket.category_id == category_id)

    if assigned_to:
        query = query.filter(models.Ticket.assigned_to == assigned_to)

    if search:
        query = query.filter(
            (models.Ticket.title.ilike(f"%{search}%"))
            | (models.Ticket.description.ilike(f"%{search}%"))
        )

    query = query.order_by(models.Ticket.created_at.desc())
    tickets = query.all()
    activity = portal.last_activity(db, tickets)
    return [ticket_read(t, current_user, activity[t.id]) for t in tickets]


# exclude_unset: el solicitante no recibe la clave ai_analysis
@router.get("/tickets/{ticket_id}", response_model=TicketDetail, response_model_exclude_unset=True)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    privacy.check_accepted(db, current_user)
    db_ticket = get_visible_ticket(db, ticket_id, current_user)
    return ticket_detail(db, db_ticket, current_user)


@router.post("/tickets/{ticket_id}/view-conversation", response_model=TicketDetail, response_model_exclude_unset=True)
def view_conversation(ticket_id: int, data: ConversationViewRequest, db: Session = Depends(get_db),
                      current_user=Depends(get_current_user)):
    """Solo el administrador: lectura puntual con motivo, que queda en el Registro de cambios."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Solo el administrador usa Ver conversación")
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    # El motivo puede traer datos personales: se guarda enmascarado
    audit.record(
        db, current_user.id, ticket_access.CONVERSATION_ACTION,
        target_label=ticket_access.conversation_label(db_ticket.id), new=_redact(data.reason)[:150],
    )
    db.commit()
    return ticket_detail(db, db_ticket, current_user)


def ticket_detail(db: Session, db_ticket: models.Ticket, current_user) -> TicketDetail:
    ticket_id = db_ticket.id
    # Determinar qué campos de contacto mostrar según el rol del usuario que ve
    # El creador del ticket, coordinator y admin ven los campos completos
    # Un technician que es el assigned_to ve teléfono completo y cédula enmascarada
    # Un technician que no está asignado ve None para ambos
    # Un technician sin assigned_to ve None para ambos
    
    contact_phone, contact_id_number = contact_fields(db_ticket, current_user)

    history = (
        db.query(models.History)
        .filter(models.History.ticket_id == ticket_id)
        .order_by(models.History.id.asc())
        .all()
    )
    as_requester = ticket_access.as_requester(db, current_user, db_ticket)
    if as_requester:
        history = [h for h in history if h.field not in grouping.STAFF_HISTORY_FIELDS + ROUTE_FIELDS]
    comments_query = db.query(models.Comment).filter(models.Comment.ticket_id == ticket_id)
    if as_requester:
        # El solicitante nunca recibe los comentarios internos
        comments_query = internal_comments.not_internal(comments_query)
    comments = comments_query.order_by(models.Comment.id.asc()).all()
    internal = internal_comments.internal_ids(db, [c.id for c in comments])
    # Solo los adjuntos de los comentarios que esta persona puede ver (sin notas internas para el solicitante)
    files = attachments.by_comment(db, [c.id for c in comments])
    following = (
        db.query(models.TicketFollower)
        .filter(models.TicketFollower.ticket_id == ticket_id, models.TicketFollower.user_id == current_user.id)
        .first()
        is not None
    )
    extra = {}
    if current_user.role == "admin" or ticket_access.access(db, current_user, db_ticket) == ticket_access.READ:
        extra["read_only"] = True
    if (current_user.role in AI_ROLES or current_user.role == "admin") and not as_requester:
        row = db_ticket.ai_analysis
        extra["ai_analysis"] = ai_analysis_read(row, db_ticket) if row else None
    if not as_requester:
        # Solo el personal: el solicitante nunca recibe esta clave
        flags = account_reports.staff_flags(db, db.get(models.User, db_ticket.created_by))
        if flags is not None:
            extra["requester_flags"] = flags
        extra["due_state"], extra["consumed_pct"] = alerts.due_state(db_ticket, nav._now())
        extra["grouping"] = grouping.staff_view(db, db_ticket, current_user)
    merge = grouping.active_merge_of(db, db_ticket.id)
    if merge is not None:
        extra["merged_into"] = merge.target_ticket_id
    return TicketDetail(
        **ticket_read(db_ticket, current_user, portal.last_activity(db, [db_ticket])[db_ticket.id]).model_dump(),
        history=[HistoryRead.model_validate(h) for h in history],
        comments=[comment_read(c, db_ticket, current_user, c.id in internal, files.get(c.id, ())) for c in comments],
        contact_phone=contact_phone,
        contact_id_number=contact_id_number,
        following=following,
        people=ticket_people(db, db_ticket, comments, history),
        auto_assigned=auto_assign.is_auto_assigned(db, db_ticket),
        **extra,
    )


@router.patch("/tickets/{ticket_id}/ai-level", response_model=AIAnalysisRead)
def update_ai_level(ticket_id: int, data: AILevelUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role not in AI_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para corregir el análisis")
    db_ticket = get_visible_ticket(db, ticket_id, current_user, full=True)
    row = db_ticket.ai_analysis
    if row is None:
        raise HTTPException(status_code=404, detail="Este ticket no tiene análisis de la IA")
    # Solo el análisis cambia: prioridad, estado y asignación quedan igual
    row.human_level = data.level
    row.human_level_by = current_user.id
    row.due_at = email_analysis.compute_due_at(db_ticket.created_at, data.level)
    db.commit()
    db.refresh(row)
    return ai_analysis_read(row, db_ticket)


@router.get("/tickets/{ticket_id}/suggestion", response_model=Optional[SuggestionRead])
def get_suggestion(ticket_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Sugerencia de responsable con la lógica de la asignación automática. No asigna nada."""
    if current_user.role not in OPERATION_ROLES:
        raise HTTPException(status_code=403, detail="No tiene permiso para ver sugerencias de asignación")
    db_ticket = get_visible_ticket(db, ticket_id, current_user)
    found = nav.suggestion(db, db_ticket)
    return SuggestionRead(**found) if found else None


@router.post("/tickets/{ticket_id}/follow", status_code=204)
def follow_ticket(ticket_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    get_visible_ticket(db, ticket_id, current_user)
    exists = (
        db.query(models.TicketFollower)
        .filter(models.TicketFollower.ticket_id == ticket_id, models.TicketFollower.user_id == current_user.id)
        .first()
    )
    if not exists:
        db.add(models.TicketFollower(ticket_id=ticket_id, user_id=current_user.id))
        db.commit()
    return Response(status_code=204)


@router.delete("/tickets/{ticket_id}/follow", status_code=204)
def unfollow_ticket(ticket_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    get_visible_ticket(db, ticket_id, current_user)
    follower = (
        db.query(models.TicketFollower)
        .filter(models.TicketFollower.ticket_id == ticket_id, models.TicketFollower.user_id == current_user.id)
        .first()
    )
    if follower:
        db.delete(follower)
        db.commit()
    return Response(status_code=204)


def check_can_comment(db: Session, ticket_id: int, current_user, internal: bool) -> models.Ticket:
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    if db_ticket.status == "closed":
        raise HTTPException(status_code=400, detail="No se puede comentar un ticket cerrado")
    privacy.check_accepted(db, current_user)
    # Regla: el solicitante solo comenta en los tickets que él creó
    if current_user.role == "requester" and db_ticket.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Solo puedes comentar en tus propios tickets")
    # Técnico: solo el asignado; el administrador no comenta. El personal comenta su propio ticket
    level = check_access(db, db_ticket, current_user, full=True, allow_own=True)
    if internal and (current_user.role not in nav.STAFF_ROLES or level == ticket_access.OWN):
        raise HTTPException(status_code=403, detail="Solo el personal puede agregar notas internas")
    return db_ticket


def create_comment(db: Session, background_tasks: BackgroundTasks, db_ticket: models.Ticket, current_user,
                   content: str, internal: bool, files=()) -> tuple[models.Comment, list]:
    """Guarda el comentario y sus adjuntos (ya validados) en un solo commit."""
    if internal:
        # Nota interna: no se avisa a los seguidores ni al solicitante
        db_comment = internal_comments.add_internal(db, db_ticket.id, current_user.id, content)
    else:
        db_comment = models.Comment(ticket_id=db_ticket.id, user_id=current_user.id, content=content)
        db.add(db_comment)
        db.flush()
    rows = attachments.save(db, db_comment, current_user.id, files) if files else []
    try:
        db.commit()
    except Exception:
        attachments.discard(rows)
        raise
    db.refresh(db_comment)
    if not internal:
        lines = [db_comment.content] if db_comment.content else []
        if rows:
            lines.append(f"Adjuntó {len(rows)} archivo(s).")
        # El solicitante recibe los archivos; los demás seguidores, solo la línea "Adjuntó N archivo(s)."
        notify_followers(
            db, background_tasks, db_ticket, current_user,
            f"[Ticket #{db_ticket.id}] Nuevo comentario de {current_user.name}",
            lines, files=[(attachments.clean_name(name), data, kind) for name, data, kind in files],
        )
    return db_comment, rows


@router.post("/tickets/{ticket_id}/comments", response_model=CommentRead)
def add_comment(ticket_id: int, comment: CommentCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    db_ticket = check_can_comment(db, ticket_id, current_user, comment.internal)
    db_comment, _ = create_comment(db, background_tasks, db_ticket, current_user, comment.content, comment.internal)
    if comment.internal:
        return comment_read(db_comment, db_ticket, current_user, internal=True)
    return db_comment


@router.post("/tickets/{ticket_id}/comments/with-attachments", response_model=CommentRead)
async def add_comment_with_attachments(
    ticket_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Comentario con adjuntos (multipart: content, internal, files). Todo o nada: si un archivo no sirve, no se guarda nada."""
    # Antes de leer el cuerpo: más de lo que pueden pesar 5 archivos de 10 MB (con margen para el formulario)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > attachments.MAX_FILES * attachments.MAX_BYTES + 1024 * 1024:
        raise HTTPException(status_code=413, detail="Los archivos pesan demasiado: máximo 10 MB por archivo y 5 archivos.")
    try:
        form = await request.form(max_files=attachments.MAX_FILES + 1, max_fields=10)
    except MultiPartException as exc:
        detail = attachments.TOO_MANY if str(exc).startswith("Too many files") else "No se pudo leer el formulario"
        raise HTTPException(status_code=400, detail=detail)
    try:
        content = str(form.get("content") or "").strip()
        internal = str(form.get("internal") or "").lower() in ("true", "1")
        uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
        db_ticket = check_can_comment(db, ticket_id, current_user, internal)
        if len(uploads) > attachments.MAX_FILES:
            raise HTTPException(status_code=400, detail=attachments.TOO_MANY)
        if not content and not uploads:
            raise HTTPException(status_code=400, detail="El comentario no puede estar vacío")
        files = []
        for upload in uploads:
            data = await upload.read(attachments.MAX_BYTES + 1)
            try:
                content_type = attachments.validate(upload.filename, data)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            files.append((upload.filename, data, content_type))
    finally:
        await form.close()
    db_comment, rows = create_comment(db, background_tasks, db_ticket, current_user, content, internal, files)
    return comment_read(db_comment, db_ticket, current_user, internal, rows)


@router.post("/tickets/cancel", response_model=list[TicketRead])
def cancel_tickets(
    data: TicketCancelRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancela uno o varios tickets con un solo motivo. Todo o nada: si uno no se puede, no cambia ninguno."""
    cancellations.require_can_cancel(current_user)
    ids = list(dict.fromkeys(data.ticket_ids))
    tickets = {t.id: t for t in db.query(models.Ticket).filter(models.Ticket.id.in_(ids)).all()}
    missing = [i for i in ids if i not in tickets]
    if missing:
        raise HTTPException(status_code=404, detail=f"El ticket #{missing[0]} no existe")
    for ticket_id in ids:
        cancellations.check_can_be_cancelled(tickets[ticket_id])
    note = data.note.strip() if data.note else None
    for ticket_id in ids:
        cancellations.cancel(db, tickets[ticket_id], current_user, data.reason, note)
    db.commit()

    reason_label = models.CANCEL_REASONS[data.reason]
    result = []
    for ticket_id in ids:
        ticket = tickets[ticket_id]
        db.refresh(ticket)
        notify_followers(
            db, background_tasks, ticket, current_user,
            f"[Ticket #{ticket.id}] Cancelado",
            [f"{current_user.name} canceló el ticket. Motivo: {reason_label}."],
            exclude_ids=(ticket.created_by,) if data.notify_requester else (),
        )
        requester = db.get(models.User, ticket.created_by)
        if data.notify_requester and requester and requester.is_active and requester.role == "requester":
            # Solo número, título y motivo: nunca teléfono ni cédula ni la nota interna
            body = "\n".join([
                f"Hola {display_name(requester)}.",
                "",
                f"Su ticket #{ticket.id} «{ticket.title}» fue cancelado.",
                f"Motivo: {reason_label}.",
                "",
                "Si todavía necesita ayuda, puede crear una solicitud nueva.",
                f"Puede ver sus solicitudes en {config.APP_URL}",
            ])
            background_tasks.add_task(mailer.send_email, requester.email, f"[Ticket #{ticket.id}] Cancelado", body)
        result.append(ticket_read(ticket, current_user))
    return result


@router.post("/tickets/{ticket_id}/reopen", response_model=TicketRead)
def reopen_ticket(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Devuelve un ticket cancelado a la cola: Nuevo y sin responsable."""
    cancellations.require_can_cancel(current_user)
    ticket = get_visible_ticket(db, ticket_id, current_user)
    cancellations.reopen(db, ticket, current_user)
    db.commit()
    db.refresh(ticket)
    return ticket_read(ticket, current_user)


ASSIGNEE_OUTSIDE_AREA = "Esa persona no pertenece al área de este ticket"


def routing_allows_assignee(db: Session, ticket: models.Ticket, user: models.User) -> bool:
    if not routing.enforced(db):
        return True
    row = routing.routing_of(db, ticket.id)
    if row is None:
        return False
    if user.role == "coordinator":
        area = routing.coordinator_area(db, user)
        return area is not None and area.id == row.area_id
    return routing.is_placed(db, user) and routing.technician_in_area(db, user, row.area_id)


@router.patch("/tickets/{ticket_id}", response_model=TicketRead)
def update_ticket(ticket_id: int, data: TicketUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")

    # Si ticket está cerrado, no se puede modificar
    if db_ticket.status == "closed":
        raise HTTPException(status_code=400, detail="No se puede modificar un ticket cerrado")

    # Regla: el solicitante no puede cambiar estado, prioridad, categoría ni asignación
    if current_user.role == "requester":
        raise HTTPException(status_code=403, detail="Los solicitantes no pueden modificar tickets")
    # Técnico: solo el que tiene asignado; el administrador no entra al flujo de tickets
    check_access(db, db_ticket, current_user, full=True)

    # Transiciones de status permitidas
    if data.status is not None:
        allowed_transitions = {
            "new": ["in_progress"],
            "in_progress": ["resolved"],
            "resolved": ["closed", "in_progress"],
        }
        current_status = db_ticket.status
        if data.status not in allowed_transitions.get(current_status, []):
            raise HTTPException(status_code=400, detail="Cambio de estado no permitido")

    # Regla 2 - No se inicia sin asignar:
    # En el PATCH, calcula assigned_final = data.assigned_to si viene en la petición, si no db_ticket.assigned_to. 
    # Si data.status es "in_progress" y assigned_final is None, responde 400 con detail exactamente "Ticket sin asignar. Asigne para iniciar". 
    # Esta validación va después de comprobar que la transición de estado es válida y antes de guardar cambios.
    assigned_final = data.assigned_to if data.assigned_to is not None else db_ticket.assigned_to
    if data.status == "in_progress" and assigned_final is None:
        raise HTTPException(status_code=400, detail="Ticket sin asignar. Asigne para iniciar")

    # Validar que assigned_to, priority_id, category_id existan si se proporcionan
    assignee = None
    if data.assigned_to is not None:
        user = db.query(models.User).filter(models.User.id == data.assigned_to).first()
        assignee = user
        # Una cuenta desactivada no recibe tickets
        if not user or not user.is_active:
            raise HTTPException(status_code=400, detail="El usuario asignado no existe")
        # Regla: solo técnicos y coordinadores pueden ser asignados
        if user.role not in ("technician", "coordinator"):
            raise HTTPException(status_code=400, detail="Solo se puede asignar a técnicos o coordinadores")
        # Regla: asignar y reasignar es del coordinador (el técnico solo atiende lo suyo)
        if current_user.role not in OPERATION_ROLES and data.assigned_to != db_ticket.assigned_to:
            raise HTTPException(status_code=403, detail="Solo un coordinador puede asignar tickets")
        # Con el enrutamiento: solo técnicos con departamento dentro del área del ticket (o su coordinador)
        if data.assigned_to != db_ticket.assigned_to and not routing_allows_assignee(db, db_ticket, user):
            raise HTTPException(status_code=400, detail=ASSIGNEE_OUTSIDE_AREA)
        # Se permite asignar a un técnico no disponible, pero solo con confirmación explícita
        if (
            data.assigned_to != db_ticket.assigned_to
            and not data.confirm_unavailable
            and not availability.is_available(db, user)
        ):
            status_, return_date = availability.get(db, user)
            raise HTTPException(status_code=409, detail=unavailable_message(user, status_, return_date))
        # Regla 3: Asignar en cualquier estado abierto (new, in_progress, resolved).
        # (El coordinador debe poder asignar o reasignar el ticket a un técnico o coordinador en los estados "new", "in_progress" y "resolved" - no en "closed", que ya da error).
        # Anteriormente ¿había alguna restricción para el coordinador que impedía asignar cuando el estado no es "new"?
        # Revisemos el código anterior: no había restricción de estado para asignar para el coordinador, solo que si el ticket estaba cerrado daba error arriba (si db_ticket.status == "closed"). Pero por las dudas, verifiquemos si hay algo más o si ya cumple.


    if data.priority_id is not None:
        priority = db.query(models.Priority).filter(models.Priority.id == data.priority_id).first()
        if not priority:
            raise HTTPException(status_code=400, detail="La prioridad no existe")

    if data.category_id is not None:
        category = db.query(models.Category).filter(models.Category.id == data.category_id).first()
        if not category:
            raise HTTPException(status_code=400, detail="La categoría no existe")

    # Capturar valores antes de modificar
    old_status = db_ticket.status
    old_assigned_to = db_ticket.assigned_to
    old_priority_id = db_ticket.priority_id
    old_category_id = db_ticket.category_id

    # Aplicar cambios solo si son diferentes
    if data.status is not None and data.status != db_ticket.status:
        db_ticket.status = data.status
        if data.status != old_status:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="status",
                old_value=old_status,
                new_value=data.status,
                changed_by=current_user.id,
            ))

    if data.assigned_to is not None and data.assigned_to != db_ticket.assigned_to:
        db_ticket.assigned_to = data.assigned_to
        # Asignado a mano: el sistema ya no lo asigna
        auto_assign.cancel_waiting(db, db_ticket.id)
        if data.assigned_to != old_assigned_to:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="assigned_to",
                old_value=str(old_assigned_to) if old_assigned_to is not None else None,
                new_value=str(data.assigned_to) if data.assigned_to is not None else None,
                changed_by=current_user.id,
            ))

    if data.priority_id is not None and data.priority_id != db_ticket.priority_id:
        db_ticket.priority_id = data.priority_id
        if data.priority_id != old_priority_id:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="priority_id",
                old_value=str(old_priority_id) if old_priority_id is not None else None,
                new_value=str(data.priority_id) if data.priority_id is not None else None,
                changed_by=current_user.id,
            ))

    if data.category_id is not None and data.category_id != db_ticket.category_id:
        db_ticket.category_id = data.category_id
        if data.category_id != old_category_id:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="category_id",
                old_value=str(old_category_id) if old_category_id is not None else None,
                new_value=str(data.category_id) if data.category_id is not None else None,
                changed_by=current_user.id,
            ))

    db.commit()
    db.refresh(db_ticket)

    # Aviso a los seguidores: un solo correo aunque cambien estado y asignación
    lines = []
    subject = None
    if db_ticket.status != old_status:
        new_label = STATUS_LABELS.get(db_ticket.status, db_ticket.status)
        old_label = STATUS_LABELS.get(old_status, old_status)
        lines.append(f"{current_user.name} cambió el estado de {old_label} a {new_label}.")
        subject = f"[Ticket #{db_ticket.id}] Cambió de estado: {new_label}"
    notified = ()
    if db_ticket.assigned_to != old_assigned_to and assignee is not None:
        lines.append(f"{current_user.name} asignó el ticket a {assignee.name}.")
        subject = subject or f"[Ticket #{db_ticket.id}] Nueva asignación"
        # "Asignarme a mí" no se avisa
        if assignment_notice.should_notify(assignee, current_user):
            assignment_notice.schedule(background_tasks, db_ticket, assignee)
            notified = (assignee.id,)
    if lines:
        notify_followers(db, background_tasks, db_ticket, current_user, subject, lines, exclude_ids=notified)

    # Tickets relacionados (posible incidente): se resuelven y se asignan junto con el principal
    if db_ticket.status == "resolved" and old_status != "resolved":
        mails = grouping.resolve_related(db, db_ticket, current_user)
        db.commit()
        for to, mail_subject, body in mails:
            background_tasks.add_task(mailer.send_email, to, mail_subject, body)
    if db_ticket.assigned_to != old_assigned_to and db_ticket.assigned_to is not None:
        assigned = grouping.propagate_assignment(db, db_ticket, current_user)
        db.commit()
        for related, related_assignee in assigned:
            if assignment_notice.should_notify(related_assignee, current_user):
                assignment_notice.schedule(background_tasks, related, related_assignee)
    return ticket_read(db_ticket, current_user)
