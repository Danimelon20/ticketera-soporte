"""Reasignar con tickets por departamento: el detalle trae solo los técnicos activos del departamento del ticket,
y el servidor sigue rechazando a los de otro departamento."""
from app import models
from app.database import SessionLocal
from org_helpers import place, set_department, team


def _deactivate(user_id):
    db = SessionLocal()
    try:
        db.get(models.User, user_id).is_active = False
        db.commit()
    finally:
        db.close()


def test_detail_lists_only_active_technicians_of_the_department(client, make_user, make_ticket, routing_on):
    dept_a, coordinator_a, technician_a = team(make_user)
    dept_b, _, technician_b = team(make_user)
    second = make_user("technician")
    place(second["id"], dept_a, 2)
    gone = make_user("technician")
    place(gone["id"], dept_a, 1)
    _deactivate(gone["id"])
    ticket = make_ticket(make_user("requester"))
    set_department(ticket["id"], dept_a)
    for viewer in (coordinator_a, make_user("admin")):
        detail = client.get(f"/api/tickets/{ticket['id']}", headers=viewer["headers"]).json()
        assert sorted(detail["assignable_ids"]) == sorted([technician_a["id"], second["id"]])
    # El servidor sigue rechazando a los de otro departamento
    response = client.patch(f"/api/tickets/{ticket['id']}", headers=coordinator_a["headers"],
                            json={"assigned_to": technician_b["id"]})
    assert response.status_code == 400
    assert client.patch(f"/api/tickets/{ticket['id']}", headers=coordinator_a["headers"],
                        json={"assigned_to": second["id"]}).status_code == 200


def test_switch_off_keeps_the_full_list(client, make_user, make_ticket):
    dept, coordinator, _ = team(make_user)
    ticket = make_ticket(make_user("requester"))
    set_department(ticket["id"], dept)
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail.get("assignable_ids") is None
