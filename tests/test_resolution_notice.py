"""Al resolverse (interruptor encendido): no sale ningún correo y quien creó el ticket recibe un aviso en su
campana con enlace a la solución, para avisarle a quien reportó. Apagado: los correos de siempre."""

from app import models
from app.database import SessionLocal
from org_helpers import department, set_department, team, unique

SOLUTION = "Se reemplazó el tóner y se imprimió una página de prueba."


def _staff_ticket(client, creator, dept):
    area = department(unique("Área"), handles=False)
    response = client.post("/api/tickets", headers=creator["headers"], json={
        "title": "La impresora no imprime", "description": "Sale en blanco", "department_id": dept,
        "reporter_name": "Luis Mora", "reporter_area_id": area, "channel": "in_person", "priority_id": 2})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _work(client, coordinator, technician, ticket_id):
    for body in ({"assigned_to": technician["id"]}, {"status": "in_progress"}):
        assert client.patch(f"/api/tickets/{ticket_id}", headers=coordinator["headers"], json=body).status_code == 200


def _bell(client, user):
    return client.get("/api/notifications/overdue", headers=user["headers"]).json()["items"]


def test_resolution_sends_no_email_and_notifies_the_creator(client, make_user, sent, routing_on):
    dept, coordinator, technician = team(make_user)
    creator = make_user("admin")
    ticket_id = _staff_ticket(client, creator, dept)
    _work(client, coordinator, technician, ticket_id)
    sent.clear()
    response = client.patch(f"/api/tickets/{ticket_id}", headers=technician["headers"],
                            json={"status": "resolved", "solution": SOLUTION})
    assert response.status_code == 200, response.text
    assert list(sent) == []
    [notice] = [i for i in _bell(client, creator) if i["kind"] == "ticket_resolved"]
    assert notice["ticket_id"] == ticket_id and "Luis Mora" in notice["text"] and not notice["read"]
    # Marcarlo leído desde la campana
    client.post("/api/notifications/overdue/read", headers=creator["headers"], json={"ticket_id": ticket_id})
    assert all(i["read"] for i in _bell(client, creator) if i["kind"] == "ticket_resolved")


def test_no_notice_when_the_creator_resolves(client, make_user, sent, routing_on):
    dept, coordinator, technician = team(make_user)
    ticket_id = _staff_ticket(client, coordinator, dept)
    _work(client, coordinator, coordinator, ticket_id)
    client.patch(f"/api/tickets/{ticket_id}", headers=coordinator["headers"],
                 json={"status": "resolved", "solution": SOLUTION})
    assert not [i for i in _bell(client, coordinator) if i["kind"] == "ticket_resolved"]


def test_switch_off_keeps_the_resolution_emails(client, make_user, make_ticket, sent):
    dept, coordinator, technician = team(make_user)
    requester = make_user("requester")
    ticket = make_ticket(requester)
    set_department(ticket["id"], dept)
    # El contacto sigue su ticket (como al llegar por correo)
    with SessionLocal() as db:
        db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=requester["id"]))
        db.commit()
    _work(client, coordinator, technician, ticket["id"])
    sent.clear()
    client.patch(f"/api/tickets/{ticket['id']}", headers=technician["headers"], json={"status": "resolved"})
    assert any(to == requester["email"] for to, _, _ in sent)
