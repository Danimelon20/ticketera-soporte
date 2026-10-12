from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..schemas import RegisterRequest, LoginRequest, AuthResponse, UserRead
from ..security import hash_password, verify_password, new_token, get_current_user


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: RegisterRequest, db: Session = Depends(get_db)):
    email = user_data.email.strip().lower()
    existente = db.query(models.User).filter(models.User.email == email).first()
    if existente:
        raise HTTPException(status_code=409, detail="El correo ya está registrado")

    pwd_hash = hash_password(user_data.password)
    db_user = models.User(
        name=user_data.name,
        email=email,
        role=user_data.role,
        password_hash=pwd_hash
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    token = new_token()
    db_session = models.Session(token=token, user_id=db_user.id)
    db.add(db_session)
    db.commit()

    return AuthResponse(token=token, user=db_user)


@router.post("/auth/login", response_model=AuthResponse)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    email = credentials.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")

    token = new_token()
    db_session = models.Session(token=token, user_id=user.id)
    db.add(db_session)
    db.commit()

    return AuthResponse(token=token, user=user)


@router.get("/auth/me", response_model=UserRead)
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        db_session = db.query(models.Session).filter(models.Session.token == token).first()
        if db_session:
            db.delete(db_session)
            db.commit()
    return None
