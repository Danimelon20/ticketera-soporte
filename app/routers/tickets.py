from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Literal, Optional
from ..database import get_db
from .. import models
from ..schemas import TicketCreate, TicketRead, TicketUpdate, TicketDetail, HistoryRead
router = APIRouter(prefix="/api", tags=["tickets"])


@router.post("/tickets", response_model=TicketRead)
def create_ticket(ticket: TicketCreate, db: Session = Depends(get_db)):
    categoria = db.query(models.Category).filter(models.Category.id == ticket.category_id).first()
    if not categoria:
        raise HTTPException(status_code=400, detail="Invalid category_id")
    prioridad = db.query(models.Priority).filter(models.Priority.id == ticket.priority_id).first()
    if not prioridad:
        raise HTTPException(status_code=400, detail="Invalid priority_id")
    db_ticket = models.Ticket(
        title=ticket.title,
        description=ticket.description,
        category_id=ticket.category_id,
        priority_id=ticket.priority_id,
        status="new",
    )
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    return db_ticket


@router.get("/tickets", response_model=list[TicketRead])
def list_tickets(
    status: Optional[Literal["new", "in_progress", "resolved", "closed"]] = None,
    priority_id: Optional[int] = None,
    category_id: Optional[int] = None,
    assigned_to: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Ticket)

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
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    history = (
        db.query(models.History)
        .filter(models.History.ticket_id == ticket_id)
        .order_by(models.History.id.asc())
        .all()
    )
    return TicketDetail(
        **TicketRead.model_validate(db_ticket).model_dump(),
        history=[HistoryRead.model_validate(h) for h in history],
    )


@router.patch("/tickets/{ticket_id}", response_model=TicketRead)
def update_ticket(ticket_id: int, data: TicketUpdate, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Verificar que actor_id sea un usuario existente
    actor = db.query(models.User).filter(models.User.id == data.actor_id).first()
    if not actor:
        raise HTTPException(status_code=400, detail="Invalid actor_id")

    # Si ticket está cerrado, no se puede modificar
    if db_ticket.status == "closed":
        raise HTTPException(status_code=400, detail="Cannot modify closed ticket")

    # Transiciones de status permitidas
    if data.status is not None:
        allowed_transitions = {
            "new": ["in_progress"],
            "in_progress": ["resolved"],
            "resolved": ["closed", "in_progress"],
        }
        current_status = db_ticket.status
        if data.status not in allowed_transitions.get(current_status, []):
            raise HTTPException(status_code=400, detail="Invalid status transition")

    # Validar que assigned_to, priority_id, category_id existan si se proporcionan
    if data.assigned_to is not None:
        user = db.query(models.User).filter(models.User.id == data.assigned_to).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid assigned_to")

    if data.priority_id is not None:
        priority = db.query(models.Priority).filter(models.Priority.id == data.priority_id).first()
        if not priority:
            raise HTTPException(status_code=400, detail="Invalid priority_id")

    if data.category_id is not None:
        category = db.query(models.Category).filter(models.Category.id == data.category_id).first()
        if not category:
            raise HTTPException(status_code=400, detail="Invalid category_id")

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