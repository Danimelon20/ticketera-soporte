from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..database import get_db
from .. import audit, due_notices, models, overdue_report
from ..schemas import NoticeReadRequest, OverdueCauseCreate, OverdueNoticeList, OverdueReport
from ..security import get_current_user
from .tickets import contact_fields, get_visible_ticket

router = APIRouter(prefix="/api", tags=["overdue"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def require_notice_reader(current_user=Depends(get_current_user)) -> models.User:
    # El solicitante nunca recibe avisos de vencimiento
    if current_user.role not in due_notices.NOTICE_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para ver estos avisos")
    return current_user


@router.get("/notifications/overdue", response_model=OverdueNoticeList)
def overdue_notices(db: Session = Depends(get_db), current_user=Depends(require_notice_reader)):
    now = _now()
    due_notices.sync(db, now)
    items = due_notices.items_for(db, current_user, now)
    return OverdueNoticeList(items=items, unread=sum(1 for item in items if not item["read"]))


@router.post("/notifications/overdue/read", status_code=204)
def mark_overdue_notices_read(
    data: NoticeReadRequest, db: Session = Depends(get_db), current_user=Depends(require_notice_reader)
):
    if not data.all and data.ticket_id is None:
        raise HTTPException(status_code=400, detail="Indique un ticket o todos")
    due_notices.mark_read(db, current_user, None if data.all else data.ticket_id, _now())
    return Response(status_code=204)


def _report(db: Session, ticket_id: int, current_user, now: datetime, for_pdf: bool = False) -> tuple:
    ticket = get_visible_ticket(db, ticket_id, current_user)
    overdue_report.check_can_view(current_user, ticket)
    contact = overdue_report.pdf_contact(ticket) if for_pdf else contact_fields(ticket, current_user)
    return ticket, overdue_report.build(db, ticket, current_user, contact, now)


@router.get("/tickets/{ticket_id}/overdue-report", response_model=OverdueReport)
def get_overdue_report(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _report(db, ticket_id, current_user, _now())[1]


@router.get("/tickets/{ticket_id}/overdue-report.pdf")
def get_overdue_report_pdf(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    now = _now()
    ticket, report = _report(db, ticket_id, current_user, now, for_pdf=True)
    content = overdue_report.render_pdf(report, current_user.name or "Sin nombre", now)
    # Queda en el Registro de cambios quién lo descargó y cuándo
    audit.record(db, current_user.id, "overdue_report_downloaded", target_label=f"Ticket #{ticket.id}")
    db.commit()
    name = overdue_report.filename(ticket.id, now)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.post("/tickets/{ticket_id}/overdue-cause", response_model=OverdueReport)
def record_overdue_cause(
    ticket_id: int, data: OverdueCauseCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Solo coordinador y administrador. Se puede cambiar: queda la última, con quién y cuándo."""
    if current_user.role not in overdue_report.CAUSE_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permiso para registrar la causa")
    now = _now()
    ticket = get_visible_ticket(db, ticket_id, current_user)
    if not overdue_report.was_overdue(db, ticket, now):
        raise HTTPException(status_code=400, detail="El ticket no está vencido")
    row = db.query(models.OverdueCause).filter(models.OverdueCause.ticket_id == ticket.id).first()
    if row is None:
        row = models.OverdueCause(ticket_id=ticket.id)
        db.add(row)
    row.cause = data.cause
    row.note = data.note
    row.recorded_by = current_user.id
    row.recorded_at = now
    db.commit()
    return _report(db, ticket_id, current_user, now)[1]
