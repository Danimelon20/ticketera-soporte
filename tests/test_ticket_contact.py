def _ticket_payload(**extra):
    payload = {
        "title": "Ticket de contacto",
        "description": "Validación de contacto",
        "category_id": 1,
        "priority_id": 1,
    }
    payload.update(extra)
    return payload



def test_coordinator_creates_ticket_with_requester_id(client, make_user):
    coordinator = make_user("coordinator", "Coordinator User")
    requester = make_user("requester", "Requester User")
    
    payload = {
        "title": "Ticket por coordinador",
        "description": "Creado por coordinador",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "8888-1234",
        "requester_id": requester["id"],
    }
    response = client.post("/api/tickets", json=payload, headers=coordinator["headers"])
    assert response.status_code == 200
    ticket = response.json()
    assert ticket["created_by"] == requester["id"]


def test_ticket_detail_shows_masked_id_for_assigned_technician(client, make_user, make_ticket):
    technician = make_user("technician", "Tech User")
    requester = make_user("requester", "Tech Requester")
    ticket = make_ticket(requester, title="Ticket para tecnico")
    
    # Assign the ticket to the technician
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        db_ticket = db.query(models.Ticket).filter(models.Ticket.id == ticket["id"]).first()
        db_ticket.assigned_to = technician["id"]
        db.commit()
    finally:
        db.close()
    
    response = client.get(f"/api/tickets/{ticket['id']}", headers=technician["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["contact_phone"] == "88881234"
    assert data["contact_id_number"] == "******" + requester["id_number"][-3:]


def test_ticket_detail_shows_none_for_unassigned_technician(client, make_user, make_ticket):
    # Sin asignar: ya no lo abre. Lo tuvo antes: lo ve en solo lectura, sin teléfono ni cédula
    technician = make_user("technician", "Tech User2")
    requester = make_user("requester", "Tech2 Requester")
    ticket = make_ticket(requester, title="Ticket sin asignar")
    assert client.get(f"/api/tickets/{ticket['id']}", headers=technician["headers"]).status_code == 403
    coordinator = make_user("coordinator")
    for user in (technician, make_user("technician")):
        client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": user["id"]}, headers=coordinator["headers"])

    response = client.get(f"/api/tickets/{ticket['id']}", headers=technician["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["contact_phone"] is None
    assert data["contact_id_number"] is None


def test_ticket_detail_shows_full_for_coordinator(client, make_user, make_ticket):
    coordinator = make_user("coordinator", "Coordinator User")
    requester = make_user("requester", "Coord Requester")
    ticket = make_ticket(requester, title="Ticket para coord")
    
    response = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["contact_phone"] == "88881234"
    assert data["contact_id_number"] == requester["id_number"]


def test_get_requesters_coordinator(client, make_user):
    coordinator = make_user("coordinator", "Coord User")
    requester1 = make_user("requester", "Daniel")
    requester2 = make_user("requester", "Another User")
    
    response = client.get("/api/requesters?q=dan", headers=coordinator["headers"])
    assert response.status_code == 200
    requesters = response.json()
    names = [r["name"] for r in requesters]
    assert "Daniel" in names


def test_get_requesters_technician_forbidden(client, make_user):
    technician = make_user("technician", "Tech User")
    
    response = client.get("/api/requesters?q=dan", headers=technician["headers"])
    assert response.status_code == 403
    assert response.json()["detail"] == "No tienes permiso para ver esta información"
