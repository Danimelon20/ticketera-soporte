from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import availability, models
from ..schemas import RequesterRead, StaffRead, UserRead
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["users"])


STAFF_ROLES = ("technician", "coordinator", "admin")


def _technician_statuses(db: Session, users: list[models.User]) -> dict[int, str]:
    found = availability.statuses_for(db, availability.ids_with_status(users))
    return {uid: value[0] for uid, value in found.items()}


@router.get("/users", response_model=list[UserRead | StaffRead])
def get_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role in ("coordinator", "admin"):
        users = db.query(models.User).filter(models.User.is_active == True).all()
        statuses = _technician_statuses(db, users)
        return [UserRead.model_validate(u).model_copy(update={"availability": statuses.get(u.id)}) for u in users]
    if current_user.role == "technician":
        # Solo el personal activo y sin correos; nunca la lista de solicitantes
        staff = (
            db.query(models.User)
            .filter(models.User.is_active == True, models.User.role.in_(STAFF_ROLES))
            .order_by(models.User.name)
            .all()
        )
        statuses = _technician_statuses(db, staff)
        return [StaffRead(id=u.id, name=u.name, role=u.role, availability=statuses.get(u.id)) for u in staff]
    raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")


@router.get("/requesters", response_model=list[RequesterRead])
def get_requesters(q: Optional[str] = None, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    # Para crear un ticket a nombre de un cliente: solo el coordinador
    if current_user.role != "coordinator":
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")
    
    query = db.query(models.User).filter(models.User.role == "requester", models.User.is_active == True)
    
    if q:
        query = query.filter(
            (models.User.name.ilike(f"%{q}%")) | (models.User.email.ilike(f"%{q}%"))
        )
    
    return query.order_by(models.User.name).limit(20).all()
