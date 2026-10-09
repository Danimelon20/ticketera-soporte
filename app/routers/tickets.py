from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Literal, Optional
from ..database import get_db
from .. import models
from ..schemas import TicketCreate, TicketRead, TicketUpdate, TicketDetail, HistoryRead, CommentCreate, CommentRead
router = APIRouter(prefix="/api", tags=["tickets"])


def get_actor(db: Session, actor_id: int) -> models.User:
    """Busca al usuario que hace la petición; 400 si no existe."""
    actor = db.query(models.User).filter(models.User.id == actor_id).first()
    if not actor:
        raise HTTPException(status_code=400, detail="El usuario que realiza la acción no existe")
    return actor


@router.post("/tickets", response_model=TicketRead)
def create_ticket(ticket: TicketCreate, db: Session = Depends(get_db)):
    actor = get_actor(db, ticket.actor_id)
    categoria = db.query(models.Category).filter(models.Category.id == ticket.category_id).first()
    if not categoria:
        raise HTTPException(status_code=400, detail="La categoría no existe")
    prioridad = db.query(models.Priority).filter(models.Priority.id == ticket.priority_id).first()
    if not prioridad:
        raise HTTPException(status_code=400, detail="La prioridad no existe")
    db_ticket = models.Ticket(
        title=ticket.title,
        description=ticket.description,
        category_id=ticket.category_id,
        priority_id=ticket.priority_id,
        status="new",
        created_by=actor.id,
    )
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    return db_ticket


@router.get("/tickets", response_model=list[TicketRead])
def list_tickets(
    actor_id: int,
    status: Optional[Literal["new", "in_progress", "resolved", "closed"]] = None,
    priority_id: Optional[int] = None,
    category_id: Optional[int] = None,
    assigned_to: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    actor = get_actor(db, actor_id)
    query = db.query(models.Ticket)

    # Regla: el solicitante solo ve los tickets que él creó
    if actor.role == "requester":
        query = query.filter(models.Ticket.created_by == actor.id)

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


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: int, actor_id: int, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    actor = get_actor(db, actor_id)
    # Regla: el solicitante no puede abrir tickets ajenos
    if actor.role == "requester" and db_ticket.created_by != actor.id:
        raise HTTPException(status_code=403, detail="No tienes permiso para ver este ticket")
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
    return TicketDetail(
        **TicketRead.model_validate(db_ticket).model_dump(),
        history=[HistoryRead.model_validate(h) for h in history],
        comments=[CommentRead.model_validate(c) for c in comments],
    )

@router.post("/tickets/{ticket_id}/comments", response_model=CommentRead)
def add_comment(ticket_id: int, comment: CommentCreate, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    if db_ticket.status == "closed":
        raise HTTPException(status_code=400, detail="No se puede comentar un ticket cerrado")
    actor = db.query(models.User).filter(models.User.id == comment.actor_id).first()
    if not actor:
        raise HTTPException(status_code=400, detail="El usuario que realiza la acción no existe")
        # Regla: el solicitante solo comenta en los tickets que él creó
    if actor.role == "requester" and db_ticket.created_by != actor.id:
        raise HTTPException(status_code=403, detail="Solo puedes comentar en tus propios tickets")
    db_comment = models.Comment(
        ticket_id=ticket_id,
        user_id=comment.actor_id,
        content=comment.content,
    )
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    return db_comment

@router.patch("/tickets/{ticket_id}", response_model=TicketRead)
def update_ticket(ticket_id: int, data: TicketUpdate, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="El ticket no existe")

    # Verificar que actor_id sea un usuario existente
    actor = db.query(models.User).filter(models.User.id == data.actor_id).first()
    if not actor:
        raise HTTPException(status_code=400, detail="El usuario que realiza la acción no existe")

    # Si ticket está cerrado, no se puede modificar
    if db_ticket.status == "closed":
        raise HTTPException(status_code=400, detail="No se puede modificar un ticket cerrado")

            # Regla: el solicitante no puede cambiar estado, prioridad, categoría ni asignación
    if actor.role == "requester":
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

    # Validar que assigned_to, priority_id, category_id existan si se proporcionan
    if data.assigned_to is not None:
        user = db.query(models.User).filter(models.User.id == data.assigned_to).first()
        if not user:
            raise HTTPException(status_code=400, detail="El usuario asignado no existe")
                # Regla: solo técnicos y coordinadores pueden ser asignados
        if user.role not in ("technician", "coordinator"):
            raise HTTPException(status_code=400, detail="Solo se puede asignar a técnicos o coordinadores")
        # Reglas: el técnico solo puede asignarse a sí mismo un ticket sin asignar
        if actor.role == "technician":
            if data.assigned_to != actor.id:
                raise HTTPException(status_code=403, detail="Un técnico solo puede asignarse tickets a sí mismo")
            if db_ticket.assigned_to is not None and db_ticket.assigned_to != actor.id:
                raise HTTPException(status_code=403, detail="Este ticket ya está asignado a otra persona")

    if data.priority_id is not None:
        priority = db.query(models.Priority).filter(models.Priority.id == data.priority_id).first()
        if not priority:
            raise HTTPException(status_code=400, detail="La prioridad no existe")

    if data.category_id is not None:
        category = db.query(models.Category).filter(models.Category.id == data.category_id).first()
        if not category:
            raise HTTPException(status_code=400, detail="La categoría no existe")

    # Registrar cambios en history y aplicar actualizaciones en una sola transacción
    from datetime import datetime, timezone

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
                changed_by=data.actor_id,
            ))

    if data.assigned_to is not None and data.assigned_to != db_ticket.assigned_to:
        db_ticket.assigned_to = data.assigned_to
        if data.assigned_to != old_assigned_to:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="assigned_to",
                old_value=str(old_assigned_to) if old_assigned_to is not None else None,
                new_value=str(data.assigned_to) if data.assigned_to is not None else None,
                changed_by=data.actor_id,
            ))

    if data.priority_id is not None and data.priority_id != db_ticket.priority_id:
        db_ticket.priority_id = data.priority_id
        if data.priority_id != old_priority_id:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="priority_id",
                old_value=str(old_priority_id) if old_priority_id is not None else None,
                new_value=str(data.priority_id) if data.priority_id is not None else None,
                changed_by=data.actor_id,
            ))

    if data.category_id is not None and data.category_id != db_ticket.category_id:
        db_ticket.category_id = data.category_id
        if data.category_id != old_category_id:
            db.add(models.History(
                ticket_id=db_ticket.id,
                field="category_id",
                old_value=str(old_category_id) if old_category_id is not None else None,
                new_value=str(data.category_id) if data.category_id is not None else None,
                changed_by=data.actor_id,
            ))

    db.commit()
    db.refresh(db_ticket)
    return db_ticket