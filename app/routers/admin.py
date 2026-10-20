import logging
import os
import sqlite3
import tempfile
from collections import deque
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..database import engine, get_db
from .. import accounts, alerts, config, daily_summary, email_analysis, email_poller, identity, models, throttle
from ..schemas import AccountChangePage, AccountChangeRead, AdminUserUpdate, DirectoryUser
from ..security import get_current_user, mask_email

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
    rows = [
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
            login_locked=throttle.is_locked(db, u.email.strip().lower()),
            identity_locked=identity.is_locked(db, u),
        )
        for u in users
    ]
    # identity.is_locked puede guardar la fecha de bloqueo de cuentas anteriores
    db.commit()
    return rows


@router.get("/directory", response_model=list[DirectoryUser])
def get_directory(
    role: Optional[Literal["requester", "technician", "coordinator", "admin"]] = None,
    q: Optional[str] = None,
    state: Optional[Literal["active", "inactive"]] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta información")

    query = db.query(models.User).filter(models.User.role != "agent")
    if role:
        query = query.filter(models.User.role == role)
    if state:
        query = query.filter(models.User.is_active.is_(state == "active"))
    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        query = query.filter(func.lower(models.User.name).like(term) | func.lower(models.User.email).like(term))
    users = query.order_by(models.User.role, models.User.name).all()
    return _directory_rows(db, users)


@router.patch("/admin/users/{user_id}", response_model=DirectoryUser)
def update_user(
    user_id: int,
    data: AdminUserUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="El usuario no existe")
    if user.role == "agent":
        raise HTTPException(status_code=400, detail=accounts.AGENT_LOCKED)
    if user.id == admin.id:
        raise HTTPException(status_code=403, detail=accounts.SELF_CHANGE)
    if data.is_active is not None and data.is_active != user.is_active:
        # Con el rol que tiene ahora: un administrador no se desactiva desde aquí
        accounts.check_can_manage(admin, user, "active")

    if data.role is not None:
        accounts.change_role(db, admin, user, data.role)
    if data.is_active is not None:
        accounts.set_active(db, admin, user, data.is_active, background_tasks)
    db.commit()
    db.refresh(user)
    return _directory_rows(db, [user])[0]


ACCOUNT_CHANGES_PAGE = 20


@router.get("/admin/account-changes", response_model=AccountChangePage)
def account_changes(
    account: Optional[str] = None,
    action: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    query = db.query(models.AccountChange).outerjoin(
        models.User, models.User.id == models.AccountChange.target_user_id
    )
    if action:
        query = query.filter(models.AccountChange.action == action)
    if account and account.strip():
        term = f"%{account.strip().lower()}%"
        query = query.filter(
            func.lower(models.User.name).like(term)
            | func.lower(models.User.email).like(term)
            | func.lower(models.AccountChange.target_label).like(term)
        )
    total = query.count()
    pages = max(1, -(-total // ACCOUNT_CHANGES_PAGE))
    page = min(max(page, 1), pages)
    rows = (
        query.order_by(models.AccountChange.created_at.desc(), models.AccountChange.id.desc())
        .offset((page - 1) * ACCOUNT_CHANGES_PAGE)
        .limit(ACCOUNT_CHANGES_PAGE)
        .all()
    )

    def _account(row: models.AccountChange) -> str:
        if row.target is not None:
            label = mask_email(row.target.email)
            return f"{row.target.name} ({label})" if row.target.name else label
        return row.target_label or "—"

    items = [
        AccountChangeRead(
            id=row.id,
            created_at=row.created_at,
            action=row.action,
            actor=(row.actor.name or mask_email(row.actor.email)) if row.actor is not None else "Sistema",
            account=_account(row),
            old_value=row.old_value,
            new_value=row.new_value,
        )
        for row in rows
    ]
    return AccountChangePage(items=items, total=total, page=page, pages=pages)


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
        "ai_analysis": _ai_analysis_stats(db),
        "drafts_by_status": dict(
            db.query(models.EmailDraft.status, func.count(models.EmailDraft.id))
            .group_by(models.EmailDraft.status)
            .all()
        ),
        "daily_summary_enabled": config.DAILY_SUMMARY_ENABLED,
        "daily_summary_last": _isoformat(daily_summary.last_automatic(db)),
        "alerts_enabled": config.ALERTS_ENABLED,
        "alerts_last": _isoformat(alerts.last_sent(db)),
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@router.post("/admin/daily-summary/send-now")
def send_daily_summary_now(db: Session = Depends(get_db), admin=Depends(require_admin)):
    # Envío manual: no cuenta para el control de una vez por día
    if not config.EMAIL_ENABLED:
        raise HTTPException(status_code=400, detail="El envío de correos está apagado")
    sent, total = daily_summary.send_summary(db)
    logging.info("Resumen diario enviado a mano por el usuario %s a %s destinatarios", admin.id, sent)
    return {"sent": sent, "recipients": total}


@router.post("/admin/ai/analyze-missing")
def analyze_missing(background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin=Depends(require_admin)):
    if not config.AI_ANALYSIS_ENABLED:
        raise HTTPException(status_code=400, detail="El análisis con IA está apagado")
    ids = [
        ticket_id
        for (ticket_id,) in db.query(models.Ticket.id)
        .outerjoin(models.TicketAIAnalysis, models.TicketAIAnalysis.ticket_id == models.Ticket.id)
        .filter(models.Ticket.status.in_(("new", "in_progress")), models.TicketAIAnalysis.id.is_(None))
        .order_by(models.Ticket.id)
        .all()
    ]
    # Uno por uno y después de responder: con poca RAM puede tardar varios minutos
    if ids:
        background_tasks.add_task(email_analysis.analyze_missing_tickets, ids)
    logging.info("Análisis de tickets sin nivel pedido por el usuario %s: %s tickets", admin.id, len(ids))
    return {"found": len(ids)}


def _ai_analysis_stats(db: Session) -> dict:
    ai = models.TicketAIAnalysis
    by_status = dict(db.query(ai.status, func.count(ai.id)).group_by(ai.status).all())
    corrected = db.query(ai).filter(ai.human_level.isnot(None), ai.human_level != ai.ai_level).count()
    done_with_level = db.query(ai).filter(ai.status == "done", ai.ai_level.isnot(None))
    total = done_with_level.count()
    # Acierto: el nivel de la IA no fue corregido por una persona
    right = done_with_level.filter((ai.human_level.is_(None)) | (ai.human_level == ai.ai_level)).count()
    return {
        "enabled": config.AI_ANALYSIS_ENABLED,
        "pending": by_status.get("pending", 0),
        "done": by_status.get("done", 0),
        "error": by_status.get("error", 0),
        "needs_review": db.query(ai).filter(ai.needs_review.is_(True)).count(),
        "corrected": corrected,
        "accuracy": round(right * 100 / total, 1) if total else None,
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
