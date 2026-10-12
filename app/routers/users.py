from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..schemas import UserRead


router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=list[UserRead])
def get_users(db: Session = Depends(get_db)):
    return db.query(models.User).filter(models.User.is_active == True).all()