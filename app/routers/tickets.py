from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
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


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket_id).first()
    if not db_ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return db_ticket