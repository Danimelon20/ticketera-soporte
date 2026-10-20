"""Reportes de cuentas sospechosas: el personal reporta solicitantes; coordinador o admin los resuelven.

Solo marcan la cuenta (no la bloquean) y el solicitante nunca se entera: sin correos ni datos en su API.
"""
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from .. import audit, models
from ..schemas import AccountReportCount, AccountReportCreate, AccountReportRead, AccountReportResolve
from ..security import get_current_user, mask_email

router = APIRouter(prefix="/api", tags=["reports"])

REPORTER_ROLES = ("technician", "coordinator", "admin")
REVIEW_ROLES = ("coordinator", "admin")
NO_PERMISSION = "No tiene permiso para hacer esto"
ONLY_REQUESTERS = "Solo se pueden reportar cuentas de solicitante"
UNKNOWN_REASON = "Elija un motivo de la lista"
OTHER_NEEDS_TEXT = "Describa el motivo del reporte"
DUPLICATE = "Ya tiene un reporte abierto sobre esta cuenta"
NOT_OPEN = "El reporte ya fue resuelto"
OWN_CONFIRM = "Otro coordinador o un administrador debe confirmar este reporte"
NOT_VISIBLE = "La cuenta no existe"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require(user: models.User, roles: tuple[str, ...]) -> None:
    if user.role not in roles:
        raise HTTPException(status_code=403, detail=NO_PERMISSION)


def _name(user: models.User | None) -> Optional[str]:
    if user is None:
        return None
    return user.name or mask_email(user.email)


def _read(row: models.AccountReport) -> AccountReportRead:
    return AccountReportRead(
        id=row.id,
        target_user_id=row.target_user_id,
        target_name=row.target.name or "Sin nombre",
        target_email=mask_email(row.target.email),
        reporter_id=row.reporter_id,
        reporter=_name(row.reporter),
        reason=row.reason,
        reason_label=models.REPORT_REASONS.get(row.reason, row.reason),
        comment=row.comment,
        status=row.status,
        resolver=_name(row.resolver),
        resolution_note=row.resolution_note,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


def _seen_by_technician(db: Session, user: models.User) -> bool:
    """El técnico reporta desde un ticket o borrador: la cuenta debe tener al menos uno."""
    has_ticket = db.query(models.Ticket.id).filter(models.Ticket.created_by == user.id).first() is not None
    return has_ticket or db.query(models.EmailDraft.id).filter(models.EmailDraft.requester_id == user.id).first() is not None


@router.post("/account-reports", response_model=AccountReportRead, status_code=status.HTTP_201_CREATED)
def create_report(data: AccountReportCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require(current_user, REPORTER_ROLES)
    target = db.get(models.User, data.target_user_id)
    if target is None or (current_user.role == "technician" and not _seen_by_technician(db, target)):
        raise HTTPException(status_code=404, detail=NOT_VISIBLE)
    if target.role != "requester":
        raise HTTPException(status_code=400, detail=ONLY_REQUESTERS)
    if data.reason not in models.REPORT_REASONS:
        raise HTTPException(status_code=422, detail=UNKNOWN_REASON)
    comment = (data.comment or "").strip() or None
    if data.reason == "other" and comment is None:
        raise HTTPException(status_code=422, detail=OTHER_NEEDS_TEXT)
    open_report = (
        db.query(models.AccountReport.id)
        .filter(
            models.AccountReport.target_user_id == target.id,
            models.AccountReport.reporter_id == current_user.id,
            models.AccountReport.status == "open",
        )
        .first()
    )
    if open_report is not None:
        raise HTTPException(status_code=409, detail=DUPLICATE)
    row = models.AccountReport(
        target_user_id=target.id,
        reporter_id=current_user.id,
        reason=data.reason,
        comment=comment,
        status="open",
        created_at=_now(),
    )
    db.add(row)
    # Solo el motivo: el comentario puede traer datos personales
    audit.record(db, current_user.id, "report_created", target=target, new=models.REPORT_REASONS[data.reason])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE)
    db.refresh(row)
    return _read(row)


@router.get("/account-reports", response_model=list[AccountReportRead])
def list_reports(
    status_filter: Optional[Literal["open", "confirmed", "dismissed"]] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require(current_user, REVIEW_ROLES)
    query = db.query(models.AccountReport)
    if status_filter:
        query = query.filter(models.AccountReport.status == status_filter)
    rows = query.order_by(models.AccountReport.created_at.desc(), models.AccountReport.id.desc()).limit(200).all()
    return [_read(r) for r in rows]


@router.get("/account-reports/open-count", response_model=AccountReportCount)
def open_count(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require(current_user, REVIEW_ROLES)
    return AccountReportCount(open=db.query(models.AccountReport).filter(models.AccountReport.status == "open").count())


def _resolve(db: Session, report_id: int, reviewer: models.User, new_status: str, note: str) -> AccountReportRead:
    _require(reviewer, REVIEW_ROLES)
    row = db.get(models.AccountReport, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="El reporte no existe")
    if row.status != "open":
        raise HTTPException(status_code=409, detail=NOT_OPEN)
    # Quien reportó puede retirarlo, pero la confirmación la da otra persona
    if new_status == "confirmed" and row.reporter_id == reviewer.id:
        raise HTTPException(status_code=403, detail=OWN_CONFIRM)
    row.status = new_status
    row.resolved_by = reviewer.id
    row.resolved_at = _now()
    row.resolution_note = note
    action = "report_confirmed" if new_status == "confirmed" else "report_dismissed"
    audit.record(db, reviewer.id, action, target=row.target, new=models.REPORT_REASONS.get(row.reason, row.reason))
    db.commit()
    db.refresh(row)
    return _read(row)


@router.post("/account-reports/{report_id}/confirm", response_model=AccountReportRead)
def confirm_report(
    report_id: int, data: AccountReportResolve, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    return _resolve(db, report_id, current_user, "confirmed", data.note)


@router.post("/account-reports/{report_id}/dismiss", response_model=AccountReportRead)
def dismiss_report(
    report_id: int, data: AccountReportResolve, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    return _resolve(db, report_id, current_user, "dismissed", data.note)
