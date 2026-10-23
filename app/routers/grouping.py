from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import attachments, grouping, models
from ..schemas import IncidentRead
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["grouping"])

INCIDENT_ROLES = ("coordinator",)


@router.post("/merges/{merge_id}/undo", status_code=204)
def undo_merge(merge_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    merge = db.get(models.TicketMerge, merge_id)
    if merge is None or merge.undone_at is not None:
        raise HTTPException(status_code=404, detail="La fusión no existe o ya se separó")
    source = db.get(models.Ticket, merge.source_ticket_id) if merge.source_ticket_id else None
    target = db.get(models.Ticket, merge.target_ticket_id)
    if not grouping.can_separate(current_user, source, target):
        raise HTTPException(status_code=403, detail=grouping.NO_PERMISSION)
    stored = grouping.undo_merge(db, merge, current_user)
    db.commit()
    # Los archivos copiados se borran solo cuando la base ya no los usa
    attachments.remove_files(stored)


@router.post("/links/{link_id}/undo", status_code=204)
def undo_link(link_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    link = db.get(models.TicketLink, link_id)
    if link is None or link.undone_at is not None:
        raise HTTPException(status_code=404, detail="La relación no existe o ya se separó")
    principal = db.get(models.Ticket, link.principal_ticket_id)
    related = db.get(models.Ticket, link.related_ticket_id)
    if not grouping.can_separate(current_user, principal, related):
        raise HTTPException(status_code=403, detail=grouping.NO_PERMISSION)
    grouping.undo_link(db, link, current_user)
    db.commit()


@router.get("/incidents", response_model=list[IncidentRead])
def list_incidents(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in INCIDENT_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para ver los incidentes")
    return grouping.incidents(db)
