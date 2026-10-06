from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..schemas import CategoryRead, PriorityRead


router = APIRouter(prefix="/api", tags=["catalogs"])


@router.get("/categories", response_model=list[CategoryRead])
def get_categories(db: Session = Depends(get_db)):
    return db.query(models.Category).filter(models.Category.is_active == True).all()


@router.get("/priorities", response_model=list[PriorityRead])
def get_priorities(db: Session = Depends(get_db)):
    return db.query(models.Priority).order_by(models.Priority.level.asc()).all()