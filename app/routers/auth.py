import hmac
import logging
import uuid
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..database import get_db
from .. import accounts, audit, availability, config, entra_login, identity, mailer, models, routing, schedules, throttle
from ..schemas import (
    LoginRequest, AuthResponse, MeRead, UserRead,
    LoginChallengeResponse, VerifyCodeRequest, ResendCodeRequest,
    SignupStarted, EmailCodeRequest, EmailRequest, ActivationResult,
    PasswordResetRequest, PasswordResetConfirm,
)
from ..security import (
    hash_password, verify_password, new_token, get_current_user, hash_code, hash_token,
    generate_login_code, generate_password, mask_email,
)


router = APIRouter(prefix="/api", tags=["auth"])

TWO_FACTOR_ROLES = ("admin",)
# Un solo formulario de inicio: el rol sale de la cuenta. El Agente virtual y los contactos (rol requester: quien
# reporta por correo y las cuentas de cliente archivadas) no entran nunca; reciben el mismo error que una
# contraseña incorrecta, así no se revela si la cuenta existe
LOGIN_ROLES = ("admin", "coordinator", "technician")
# Con MS_LOGIN_ENABLED, técnicos y coordinadores entran solo con Microsoft
MICROSOFT_ONLY_ROLES = ("coordinator", "technician")
LOGIN_ERROR = "Correo o contraseña incorrectos"
# Para que un correo sin cuenta tarde lo mismo que uno con cuenta
_DUMMY_HASH = hash_password("contraseña-de-relleno")
CODE_MINUTES = 10
MAX_CODE_ATTEMPTS = 5
RESEND_SECONDS = 60
CODE_ERROR = "Error: Intente Nuevamente"
# Invitaciones del personal y restablecer la contraseña
CONFIRM_MINUTES = 15
INVITE_HOURS = 72
# Mismo mensaje para código incorrecto, vencido, con demasiados intentos o correo desconocido
CODE_INVALID = "El código no es válido o venció. Puede pedir uno nuevo."
ACTIVATE_PATH = "/#activar-cuenta"
# Abre directo el paso de escribir el código (el correo va en el fragmento: no llega al servidor)
RESET_PATH = "/#restablecer"
# Con MS_LOGIN_ENABLED, técnicos y coordinadores no usan contraseña
STAFF_USE_MICROSOFT = accounts.STAFF_USE_MICROSOFT


def uses_microsoft(user: models.User) -> bool:
    """El personal (técnico o coordinador) entra con Microsoft cuando está encendido; nunca con contraseña."""
    return config.MS_LOGIN_ENABLED and user.role in MICROSOFT_ONLY_ROLES


def _now() -> datetime:
    # La base guarda fechas UTC sin zona horaria
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _create_session(db: Session, user: models.User) -> AuthResponse:
    token = new_token()
    # Solo el hash queda en la base; el token en claro va únicamente en la respuesta
    db_session = models.Session(token=hash_token(token), user_id=user.id)
    db.add(db_session)
    db.flush()
    db.add(models.SessionActivity(session_id=db_session.id, last_used_at=_now()))
    db.commit()
    return AuthResponse(token=token, user=user)


def send_code_email(to: str, subject: str, body: str, code: str) -> bool:
    sent = mailer.send_email(to, subject, body)
    # Solo sin correo real; con el correo encendido el código nunca va a los logs
    if config.MAIL_TRANSPORT == "log":
        logging.warning("[SOLO DESARROLLO] Código para %s: %s", mask_email(to), code)
    return sent


def _send_code(user: models.User, code: str) -> None:
    send_code_email(
        user.email,
        "Código de verificación de la Ticketera",
        f"Tu código de verificación es: {code}. Vence en {CODE_MINUTES} minutos. "
        "Si no fuiste tú, ignora este correo y avisa al administrador.",
        code,
    )


def normalize_email(value: str) -> str:
    return value.strip().lower()


def _code_matches(code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_code((code or "").strip()), code_hash)


def _can_resend(sent_at: datetime | None) -> bool:
    return sent_at is None or (_now() - sent_at).total_seconds() >= RESEND_SECONDS


def _find_user(db: Session, email: str) -> models.User | None:
    return db.query(models.User).filter(func.lower(models.User.email) == email).first()


def start_challenge(db: Session, user: models.User) -> LoginChallengeResponse:
    code = generate_login_code()
    challenge = models.LoginChallenge(
        id=str(uuid.uuid4()),
        user_id=user.id,
        code_hash=hash_code(code),
        expires_at=_now() + timedelta(minutes=CODE_MINUTES),
        attempts=0,
        created_at=_now(),
    )
    db.add(challenge)
    db.commit()
    _send_code(user, code)
    return LoginChallengeResponse(challenge_id=challenge.id, email_hint=mask_email(user.email))


def _active_invitation(db: Session, email: str) -> models.StaffInvitation | None:
    return (
        db.query(models.StaffInvitation)
        .filter(models.StaffInvitation.email == email, models.StaffInvitation.used_by.is_(None))
        .order_by(models.StaffInvitation.id.desc())
        .first()
    )


def send_invitation(invitation: models.StaffInvitation) -> bool:
    """Código nuevo con 72 horas y correo con el enlace a la página de activación."""
    code = generate_login_code()
    now = _now()
    invitation.code_hash = hash_code(code)
    invitation.expires_at = now + timedelta(hours=INVITE_HOURS)
    invitation.attempts = 0
    invitation.sent_at = now
    role = "técnico" if invitation.role == "technician" else "coordinador"
    body = "\n".join([
        f"Buen día, {invitation.name}.",
        "",
        f"Le invitamos a la Ticketera de Soporte como {role}.",
        f"Para activar su cuenta entre a {config.APP_URL.rstrip('/')}{ACTIVATE_PATH}, "
        "escriba este correo y el código:",
        "",
        f"Código: {code}",
        "",
        f"El código vence en {INVITE_HOURS} horas. "
        + ("Al activarla entrará con su cuenta Microsoft." if config.MS_LOGIN_ENABLED
           else "Al activarla verá su contraseña una sola vez."),
    ])
    return send_code_email(invitation.email, "Active su cuenta de la Ticketera", body, code)


def valid_invitation(db: Session, email: str, code: str) -> models.StaffInvitation:
    """Invitación vigente con ese código (400 si no; un código incorrecto suma un intento). No la gasta."""
    invitation = _active_invitation(db, email)
    if invitation is None or invitation.expires_at <= _now() or invitation.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    if not _code_matches(code, invitation.code_hash):
        invitation.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    if _find_user(db, email) is not None:
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    return invitation


@router.post("/auth/activate", response_model=ActivationResult)
def activate_staff(data: EmailCodeRequest, response: Response, db: Session = Depends(get_db)):
    # Con Microsoft la invitación se activa en /auth/activate/microsoft, sin contraseña
    if config.MS_LOGIN_ENABLED:
        raise HTTPException(status_code=403, detail=STAFF_USE_MICROSOFT)
    email = normalize_email(data.email)
    invitation = valid_invitation(db, email, data.code)

    password = generate_password()
    user = models.User(name=invitation.name, email=email, role=invitation.role, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    invitation.used_by = user.id
    invitation.used_at = _now()
    audit.record(db, user.id, "invitation_used", target=user, new=audit.role_name(user.role))
    # Departamento elegido al invitar
    routing.apply_invitation_placement(db, invitation, user)
    db.commit()
    # Única respuesta con la contraseña: que el navegador no la guarde
    response.headers["Cache-Control"] = "no-store"
    return ActivationResult(email=email, password=password)


@router.post("/auth/activate/resend", response_model=SignupStarted, status_code=status.HTTP_202_ACCEPTED)
def resend_activation(data: EmailRequest, db: Session = Depends(get_db)):
    email = normalize_email(data.email)
    invitation = _active_invitation(db, email)
    if invitation is not None and _can_resend(invitation.sent_at):
        send_invitation(invitation)
        db.commit()
    return SignupStarted(email_hint=mask_email(email))


def reset_link(user: models.User) -> str:
    """Enlace al paso de escribir el código, con el correo ya puesto (un solo formulario de inicio)."""
    return f"{config.APP_URL.rstrip('/')}{RESET_PATH}?{urlencode({'correo': user.email})}"


def send_reset_code(db: Session, user: models.User, requested_by: models.User | None = None) -> bool:
    """Código nuevo de 15 minutos al correo de la persona. Quien lo pidió nunca lo ve. Hace commit."""
    reset = db.query(models.PasswordReset).filter(models.PasswordReset.user_id == user.id).first()
    if reset is None:
        reset = models.PasswordReset(user_id=user.id)
        db.add(reset)
    code = generate_login_code()
    now = _now()
    reset.code_hash = hash_code(code)
    reset.expires_at = now + timedelta(minutes=CONFIRM_MINUTES)
    reset.attempts = 0
    reset.sent_at = now
    reset.requested_by = requested_by.id if requested_by is not None else None
    db.commit()
    if requested_by is not None:
        intro = [
            "El equipo de soporte forzó el restablecimiento de la contraseña de su cuenta de la Ticketera.",
            "Su contraseña anterior ya no funciona: cree una nueva con este código.",
        ]
        closing = "Si tiene dudas, comuníquese con el equipo de soporte."
    else:
        intro = ["Recibimos una solicitud para restablecer la contraseña de su cuenta de la Ticketera."]
        closing = "Si usted no lo pidió, ignore este correo; su contraseña no cambia hasta que se use el código."
    body = "\n".join([
        *intro,
        "",
        f"Su código es: {code}. Vence en {CONFIRM_MINUTES} minutos.",
        f"Para escribirlo entre a {reset_link(user)}",
        "",
        closing,
    ])
    return send_code_email(user.email, "Restablecer el acceso a la Ticketera", body, code)


# Los contactos no tienen contraseña: el mismo 202 sin enviar nada
RESET_ROLES = ("admin", "coordinator", "technician")


def _reset_user(db: Session, email: str) -> models.User | None:
    """Cuenta activa que puede restablecer su contraseña; si no, None (sin revelar por qué).

    No hay pestañas: el código va al buzón del dueño de la cuenta. Antes, una pestaña recordada por el
    navegador (p. ej., Administración) hacía que el pedido de un técnico respondiera 202 sin enviar nada."""
    user = _find_user(db, email)
    if user is None or not user.is_active or user.role not in RESET_ROLES:
        return None
    # Con Microsoft, técnicos y coordinadores no tienen contraseña que restablecer
    if uses_microsoft(user):
        logging.info("Restablecimiento omitido para el usuario %s: el personal entra con Microsoft", user.id)
        return None
    return user


def _reset_row(db: Session, user: models.User | None) -> models.PasswordReset | None:
    if user is None:
        return None
    return db.query(models.PasswordReset).filter(models.PasswordReset.user_id == user.id).first()


RESET_SENT = "Si el correo tiene una cuenta activa, le enviamos un código."


@router.post("/auth/password-reset", response_model=SignupStarted, status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(data: PasswordResetRequest, db: Session = Depends(get_db)):
    # Misma respuesta exista o no la cuenta
    email = normalize_email(data.email)
    user = _reset_user(db, email)
    reset = _reset_row(db, user)
    if user is None:
        pass
    elif throttle.is_locked(db, email):
        logging.info("Restablecimiento omitido para el usuario %s: bloqueado por intentos fallidos", user.id)
    elif not _can_resend(reset.sent_at if reset else None):
        logging.info("Restablecimiento omitido para el usuario %s: se pidió hace menos de %s s", user.id, RESEND_SECONDS)
    else:
        send_reset_code(db, user)
    return SignupStarted(email_hint=mask_email(email))


@router.post("/auth/password-reset/resend", response_model=SignupStarted, status_code=status.HTTP_202_ACCEPTED)
def resend_password_reset(data: PasswordResetRequest, db: Session = Depends(get_db)):
    email = normalize_email(data.email)
    user = _reset_user(db, email)
    reset = _reset_row(db, user)
    # Solo si ya había un código pedido: el reenvío no crea uno de la nada
    if reset is not None and not throttle.is_locked(db, email) and _can_resend(reset.sent_at):
        send_reset_code(db, user, db.get(models.User, reset.requested_by) if reset.requested_by else None)
    return SignupStarted(email_hint=mask_email(email))


@router.post("/auth/password-reset/confirm", response_model=ActivationResult)
def confirm_password_reset(data: PasswordResetConfirm, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(data.email)
    if throttle.is_locked(db, email):
        raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
    user = _reset_user(db, email)
    reset = _reset_row(db, user)
    if reset is None or reset.expires_at <= _now() or reset.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(status_code=400, detail=CODE_INVALID)
    if not _code_matches(data.code, reset.code_hash):
        reset.attempts += 1
        # Mismo contador que las contraseñas incorrectas
        if throttle.register_failure(db, email, user):
            raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
        raise HTTPException(status_code=400, detail=CODE_INVALID)

    # El personal recibe una contraseña generada (se ve una sola vez)
    generated = generate_password()
    user.password_hash = hash_password(generated)
    db.delete(reset)
    throttle.reset(db, email)
    accounts.close_sessions(db, user.id)
    audit.record(db, user.id, "password_reset", target=user)
    db.commit()
    # Única respuesta con la contraseña: que el navegador no la guarde
    response.headers["Cache-Control"] = "no-store"
    return ActivationResult(email=user.email, password=generated)


@router.post("/auth/login", response_model=AuthResponse | LoginChallengeResponse)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    # Un solo formulario: el rol sale de la cuenta. Un "portal" que mande una página vieja se ignora
    email = credentials.email.strip().lower()
    # Bloqueado: ni se revisa la contraseña, ni se crea reto, ni se envían correos
    if throttle.is_locked(db, email):
        raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
    user = db.query(models.User).filter(models.User.email == email).first()
    password_ok = verify_password(credentials.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not password_ok or not user.is_active or user.role not in LOGIN_ROLES:
        # Cualquier 401 cuenta como fallo: el bloqueo no revela si la contraseña era correcta
        if throttle.register_failure(db, email, user):
            raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
        raise HTTPException(status_code=401, detail=LOGIN_ERROR)
    # Con Microsoft, el personal no entra con contraseña. Solo lo sabe quien escribió la correcta
    if uses_microsoft(user):
        raise HTTPException(status_code=403, detail=STAFF_USE_MICROSOFT)

    if user.role in TWO_FACTOR_ROLES:
        # El contador se reinicia cuando el código también es correcto
        return start_challenge(db, user)

    throttle.reset(db, email)
    audit.record(db, user.id, "login", target=user)
    return _create_session(db, user)


@router.post("/auth/verify-code", response_model=AuthResponse)
def verify_code(data: VerifyCodeRequest, db: Session = Depends(get_db)):
    challenge = db.query(models.LoginChallenge).filter(models.LoginChallenge.id == data.challenge_id).first()
    if not challenge or challenge.used:
        raise HTTPException(status_code=400, detail=CODE_ERROR)
    if challenge.expires_at <= _now():
        raise HTTPException(status_code=400, detail="El código venció. Inicie sesión de nuevo.")
    if challenge.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Inicie sesión de nuevo.")

    user = db.query(models.User).filter(models.User.id == challenge.user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=400, detail=CODE_ERROR)
    email = normalize_email(user.email)
    if throttle.is_locked(db, email):
        raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)

    if not hmac.compare_digest(hash_code(data.code.strip()), challenge.code_hash):
        challenge.attempts += 1
        # Mismo contador que las contraseñas incorrectas
        if throttle.register_failure(db, email, user):
            raise HTTPException(status_code=429, detail=throttle.LOCK_MESSAGE)
        raise HTTPException(status_code=400, detail=CODE_ERROR)

    challenge.used = True
    throttle.reset(db, email)
    # Con Microsoft, la contraseña y el código del administrador son el acceso de emergencia
    if config.MS_LOGIN_ENABLED and user.role == "admin":
        entra_login.record_emergency_access(db, user)
    else:
        audit.record(db, user.id, "login", target=user)
    db.commit()
    return _create_session(db, user)


@router.post("/auth/resend-code", response_model=LoginChallengeResponse)
def resend_code(data: ResendCodeRequest, db: Session = Depends(get_db)):
    challenge = db.query(models.LoginChallenge).filter(models.LoginChallenge.id == data.challenge_id).first()
    now = _now()
    if not challenge or challenge.used or challenge.expires_at <= now:
        raise HTTPException(status_code=400, detail="Inicie sesión de nuevo.")
    if (now - challenge.created_at).total_seconds() < RESEND_SECONDS:
        raise HTTPException(status_code=429, detail="Espere un minuto antes de pedir otro código.")

    user = db.query(models.User).filter(models.User.id == challenge.user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=400, detail="Inicie sesión de nuevo.")

    code = generate_login_code()
    challenge.code_hash = hash_code(code)
    challenge.expires_at = now + timedelta(minutes=CODE_MINUTES)
    challenge.attempts = 0
    challenge.created_at = now
    db.commit()
    _send_code(user, code)
    return LoginChallengeResponse(challenge_id=challenge.id, email_hint=mask_email(user.email))


def me_read(db: Session, user: models.User) -> MeRead:
    locked = identity.is_locked(db, user)
    db.commit()
    return MeRead(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        phone=user.phone,
        id_number=user.id_number,
        profile_complete=identity.profile_complete(user),
        identity_locked=locked,
        **_availability_fields(db, user),
        **_department_fields(db, user),
    )


def _department_fields(db: Session, user: models.User) -> dict:
    """Técnicos y coordinadores: su departamento. Personal y administrador: el interruptor."""
    if user.role == "admin":
        return {"routing_enforced": routing.enforced(db)}
    if user.role not in routing.PLACED_ROLES:
        return {}
    dept = routing.department_of(db, user)
    return {"department_name": routing.path_label(dept, routing.departments_by_id(db)),
            "placement_missing": routing.placement_missing(db, user), "routing_enforced": routing.enforced(db)}


def _availability_fields(db: Session, user: models.User) -> dict:
    if user.role not in availability.ROLES:
        return {}
    status_, return_date = availability.get(db, user)
    days = schedules.as_dicts(schedules.get(db, user.id))
    return {
        "availability": status_, "return_date": return_date,
        "schedule": days, "schedule_summary": schedules.summary(days),
        "periods": availability.periods_for(db, [user.id])[user.id],
    }


@router.get("/auth/me", response_model=MeRead)
def get_me(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return me_read(db, current_user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        db_session = db.query(models.Session).filter(models.Session.token == hash_token(authorization[7:])).first()
        if db_session:
            db.delete(db_session)
            db.commit()
    return None
