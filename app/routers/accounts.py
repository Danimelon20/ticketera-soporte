"""Gestión de cuentas por rol y solicitudes con doble aprobación (coordinador pide, administrador aprueba)."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from .. import accounts, audit, availability, config, schedules, hacienda, identity, mailer, models, throttle
from ..schemas import (
    AccountRequestCreate, AccountRequestRead, AvailabilityUpdate, RejectRequest, ScheduleUpdate, SignupStarted,
)
from ..security import get_current_user, mask_email, unusable_password_hash
from ..validators import mask_last4, normalize_id_number
from .admin import require_admin
from .auth import _can_resend, _now, normalize_email, send_reset_code

router = APIRouter(prefix="/api", tags=["accounts"])

WAIT_MESSAGE = "Espere un minuto antes de pedir otro código."
INACTIVE_ACCOUNT = "La cuenta está desactivada. Reactívela primero."
DUPLICATE_REQUEST = "Esta cuenta ya tiene una solicitud pendiente de ese tipo"
NOT_PENDING = "La solicitud ya fue resuelta"
OWN_REQUEST = "No puedes resolver tu propia solicitud"
NOT_LOCKED = "El solicitante todavía puede corregirlo desde su perfil"
REQUEST_ROLES = ("requester", "technician", "coordinator")
KIND_LABELS = {"role": "Cambio de rol", "name": "Corrección de nombre", "id_number": "Corrección de cédula"}


def _target(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="El usuario no existe")
    return user


@router.post("/accounts/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate(
    user_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "active")
    accounts.set_active(db, current_user, user, False, background_tasks)
    db.commit()
    return None


@router.post("/accounts/{user_id}/reactivate", status_code=status.HTTP_204_NO_CONTENT)
def reactivate(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "active")
    accounts.set_active(db, current_user, user, True)
    db.commit()
    return None


@router.post("/accounts/{user_id}/reset-access", response_model=SignupStarted, status_code=status.HTTP_202_ACCEPTED)
def reset_access(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "reset")
    if not user.is_active:
        raise HTTPException(status_code=400, detail=INACTIVE_ACCOUNT)
    reset = db.query(models.PasswordReset).filter(models.PasswordReset.user_id == user.id).first()
    if reset is not None and not _can_resend(reset.sent_at):
        raise HTTPException(status_code=429, detail=WAIT_MESSAGE)
    # Restablecimiento forzado: fuera de todas sus sesiones y sin contraseña válida hasta usar el código
    user.password_hash = unusable_password_hash()
    accounts.close_sessions(db, user.id)
    audit.record(db, current_user.id, "access_reset_forced", target=user)
    # El código va solo al correo de la persona: la respuesta nunca lo trae (send_reset_code hace commit)
    send_reset_code(db, user, requested_by=current_user)
    return SignupStarted(email_hint=mask_email(user.email))


@router.put("/accounts/{user_id}/availability", status_code=status.HTTP_204_NO_CONTENT)
def change_availability(
    user_id: int, data: AvailabilityUpdate, db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "availability")
    availability.set_availability(db, current_user, user, data.status, data.return_date)
    db.commit()
    return None


@router.put("/accounts/{user_id}/schedule", status_code=status.HTTP_204_NO_CONTENT)
def change_schedule(
    user_id: int, data: ScheduleUpdate, db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "schedule")
    schedules.set_schedule(db, current_user, user, [d.model_dump() for d in data.days])
    db.commit()
    return None


@router.post("/accounts/{user_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
def unlock(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "unlock")
    email = normalize_email(user.email)
    if throttle.is_locked(db, email):
        audit.record(db, current_user.id, "login_unlocked", target=user)
    throttle.reset(db, email)
    db.commit()
    return None


@router.post("/accounts/{user_id}/close-sessions", status_code=status.HTTP_204_NO_CONTENT)
def close_sessions(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Cierra todas las sesiones de la cuenta en todos sus dispositivos."""
    user = _target(db, user_id)
    accounts.check_can_manage(current_user, user, "sessions")
    accounts.close_sessions(db, user.id)
    audit.record(db, current_user.id, "sessions_closed", target=user)
    db.commit()
    return None


# ----- Solicitudes con doble aprobación -----

def _shown_value(row: models.AccountRequest, viewer: models.User) -> str:
    # La cédula nueva completa solo la ve el administrador mientras está pendiente
    if row.kind == "id_number" and not (viewer.role == "admin" and row.status == "pending"):
        return mask_last4(row.new_value) if row.status == "pending" else row.new_value
    return row.new_value


def _request_read(row: models.AccountRequest, viewer: models.User) -> AccountRequestRead:
    def _name(user: models.User | None) -> str | None:
        return (user.name or mask_email(user.email)) if user is not None else None

    return AccountRequestRead(
        id=row.id,
        kind=row.kind,
        target_user_id=row.target_user_id,
        target_name=_name(row.target) or "—",
        created_by_name=_name(row.creator) or "—",
        reason=row.reason,
        new_value=_shown_value(row, viewer),
        old_value=row.old_value,
        status=row.status,
        resolved_by_name=_name(row.resolver),
        resolved_at=row.resolved_at,
        rejection_reason=row.rejection_reason,
        created_at=row.created_at,
    )


def _check_name(name: str) -> None:
    if len(name.split()) < 2 or len(name) > 100:
        raise HTTPException(status_code=422, detail="Escriba el nombre completo (nombre y apellidos)")


def _normalized_id(value: str) -> str:
    try:
        return normalize_id_number(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _active_admin_emails(db: Session) -> list[str]:
    admins = db.query(models.User).filter(models.User.role == "admin", models.User.is_active.is_(True))
    return [u.email for u in admins.order_by(models.User.id)]


@router.post("/account-requests", response_model=AccountRequestRead, status_code=status.HTTP_201_CREATED)
def create_request(
    data: AccountRequestCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != "coordinator":
        raise HTTPException(status_code=403, detail="Solo el coordinador crea solicitudes")
    user = _target(db, data.target_user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=403, detail=accounts.SELF_CHANGE)

    if data.kind == "role":
        if user.role not in ("technician", "requester"):
            raise HTTPException(status_code=403, detail=accounts.NO_PERMISSION)
        if data.new_value not in REQUEST_ROLES:
            raise HTTPException(status_code=422, detail="Rol no válido")
        if data.new_value == user.role:
            raise HTTPException(status_code=400, detail="La cuenta ya tiene ese rol")
        new_value, old_value = data.new_value, user.role
    else:
        if user.role != "requester":
            raise HTTPException(status_code=403, detail=accounts.NO_PERMISSION)
        if not identity.is_locked(db, user):
            db.commit()
            raise HTTPException(status_code=400, detail=NOT_LOCKED)
        if data.kind == "name":
            _check_name(data.new_value)
            new_value, old_value = data.new_value, user.name or None
        else:
            new_value = _normalized_id(data.new_value)
            if new_value == user.id_number:
                raise HTTPException(status_code=400, detail="Esa ya es la cédula de la cuenta")
            if identity.id_number_taken(db, new_value, user.id):
                raise HTTPException(status_code=400, detail=identity.ID_IN_USE)
            old_value = mask_last4(user.id_number)

    pending = db.query(models.AccountRequest).filter(
        models.AccountRequest.target_user_id == user.id,
        models.AccountRequest.kind == data.kind,
        models.AccountRequest.status == "pending",
    ).first()
    if pending is not None:
        raise HTTPException(status_code=409, detail=DUPLICATE_REQUEST)

    row = models.AccountRequest(
        kind=data.kind, target_user_id=user.id, created_by=current_user.id, reason=data.reason,
        new_value=new_value, old_value=old_value, status="pending", created_at=_now(),
    )
    db.add(row)
    audit.record(db, current_user.id, "request_created", target=user, new=KIND_LABELS[data.kind])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_REQUEST) from None
    db.refresh(row)

    # Sin la cédula ni el motivo: el administrador los ve en la página
    body = "\n".join([
        f"{current_user.name} creó una solicitud: {KIND_LABELS[row.kind]} de {user.name or mask_email(user.email)}.",
        "",
        f"Revísela en Administración → Solicitudes pendientes: {config.APP_URL}",
    ])
    for email in _active_admin_emails(db):
        background_tasks.add_task(mailer.send_email, email, "[Ticketera] Nueva solicitud pendiente", body)
    return _request_read(row, current_user)


@router.get("/account-requests", response_model=list[AccountRequestRead])
def list_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")
    query = db.query(models.AccountRequest)
    if current_user.role == "coordinator":
        query = query.filter(models.AccountRequest.created_by == current_user.id)
    if status_filter in models.ACCOUNT_REQUEST_STATUSES:
        query = query.filter(models.AccountRequest.status == status_filter)
    rows = query.order_by(models.AccountRequest.created_at.desc(), models.AccountRequest.id.desc()).limit(200).all()
    return [_request_read(r, current_user) for r in rows]


def _pending_request(db: Session, request_id: int, admin: models.User) -> models.AccountRequest:
    row = db.get(models.AccountRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="La solicitud no existe")
    if row.created_by == admin.id:
        raise HTTPException(status_code=403, detail=OWN_REQUEST)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=NOT_PENDING)
    return row


def _apply(db: Session, admin: models.User, row: models.AccountRequest) -> None:
    """Aplica el cambio con las validaciones normales. No hace commit."""
    user = _target(db, row.target_user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=403, detail=accounts.SELF_CHANGE)
    if row.kind == "role":
        accounts.change_role(db, admin, user, row.new_value)
        return
    if row.kind == "name":
        _check_name(row.new_value)
        audit.record(db, admin.id, "name_changed", target=user, old=user.name or None, new=row.new_value)
        user.name = row.new_value
        if user.id_number:
            identity.save_hacienda_result(db, user, user.name, user.id_number)
        return
    id_number = _normalized_id(row.new_value)
    if identity.id_number_taken(db, id_number, user.id):
        raise HTTPException(status_code=400, detail=identity.ID_IN_USE)
    # Modo tolerante: si Hacienda no la encuentra o no responde, se acepta igual
    error = hacienda.check_id_number(id_number, tolerant=True)
    if error:
        raise HTTPException(status_code=400, detail=error)
    audit.record(
        db, admin.id, "id_number_changed", target=user,
        old=audit.masked_digits(user.id_number), new=audit.masked_digits(id_number),
    )
    user.id_number = id_number
    if (user.name or "").strip():
        identity.save_hacienda_result(db, user, user.name, id_number)


def _resolve(
    db: Session, admin: models.User, row: models.AccountRequest, approved: bool,
    background_tasks: BackgroundTasks, rejection_reason: str | None = None,
) -> None:
    row.status = "approved" if approved else "rejected"
    row.resolved_by = admin.id
    row.resolved_at = _now()
    row.rejection_reason = rejection_reason
    if row.kind == "id_number":
        row.new_value = mask_last4(row.new_value)
    audit.record(
        db, admin.id, "request_approved" if approved else "request_rejected",
        target=row.target, new=KIND_LABELS[row.kind],
    )
    db.commit()
    db.refresh(row)
    creator = row.creator
    if creator is not None and creator.is_active:
        result = "aprobó" if approved else "rechazó"
        lines = [f"El administrador {result} su solicitud: {KIND_LABELS[row.kind]} de {row.target.name}."]
        if rejection_reason:
            lines += ["", f"Motivo: {rejection_reason}"]
        lines += ["", f"Puede verla en {config.APP_URL}"]
        background_tasks.add_task(
            mailer.send_email, creator.email, "[Ticketera] Su solicitud fue resuelta", "\n".join(lines)
        )


@router.post("/account-requests/{request_id}/approve", response_model=AccountRequestRead)
def approve_request(
    request_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin=Depends(require_admin)
):
    row = _pending_request(db, request_id, admin)
    # Si falla una validación (p. ej. la cédula ya se usó) la solicitud sigue pendiente
    _apply(db, admin, row)
    _resolve(db, admin, row, True, background_tasks)
    return _request_read(row, admin)


@router.post("/account-requests/{request_id}/reject", response_model=AccountRequestRead)
def reject_request(
    request_id: int,
    data: RejectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    row = _pending_request(db, request_id, admin)
    _resolve(db, admin, row, False, background_tasks, data.reason)
    return _request_read(row, admin)
