from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..schemas import UserCreate, UserRead


router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=list[UserRead])
def get_users(db: Session = Depends(get_db)):
    return db.query(models.User).filter(models.User.is_active == True).all()


@router.post("/users", response_model=UserRead)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    existente = db.query(models.User).filter(models.User.email == user.email).first()
    if existente:
        raise HTTPException(status_code=409, detail="El correo ya está registrado")
    db_user = models.User(name=user.name, email=user.email)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user