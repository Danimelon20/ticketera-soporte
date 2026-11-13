"""Datos nuevos del ticket: departamento ("Sin departamento" para los anteriores), clasificación por el
administrador y la solución obligatoria al resolver (solo con el interruptor encendido)."""
from app import models, routing
from app.database import SessionLocal
from org_helpers import department, history, set_department, team, unique

SOLUTION = "Se reinició el servicio de correo y se probó con el usuario."


def _assigned_ticket(client, make_user, make_ticket, coordinator, technician, dept=None):
    ticket = make_ticket(make_user("requester"))
    if dept is not None:
        set_department(ticket["id"], dept)
    response = client.patch(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"],
                            json={"assigned_to": technician["id"], "status": "in_progress"})
    assert response.status_code == 200, response.text
    return ticket


def _resolve(client, actor, ticket_id, **extra):
    return client.patch(f"/api/tickets/{ticket_id}", headers=actor["headers"], json={"status": "resolved", **extra})


def test_old_tickets_have_no_department(client, make_user, make_ticket):
    requester = make_user("requester")
    ticket = make_ticket(requester)
    coordinator = make_user("coordinator")
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["department_id"] is None and detail["department_name"] is None
    # El solicitante no recibe los datos del departamento
    own = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"]).json()
    assert "department_name" not in own and "reporter" not in own


def test_switch_off_resolves_without_solution(client, make_user, make_ticket):
    dept, coordinator, technician = team(make_user)
    ticket = _assigned_ticket(client, make_user, make_ticket, coordinator, technician, dept)
    assert _resolve(client, technician, ticket["id"]).status_code == 200


def test_switch_on_requires_the_solution(client, make_user, make_ticket, routing_on):
    dept, coordinator, technician = team(make_user)
    ticket = _assigned_ticket(client, make_user, make_ticket, coordinator, technician, dept)
    for extra in ({}, {"solution": "corto"}, {"solution": "          "}):
        response = _resolve(client, technician, ticket["id"], **extra)
        assert response.status_code == 422, extra
        assert "Documente la solución" in response.json()["detail"]
    response = _resolve(client, technician, ticket["id"], solution=SOLUTION)
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["solution"]["text"] == SOLUTION
    assert detail["solution"]["resolved_by_name"] == technician["name"]
    assert detail["department_id"] == dept and detail["routing_enforced"] is True


def test_admin_classifies_unclassified_tickets(client, make_user, make_ticket):
    admin = make_user("admin")
    dept = department(unique("Clasificación"))
    ticket = make_ticket(make_user("requester"), title=unique("Sin departamento"))
    listed = client.get("/api/admin/tickets/unclassified", headers=admin["headers"])
    assert listed.status_code == 200
    assert ticket["id"] in [t["id"] for t in listed.json()]
    counts = client.get("/api/nav/counts", headers=admin["headers"]).json()
    assert counts["unclassified"] >= 1

    # Solo departamentos que atienden tickets
    off = department(handles=False)
    assert client.put(f"/api/admin/tickets/{ticket['id']}/department", headers=admin["headers"],
                      json={"department_id": off}).status_code == 422
    response = client.put(f"/api/admin/tickets/{ticket['id']}/department", headers=admin["headers"],
                          json={"department_id": dept})
    assert response.status_code == 204, response.text
    db = SessionLocal()
    try:
        assert routing.ticket_department(db, ticket["id"]).id == dept
    finally:
        db.close()
    [entry] = history(ticket["id"], "department")
    assert entry.new_value.startswith("Clasificado en ") and admin["name"] in entry.new_value
    assert ticket["id"] not in [t["id"] for t in client.get("/api/admin/tickets/unclassified",
                                                             headers=admin["headers"]).json()]
    # Ya tiene departamento: se cambia con una transferencia, no clasificando otra vez
    assert client.put(f"/api/admin/tickets/{ticket['id']}/department", headers=admin["headers"],
                      json={"department_id": dept}).status_code == 409


def test_classification_is_admin_only(client, make_user, make_ticket):
    ticket = make_ticket(make_user("requester"))
    for role in ("coordinator", "technician"):
        user = make_user(role)
        assert client.get("/api/admin/tickets/unclassified", headers=user["headers"]).status_code == 403
        assert client.put(f"/api/admin/tickets/{ticket['id']}/department", headers=user["headers"],
                          json={"department_id": department()}).status_code == 403


def test_unclassified_tickets_warn_but_do_not_block(client, make_user, make_ticket):
    admin = make_user("admin")
    make_ticket(make_user("requester"))
    [switch] = client.get("/api/admin/activation", headers=admin["headers"]).json()
    item = next(i for i in switch["checklist"] if i["key"] == "unclassified_tickets")
    assert item["warning"] is True and item["ok"] is False
    # La lista sigue decidiendo por los demás puntos
    others = [i["ok"] for i in switch["checklist"] if not i["warning"]]
    assert switch["ready"] == all(others)

