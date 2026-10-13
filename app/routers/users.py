from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..schemas import RequesterRead, UserRead
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=list[UserRead])
def get_users(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    return db.query(models.User).filter(models.User.is_active == True).all()


@router.get("/requesters", response_model=list[RequesterRead])
def get_requesters(q: Optional[str] = None, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")
    
    query = db.query(models.User).filter(models.User.role == "requester", models.User.is_active == True)
    
    if q:
        query = query.filter(
            (models.User.name.ilike(f"%{q}%")) | (models.User.email.ilike(f"%{q}%"))
        )
    
    return query.order_by(models.User.name).limit(20).all()