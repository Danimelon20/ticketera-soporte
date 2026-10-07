from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Literal, Optional
from ..database import get_db
from .. import models
from ..schemas import TicketCreate, TicketRead

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


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return db_ticket