"""Mi cuenta: contraseña, teléfono y correo de la persona que inició sesión (todos los roles)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from .. import audit, availability, config, mailer, models, throttle
from ..validators import INTERNAL_EMAIL, is_internal_email
from ..schemas import AvailabilityPeriodCreate, CodeRequest, EmailChangeRequest, MeRead, PasswordChange, PhoneUpdate, SignupStarted
from ..security import (
    generate_login_code, get_current_session, get_current_user, hash_code, hash_password,
    mask_email, password_min, verify_password,
)
from .auth import (
    CODE_INVALID, CONFIRM_MINUTES, MAX_CODE_ATTEMPTS, STAFF_USE_MICROSOFT, _can_resend, _code_matches, _find_user, _now,
    me_read, normalize_email, send_code_email,
)

router = APIRouter(prefix="/api/account", tags=["account"])

WRONG_PASSWORD = "La contraseña actual no es correcta"
OLD_EMAIL_NOTICE = "El correo de su cuenta cambió; si no fue usted, contacte a soporte."
EMAIL_IN_USE = "Ese correo ya está en uso por otra cuenta. Use otro correo."
WAIT_MESSAGE = "Espere un minuto antes de pedir otro código."


def _check_password(db: Session, user: models.User, password: str) -> None:
    """Contraseña actual: si es incorrecta suma al mismo contador del bloqueo por intentos."""
    email = normalize_email(user.email)
    if throttle.is_locked(db, email):
        raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
    if not verify_password(password, user.password_hash):
        if throttle.register_failure(db, email, user):
            raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
        raise HTTPException(status_code=400, detail=WRONG_PASSWORD)


def _close_other_sessions(db: Session, user: models.User, current: models.Session) -> None:
    db.query(models.Session).filter(models.Session.user_id == user.id, models.Session.id != current.id).delete()


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    data: PasswordChange,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    current_session: models.Session = Depends(get_current_session),
):
    if config.MS_LOGIN_ENABLED and current_user.role in ("technician", "coordinator"):
        raise HTTPException(status_code=403, detail=STAFF_USE_MICROSOFT)
    _check_password(db, current_user, data.current_password)
    minimum = password_min(current_user.role)
    if len(data.new_password) < minimum:
        raise HTTPException(status_code=422, detail=f"La contraseña debe tener al menos {minimum} caracteres")
    current_user.password_hash = hash_password(data.new_password)
    _close_other_sessions(db, current_user, current_session)
    audit.record(db, current_user.id, "password_changed", target=current_user)
    db.commit()
    return None


# La disponibilidad es automática: aquí solo se registran o quitan vacaciones e incapacidades propias
@router.post("/availability-periods", response_model=MeRead, status_code=status.HTTP_201_CREATED)
def add_own_period(
    data: AvailabilityPeriodCreate, db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role not in availability.ROLES:
        raise HTTPException(status_code=403, detail=availability.NOT_STAFF)
    availability.add_period(db, current_user, current_user, data.kind, data.start_date, data.end_date)
    db.commit()
    return me_read(db, current_user)


@router.delete("/availability-periods/{period_id}", response_model=MeRead)
def remove_own_period(
    period_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user),
):
    if current_user.role not in availability.ROLES:
        raise HTTPException(status_code=403, detail=availability.NOT_STAFF)
    availability.remove_period(db, current_user, current_user, period_id)
    db.commit()
    return me_read(db, current_user)


@router.put("/phone", response_model=MeRead)
def change_phone(data: PhoneUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if data.phone != current_user.phone:
        audit.record(
            db, current_user.id, "phone_changed", target=current_user,
            old=audit.masked_digits(current_user.phone), new=audit.masked_digits(data.phone),
        )
        current_user.phone = data.phone
        db.commit()
        db.refresh(current_user)
    return me_read(db, current_user)


def _pending(db: Session, user: models.User) -> models.EmailChange | None:
    return db.query(models.EmailChange).filter(models.EmailChange.user_id == user.id).first()


def _send_change_code(change: models.EmailChange) -> None:
    code = generate_login_code()
    now = _now()
    change.code_hash = hash_code(code)
    change.expires_at = now + timedelta(minutes=CONFIRM_MINUTES)
    change.attempts = 0
    change.sent_at = now
    send_code_email(
        change.new_email,
        "Confirme su nuevo correo de la Ticketera",
        f"Su código para confirmar este correo es: {code}. Vence en {CONFIRM_MINUTES} minutos.\n\n"
        "Si usted no pidió cambiar el correo de su cuenta, ignore este mensaje.",
        code,
    )


@router.post("/email", response_model=SignupStarted, status_code=status.HTTP_202_ACCEPTED)
def request_email_change(
    data: EmailChangeRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    _check_password(db, current_user, data.current_password)
    new_email = normalize_email(data.new_email)
    if "@" not in new_email:
        raise HTTPException(status_code=422, detail="Escriba un correo válido")
    if new_email == normalize_email(current_user.email):
        raise HTTPException(status_code=400, detail="Ese ya es el correo de su cuenta")
    if current_user.role == "requester" and is_internal_email(new_email):
        raise HTTPException(status_code=400, detail=INTERNAL_EMAIL)

    # La espera cuenta para correos libres y en uso: así no se pueden probar muchos correos seguidos
    attempt = db.query(models.EmailChangeAttempt).filter(models.EmailChangeAttempt.user_id == current_user.id).first()
    if attempt is not None and not _can_resend(attempt.requested_at):
        raise HTTPException(status_code=429, detail=WAIT_MESSAGE)
    if attempt is None:
        attempt = models.EmailChangeAttempt(user_id=current_user.id)
        db.add(attempt)
    attempt.requested_at = _now()

    if _find_user(db, new_email) is not None:
        # No se crea cambio pendiente ni se envía nada
        db.commit()
        raise HTTPException(status_code=409, detail=EMAIL_IN_USE)

    change = _pending(db, current_user)
    if change is None:
        change = models.EmailChange(user_id=current_user.id)
        db.add(change)
    change.new_email = new_email
    _send_change_code(change)
    db.commit()
    return SignupStarted(email_hint=mask_email(new_email))


@router.post("/email/resend", response_model=SignupStarted, status_code=status.HTTP_202_ACCEPTED)
def resend_email_change(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    change = _pending(db, current_user)
    if change is None:
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    if not _can_resend(change.sent_at):
        raise HTTPException(status_code=429, detail=WAIT_MESSAGE)
    if change.code_hash == "!":
        change.sent_at = _now()
    else:
        _send_change_code(change)
    db.commit()
    return SignupStarted(email_hint=mask_email(change.new_email))


@router.post("/email/verify", response_model=MeRead)
def confirm_email_change(
    data: CodeRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    current_session: models.Session = Depends(get_current_session),
):
    old_email = current_user.email
    if throttle.is_locked(db, normalize_email(old_email)):
        raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
    change = _pending(db, current_user)
    if (
        change is None
        or change.code_hash == "!"
        or change.expires_at <= _now()
        or change.attempts >= MAX_CODE_ATTEMPTS
    ):
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    if not _code_matches(data.code, change.code_hash):
        change.attempts += 1
        if throttle.register_failure(db, normalize_email(old_email), current_user):
            raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    # Cambio pedido antes de agregar el dominio a INTERNAL_DOMAINS
    if current_user.role == "requester" and is_internal_email(change.new_email):
        db.delete(change)
        db.commit()
        raise HTTPException(status_code=400, detail=INTERNAL_EMAIL)
    if _find_user(db, change.new_email) is not None:
        db.delete(change)
        db.commit()
        raise HTTPException(status_code=400, detail=CODE_INVALID)

    current_user.email = change.new_email
    db.delete(change)
    _close_other_sessions(db, current_user, current_session)
    audit.record(
        db, current_user.id, "email_changed", target=current_user,
        old=audit.masked_email(old_email), new=audit.masked_email(current_user.email),
    )
    db.commit()
    db.refresh(current_user)
    mailer.send_email(old_email, "El correo de su cuenta cambió", OLD_EMAIL_NOTICE)
    return me_read(db, current_user)
