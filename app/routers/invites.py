from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..database import get_db
from .. import audit, models, routing
from ..schemas import StaffInvitationCreate, StaffInvitationRead
from ..security import get_current_user, mask_email
from .auth import _can_resend, _find_user, _now, normalize_email, send_invitation


router = APIRouter(prefix="/api", tags=["invites"])


def _check_can_invite(current_user: models.User, role: str) -> None:
    # Las cuentas las cuida el administrador: el coordinador ya no invita
    if current_user.role == "admin":
        return
    raise HTTPException(status_code=403, detail="Solo el administrador puede invitar personal")


def _record_sent(db: Session, actor: models.User, invitation: models.StaffInvitation) -> None:
    audit.record(
        db, actor.id, "invitation_sent",
        target_label=mask_email(invitation.email), new=audit.role_name(invitation.role),
    )


def _registered_message(user: models.User, invited_role: str) -> str:
    """El correo ya tiene cuenta: dice con qué rol, sin revelar más."""
    if user.role == "requester":
        return "Ese correo es de una cuenta de cliente: no se puede invitar como personal."
    current = audit.role_name(user.role)
    if user.role == invited_role:
        return f"Esa persona ya está registrada como {current}."
    return f"Esa persona ya está registrada con el rol {current}. Cambie su rol desde Acciones en Cuentas."


def _invitation_read(
    invitation: models.StaffInvitation, email_sent: bool | None = None, registered: bool = False,
    placement: str | None = None,
) -> StaffInvitationRead:
    if invitation.used_by is not None:
        state = "used"
    elif registered:
        # No se usó, pero el correo ya tiene cuenta: no se reenvía
        state = "registered"
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
        department_name=placement,
    )


def _placement_of(db: Session, invitation: models.StaffInvitation, by_id: dict) -> str | None:
    row = routing.invitation_placement(db, invitation.id)
    if row is None:
        return None
    return routing.path_label(by_id.get(row.department_id), by_id)


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
    existing = _find_user(db, email)
    if existing is not None:
        raise HTTPException(status_code=409, detail=_registered_message(existing, data.role))
    # Departamento obligatorio (que atienda tickets); un coordinador por departamento
    department_id = routing.validate_invitation_placement(db, data.role, data.department_id, email)

    # Una sola invitación vigente por correo: la anterior deja de servir
    db.query(models.StaffInvitation).filter(
        models.StaffInvitation.email == email, models.StaffInvitation.used_by.is_(None)
    ).delete()
    invitation = models.StaffInvitation(email=email, name=data.name, role=data.role, created_by=current_user.id)
    db.add(invitation)
    sent = send_invitation(invitation)
    db.flush()
    routing.save_invitation_placement(db, invitation, department_id)
    _record_sent(db, current_user, invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_read(invitation, email_sent=sent,
                            placement=_placement_of(db, invitation, routing.departments_by_id(db)))


@router.get("/staff-invitations", response_model=list[StaffInvitationRead])
def list_invitations(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="No tienes permiso")
    query = db.query(models.StaffInvitation)
    invitations = query.order_by(models.StaffInvitation.created_at.desc(), models.StaffInvitation.id.desc()).all()
    emails = {i.email for i in invitations if i.used_by is None}
    registered = {
        email for (email,) in db.query(func.lower(models.User.email)).filter(func.lower(models.User.email).in_(emails))
    } if emails else set()
    by_id = routing.departments_by_id(db)
    return [_invitation_read(i, registered=i.email.lower() in registered, placement=_placement_of(db, i, by_id))
            for i in invitations]


def _own_invitation(db: Session, current_user: models.User, invitation_id: int) -> models.StaffInvitation:
    """Solo el administrador las gestiona (todas)."""
    _check_can_invite(current_user, "")
    invitation = db.get(models.StaffInvitation, invitation_id)
    if invitation is None:
        raise HTTPException(status_code=404, detail="La invitación no existe")
    if invitation.used_by is not None:
        raise HTTPException(status_code=409, detail="Esta invitación ya se usó: la cuenta está activada")
    return invitation


@router.post("/staff-invitations/{invitation_id}/resend", response_model=StaffInvitationRead)
def resend_invitation(
    invitation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invitation = _own_invitation(db, current_user, invitation_id)
    # La cuenta ya existe (se activó por otra vía o cambió de rol): nada que activar
    existing = _find_user(db, normalize_email(invitation.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail=_registered_message(existing, invitation.role))
    if not _can_resend(invitation.sent_at):
        raise HTTPException(status_code=429, detail="Espera un minuto antes de reenviarla.")
    sent = send_invitation(invitation)
    _record_sent(db, current_user, invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_read(invitation, email_sent=sent,
                            placement=_placement_of(db, invitation, routing.departments_by_id(db)))


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
