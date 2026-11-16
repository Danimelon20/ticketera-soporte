"""Visibilidad con el interruptor de tickets por departamento: el coordinador solo ve su departamento, el técnico
solo lo asignado y el administrador ve y gestiona todo. Apagado: como antes."""
from datetime import datetime, timedelta, timezone

from app import models
from app.database import SessionLocal
from org_helpers import set_department, team, unique

SOLUTION = "Se cambió el cable de red y se probó la conexión."


def _ticket(make_user, make_ticket, dept=None, title=None):
    ticket = make_ticket(make_user("requester"), **({"title": title} if title else {}))
    if dept is not None:
        set_department(ticket["id"], dept)
    return ticket


def _queue_ids(client, user, **params):
    response = client.get("/api/queue", headers=user["headers"], params={"view": "open", **params})
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def test_switch_off_coordinator_sees_everything(client, make_user, make_ticket):
    dept_a, coordinator_a, _ = team(make_user)
    dept_b, _, _ = team(make_user)
    other = _ticket(make_user, make_ticket, dept_b)
    assert client.get(f"/api/tickets/{other['id']}", headers=coordinator_a["headers"]).status_code == 200


def test_coordinator_only_sees_own_department(client, make_user, make_ticket, routing_on):
    dept_a, coordinator_a, _ = team(make_user)
    dept_b, _, _ = team(make_user)
    own = _ticket(make_user, make_ticket, dept_a)
    other = _ticket(make_user, make_ticket, dept_b)
    loose = _ticket(make_user, make_ticket)
    headers = coordinator_a["headers"]
    assert client.get(f"/api/tickets/{own['id']}", headers=headers).status_code == 200
    for ticket in (other, loose):
        assert client.get(f"/api/tickets/{ticket['id']}", headers=headers).status_code == 403
        assert client.patch(f"/api/tickets/{ticket['id']}", headers=headers,
                            json={"status": "in_progress"}).status_code == 403
    ids = _queue_ids(client, coordinator_a)
    assert own["id"] in ids and other["id"] not in ids and loose["id"] not in ids
    listed = {t["id"] for t in client.get("/api/tickets", headers=headers).json()}
    assert own["id"] in listed and other["id"] not in listed


def test_coordinator_without_department_sees_nothing(client, make_user, make_ticket, routing_on):
    dept, _, _ = team(make_user)
    ticket = _ticket(make_user, make_ticket, dept)
    lonely = make_user("coordinator")
    assert client.get(f"/api/tickets/{ticket['id']}", headers=lonely["headers"]).status_code == 403
    assert _queue_ids(client, lonely) == set()


def test_technician_only_sees_assigned(client, make_user, make_ticket, routing_on):
    dept, coordinator, technician = team(make_user)
    mine = _ticket(make_user, make_ticket, dept)
    other = _ticket(make_user, make_ticket, dept)
    client.patch(f"/api/tickets/{mine['id']}", headers=coordinator["headers"], json={"assigned_to": technician["id"]})
    assert client.get(f"/api/tickets/{mine['id']}", headers=technician["headers"]).status_code == 200
    assert client.get(f"/api/tickets/{other['id']}", headers=technician["headers"]).status_code == 403


def test_admin_sees_and_manages_everything(client, make_user, make_ticket, routing_on):
    admin = make_user("admin")
    dept_a, _, technician_a = team(make_user)
    dept_b, _, _ = team(make_user)
    # Título único: la base es compartida y la cola va de 25 en 25
    tag = unique("Admin gestiona")
    in_a = _ticket(make_user, make_ticket, dept_a, tag)
    in_b = _ticket(make_user, make_ticket, dept_b, tag)
    loose = _ticket(make_user, make_ticket, title=tag)
    headers = admin["headers"]
    detail = client.get(f"/api/tickets/{in_b['id']}", headers=headers)
    assert detail.status_code == 200 and detail.json().get("read_only", False) is False
    ids = _queue_ids(client, admin, search=tag)
    assert {in_a["id"], in_b["id"], loose["id"]} <= ids
    # Filtro por departamento (0: sin departamento)
    only_a = _queue_ids(client, admin, department_id=dept_a, search=tag)
    assert in_a["id"] in only_a and in_b["id"] not in only_a
    assert loose["id"] in _queue_ids(client, admin, department_id=0, search=tag)
    # Reasigna, cambia el estado y cierra
    for body in ({"assigned_to": technician_a["id"]}, {"status": "in_progress"},
                 {"status": "resolved", "solution": SOLUTION}, {"status": "closed"}):
        response = client.patch(f"/api/tickets/{in_a['id']}", headers=headers, json=body)
        assert response.status_code == 200, (body, response.text)
    assert client.post(f"/api/tickets/{in_b['id']}/comments", headers=headers,
                       json={"content": "Revisado por el administrador"}).status_code == 200


def test_admin_switch_off_keeps_old_limits(client, make_user, make_ticket):
    admin = make_user("admin")
    dept, _, _ = team(make_user)
    ticket = _ticket(make_user, make_ticket, dept)
    assert client.get("/api/queue", headers=admin["headers"]).status_code == 403
    assert client.get(f"/api/tickets/{ticket['id']}", headers=admin["headers"]).status_code == 403


def test_coordinator_reassigns_only_within_department(client, make_user, make_ticket, routing_on):
    dept_a, coordinator_a, technician_a = team(make_user)
    dept_b, _, technician_b = team(make_user)
    ticket = _ticket(make_user, make_ticket, dept_a)
    outside = client.patch(f"/api/tickets/{ticket['id']}", headers=coordinator_a["headers"],
                           json={"assigned_to": technician_b["id"]})
    assert outside.status_code == 400 and "no pertenece al departamento" in outside.json()["detail"]
    inside = client.patch(f"/api/tickets/{ticket['id']}", headers=coordinator_a["headers"],
                          json={"assigned_to": technician_a["id"]})
    assert inside.status_code == 200


def test_counts_and_panel_follow_the_department(client, make_user, make_ticket, routing_on):
    admin = make_user("admin")
    dept_a, coordinator_a, _ = team(make_user)
    dept_b, _, _ = team(make_user)
    _ticket(make_user, make_ticket, dept_a)
    late = _ticket(make_user, make_ticket, dept_b)
    db = SessionLocal()
    try:
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        db.add(models.TicketAIAnalysis(ticket_id=late["id"], status="done", ai_level=2, due_at=past))
        db.get(models.Ticket, late["id"]).created_at = past - timedelta(days=1)
        db.commit()
    finally:
        db.close()
    counts = client.get("/api/nav/counts", headers=coordinator_a["headers"]).json()
    assert counts["open"] == 1 and counts["overdue"] == 0
    rows = {r["id"]: r for r in client.get("/api/admin/overview", headers=admin["headers"]).json()["departments"]}
    assert rows[dept_a]["open"] == 1 and rows[dept_a]["overdue"] == 0
    assert rows[dept_b]["open"] == 1 and rows[dept_b]["overdue"] == 1
    admin_counts = client.get("/api/nav/counts", headers=admin["headers"]).json()
    assert "open" in admin_counts and "overdue" in admin_counts
