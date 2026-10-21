from datetime import date, datetime, timedelta

import pytest

from app import auto_assign, config, models, nav
from app.database import SessionLocal

# Lunes 14 de abril de 2031 en Costa Rica (UTC-6). Oficina de 08:00 a 17:00.
MONDAY_8AM = datetime(2031, 4, 14, 14, 0)
MONDAY_9AM = datetime(2031, 4, 14, 15, 0)
FAR_AWAY = MONDAY_9AM + timedelta(days=30)


@pytest.fixture(autouse=True)
def office(monkeypatch):
    monkeypatch.setattr(config, "ALERT_WARN_PERCENT", 75)
    monkeypatch.setattr(config, "OFFICE_DAYS", "1-5")
    monkeypatch.setattr(config, "OFFICE_START", "08:00")
    monkeypatch.setattr(config, "OFFICE_END", "17:00")
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    db = SessionLocal()
    try:
        # Los tickets abiertos de otras pruebas cambiarían la cola
        db.query(models.Ticket).update({"status": "closed"})
        db.commit()
    finally:
        db.close()


def _ticket(requester, *, due_at=FAR_AWAY, status="new", assigned_to=None, level=2, source="web",
            title="Ticket de la cola"):
    db = SessionLocal()
    try:
        ticket = models.Ticket(
            title=title, description="Llamar al 8888-1234, cédula 102340567", category_id=1, priority_id=1,
            status=status, assigned_to=assigned_to, created_by=requester["id"], source=source,
            contact_phone="88881234", contact_id_number="102340567", created_at=MONDAY_8AM,
        )
        db.add(ticket)
        db.flush()
        if due_at is not None or level is not None:
            db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="done", ai_level=level, due_at=due_at))
        db.commit()
        return ticket.id
    finally:
        db.close()


def _queue(user, now=MONDAY_9AM, **params):
    db = SessionLocal()
    try:
        return nav.queue(db, db.get(models.User, user["id"]), now=now, **params)
    finally:
        db.close()


def _ids(result):
    return [item["id"] for item in result["items"]]


def test_sorted_by_due_date_without_date_last(make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    late = _ticket(requester, due_at=MONDAY_9AM + timedelta(days=3))
    no_due = _ticket(requester, due_at=None, level=None)
    soon = _ticket(requester, due_at=MONDAY_9AM + timedelta(hours=2))
    overdue = _ticket(requester, due_at=MONDAY_8AM + timedelta(minutes=30))

    result = _queue(coordinator)
    assert _ids(result) == [overdue, soon, late, no_due]
    first = result["items"][0]
    assert first["due_state"] == "overdue" and first["consumed_pct"] == 100
    assert first["remaining_seconds"] == -30 * 60
    assert result["items"][-1]["due_state"] is None


def test_views_and_filters(make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    overdue = _ticket(requester, due_at=MONDAY_8AM + timedelta(minutes=30), level=3)
    # Creado 08:00, vence 10:00: a las 09:00 lleva 50 %; a las 09:30, 75 %
    due_soon = _ticket(requester, due_at=MONDAY_8AM + timedelta(hours=2), assigned_to=technician["id"],
                       status="in_progress", source="email")
    resolved = _ticket(requester, status="resolved", assigned_to=technician["id"])
    no_level = _ticket(requester, due_at=None, level=None)
    now = MONDAY_8AM + timedelta(minutes=90)

    assert _ids(_queue(coordinator, now, view="overdue")) == [overdue]
    assert _ids(_queue(coordinator, now, view="due_soon")) == [due_soon]
    assert set(_ids(_queue(coordinator, now, view="unassigned"))) == {overdue, no_level}
    assert resolved not in _ids(_queue(coordinator, now, view="open"))
    assert _ids(_queue(coordinator, now, view="all", status="resolved")) == [resolved]
    assert _ids(_queue(coordinator, now, view="open", source="email")) == [due_soon]
    assert _ids(_queue(coordinator, now, view="open", level="3")) == [overdue]
    assert _ids(_queue(coordinator, now, view="open", level="none")) == [no_level]
    assert _ids(_queue(coordinator, now, view="open", assigned_to=technician["id"])) == [due_soon]
    assert set(_ids(_queue(coordinator, now, view="open", assigned_to=0))) == {overdue, no_level}
    assert _ids(_queue(technician, now, view="mine_due_soon")) == [due_soon]


def test_pagination_25(make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    created = [_ticket(requester, due_at=MONDAY_9AM + timedelta(hours=i + 1)) for i in range(27)]

    first = _queue(coordinator)
    assert first["total"] == 27 and first["pages"] == 2 and first["page"] == 1
    assert _ids(first) == created[:25]
    second = _queue(coordinator, page=2)
    assert _ids(second) == created[25:]
    # Una página que no existe muestra la última
    assert _queue(coordinator, page=9)["page"] == 2


def test_assignee_notes(make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    away = make_user("technician")
    requester = make_user("requester")
    db = SessionLocal()
    try:
        db.add(models.TechnicianSchedule(user_id=technician["id"], weekday=0, start_time="08:00", end_time="12:00"))
        db.add(models.AvailabilityPeriod(user_id=away["id"], kind="vacation", start_date=date(2031, 4, 10),
                                         end_date=date(2031, 4, 18), created_at=MONDAY_8AM))
        db.commit()
    finally:
        db.close()
    # 15:00 en Costa Rica: después del fin de su turno (12:00)
    after = _ticket(requester, assigned_to=technician["id"], due_at=datetime(2031, 4, 14, 21, 0))
    within = _ticket(requester, assigned_to=technician["id"], due_at=datetime(2031, 4, 14, 17, 0))
    vacation = _ticket(requester, assigned_to=away["id"])

    notes = {item["id"]: item["assignee_note"] for item in _queue(coordinator)["items"]}
    assert notes[after] == "after_shift"
    assert notes[within] is None
    assert notes[vacation] == "vacation"


def test_technician_assigned_to_is_ignored(make_user, client):
    technician = make_user("technician")
    other = make_user("technician")
    requester = make_user("requester")
    _ticket(requester, assigned_to=other["id"])
    _ticket(requester)

    everything = _queue(technician, view="open")
    filtered = _queue(technician, view="open", assigned_to=other["id"])
    assert filtered["total"] == everything["total"] == 2

    response = client.get(f"/api/queue?assigned_to={other['id']}", headers=technician["headers"])
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_queue_response_has_no_personal_data(make_user, client):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    _ticket(requester, title="Sin acceso al correo")

    response = client.get("/api/queue?view=open", headers=coordinator["headers"])
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item) == {
        "id", "title", "requester_name", "level", "source", "status", "assigned_to", "assigned_name",
        "reported", "auto_assigned", "due_at", "due_state", "remaining_seconds", "consumed_pct", "assignee_note",
    }
    text = response.text
    for secret in ("8888", "102340567", requester["email"], requester["phone"], requester["id_number"], "Llamar"):
        assert secret not in text


def test_requester_cannot_use_queue_or_suggestion(make_user, client):
    requester = make_user("requester")
    ticket_id = _ticket(requester)
    assert client.get("/api/queue", headers=requester["headers"]).status_code == 403
    assert client.get(f"/api/tickets/{ticket_id}/suggestion", headers=requester["headers"]).status_code == 403


def _always_on_shift(user_id):
    db = SessionLocal()
    try:
        for day in range(7):
            db.add(models.TechnicianSchedule(user_id=user_id, weekday=day, start_time="00:00", end_time="23:59"))
        db.commit()
    finally:
        db.close()


def test_suggestion_same_as_auto_assign_and_assigns_nothing(make_user, client):
    coordinator = make_user("coordinator")
    candidate = make_user("technician")
    _always_on_shift(candidate["id"])
    requester = make_user("requester")
    ticket_id = _ticket(requester)

    response = client.get(f"/api/tickets/{ticket_id}/suggestion", headers=coordinator["headers"])
    assert response.status_code == 200
    db = SessionLocal()
    try:
        expected = auto_assign.choose(db, nav._now())
        assert db.get(models.Ticket, ticket_id).assigned_to is None
        assert db.query(models.History).filter(models.History.ticket_id == ticket_id).count() == 0
    finally:
        db.close()
    body = response.json()
    if expected is None:
        assert body is None
    else:
        assert body["user_id"] == expected.id
        assert set(body) == {"user_id", "name", "load"}


def test_no_suggestion_when_assignee_available_or_closed(make_user, client):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    assigned = _ticket(requester, assigned_to=technician["id"], status="in_progress")
    closed = _ticket(requester, status="closed")
    for ticket_id in (assigned, closed):
        response = client.get(f"/api/tickets/{ticket_id}/suggestion", headers=coordinator["headers"])
        assert response.status_code == 200 and response.json() is None


def test_technician_cannot_see_suggestion(make_user, client):
    technician = make_user("technician")
    requester = make_user("requester")
    ticket_id = _ticket(requester)
    assert client.get(f"/api/tickets/{ticket_id}/suggestion", headers=technician["headers"]).status_code == 403


def test_detail_due_state_only_for_staff(make_user, make_ticket, client):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    staff = client.get(f"/api/tickets/{ticket_id}", headers=coordinator["headers"]).json()
    assert "due_state" in staff and "consumed_pct" in staff
    own = client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"]).json()
    assert "due_state" not in own and "consumed_pct" not in own
