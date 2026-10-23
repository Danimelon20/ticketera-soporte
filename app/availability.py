"""Disponibilidad de técnicos y coordinadores. Es automática, nadie la cambia a mano:

- Vacaciones o Incapacidad mientras dura un periodo registrado (primer y último día, inclusive).
- Si no hay periodo: Disponible dentro de su horario y Ausente fuera de él (un feriado es fuera de horario).
- Sin horario no hay con qué calcularla: Disponible.

El técnico o el coordinador registran sus propios periodos; quien gestiona la cuenta también (PERMISSIONS).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import audit, config, models, schedules

ROLES = ("technician", "coordinator")
LABELS = {"available": "Disponible", "vacation": "Vacaciones", "incapacity": "Ausencia", "away": "Ausente"}
NOT_STAFF = "La disponibilidad solo aplica a técnicos y coordinadores"
BAD_KIND = "Elija Vacaciones o Ausencia"
START_AFTER_END = "El primer día no puede ser posterior al último"
END_IN_PAST = "El periodo ya terminó: el último día no puede ser anterior a hoy"
OVERLAP = "Ya hay un periodo registrado en esas fechas"
PERIOD_NOT_FOUND = "El periodo no existe"
PERIOD_ENDED = "Ese periodo ya terminó y queda en el historial"
MAX_DAYS = 366
TOO_LONG = "Un periodo puede durar como máximo un año"


def _now() -> datetime:
    # La misma hora que la asignación automática (las pruebas la fijan ahí)
    from . import auto_assign
    return auto_assign._now()


def office_today() -> date:
    return datetime.now(ZoneInfo(config.OFFICE_TZ)).date()


def _local_day(now: datetime) -> date:
    """now en UTC sin zona → fecha en la oficina."""
    return now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(config.OFFICE_TZ)).date()


def _active_periods(db: Session, user_ids: list[int], day: date) -> dict[int, models.AvailabilityPeriod]:
    rows = (
        db.query(models.AvailabilityPeriod)
        .filter(
            models.AvailabilityPeriod.user_id.in_(user_ids),
            models.AvailabilityPeriod.start_date <= day,
            models.AvailabilityPeriod.end_date >= day,
        )
        .order_by(models.AvailabilityPeriod.start_date)
        .all()
    )
    return {row.user_id: row for row in rows}


def statuses_for(
    db: Session, user_ids: list[int], now: datetime | None = None
) -> dict[int, tuple[str, date | None]]:
    """Estado de varios usuarios: (estado, fecha de regreso). La fecha solo existe durante un periodo
    (el día siguiente al último). Los ids que no son técnicos ni coordinadores no deben llegar aquí."""
    if not user_ids:
        return {}
    now = now or _now()
    periods = _active_periods(db, user_ids, _local_day(now))
    shifts = schedules.for_users(db, user_ids)
    result = {}
    for uid in user_ids:
        period = periods.get(uid)
        if period is not None:
            result[uid] = (period.kind, period.end_date + timedelta(days=1))
        elif shifts[uid]:
            result[uid] = ("available" if schedules.on_shift(shifts[uid], now) else "away", None)
        else:
            result[uid] = ("available", None)
    return result


def ids_with_status(users: list[models.User]) -> list[int]:
    """De una lista de cuentas, las que tienen disponibilidad (técnicos y coordinadores)."""
    return [u.id for u in users if u.role in ROLES]


def get(db: Session, user: models.User, now: datetime | None = None) -> tuple[str, date | None]:
    if user.role not in ROLES:
        return ("available", None)
    return statuses_for(db, [user.id], now)[user.id]


def is_available(db: Session, user: models.User, now: datetime | None = None) -> bool:
    return get(db, user, now)[0] == "available"


def describe(status: str, return_date: date | None) -> str:
    label = LABELS.get(status, status)
    return f"{label} (regresa {return_date.strftime('%d/%m/%Y')})" if return_date else label


def describe_period(period: models.AvailabilityPeriod) -> str:
    return (f"{LABELS.get(period.kind, period.kind)} del {period.start_date.strftime('%d/%m/%Y')} "
            f"al {period.end_date.strftime('%d/%m/%Y')}")


def periods_for(db: Session, user_ids: list[int], today: date | None = None) -> dict[int, list[models.AvailabilityPeriod]]:
    """Periodos vigentes y futuros (los que ya terminaron no se muestran), del más próximo al más lejano."""
    found: dict[int, list[models.AvailabilityPeriod]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return found
    today = today or _local_day(_now())
    rows = (
        db.query(models.AvailabilityPeriod)
        .filter(models.AvailabilityPeriod.user_id.in_(user_ids), models.AvailabilityPeriod.end_date >= today)
        .order_by(models.AvailabilityPeriod.start_date)
        .all()
    )
    for row in rows:
        found[row.user_id].append(row)
    return found


def add_period(
    db: Session, actor: models.User, user: models.User, kind: str, start: date, end: date
) -> models.AvailabilityPeriod:
    """Valida, guarda y lo anota en el registro de cambios. No hace commit."""
    if user.role not in ROLES:
        raise HTTPException(status_code=400, detail=NOT_STAFF)
    if kind not in models.AVAILABILITY_PERIOD_KINDS:
        raise HTTPException(status_code=422, detail=BAD_KIND)
    if start > end:
        raise HTTPException(status_code=422, detail=START_AFTER_END)
    if end < _local_day(_now()):
        raise HTTPException(status_code=422, detail=END_IN_PAST)
    if (end - start).days >= MAX_DAYS:
        raise HTTPException(status_code=422, detail=TOO_LONG)
    overlap = (
        db.query(models.AvailabilityPeriod)
        .filter(
            models.AvailabilityPeriod.user_id == user.id,
            models.AvailabilityPeriod.start_date <= end,
            models.AvailabilityPeriod.end_date >= start,
        )
        .first()
    )
    if overlap is not None:
        raise HTTPException(status_code=409, detail=OVERLAP)
    period = models.AvailabilityPeriod(
        user_id=user.id, kind=kind, start_date=start, end_date=end,
        created_by=actor.id, created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(period)
    audit.record(db, actor.id, "availability_period_added", target=user, new=describe_period(period))
    return period


def remove_period(db: Session, actor: models.User, user: models.User, period_id: int) -> None:
    """Quita un periodo vigente o futuro (por ejemplo, si la persona regresó antes). No hace commit."""
    period = db.get(models.AvailabilityPeriod, period_id)
    if period is None or period.user_id != user.id:
        raise HTTPException(status_code=404, detail=PERIOD_NOT_FOUND)
    if period.end_date < _local_day(_now()):
        raise HTTPException(status_code=400, detail=PERIOD_ENDED)
    audit.record(db, actor.id, "availability_period_removed", target=user, old=describe_period(period))
    db.delete(period)
