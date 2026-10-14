from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import config
from app.business_hours import add_business_hours, day_hours

CR = ZoneInfo("America/Costa_Rica")


@pytest.fixture(autouse=True)
def office(monkeypatch):
    monkeypatch.setattr(config, "OFFICE_DAYS", "1-5")
    monkeypatch.setattr(config, "OFFICE_START", "08:00")
    monkeypatch.setattr(config, "OFFICE_END", "17:00")
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")


def test_day_hours():
    assert day_hours() == 9


def test_level_3_from_friday_afternoon():
    # Viernes 16 de octubre de 2026, 16:00: 1 h el viernes, 9 h el lunes y 6 h el martes
    friday = datetime(2026, 10, 16, 16, 0, tzinfo=CR)
    assert add_business_hours(friday, 16) == datetime(2026, 10, 20, 14, 0, tzinfo=CR)


def test_saturday_starts_monday_morning():
    saturday = datetime(2026, 10, 17, 10, 0, tzinfo=CR)
    assert add_business_hours(saturday, 4) == datetime(2026, 10, 19, 12, 0, tzinfo=CR)
    assert add_business_hours(saturday, 0) == datetime(2026, 10, 19, 8, 0, tzinfo=CR)


def test_before_opening_starts_at_opening():
    early = datetime(2026, 10, 19, 6, 30, tzinfo=CR)
    assert add_business_hours(early, 1) == datetime(2026, 10, 19, 9, 0, tzinfo=CR)


def test_holiday_is_skipped(monkeypatch):
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "2026-10-19, 2026-12-25")
    friday = datetime(2026, 10, 16, 16, 0, tzinfo=CR)
    assert add_business_hours(friday, 16) == datetime(2026, 10, 21, 14, 0, tzinfo=CR)


def test_naive_date_is_utc():
    # Viernes 22:00 UTC = viernes 16:00 en Costa Rica (UTC-6)
    naive = datetime(2026, 10, 16, 22, 0)
    expected = datetime(2026, 10, 20, 14, 0, tzinfo=CR)
    assert add_business_hours(naive, 16) == expected
    assert add_business_hours(naive.replace(tzinfo=timezone.utc), 16) == expected
