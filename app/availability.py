"""Disponibilidad de los técnicos: Disponible, Vacaciones o Ausente, con fecha de regreso opcional."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import audit, config, models

LABELS = {"available": "Disponible", "vacation": "Vacaciones", "away": "Ausente"}
ONLY_TECHNICIANS = "La disponibilidad solo aplica a técnicos"
DATE_NOT_ALLOWED = "La fecha de regreso solo se usa con Vacaciones o Ausente"
DATE_IN_PAST = "La fecha de regreso no puede ser anterior a hoy"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def office_today() -> date:
    return datetime.now(ZoneInfo(config.OFFICE_TZ)).date()


def _row(db: Session, user_id: int) -> models.TechnicianAvailability | None:
    return db.query(models.TechnicianAvailability).filter(models.TechnicianAvailability.user_id == user_id).first()


def get(db: Session, user: models.User) -> tuple[str, date | None]:
    row = _row(db, user.id)
    return (row.status, row.return_date) if row else ("available", None)


def is_available(db: Session, user: models.User) -> bool:
    return user.role != "technician" or get(db, user)[0] == "available"


def statuses_for(db: Session, user_ids: list[int]) -> dict[int, tuple[str, date | None]]:
    """Estado de varios usuarios en una sola consulta; quien no tiene fila está Disponible."""
    if not user_ids:
        return {}
    rows = db.query(models.TechnicianAvailability).filter(models.TechnicianAvailability.user_id.in_(user_ids)).all()
    found = {r.user_id: (r.status, r.return_date) for r in rows}
    return {uid: found.get(uid, ("available", None)) for uid in user_ids}


def describe(status: str, return_date: date | None) -> str:
    label = LABELS.get(status, status)
    return f"{label} (regresa {return_date.strftime('%d/%m/%Y')})" if return_date else label


def set_availability(
    db: Session, actor: models.User, user: models.User, status: str, return_date: date | None
) -> None:
    """Valida, guarda y lo anota en el registro de cambios. No hace commit."""
    if user.role != "technician":
        raise HTTPException(status_code=400, detail=ONLY_TECHNICIANS)
    if status not in models.AVAILABILITY_STATUSES:
        raise HTTPException(status_code=422, detail="Estado de disponibilidad no válido")
    if status == "available" and return_date is not None:
        raise HTTPException(status_code=422, detail=DATE_NOT_ALLOWED)
    if return_date is not None and return_date < office_today():
        raise HTTPException(status_code=422, detail=DATE_IN_PAST)
    old = get(db, user)
    if old == (status, return_date):
        return
    row = _row(db, user.id)
    if row is None:
        row = models.TechnicianAvailability(user_id=user.id)
        db.add(row)
    row.status = status
    row.return_date = return_date
    row.updated_by = actor.id
    row.updated_at = _now()
    audit.record(
        db, actor.id, "availability_changed", target=user,
        old=describe(*old), new=describe(status, return_date),
    )
