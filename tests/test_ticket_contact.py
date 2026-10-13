def test_create_ticket_missing_contact_phone(client, make_user):
    user = make_user("requester")
    payload = {
        "title": "Ticket sin telefono",
        "description": "Sin telefono",
        "category_id": 1,
        "priority_id": 1,
    }
    response = client.post("/api/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 422


def test_create_ticket_missing_contact_id_number(client, make_user):
    user = make_user("requester")
    payload = {
        "title": "Ticket sin cedula",
        "description": "Sin cedula",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "8888-1234",
    }
    response = client.post("/api/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 422


def test_phone_short_invalid(client, make_user):
    user = make_user("requester")
    payload = {
        "title": "Ticket corto",
        "description": "Corto",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "1234",
    }
    response = client.post("/api/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 422


def test_phone_formatted_saved_clean(client, make_user):
    user = make_user("requester")
    payload = {
        "title": "Ticket con telefono formateado",
        "description": "Formateado",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "+506 8888 1234",
        "contact_id_number": "1-0234-0567",
    }
    response = client.post("/api/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 200
    response = client.get(f"/api/tickets/{response.json()['id']}", headers=user["headers"])
    ticket = response.json()
    assert ticket["contact_phone"] == "88881234"


def test_id_number_formatted_saved_clean(client, make_user):
    user = make_user("requester")
    payload = {
        "title": "Ticket con cedula formateada",
        "description": "Formateada",
        "category_id": 1,
        "priority_id": 1,
        "contact_id_number": "1-0234-0567",
        "contact_phone": "8888-1234",
    }
    response = client.post("/api/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 200
    response = client.get(f"/api/tickets/{response.json()['id']}", headers=user["headers"])
    ticket = response.json()
    assert ticket["contact_id_number"] == "102340567"


def test_id_number_short_invalid(client, make_user):
    user = make_user("requester")
    payload = {
        "title": "Ticket cedula corta",
        "description": "Corta",
        "category_id": 1,
        "priority_id": 1,
        "contact_id_number": "12345",
    }
    response = client.post("/api/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 422


def test_auth_me_returns_phone_and_id_number(client, make_user):
    user = make_user("requester", "Me User")
    response = client.get("/api/auth/me", headers=user["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "phone" in data
    assert "id_number" in data


def test_coordinator_creates_ticket_with_requester_id(client, make_user):
    coordinator = make_user("coordinator", "Coordinator User")
    requester = make_user("requester", "Requester User")
    
    payload = {
        "title": "Ticket por coordinador",
        "description": "Creado por coordinador",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "8888-1234",
        "contact_id_number": "1-0234-0567",
        "requester_id": requester["id"],
    }
    response = client.post("/api/tickets", json=payload, headers=coordinator["headers"])
    assert response.status_code == 200
    ticket = response.json()
    assert ticket["created_by"] == requester["id"]


def test_requester_cannot_create_ticket_with_requester_id(client, make_user):
    requester = make_user("requester", "Requester User")
    
    payload = {
        "title": "Ticket por solicitante",
        "description": "No debería permitir",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "8888-1234",
        "contact_id_number": "1-0234-0567",
        "requester_id": requester["id"],
    }
    response = client.post("/api/tickets", json=payload, headers=requester["headers"])
    assert response.status_code == 403


def test_ticket_detail_shows_contact_for_creator(client, make_user, make_ticket):
    requester = make_user("requester", "Creator User")
    ticket = make_ticket(requester, title="Ticket del creador")
    
    response = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["contact_phone"] == "88881234"
    assert data["contact_id_number"] == "102340567"


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
    assert data["contact_id_number"] == "******567"


def test_ticket_detail_shows_none_for_unassigned_technician(client, make_user, make_ticket):
    technician = make_user("technician", "Tech User2")
    requester = make_user("requester", "Tech2 Requester")
    ticket = make_ticket(requester, title="Ticket sin asignar")
    
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
    assert data["contact_id_number"] == "102340567"


def test_list_tickets_no_contact_fields(client, make_user, make_ticket):
    user = make_user("requester")
    ticket = make_ticket(user, title="Lista sin contactos")
    
    response = client.get("/api/tickets", headers=user["headers"])
    assert response.status_code == 200
    tickets = response.json()
    assert len(tickets) >= 1
    ticket_data = tickets[0]
    assert "contact_phone" not in ticket_data
    assert "contact_id_number" not in ticket_data


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

def _contact_payload(phone="8888-1234", id_number="1-0234-0567"):
    return {
        "title": "Ticket de contacto",
        "description": "Validación de contacto",
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": phone,
        "contact_id_number": id_number,
    }


def test_phone_nine_digits_invalid(client, make_user):
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(phone="888812345"), headers=user["headers"])
    assert response.status_code == 422


def test_phone_with_prefix_and_extra_digit_invalid(client, make_user):
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(phone="+506 8888 12345"), headers=user["headers"])
    assert response.status_code == 422


def test_phone_error_message_is_about_phone(client, make_user):
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(phone="888812345"), headers=user["headers"])
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert "El teléfono debe tener 8 dígitos" in detail[0]["msg"]
    assert "contraseña" not in response.text


def test_id_number_ten_digits_invalid(client, make_user):
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(id_number="1023405670"), headers=user["headers"])
    assert response.status_code == 422


def test_id_number_thirteen_digits_invalid(client, make_user):
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(id_number="1234567890123"), headers=user["headers"])
    assert response.status_code == 422


def test_id_number_dimex_twelve_digits_valid(client, make_user, monkeypatch):
    from app import hacienda
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: {"tipo": "03"})
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(id_number="123456789012"), headers=user["headers"])
    assert response.status_code == 200, response.text


def test_id_number_legal_entity_rejected(client, make_user, monkeypatch):
    from app import hacienda
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: {"tipo": "02"})
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(id_number="310123456"), headers=user["headers"])
    assert response.status_code == 400
    assert response.json()["detail"] == "La cédula no corresponde a una persona física o DIMEX"


def test_id_number_not_found_accepted_when_not_strict(client, make_user, monkeypatch):
    from app import hacienda
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: None)
    monkeypatch.setenv("HACIENDA_STRICT", "false")
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(), headers=user["headers"])
    assert response.status_code == 200, response.text


def test_id_number_not_found_rejected_when_strict(client, make_user, monkeypatch):
    from app import hacienda
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: None)
    monkeypatch.setenv("HACIENDA_STRICT", "true")
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(), headers=user["headers"])
    assert response.status_code == 400
    assert response.json()["detail"] == "La cédula no está registrada en Hacienda"


def test_id_number_accepted_when_hacienda_unavailable(client, make_user, monkeypatch):
    from app import hacienda

    def _unavailable(id_number):
        raise hacienda.HaciendaUnavailable("caído")

    monkeypatch.setattr(hacienda, "lookup_id", _unavailable)
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(), headers=user["headers"])
    assert response.status_code == 200, response.text


def test_non_ascii_digits_not_counted(client, make_user):
    user = make_user("requester")
    response = client.post("/api/tickets", json=_contact_payload(phone="8888123²"), headers=user["headers"])
    assert response.status_code == 422
    response = client.post("/api/tickets", json=_contact_payload(id_number="10234056٣"), headers=user["headers"])
    assert response.status_code == 422
