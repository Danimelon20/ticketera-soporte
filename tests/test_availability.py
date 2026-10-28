"""Disponibilidad automática: horario (Disponible / Ausente) y periodos de Vacaciones o Incapacidad."""
from datetime import date, datetime, timedelta

import pytest

from app import auto_assign, availability, config, models
from app.database import SessionLocal

# Lunes 10/03/2031 en Costa Rica (UTC-6)
MONDAY_10AM = datetime(2031, 3, 10, 16, 0)
MONDAY_6PM = datetime(2031, 3, 11, 0, 0)
TODAY = date(2031, 3, 10)
WEEKDAYS = [{"weekday": d, "start": "08:00", "end": "17:00"} for d in range(5)]


@pytest.fixture(autouse=True)
def office(monkeypatch):
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)


def _schedule(user_id, days=WEEKDAYS):
    db = SessionLocal()
    try:
        for day in days:
            db.add(models.TechnicianSchedule(user_id=user_id, weekday=day["weekday"],
                                             start_time=day["start"], end_time=day["end"]))
        db.commit()
    finally:
        db.close()


def _status(user, now=MONDAY_10AM):
    db = SessionLocal()
    try:
        return availability.get(db, db.get(models.User, user["id"]), now)
    finally:
        db.close()


def _own(client, user, kind, start, end):
    body = {"kind": kind, "start_date": start.isoformat(), "end_date": end.isoformat()}
    return client.post("/api/account/availability-periods", json=body, headers=user["headers"])


def _other(client, actor, user_id, kind, start, end):
    body = {"kind": kind, "start_date": start.isoformat(), "end_date": end.isoformat()}
    return client.post(f"/api/accounts/{user_id}/availability-periods", json=body, headers=actor["headers"])


def _changes(user_id, action):
    db = SessionLocal()
    try:
        return (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == user_id, models.AccountChange.action == action)
            .order_by(models.AccountChange.id)
            .all()
        )
    finally:
        db.close()


# ----- Estado automático -----

@pytest.mark.parametrize("role", ["technician", "coordinator"])
def test_schedule_gives_available_or_away(make_user, role):
    person = make_user(role)
    _schedule(person["id"])
    assert _status(person) == ("available", None)
    # Después de su salida (17:00) queda Ausente, sin que nadie lo cambie
    assert _status(person, MONDAY_6PM) == ("away", None)


def test_holiday_is_away(make_user, monkeypatch):
    technician = make_user("technician")
    _schedule(technician["id"])
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", TODAY.isoformat())
    assert _status(technician)[0] == "away"


def test_without_schedule_is_available(make_user):
    assert _status(make_user("technician")) == ("available", None)


def test_other_roles_have_no_status(make_user, client):
    for role in ("requester", "admin"):
        user = make_user(role)
        assert client.get("/api/auth/me", headers=user["headers"]).json()["availability"] is None


@pytest.mark.parametrize("kind", ["vacation", "incapacity"])
def test_period_wins_over_schedule(make_user, client, kind):
    technician = make_user("technician")
    _schedule(technician["id"])
    assert _own(client, technician, kind, TODAY, TODAY + timedelta(days=4)).status_code == 201
    # Dentro de su horario, pero con un periodo vigente: regresa el día después del último
    assert _status(technician) == (kind, TODAY + timedelta(days=5))
    me = client.get("/api/auth/me", headers=technician["headers"]).json()
    assert me["availability"] == kind
    assert [p["kind"] for p in me["periods"]] == [kind]


def test_vacation_starts_on_its_first_day(make_user, client):
    technician = make_user("technician")
    _schedule(technician["id"])
    start = TODAY + timedelta(days=1)
    assert _own(client, technician, "vacation", start, start + timedelta(days=6)).status_code == 201
    # Hoy todavía trabaja; el martes ya está de vacaciones. El último día (lunes 17) sigue de vacaciones
    # y al día siguiente vuelve lo que diga el horario
    assert _status(technician)[0] == "available"
    assert _status(technician, MONDAY_10AM + timedelta(days=1))[0] == "vacation"
    assert _status(technician, MONDAY_10AM + timedelta(days=7))[0] == "vacation"
    assert _status(technician, MONDAY_10AM + timedelta(days=8))[0] == "available"


# ----- Periodos propios -----

@pytest.mark.parametrize("role", ["technician", "coordinator"])
def test_own_period_is_logged(client, make_user, role):
    person = make_user(role)
    assert _own(client, person, "vacation", TODAY, TODAY + timedelta(days=2)).status_code == 201
    rows = _changes(person["id"], "availability_period_added")
    assert len(rows) == 1
    assert rows[0].actor_id == person["id"]
    assert rows[0].new_value == "Vacaciones del 10/03/2031 al 12/03/2031"


@pytest.mark.parametrize("role", ["requester", "admin"])
def test_only_staff_have_own_periods(client, make_user, role):
    assert _own(client, make_user(role), "vacation", TODAY, TODAY).status_code == 403


def test_manual_status_route_is_gone(client, make_user):
    technician = make_user("technician")
    response = client.put("/api/account/availability", json={"status": "away"}, headers=technician["headers"])
    assert response.status_code in (404, 405)


@pytest.mark.parametrize("kind,start,end,code", [
    ("vacation", TODAY + timedelta(days=3), TODAY, 422),        # primer día después del último
    ("vacation", TODAY - timedelta(days=5), TODAY - timedelta(days=1), 422),  # ya terminó
    ("vacation", TODAY, TODAY + timedelta(days=400), 422),      # más de un año
    ("away", TODAY, TODAY, 422),                                # Ausente no se registra: sale del horario
])
def test_period_rules(client, make_user, kind, start, end, code):
    assert _own(client, make_user("technician"), kind, start, end).status_code == code


def test_periods_cannot_overlap(client, make_user):
    technician = make_user("technician")
    assert _own(client, technician, "vacation", TODAY, TODAY + timedelta(days=5)).status_code == 201
    assert _own(client, technician, "incapacity", TODAY + timedelta(days=5), TODAY + timedelta(days=8)).status_code == 409
    assert _own(client, technician, "incapacity", TODAY + timedelta(days=6), TODAY + timedelta(days=8)).status_code == 201


def test_remove_own_period(client, make_user):
    technician = make_user("technician")
    me = _own(client, technician, "incapacity", TODAY, TODAY + timedelta(days=2)).json()
    period_id = me["periods"][0]["id"]
    other = make_user("technician")
    # Un periodo de otra persona: como si no existiera
    assert client.delete(f"/api/account/availability-periods/{period_id}", headers=other["headers"]).status_code == 404
    response = client.delete(f"/api/account/availability-periods/{period_id}", headers=technician["headers"])
    assert response.status_code == 200
    assert response.json()["periods"] == []
    assert len(_changes(technician["id"], "availability_period_removed")) == 1


# ----- Periodos de otras personas (permisos en accounts.PERMISSIONS) -----

@pytest.mark.parametrize("actor_role,target_role", [
    ("coordinator", "technician"), ("admin", "technician"), ("admin", "coordinator"),
])
def test_managers_register_periods(client, make_user, actor_role, target_role):
    actor = make_user(actor_role)
    target = make_user(target_role)
    response = _other(client, actor, target["id"], "incapacity", TODAY, TODAY + timedelta(days=1))
    assert response.status_code == 201
    rows = _changes(target["id"], "availability_period_added")
    assert rows[0].actor_id == actor["id"]
    assert client.delete(f"/api/accounts/{target['id']}/availability-periods/{response.json()['id']}",
                         headers=actor["headers"]).status_code == 204


@pytest.mark.parametrize("actor_role,target_role", [
    ("technician", "technician"), ("requester", "technician"), ("coordinator", "coordinator"),
    ("admin", "requester"), ("admin", "admin"),
])
def test_cannot_register_others_periods(client, make_user, actor_role, target_role):
    actor = make_user(actor_role)
    target = make_user(target_role)
    assert _other(client, actor, target["id"], "vacation", TODAY, TODAY).status_code == 403
    assert _changes(target["id"], "availability_period_added") == []


def test_nobody_uses_the_accounts_route_on_self(client, make_user):
    coordinator = make_user("coordinator")
    assert _other(client, coordinator, coordinator["id"], "vacation", TODAY, TODAY).status_code == 403


def test_coordinator_cannot_remove_period_of_coordinator(client, make_user):
    admin = make_user("admin")
    target = make_user("coordinator")
    period = _other(client, admin, target["id"], "vacation", TODAY, TODAY + timedelta(days=1)).json()
    coordinator = make_user("coordinator")
    response = client.delete(f"/api/accounts/{target['id']}/availability-periods/{period['id']}",
                             headers=coordinator["headers"])
    assert response.status_code == 403


# ----- Listas y asignación -----

def test_lists_show_availability(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    _own(client, technician, "vacation", TODAY, TODAY + timedelta(days=1))
    users = {u["id"]: u for u in client.get("/api/users", headers=coordinator["headers"]).json()}
    assert users[technician["id"]]["availability"] == "vacation"
    # El coordinador también tiene disponibilidad (sin horario ni periodo: Disponible)
    assert users[coordinator["id"]]["availability"] == "available"
    directory = client.get("/api/directory?role=technician", headers=coordinator["headers"]).json()
    assert next(u for u in directory if u["id"] == technician["id"])["availability"] == "vacation"
    team = {u["id"]: u for u in client.get("/api/team", headers=coordinator["headers"]).json()}
    assert [p["kind"] for p in team[technician["id"]]["periods"]] == ["vacation"]


def _assign(client, actor, ticket_id, user_id, confirm=False):
    body = {"assigned_to": user_id}
    if confirm:
        body["confirm_unavailable"] = True
    return client.patch(f"/api/tickets/{ticket_id}", json=body, headers=actor["headers"])


def test_assigning_technician_on_vacation_needs_confirmation(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Ana Mora")
    _own(client, technician, "vacation", TODAY, TODAY + timedelta(days=4))
    ticket = make_ticket(make_user("requester"))

    response = _assign(client, coordinator, ticket["id"], technician["id"])
    assert response.status_code == 409
    assert response.json()["detail"] == "Ana Mora está de vacaciones (regresa el 15/03/2031). ¿Asignar de todas formas?"
    assert _assign(client, coordinator, ticket["id"], technician["id"], confirm=True).status_code == 200
    # Ya asignado: guardar otra vez la misma persona no pide confirmación
    assert _assign(client, coordinator, ticket["id"], technician["id"]).status_code == 200


def test_assigning_technician_out_of_shift_needs_confirmation(client, make_user, make_ticket, monkeypatch):
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Luis Soto")
    _schedule(technician["id"])
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_6PM)
    ticket = make_ticket(make_user("requester"))
    response = _assign(client, coordinator, ticket["id"], technician["id"])
    assert response.status_code == 409
    assert response.json()["detail"] == "Luis Soto está fuera de su horario. ¿Asignar de todas formas?"


def test_technician_with_incapacity_is_assigned_only_by_coordinator_with_confirmation(client, make_user, make_ticket):
    # El técnico ya no se asigna tickets (ni confirmando); el coordinador sí, con la confirmación de siempre
    technician = make_user("technician")
    _own(client, technician, "incapacity", TODAY, TODAY)
    ticket = make_ticket(make_user("requester"))
    assert _assign(client, technician, ticket["id"], technician["id"]).status_code == 403
    assert _assign(client, technician, ticket["id"], technician["id"], confirm=True).status_code == 403
    coordinator = make_user("coordinator")
    assert _assign(client, coordinator, ticket["id"], technician["id"]).status_code == 409
    assert _assign(client, coordinator, ticket["id"], technician["id"], confirm=True).status_code == 200


def test_available_technician_needs_no_confirmation(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    _schedule(technician["id"])
    ticket = make_ticket(make_user("requester"))
    assert _assign(client, coordinator, ticket["id"], technician["id"]).status_code == 200
