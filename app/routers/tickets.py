from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from typing import Literal, Optional
from ..database import get_db
from .. import models
from ..schemas import TicketCreate, TicketRead, TicketUpdate, TicketDetail, HistoryRead, CommentCreate, CommentRead, AIAnalysisRead, AILevelUpdate
from ..security import get_current_user
from ..validators import mask_id_number
from .. import hacienda
from ..notifications import STATUS_LABELS, notify_followers
from .. import email_analysis

AI_ROLES = ("technician", "coordinator", "admin")

router = APIRouter(prefix="/api", tags=["tickets"])


def verify_id_number(id_number: str) -> None:
    """Verifica en Hacienda que la cédula (ya normalizada) sea física o DIMEX."""
    error = hacienda.check_id_number(id_number)
    if error:
        raise HTTPException(status_code=400, detail=error)


def get_visible_ticket(db: Session, ticket_id: int, current_user) -> models.Ticket:
    """Mismas reglas que el detalle: 404 si no existe, 403 si no puede verlo."""
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    # Regla: el solicitante no puede abrir tickets ajenos
    if current_user.role == "requester" and db_ticket.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="No tienes permiso para ver este ticket")
    return db_ticket


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


@router.post("/tickets", response_model=TicketRead)
def create_ticket(ticket: TicketCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role == "technician":
        raise HTTPException(status_code=403, detail="Los técnicos no pueden crear tickets")
    
    if ticket.requester_id is not None:
        if current_user.role not in ("coordinator", "admin"):
            raise HTTPException(status_code=403, detail="No puedes crear tickets a nombre de otra persona")
        requester = db.query(models.User).filter(models.User.id == ticket.requester_id).first()
        if not requester or requester.role != "requester":
            raise HTTPException(status_code=400, detail="El solicitante no existe")
        created_by = ticket.requester_id
    else:
        created_by = current_user.id
    
    categoria = db.query(models.Category).filter(models.Category.id == ticket.category_id).first()
    if not categoria:
        raise HTTPException(status_code=400, detail="La categoría no existe")
    prioridad = db.query(models.Priority).filter(models.Priority.id == ticket.priority_id).first()
    if not prioridad:
        raise HTTPException(status_code=400, detail="La prioridad no existe")

    verify_id_number(ticket.contact_id_number)
    
    db_ticket = models.Ticket(
        title=ticket.title,
        description=ticket.description,
        category_id=ticket.category_id,
        priority_id=ticket.priority_id,
        status="new",
        created_by=created_by,
        contact_phone=ticket.contact_phone,
        contact_id_number=ticket.contact_id_number,
        source="web",
    )
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    
    # Si el dueño del ticket tiene phone o id_number vacíos, guárdalos en su perfil
    owner = db.query(models.User).filter(models.User.id == db_ticket.created_by).first()
    if owner:
        if not owner.phone:
            owner.phone = ticket.contact_phone
        if not owner.id_number:
            owner.id_number = ticket.contact_id_number
        db.add(owner)
        db.commit()
    
    return db_ticket


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
    query = db.query(models.Ticket)

    # Regla: el solicitante solo ve los tickets que él creó
    if current_user.role == "requester":
        query = query.filter(models.Ticket.created_by == current_user.id)

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
    return query.all()


# exclude_unset: el solicitante no recibe la clave ai_analysis
@router.get("/tickets/{ticket_id}", response_model=TicketDetail, response_model_exclude_unset=True)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    db_ticket = get_visible_ticket(db, ticket_id, current_user)
    
    # Determinar qué campos de contacto mostrar según el rol del usuario que ve
    # El creador del ticket, coordinator y admin ven los campos completos
    # Un technician que es el assigned_to ve teléfono completo y cédula enmascarada
    # Un technician que no está asignado ve None para ambos
    # Un technician sin assigned_to ve None para ambos
    
    is_creator = current_user.role != "requester" or db_ticket.created_by == current_user.id
    is_coordinator_or_admin = current_user.role in ("coordinator", "admin")
    is_assigned_technician = (
        current_user.role == "technician"
        and db_ticket.assigned_to is not None
        and db_ticket.assigned_to == current_user.id
    )
    
    if is_coordinator_or_admin or (is_creator and current_user.role != "technician"):
        # Coordinator, admin, or creator: ver campos completos
        contact_phone = db_ticket.contact_phone
        contact_id_number = db_ticket.contact_id_number
    elif is_assigned_technician:
        # Técnico asignado: teléfono completo, cédula enmascarada
        contact_phone = db_ticket.contact_phone
        contact_id_number = mask_id_number(db_ticket.contact_id_number)
    else:
        # Técnico no asignado o rol desconocido: None para ambos
        contact_phone = None
        contact_id_number = None
    
    history = (
        db.query(models.History)
        .filter(models.History.ticket_id == ticket_id)
        .order_by(models.History.id.asc())
        .all()
    )
    comments = (
        db.query(models.Comment)
        .filter(models.Comment.ticket_id == ticket_id)
        .order_by(models.Comment.id.asc())
        .all()
    )
    following = (
        db.query(models.TicketFollower)
        .filter(models.TicketFollower.ticket_id == ticket_id, models.TicketFollower.user_id == current_user.id)
        .first()
        is not None
    )
    extra = {}
    if current_user.role in AI_ROLES:
        row = db_ticket.ai_analysis
        extra["ai_analysis"] = ai_analysis_read(row, db_ticket) if row else None
    return TicketDetail(
        **TicketRead.model_validate(db_ticket).model_dump(),
        history=[HistoryRead.model_validate(h) for h in history],
        comments=[CommentRead.model_validate(c) for c in comments],
        contact_phone=contact_phone,
        contact_id_number=contact_id_number,
        following=following,
        **extra,
    )


@router.patch("/tickets/{ticket_id}/ai-level", response_model=AIAnalysisRead)
def update_ai_level(ticket_id: int, data: AILevelUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role not in AI_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para corregir el análisis")
    db_ticket = get_visible_ticket(db, ticket_id, current_user)
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


@router.post("/tickets/{ticket_id}/comments", response_model=CommentRead)
def add_comment(ticket_id: int, comment: CommentCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    if db_ticket.status == "closed":
        raise HTTPException(status_code=400, detail="No se puede comentar un ticket cerrado")
    # Regla: el solicitante solo comenta en los tickets que él creó
    if current_user.role == "requester" and db_ticket.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Solo puedes comentar en tus propios tickets")
    db_comment = models.Comment(
        ticket_id=ticket_id,
        user_id=current_user.id,
        content=comment.content,
    )
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    notify_followers(
        db, background_tasks, db_ticket, current_user,
        f"[Ticket #{db_ticket.id}] Nuevo comentario de {current_user.name}",
        [db_comment.content],
    )
    return db_comment


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
        if not user:
            raise HTTPException(status_code=400, detail="El usuario asignado no existe")
        # Regla: solo técnicos y coordinadores pueden ser asignados
        if user.role not in ("technician", "coordinator"):
            raise HTTPException(status_code=400, detail="Solo se puede asignar a técnicos o coordinadores")
        # Reglas: el técnico solo puede asignarse a sí mismo un ticket sin asignar
        if current_user.role == "technician":
            if data.assigned_to != current_user.id:
                raise HTTPException(status_code=403, detail="Un técnico solo puede asignarse tickets a sí mismo")
            if db_ticket.assigned_to is not None and db_ticket.assigned_to != current_user.id:
                raise HTTPException(status_code=403, detail="Este ticket ya está asignado a otra persona")
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
    if db_ticket.assigned_to != old_assigned_to and assignee is not None:
        lines.append(f"{current_user.name} asignó el ticket a {assignee.name}.")
        subject = subject or f"[Ticket #{db_ticket.id}] Nueva asignación"
    if lines:
        notify_followers(db, background_tasks, db_ticket, current_user, subject, lines)
    return db_ticket
