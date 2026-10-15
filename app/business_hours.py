from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

# Se lee config en cada llamada para poder cambiarla en pruebas


def _parse_time(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


def _office_days() -> set[int]:
    days = set()
    for part in config.OFFICE_DAYS.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = part.split("-")
            days.update(range(int(first), int(last) + 1))
        else:
            days.add(int(part))
    return days


def _holidays() -> set[date]:
    return {date.fromisoformat(d.strip()) for d in config.OFFICE_HOLIDAYS.split(",") if d.strip()}


def is_business_day(day: date) -> bool:
    """Día hábil: está en OFFICE_DAYS y no es feriado."""
    return day.isoweekday() in _office_days() and day not in _holidays()


def day_hours() -> float:
    """Horas de una jornada (un día hábil)."""
    start = _parse_time(config.OFFICE_START)
    end = _parse_time(config.OFFICE_END)
    return (datetime.combine(date.min, end) - datetime.combine(date.min, start)).total_seconds() / 3600


def add_business_hours(start: datetime, hours: float) -> datetime:
    """Suma horas hábiles. Una fecha sin zona se toma como UTC (así se guardan en la base).
    Devuelve la fecha en la zona de la oficina."""
    tz = ZoneInfo(config.OFFICE_TZ)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    current = start.astimezone(tz)
    open_time = _parse_time(config.OFFICE_START)
    close_time = _parse_time(config.OFFICE_END)
    days = _office_days()
    holidays = _holidays()
    remaining = timedelta(hours=hours)

    # Límite de seguridad: diez años de días
    for _ in range(3660):
        day = current.date()
        if day.isoweekday() in days and day not in holidays:
            day_open = datetime.combine(day, open_time, tzinfo=tz)
            day_close = datetime.combine(day, close_time, tzinfo=tz)
            if current < day_open:
                current = day_open
            if current < day_close:
                available = day_close - current
                if remaining <= available:
                    return current + remaining
                remaining -= available
        current = datetime.combine(day + timedelta(days=1), open_time, tzinfo=tz)
    raise ValueError("El horario de oficina no tiene días hábiles")
