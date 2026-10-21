from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..database import get_db
from .. import audit, models
from ..schemas import StaffInvitationCreate, StaffInvitationRead
from ..security import get_current_user, mask_email
from .auth import _can_resend, _find_user, _now, normalize_email, send_invitation


router = APIRouter(prefix="/api", tags=["invites"])


def _check_can_invite(current_user: models.User, role: str) -> None:
    if current_user.role == "admin":
        return
    if current_user.role == "coordinator":
        if role == "coordinator":
            raise HTTPException(status_code=403, detail="Solo el administrador puede invitar coordinadores")
        return
    raise HTTPException(status_code=403, detail="No tienes permiso para invitar personal")


def _record_sent(db: Session, actor: models.User, invitation: models.StaffInvitation) -> None:
    audit.record(
        db, actor.id, "invitation_sent",
        target_label=mask_email(invitation.email), new=audit.role_name(invitation.role),
    )


def _invitation_read(invitation: models.StaffInvitation, email_sent: bool | None = None) -> StaffInvitationRead:
    if invitation.used_by is not None:
        state = "used"
    elif invitation.expires_at <= _now():
        state = "expired"
    else:
        state = "available"
    return StaffInvitationRead(
        id=invitation.id,
        email=invitation.email,
        name=invitation.name,
        role=invitation.role,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        status=state,
        email_sent=email_sent,
    )


@router.post("/staff-invitations", response_model=StaffInvitationRead, status_code=status.HTTP_201_CREATED)
def create_invitation(
    data: StaffInvitationCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_can_invite(current_user, data.role)
    email = normalize_email(data.email)
    if "@" not in email:
        raise HTTPException(status_code=422, detail="Escriba un correo válido")
    if _find_user(db, email) is not None:
        raise HTTPException(status_code=409, detail="Ese correo ya tiene una cuenta. Cambie su rol desde el Directorio.")

    # Una sola invitación vigente por correo: la anterior deja de servir
    db.query(models.StaffInvitation).filter(
        models.StaffInvitation.email == email, models.StaffInvitation.used_by.is_(None)
    ).delete()
    invitation = models.StaffInvitation(email=email, name=data.name, role=data.role, created_by=current_user.id)
    db.add(invitation)
    sent = send_invitation(invitation)
    _record_sent(db, current_user, invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_read(invitation, email_sent=sent)


@router.get("/staff-invitations", response_model=list[StaffInvitationRead])
def list_invitations(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso")
    query = db.query(models.StaffInvitation)
    if current_user.role == "coordinator":
        query = query.filter(models.StaffInvitation.created_by == current_user.id)
    invitations = query.order_by(models.StaffInvitation.created_at.desc(), models.StaffInvitation.id.desc()).all()
    return [_invitation_read(i) for i in invitations]


def _own_invitation(db: Session, current_user: models.User, invitation_id: int) -> models.StaffInvitation:
    """El administrador gestiona todas; el coordinador, solo las suyas y nunca una de coordinador."""
    invitation = db.get(models.StaffInvitation, invitation_id)
    if invitation is None or (current_user.role != "admin" and invitation.created_by != current_user.id):
        raise HTTPException(status_code=404, detail="La invitación no existe")
    _check_can_invite(current_user, invitation.role)
    if invitation.used_by is not None:
        raise HTTPException(status_code=409, detail="Esta invitación ya se usó")
    return invitation


@router.post("/staff-invitations/{invitation_id}/resend", response_model=StaffInvitationRead)
def resend_invitation(
    invitation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invitation = _own_invitation(db, current_user, invitation_id)
    if not _can_resend(invitation.sent_at):
        raise HTTPException(status_code=429, detail="Espera un minuto antes de reenviarla.")
    sent = send_invitation(invitation)
    _record_sent(db, current_user, invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_read(invitation, email_sent=sent)


@router.delete("/staff-invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_invitation(
    invitation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """El código deja de servir: la invitación se borra (todavía no es una cuenta)."""
    invitation = _own_invitation(db, current_user, invitation_id)
    audit.record(
        db, current_user.id, "invitation_cancelled",
        target_label=mask_email(invitation.email), new=audit.role_name(invitation.role),
    )
    db.delete(invitation)
    db.commit()
    return None
