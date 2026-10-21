"""Horarios de los técnicos: un rango por día de la semana, en la hora local de OFFICE_TZ."""
import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import audit, models
from .business_hours import _holidays
from .daily_summary import _local

DAY_NAMES = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
NO_SCHEDULE = "Sin horario"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
BAD_TIME = "Escriba las horas con el formato HH:MM"
START_AFTER_END = "La hora de inicio debe ser anterior a la de fin"
BAD_DAY = "Cada día de la semana puede aparecer una sola vez"
ONLY_TECHNICIANS = "El horario solo aplica a técnicos"


def get(db: Session, user_id: int) -> list[models.TechnicianSchedule]:
    return (
        db.query(models.TechnicianSchedule)
        .filter(models.TechnicianSchedule.user_id == user_id)
        .order_by(models.TechnicianSchedule.weekday)
        .all()
    )


def for_users(db: Session, user_ids: list[int]) -> dict[int, list[models.TechnicianSchedule]]:
    """Horarios de varios técnicos en una sola consulta."""
    found: dict[int, list[models.TechnicianSchedule]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return found
    rows = (
        db.query(models.TechnicianSchedule)
        .filter(models.TechnicianSchedule.user_id.in_(user_ids))
        .order_by(models.TechnicianSchedule.weekday)
        .all()
    )
    for row in rows:
        found[row.user_id].append(row)
    return found


def as_dicts(rows: list[models.TechnicianSchedule]) -> list[dict]:
    return [{"weekday": r.weekday, "start": r.start_time, "end": r.end_time} for r in rows]


def summary(days: list[dict]) -> str:
    """"Lun–Vie 08:00–17:00 · Sáb 08:00–12:00": días seguidos con el mismo rango van juntos."""
    if not days:
        return NO_SCHEDULE
    groups: list[list] = []
    for day in sorted(days, key=lambda d: d["weekday"]):
        last = groups[-1] if groups else None
        if last and last[1] == day["weekday"] - 1 and last[2] == (day["start"], day["end"]):
            last[1] = day["weekday"]
        else:
            groups.append([day["weekday"], day["weekday"], (day["start"], day["end"])])
    parts = []
    for first, last, (start, end) in groups:
        label = DAY_NAMES[first] if first == last else f"{DAY_NAMES[first]}–{DAY_NAMES[last]}"
        parts.append(f"{label} {start}–{end}")
    return " · ".join(parts)


def on_shift(rows: list[models.TechnicianSchedule], now: datetime) -> bool:
    """now en UTC sin zona. En turno: el rango del día local incluye la hora (inicio ≤ ahora < fin)."""
    local = _local(now)
    # Un feriado de la oficina es un día sin turno
    if local.date() in _holidays():
        return False
    current = local.strftime("%H:%M")
    return any(r.weekday == local.weekday() and r.start_time <= current < r.end_time for r in rows)


def today_range(rows: list[models.TechnicianSchedule], now: datetime) -> tuple[str, str] | None:
    """now en UTC sin zona. Rango ("HH:MM", "HH:MM") del turno de hoy en hora local; None si hoy no trabaja."""
    local = _local(now)
    # Mismo criterio que on_shift: un feriado de la oficina es un día sin turno
    if local.date() in _holidays():
        return None
    for r in rows:
        if r.weekday == local.weekday():
            return r.start_time, r.end_time
    return None


def _validate(days: list[dict]) -> list[dict]:
    seen = set()
    clean = []
    for day in days:
        weekday, start, end = day["weekday"], day["start"].strip(), day["end"].strip()
        if not 0 <= weekday <= 6 or weekday in seen:
            raise HTTPException(status_code=422, detail=BAD_DAY)
        if not _TIME_RE.match(start) or not _TIME_RE.match(end):
            raise HTTPException(status_code=422, detail=BAD_TIME)
        # "HH:MM" con ceros a la izquierda: comparar el texto es comparar la hora
        if start >= end:
            raise HTTPException(status_code=422, detail=START_AFTER_END)
        seen.add(weekday)
        clean.append({"weekday": weekday, "start": start, "end": end})
    return sorted(clean, key=lambda d: d["weekday"])


def set_schedule(db: Session, actor: models.User, user: models.User, days: list[dict]) -> None:
    """Reemplaza el horario completo y lo anota en el registro de cambios. No hace commit."""
    if user.role != "technician":
        raise HTTPException(status_code=400, detail=ONLY_TECHNICIANS)
    clean = _validate(days)
    old = summary(as_dicts(get(db, user.id)))
    new = summary(clean)
    db.query(models.TechnicianSchedule).filter(models.TechnicianSchedule.user_id == user.id).delete()
    for day in clean:
        db.add(models.TechnicianSchedule(
            user_id=user.id, weekday=day["weekday"], start_time=day["start"], end_time=day["end"],
        ))
    if old != new:
        audit.record(db, actor.id, "schedule_changed", target=user, old=old, new=new)
