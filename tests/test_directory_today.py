from app import config, models
from app.database import SessionLocal


def test_directory_shows_todays_shift(make_user, client, monkeypatch):
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    coordinator = make_user("coordinator")
    with_shift = make_user("technician")
    without_shift = make_user("technician")
    db = SessionLocal()
    try:
        for day in range(7):
            db.add(models.TechnicianSchedule(user_id=with_shift["id"], weekday=day, start_time="00:00", end_time="23:59"))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/directory?role=technician", headers=coordinator["headers"])
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert rows[with_shift["id"]]["schedule_today"] == "00:00–23:59"
    assert rows[without_shift["id"]]["schedule_today"] is None


def test_holiday_has_no_shift_today(make_user, client, monkeypatch):
    from app.availability import office_today

    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", office_today().isoformat())
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    db = SessionLocal()
    try:
        for day in range(7):
            db.add(models.TechnicianSchedule(user_id=technician["id"], weekday=day, start_time="00:00", end_time="23:59"))
        db.commit()
    finally:
        db.close()

    rows = client.get("/api/directory?role=technician", headers=coordinator["headers"]).json()
    assert next(r for r in rows if r["id"] == technician["id"])["schedule_today"] is None
