from datetime import datetime, timedelta, timezone

from app import availability, models
from app.database import SessionLocal

MONDAY = 0


def _schedule(user_id, start, end, weekday=MONDAY):
    db = SessionLocal()
    try:
        db.add(models.TechnicianSchedule(user_id=user_id, weekday=weekday, start_time=start, end_time=end))
        db.commit()
    finally:
        db.close()


def _filter(client, admin, start, end, match=None, day=MONDAY, **extra):
    params = {"schedule_day": day, "schedule_start": start, "schedule_end": end, **extra}
    if match:
        params["schedule_match"] = match
    return client.get("/api/admin/accounts", params=params, headers=admin["headers"])


def _ids(response):
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


def test_covers_whole_range(make_user, client):
    admin = make_user("admin")
    full = make_user("technician")
    late = make_user("technician")
    coordinator = make_user("coordinator")
    _schedule(full["id"], "08:00", "17:00")
    _schedule(late["id"], "10:00", "19:00")
    _schedule(coordinator["id"], "07:00", "18:00")

    response = _filter(client, admin, "08:00", "12:00")
    ids = _ids(response)
    assert full["id"] in ids and coordinator["id"] in ids
    assert late["id"] not in ids
    row = next(r for r in response.json() if r["id"] == full["id"])
    assert row["schedule_day"] == "08:00–17:00"


def test_partial_overlap(make_user, client):
    admin = make_user("admin")
    late = make_user("technician")
    touching = make_user("technician")
    _schedule(late["id"], "10:00", "19:00")
    # Termina justo a la hora de entrada: no se cruza ni un minuto
    _schedule(touching["id"], "06:00", "08:00")

    ids = _ids(_filter(client, admin, "08:00", "12:00", match="overlaps"))
    assert late["id"] in ids
    assert touching["id"] not in ids
    assert late["id"] not in _ids(_filter(client, admin, "08:00", "12:00", match="covers"))


def test_without_schedule_that_day_is_left_out(make_user, client):
    admin = make_user("admin")
    no_schedule = make_user("technician")
    other_day = make_user("technician")
    _schedule(other_day["id"], "08:00", "17:00", weekday=2)
    ids = _ids(_filter(client, admin, "08:00", "12:00"))
    assert no_schedule["id"] not in ids and other_day["id"] not in ids
    assert other_day["id"] in _ids(_filter(client, admin, "08:00", "12:00", day=2))


def test_away_staff_still_listed_with_state(make_user, client):
    admin = make_user("admin")
    technician = make_user("technician")
    _schedule(technician["id"], "08:00", "17:00")
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today = availability._local_day(now)
        db.add(models.AvailabilityPeriod(user_id=technician["id"], kind="vacation", start_date=today,
                                         end_date=today + timedelta(days=5), created_at=now))
        db.commit()
    finally:
        db.close()
    rows = _filter(client, admin, "08:00", "12:00").json()
    row = next(r for r in rows if r["id"] == technician["id"])
    assert row["state"] == "vacation"


def test_only_support_team_with_filter(make_user, client):
    admin = make_user("admin")
    requester = make_user("requester")
    other_admin = make_user("admin")
    _schedule(requester["id"], "00:00", "23:59")
    _schedule(other_admin["id"], "00:00", "23:59")
    response = _filter(client, admin, "08:00", "12:00", match="overlaps")
    assert all(row["role"] in ("technician", "coordinator") for row in response.json())
    assert all(row["kind"] == "user" for row in response.json())
    ids = _ids(response)
    assert requester["id"] not in ids and other_admin["id"] not in ids
    # Aunque se pida el rol de solicitante
    assert _ids(_filter(client, admin, "08:00", "12:00", role="requester")) == set()


def test_invalid_range_is_422(make_user, client):
    admin = make_user("admin")
    assert _filter(client, admin, "12:00", "08:00").status_code == 422
    assert _filter(client, admin, "08:00", "08:00").status_code == 422
    assert _filter(client, admin, "8:00", "12:00").status_code == 422
    assert client.get("/api/admin/accounts", params={"schedule_day": MONDAY},
                      headers=admin["headers"]).status_code == 422


def test_only_admin_can_filter(make_user, client):
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert _filter(client, user, "08:00", "12:00").status_code == 403


def test_without_filter_lists_everything(make_user, client):
    admin = make_user("admin")
    requester = make_user("requester")
    response = client.get("/api/admin/accounts", headers=admin["headers"])
    assert requester["id"] in _ids(response)
    assert all(row["schedule_day"] is None for row in response.json())
