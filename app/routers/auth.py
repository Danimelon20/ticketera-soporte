import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from ..database import get_db
from .. import config, mailer, models
from ..schemas import (
    RegisterRequest, LoginRequest, AuthResponse, MeRead, UserRead,
    LoginChallengeResponse, VerifyCodeRequest, ResendCodeRequest,
)
from ..security import (
    hash_password, verify_password, new_token, get_current_user, hash_code,
    generate_login_code, mask_email,
)


router = APIRouter(prefix="/api", tags=["auth"])

TWO_FACTOR_ROLES = ("coordinator", "admin")
CODE_MINUTES = 10
MAX_CODE_ATTEMPTS = 5
RESEND_SECONDS = 60
CODE_ERROR = "Error: Intente Nuevamente"


def _now() -> datetime:
    # La base guarda fechas UTC sin zona horaria
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _create_session(db: Session, user: models.User) -> AuthResponse:
    token = new_token()
    db.add(models.Session(token=token, user_id=user.id))
    db.commit()
    return AuthResponse(token=token, user=user)


def _send_code(user: models.User, code: str) -> None:
    mailer.send_email(
        user.email,
        "Código de verificación de la Ticketera",
        f"Tu código de verificación es: {code}. Vence en {CODE_MINUTES} minutos. "
        "Si no fuiste tú, ignora este correo y avisa al administrador.",
    )
    # Solo sin correo real; con el correo encendido el código nunca va a los logs
    if not config.EMAIL_ENABLED:
        logging.warning("[SOLO DESARROLLO] Código de verificación para %s: %s", mask_email(user.email), code)


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


@router.post("/auth/register", response_model=AuthResponse | LoginChallengeResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: RegisterRequest, db: Session = Depends(get_db)):
    email = user_data.email.strip().lower()
    existente = db.query(models.User).filter(models.User.email == email).first()
    if existente:
        raise HTTPException(status_code=409, detail="El correo ya está registrado")

    role = "requester"
    invite = None

    if user_data.invite_code and user_data.invite_code.strip():
        c_hash = hash_code(user_data.invite_code)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        invite = db.query(models.InviteCode).filter(
            models.InviteCode.code_hash == c_hash,
            models.InviteCode.used_by == None,
            models.InviteCode.expires_at > now
        ).first()
        if not invite:
            raise HTTPException(status_code=400, detail="Código de invitación inválido o vencido")
        role = invite.role

    pwd_hash = hash_password(user_data.password)
    db_user = models.User(
        name=user_data.name.strip(),
        email=email,
        role=role,
        password_hash=pwd_hash
    )
    db.add(db_user)
    db.flush()

    if invite:
        invite.used_by = db_user.id
        invite.used_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(db_user)

    if db_user.role == "coordinator":
        return start_challenge(db, db_user)

    return _create_session(db, db_user)


@router.post("/auth/login", response_model=AuthResponse | LoginChallengeResponse)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    email = credentials.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(credentials.password, user.password_hash) or user.role == "agent" or not user.is_active:
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")

    if user.role in TWO_FACTOR_ROLES:
        return start_challenge(db, user)

    return _create_session(db, user)


@router.post("/auth/verify-code", response_model=AuthResponse)
def verify_code(data: VerifyCodeRequest, db: Session = Depends(get_db)):
    challenge = db.query(models.LoginChallenge).filter(models.LoginChallenge.id == data.challenge_id).first()
    if not challenge or challenge.used:
        raise HTTPException(status_code=400, detail=CODE_ERROR)
    if challenge.expires_at <= _now():
        raise HTTPException(status_code=400, detail="El código venció. Inicia sesión de nuevo.")
    if challenge.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Inicia sesión de nuevo.")

    if not hmac.compare_digest(hash_code(data.code.strip()), challenge.code_hash):
        challenge.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail=CODE_ERROR)

    user = db.query(models.User).filter(models.User.id == challenge.user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=400, detail=CODE_ERROR)

    challenge.used = True
    db.commit()
    return _create_session(db, user)


@router.post("/auth/resend-code", response_model=LoginChallengeResponse)
def resend_code(data: ResendCodeRequest, db: Session = Depends(get_db)):
    challenge = db.query(models.LoginChallenge).filter(models.LoginChallenge.id == data.challenge_id).first()
    now = _now()
    if not challenge or challenge.used or challenge.expires_at <= now:
        raise HTTPException(status_code=400, detail="Inicia sesión de nuevo.")
    if (now - challenge.created_at).total_seconds() < RESEND_SECONDS:
        raise HTTPException(status_code=429, detail="Espera un minuto antes de pedir otro código.")

    user = db.query(models.User).filter(models.User.id == challenge.user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=400, detail="Inicia sesión de nuevo.")

    code = generate_login_code()
    challenge.code_hash = hash_code(code)
    challenge.expires_at = now + timedelta(minutes=CODE_MINUTES)
    challenge.attempts = 0
    challenge.created_at = now
    db.commit()
    _send_code(user, code)
    return LoginChallengeResponse(challenge_id=challenge.id, email_hint=mask_email(user.email))


@router.get("/auth/me", response_model=MeRead)
def get_me(current_user: models.User = Depends(get_current_user)):
    return MeRead(
        id=current_user.id,
        name=current_user.name,
        email=current_user.email,
        role=current_user.role,
        phone=current_user.phone,
        id_number=current_user.id_number,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        db_session = db.query(models.Session).filter(models.Session.token == token).first()
        if db_session:
            db.delete(db_session)
            db.commit()
    return None
