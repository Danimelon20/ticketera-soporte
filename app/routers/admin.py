import logging
import os
import sqlite3
import tempfile
from collections import deque
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..database import engine, get_db
from .. import config, email_poller, models
from ..schemas import AdminUserUpdate, DirectoryUser
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["admin"])

LOG_FILE = "data/app.log"
LOG_LINES = 200


def require_admin(current_user=Depends(get_current_user)) -> models.User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Solo el administrador puede hacer esto")
    return current_user


def _directory_rows(db: Session, users: list[models.User]) -> list[DirectoryUser]:
    ids = [u.id for u in users]
    if not ids:
        return []
    created = dict(
        db.query(models.Ticket.created_by, func.count(models.Ticket.id))
        .filter(models.Ticket.created_by.in_(ids))
        .group_by(models.Ticket.created_by)
        .all()
    )
    assigned_open = dict(
        db.query(models.Ticket.assigned_to, func.count(models.Ticket.id))
        .filter(models.Ticket.assigned_to.in_(ids), models.Ticket.status != "closed")
        .group_by(models.Ticket.assigned_to)
        .all()
    )
    return [
        DirectoryUser(
            id=u.id,
            name=u.name,
            email=u.email,
            role=u.role,
            phone=u.phone,
            id_number=u.id_number,
            is_active=u.is_active,
            tickets_created=created.get(u.id, 0),
            tickets_assigned_open=assigned_open.get(u.id, 0),
        )
        for u in users
    ]


@router.get("/directory", response_model=list[DirectoryUser])
def get_directory(
    role: Optional[Literal["requester", "technician", "coordinator", "admin"]] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")

    query = db.query(models.User).filter(models.User.role != "agent")
    if role:
        query = query.filter(models.User.role == role)
    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        query = query.filter(func.lower(models.User.name).like(term) | func.lower(models.User.email).like(term))
    users = query.order_by(models.User.role, models.User.name).all()
    return _directory_rows(db, users)


@router.patch("/admin/users/{user_id}", response_model=DirectoryUser)
def update_user(user_id: int, data: AdminUserUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="El usuario no existe")
    if user.role == "agent":
        raise HTTPException(status_code=400, detail="El Agente virtual no se puede modificar")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="No puedes cambiar tu propio rol ni desactivarte")

    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
        if not data.is_active:
            # Sin sesiones abiertas: sale de inmediato en todos sus dispositivos
            db.query(models.Session).filter(models.Session.user_id == user.id).delete()
    db.commit()
    db.refresh(user)
    return _directory_rows(db, [user])[0]


@router.get("/admin/health")
def admin_health(db: Session = Depends(get_db), admin=Depends(require_admin)):
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:
        database = str(exc)

    users_by_role = dict(db.query(models.User.role, func.count(models.User.id)).group_by(models.User.role).all())
    tickets_by_status = dict(
        db.query(models.Ticket.status, func.count(models.Ticket.id)).group_by(models.Ticket.status).all()
    )
    last_run = email_poller.last_run
    return {
        "database": database,
        "users_by_role": users_by_role,
        "tickets_by_status": tickets_by_status,
        "email_enabled": config.EMAIL_ENABLED,
        "email_last_run": last_run.isoformat() if last_run else None,
        "email_last_processed": email_poller.last_processed,
        "email_last_error": email_poller.last_error,
        "chatbot_enabled": getattr(config, "CHATBOT_ENABLED", False),
    }


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


@router.get("/admin/backup")
def admin_backup(admin=Depends(require_admin)):
    os.makedirs("data", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="respaldo-", suffix=".db", dir="data")
    os.close(fd)
    try:
        source = sqlite3.connect(engine.url.database)
        target = sqlite3.connect(tmp_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    except Exception:
        _remove_file(tmp_path)
        raise

    logging.info("Copia de seguridad descargada por el usuario %s", admin.id)
    filename = f"ticketera-respaldo-{datetime.now().strftime('%Y%m%d-%H%M')}.db"
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename=filename,
        background=BackgroundTask(_remove_file, tmp_path),
    )


@router.get("/admin/logs")
def admin_logs(admin=Depends(require_admin)):
    if not os.path.exists(LOG_FILE):
        return {"lines": []}
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        lines = deque(f, maxlen=LOG_LINES)
    return {"lines": [line.rstrip("\n") for line in lines]}
