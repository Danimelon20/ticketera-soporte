from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from .. import cancellations, nav
from ..schemas import NavCounts, QueuePage, SystemStatus
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


@router.get("/queue", response_model=QueuePage)
def ticket_queue(
    view: Literal[nav.QUEUE_VIEWS] = "open",
    status: Optional[Literal["new", "in_progress", "resolved", "closed"]] = None,
    level: Optional[Literal["0", "1", "2", "3", "4", "none"]] = None,
    # 0: sin asignar. El técnico no filtra por responsable: se ignora
    assigned_to: Optional[int] = Query(default=None, ge=0),
    source: Optional[Literal["web", "email"]] = None,
    search: Optional[str] = Query(default=None, max_length=150),
    # Solo los tickets que creó ese cliente
    requester_id: Optional[int] = Query(default=None, ge=1),
    # Área de atención y departamento (con sus subdepartamentos)
    area_id: Optional[int] = Query(default=None, ge=1),
    department_id: Optional[int] = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    current_user=Depends(require_staff),
):
    # El administrador no entra al flujo de tickets; el técnico no ve los tickets sin asignar
    if current_user.role == "admin" or (view == "unassigned" and current_user.role == "technician"):
        raise HTTPException(status_code=403, detail="No tiene permiso para ver esta información")
    # Cancelados: solo quien puede cancelar y reabrir
    if view == "cancelled":
        cancellations.require_can_cancel(current_user)
    return QueuePage(**nav.queue(
        db, current_user, view=view, status=status, level=level, assigned_to=assigned_to,
        source=source, search=search, requester_id=requester_id, page=page,
        area_id=area_id, department_id=department_id,
    ))
