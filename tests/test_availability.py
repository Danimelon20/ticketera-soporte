from datetime import date, timedelta

import pytest

from app import models
from app.availability import office_today
from app.database import SessionLocal


def _own(client, user, status, return_date=None):
    body = {"status": status, "return_date": return_date.isoformat() if return_date else None}
    return client.put("/api/account/availability", json=body, headers=user["headers"])


def _other(client, actor, user_id, status, return_date=None):
    body = {"status": status, "return_date": return_date.isoformat() if return_date else None}
    return client.put(f"/api/accounts/{user_id}/availability", json=body, headers=actor["headers"])


def _changes(user_id):
    db = SessionLocal()
    try:
        return (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == user_id, models.AccountChange.action == "availability_changed")
            .order_by(models.AccountChange.id)
            .all()
        )
    finally:
        db.close()


def test_technician_changes_own_availability(client, make_user):
    technician = make_user("technician")
    back = office_today() + timedelta(days=7)
    response = _own(client, technician, "vacation", back)
    assert response.status_code == 200
    assert response.json()["availability"] == "vacation"
    assert response.json()["return_date"] == back.isoformat()
    me = client.get("/api/auth/me", headers=technician["headers"]).json()
    assert me["availability"] == "vacation"

    rows = _changes(technician["id"])
    assert len(rows) == 1
    assert rows[0].actor_id == technician["id"]
    assert rows[0].old_value == "Disponible"
    assert rows[0].new_value == f"Vacaciones (regresa {back.strftime('%d/%m/%Y')})"


@pytest.mark.parametrize("role", ["requester", "coordinator", "admin"])
def test_only_technicians_have_own_availability(client, make_user, role):
    assert _own(client, make_user(role), "away").status_code == 403


@pytest.mark.parametrize("actor_role", ["coordinator", "admin"])
def test_coordinator_and_admin_change_technician(client, make_user, actor_role):
    actor = make_user(actor_role)
    technician = make_user("technician")
    assert _other(client, actor, technician["id"], "away").status_code == 204
    rows = _changes(technician["id"])
    assert [r.new_value for r in rows] == ["Ausente"]
    assert rows[0].actor_id == actor["id"]


@pytest.mark.parametrize("actor_role,target_role", [
    ("technician", "technician"), ("requester", "technician"), ("coordinator", "coordinator"),
    ("admin", "coordinator"), ("admin", "requester"),
])
def test_cannot_change_others_availability(client, make_user, actor_role, target_role):
    actor = make_user(actor_role)
    target = make_user(target_role)
    assert _other(client, actor, target["id"], "away").status_code == 403
    assert _changes(target["id"]) == []


def test_return_date_rules(client, make_user):
    technician = make_user("technician")
    soon = office_today() + timedelta(days=3)
    # Solo con Vacaciones o Ausente, y nunca en el pasado
    assert _own(client, technician, "available", soon).status_code == 422
    assert _own(client, technician, "away", office_today() - timedelta(days=1)).status_code == 422
    assert _own(client, technician, "away", soon).status_code == 200
    # Al volver a Disponible la fecha se borra
    response = _own(client, technician, "available")
    assert response.status_code == 200
    assert response.json()["availability"] == "available"
    assert response.json()["return_date"] is None


def test_lists_show_availability(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    _own(client, technician, "away")
    users = {u["id"]: u for u in client.get("/api/users", headers=coordinator["headers"]).json()}
    assert users[technician["id"]]["availability"] == "away"
    assert users[coordinator["id"]]["availability"] is None
    staff = {u["id"]: u for u in client.get("/api/users", headers=technician["headers"]).json()}
    assert staff[technician["id"]]["availability"] == "away"
    directory = client.get("/api/directory?role=technician", headers=coordinator["headers"]).json()
    assert next(u for u in directory if u["id"] == technician["id"])["availability"] == "away"


def _assign(client, actor, ticket_id, user_id, confirm=False):
    body = {"assigned_to": user_id}
    if confirm:
        body["confirm_unavailable"] = True
    return client.patch(f"/api/tickets/{ticket_id}", json=body, headers=actor["headers"])


def test_assigning_unavailable_technician_needs_confirmation(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Ana Mora")
    back = office_today() + timedelta(days=5)
    _own(client, technician, "vacation", back)
    ticket = make_ticket(make_user("requester"))

    response = _assign(client, coordinator, ticket["id"], technician["id"])
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Ana Mora está de vacaciones (regresa el {back.strftime('%d/%m/%Y')}). ¿Asignar de todas formas?"
    )
    assert _assign(client, coordinator, ticket["id"], technician["id"], confirm=True).status_code == 200
    # Ya asignado: guardar otra vez la misma persona no pide confirmación
    assert _assign(client, coordinator, ticket["id"], technician["id"]).status_code == 200


def test_unavailable_technician_assigning_self_needs_confirmation(client, make_user, make_ticket):
    technician = make_user("technician")
    _own(client, technician, "away")
    ticket = make_ticket(make_user("requester"))
    assert _assign(client, technician, ticket["id"], technician["id"]).status_code == 409
    assert _assign(client, technician, ticket["id"], technician["id"], confirm=True).status_code == 200


def test_available_technician_needs_no_confirmation(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    assert _assign(client, coordinator, ticket["id"], technician["id"]).status_code == 200
