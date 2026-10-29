"""Coordinador: con el enrutamiento encendido solo ve y gestiona los tickets y técnicos de su área."""
from app import models
from app.database import SessionLocal
from org_helpers import area, department, place, route


def _ticket(requester_id, title="Ticket de área"):
    db = SessionLocal()
    try:
        ticket = models.Ticket(title=title, description="Descripción", category_id=1, priority_id=1,
                               created_by=requester_id, source="web")
        db.add(ticket)
        db.commit()
        return ticket.id
    finally:
        db.close()


def _two_areas(make_user):
    coordinator = make_user("coordinator", "Coordinador A")
    other_coordinator = make_user("coordinator", "Coordinador B")
    dept_a, dept_b = department(), department()
    area_a = area(coordinator_id=coordinator["id"], department_ids=[dept_a], entry_department_id=dept_a)
    area_b = area(coordinator_id=other_coordinator["id"], department_ids=[dept_b], entry_department_id=dept_b)
    requester = make_user("requester")
    mine, theirs = _ticket(requester["id"], "Mío"), _ticket(requester["id"], "Ajeno")
    route(mine, area_a, dept_a)
    route(theirs, area_b, dept_b)
    return coordinator, mine, theirs, dept_a, dept_b


def test_coordinator_only_sees_own_area(client, make_user, routing_on):
    coordinator, mine, theirs, _, _ = _two_areas(make_user)
    assert client.get(f"/api/tickets/{mine}", headers=coordinator["headers"]).status_code == 200
    assert client.get(f"/api/tickets/{theirs}", headers=coordinator["headers"]).status_code == 403
    assert client.patch(f"/api/tickets/{theirs}", headers=coordinator["headers"],
                        json={"status": "in_progress"}).status_code == 403
    listed = {t["id"] for t in client.get("/api/tickets", headers=coordinator["headers"]).json()}
    assert mine in listed and theirs not in listed
    queue = {t["id"] for t in client.get("/api/queue?view=all", headers=coordinator["headers"]).json()["items"]}
    assert mine in queue and theirs not in queue


def test_without_switch_coordinator_sees_everything(client, make_user):
    coordinator, mine, theirs, _, _ = _two_areas(make_user)
    assert client.get(f"/api/tickets/{theirs}", headers=coordinator["headers"]).status_code == 200


def test_coordinator_without_area_sees_nothing(client, make_user, routing_on):
    _, mine, _, _, _ = _two_areas(make_user)
    lonely = make_user("coordinator", "Sin área")
    assert client.get(f"/api/tickets/{mine}", headers=lonely["headers"]).status_code == 403
    assert client.get("/api/queue?view=all", headers=lonely["headers"]).json()["items"] == []
    me = client.get("/api/auth/me", headers=lonely["headers"]).json()
    assert me["placement_missing"] is True


def test_coordinator_assigns_only_technicians_of_area(client, make_user, routing_on):
    coordinator, mine, _, dept_a, dept_b = _two_areas(make_user)
    inside = make_user("technician", "Del área")
    outside = make_user("technician", "De otra área")
    place(inside["id"], dept_a)
    place(outside["id"], dept_b)
    response = client.patch(f"/api/tickets/{mine}", headers=coordinator["headers"],
                            json={"assigned_to": outside["id"], "confirm_unavailable": True})
    assert response.status_code == 400
    response = client.patch(f"/api/tickets/{mine}", headers=coordinator["headers"],
                            json={"assigned_to": inside["id"], "confirm_unavailable": True})
    assert response.status_code == 200, response.text


def test_coordinator_manages_only_technicians_of_area(client, make_user, routing_on):
    coordinator, _, _, dept_a, dept_b = _two_areas(make_user)
    inside = make_user("technician")
    outside = make_user("technician")
    place(inside["id"], dept_a)
    place(outside["id"], dept_b)
    schedule = {"days": [{"weekday": 0, "start": "08:00", "end": "17:00"}]}
    assert client.put(f"/api/accounts/{outside['id']}/schedule", headers=coordinator["headers"],
                      json=schedule).status_code == 403
    assert client.put(f"/api/accounts/{inside['id']}/schedule", headers=coordinator["headers"],
                      json=schedule).status_code == 204


def test_menu_counts_follow_the_area(client, make_user, routing_on):
    coordinator, _, _, _, _ = _two_areas(make_user)
    counts = client.get("/api/nav/counts", headers=coordinator["headers"]).json()
    assert counts["open"] + counts["overdue"] == 1


def test_coordinator_only_lists_technicians_of_area(client, make_user, routing_on):
    coordinator, _, _, dept_a, dept_b = _two_areas(make_user)
    inside = make_user("technician", "Técnico dentro")
    outside = make_user("technician", "Técnico fuera")
    place(inside["id"], dept_a)
    place(outside["id"], dept_b)
    team = {m["id"] for m in client.get("/api/team", headers=coordinator["headers"]).json()}
    assert inside["id"] in team and outside["id"] not in team
    users = {u["id"] for u in client.get("/api/users", headers=coordinator["headers"]).json()}
    assert inside["id"] in users and outside["id"] not in users
    assert client.get(f"/api/accounts/{outside['id']}", headers=coordinator["headers"]).status_code == 404
    assert client.get(f"/api/accounts/{inside['id']}", headers=coordinator["headers"]).status_code == 200
