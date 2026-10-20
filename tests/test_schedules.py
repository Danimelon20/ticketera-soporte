from datetime import datetime

import pytest

from app import config, models, schedules
from app.database import SessionLocal

WEEKDAYS = [{"weekday": d, "start": "08:00", "end": "17:00"} for d in range(5)]


@pytest.fixture(autouse=True)
def clean_schedules(client):
    yield
    # Un técnico con horario sería candidato a asignación automática en otras pruebas
    db = SessionLocal()
    try:
        db.query(models.TechnicianSchedule).delete()
        db.commit()
    finally:
        db.close()


def _put(client, actor, user_id, days):
    return client.put(f"/api/accounts/{user_id}/schedule", json={"days": days}, headers=actor["headers"])


def _rows(user_id):
    db = SessionLocal()
    try:
        return [(r.weekday, r.start_time, r.end_time) for r in schedules.get(db, user_id)]
    finally:
        db.close()


def _changes(user_id):
    db = SessionLocal()
    try:
        return (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == user_id, models.AccountChange.action == "schedule_changed")
            .order_by(models.AccountChange.id)
            .all()
        )
    finally:
        db.close()


@pytest.mark.parametrize("role", ["coordinator", "admin"])
def test_coordinator_and_admin_set_schedule(client, make_user, role):
    actor = make_user(role)
    technician = make_user("technician")
    days = WEEKDAYS + [{"weekday": 5, "start": "08:00", "end": "12:00"}]
    assert _put(client, actor, technician["id"], days).status_code == 204
    assert _rows(technician["id"])[-1] == (5, "08:00", "12:00")
    rows = _changes(technician["id"])
    assert len(rows) == 1
    assert rows[0].actor_id == actor["id"]
    assert rows[0].old_value == "Sin horario"
    assert rows[0].new_value == "Lun–Vie 08:00–17:00 · Sáb 08:00–12:00"
    # Lista vacía: sin horario
    assert _put(client, actor, technician["id"], []).status_code == 204
    assert _rows(technician["id"]) == []
    assert _changes(technician["id"])[-1].new_value == "Sin horario"


@pytest.mark.parametrize("actor_role,target_role", [
    ("technician", "technician"), ("requester", "technician"), ("coordinator", "coordinator"),
    ("admin", "requester"),
])
def test_others_cannot_set_schedule(client, make_user, actor_role, target_role):
    actor = make_user(actor_role)
    target = make_user(target_role)
    assert _put(client, actor, target["id"], WEEKDAYS).status_code == 403
    assert _rows(target["id"]) == []


def test_technician_cannot_set_own_schedule(client, make_user):
    technician = make_user("technician")
    assert _put(client, technician, technician["id"], WEEKDAYS).status_code == 403


@pytest.mark.parametrize("day", [
    {"weekday": 0, "start": "8:00", "end": "17:00"},
    {"weekday": 0, "start": "08:00", "end": "24:00"},
    {"weekday": 0, "start": "08:60", "end": "17:00"},
    {"weekday": 0, "start": "17:00", "end": "08:00"},
    {"weekday": 0, "start": "08:00", "end": "08:00"},
    {"weekday": 7, "start": "08:00", "end": "17:00"},
])
def test_schedule_validation(client, make_user, day):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    assert _put(client, coordinator, technician["id"], [day]).status_code == 422
    assert _rows(technician["id"]) == []
    assert _changes(technician["id"]) == []


def test_repeated_day_is_rejected(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    days = [{"weekday": 1, "start": "08:00", "end": "12:00"}, {"weekday": 1, "start": "13:00", "end": "17:00"}]
    assert _put(client, coordinator, technician["id"], days).status_code == 422


def test_schedule_is_shown_in_directory_and_me(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    all_day = [{"weekday": d, "start": "00:00", "end": "23:59"} for d in range(7)]
    _put(client, coordinator, technician["id"], all_day)

    rows = client.get("/api/directory?role=technician", headers=coordinator["headers"]).json()
    row = next(r for r in rows if r["id"] == technician["id"])
    assert row["schedule_summary"] == "Lun–Dom 00:00–23:59"
    assert len(row["schedule"]) == 7
    assert isinstance(row["on_shift"], bool)

    me = client.get("/api/auth/me", headers=technician["headers"]).json()
    assert me["schedule_summary"] == "Lun–Dom 00:00–23:59"
    assert me["schedule"][0] == {"weekday": 0, "start": "00:00", "end": "23:59"}
    # Los demás roles no reciben estos campos con datos
    assert client.get("/api/auth/me", headers=coordinator["headers"]).json()["schedule"] is None


def test_on_shift(monkeypatch):
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    rows = [models.TechnicianSchedule(weekday=0, start_time="08:00", end_time="17:00")]
    # Lunes 10/03/2031 en Costa Rica (UTC-6)
    assert schedules.on_shift(rows, datetime(2031, 3, 10, 14, 0)) is True   # 08:00
    assert schedules.on_shift(rows, datetime(2031, 3, 10, 13, 59)) is False  # 07:59
    assert schedules.on_shift(rows, datetime(2031, 3, 10, 23, 0)) is False  # 17:00
    assert schedules.on_shift(rows, datetime(2031, 3, 11, 15, 0)) is False  # martes
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "2031-03-10")
    assert schedules.on_shift(rows, datetime(2031, 3, 10, 15, 0)) is False


def test_summary_groups_consecutive_days():
    days = [
        {"weekday": 0, "start": "08:00", "end": "17:00"},
        {"weekday": 1, "start": "08:00", "end": "17:00"},
        {"weekday": 3, "start": "08:00", "end": "17:00"},
        {"weekday": 6, "start": "09:00", "end": "13:00"},
    ]
    assert schedules.summary(days) == "Lun–Mar 08:00–17:00 · Jue 08:00–17:00 · Dom 09:00–13:00"
