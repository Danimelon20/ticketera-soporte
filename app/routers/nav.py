from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import nav
from ..schemas import NavCounts, SystemStatus
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["nav"])


def require_staff(current_user=Depends(get_current_user)):
    if current_user.role not in nav.STAFF_ROLES:
        raise HTTPException(status_code=403, detail="No tiene permiso para ver esta información")
    return current_user


# exclude_unset: cada rol recibe solo sus claves
@router.get("/nav/counts", response_model=NavCounts, response_model_exclude_unset=True)
def nav_counts(db: Session = Depends(get_db), current_user=Depends(require_staff)):
    return NavCounts(**nav.counts(db, current_user))


@router.get("/system/status", response_model=SystemStatus)
def system_status(db: Session = Depends(get_db), current_user=Depends(require_staff)):
    return SystemStatus(**nav.system_status(db))
