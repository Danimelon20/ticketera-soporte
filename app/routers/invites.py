from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..schemas import InviteCreate, InviteCreated, InviteRead
from ..security import get_current_user, generate_invite_code, hash_code


router = APIRouter(prefix="/api", tags=["invites"])


@router.post("/invites", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
def create_invite(
    invite_data: InviteCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role == "coordinator":
        if invite_data.role == "coordinator":
            raise HTTPException(status_code=403, detail="Solo el administrador puede invitar coordinadores")
    elif current_user.role == "admin":
        pass
    else:
        raise HTTPException(status_code=403, detail="No tienes permiso para generar códigos")

    code = generate_invite_code(invite_data.role)
    c_hash = hash_code(code)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + timedelta(days=7)

    db_invite = models.InviteCode(
        code_hash=c_hash,
        role=invite_data.role,
        created_by=current_user.id,
        expires_at=expires_at
    )
    db.add(db_invite)
    db.commit()
    db.refresh(db_invite)

    return InviteCreated(
        code=code,
        role=db_invite.role,
        expires_at=db_invite.expires_at
    )


@router.get("/invites", response_model=list[InviteRead])
def list_invites(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso")

    query = db.query(models.InviteCode)
    if current_user.role == "coordinator":
        query = query.filter(models.InviteCode.created_by == current_user.id)

    invites = query.order_by(models.InviteCode.created_at.desc()).all()
    return invites
