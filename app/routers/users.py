from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import audit, availability, identity, models
from ..schemas import MeRead, ProfileUpdate, RequesterRead, StaffRead, UserRead
from ..security import get_current_user
from .auth import me_read

router = APIRouter(prefix="/api", tags=["users"])


STAFF_ROLES = ("technician", "coordinator", "admin")


def _technician_statuses(db: Session, users: list[models.User]) -> dict[int, str]:
    found = availability.statuses_for(db, [u.id for u in users if u.role == "technician"])
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
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")
    
    query = db.query(models.User).filter(models.User.role == "requester", models.User.is_active == True)
    
    if q:
        query = query.filter(
            (models.User.name.ilike(f"%{q}%")) | (models.User.email.ilike(f"%{q}%"))
        )
    
    return query.order_by(models.User.name).limit(20).all()


@router.put("/profile", response_model=MeRead)
def update_profile(data: ProfileUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role != "requester":
        raise HTTPException(status_code=403, detail="Solo los clientes completan este perfil")

    name_changed = data.name != " ".join((current_user.name or "").split())
    identity_changed = name_changed or data.id_number != current_user.id_number
    if identity_changed and identity.is_locked(db, current_user):
        # Guarda la fecha de bloqueo si recién se calculó (cuentas con tickets anteriores)
        db.commit()
        raise HTTPException(status_code=403, detail=identity.LOCKED)
    if name_changed and len(data.name.split()) < 2:
        raise HTTPException(status_code=422, detail="Escriba su nombre completo (nombre y apellidos)")
    if data.id_number != current_user.id_number:
        error = identity.check_id_number(db, current_user, data.id_number)
        if error:
            raise HTTPException(status_code=400, detail=error)

    if data.phone != current_user.phone:
        audit.record(
            db, current_user.id, "phone_changed", target=current_user,
            old=audit.masked_digits(current_user.phone), new=audit.masked_digits(data.phone),
        )
    current_user.phone = data.phone
    if identity_changed:
        if name_changed:
            audit.record(db, current_user.id, "name_changed", target=current_user, old=current_user.name or None, new=data.name)
        if data.id_number != current_user.id_number:
            audit.record(
                db, current_user.id, "id_number_changed", target=current_user,
                old=audit.masked_digits(current_user.id_number), new=audit.masked_digits(data.id_number),
            )
        current_user.name = data.name
        current_user.id_number = data.id_number
        identity.save_hacienda_result(db, current_user, data.name, data.id_number)
    db.commit()
    db.refresh(current_user)
    return me_read(db, current_user)
