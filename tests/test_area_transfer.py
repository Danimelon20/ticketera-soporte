"""Ticket nuevo con área, transferir a otra área y mover entre departamentos de la misma área."""
from app import area_ai, config, models, routing
from app.database import SessionLocal
from org_helpers import area, department, history, place, route, routing_of, setting

PAYLOAD = {"title": "Sin internet", "description": "No hay conexión desde la mañana", "category_id": 1,
           "priority_id": 1}


def _create(client, user, **extra):
    return client.post("/api/tickets", headers=user["headers"], json={**PAYLOAD, **extra})


def test_requester_picks_external_area(client, make_user, routing_on):
    dept = department()
    area_id = area(department_ids=[dept], entry_department_id=dept)
    internal = area(audience="internal")
    requester = make_user("requester")

    options = client.get("/api/areas", headers=requester["headers"]).json()
    ids = {a["id"] for a in options["areas"]}
    assert area_id in ids and internal not in ids
    assert options["unsure_allowed"] is True and options["required"] is True

    assert _create(client, requester).status_code == 400  # Con el enrutamiento, elegir es obligatorio
    assert _create(client, requester, area_id=internal).status_code == 400
    response = _create(client, requester, area_id=area_id)
    assert response.status_code == 200, response.text
    assert response.json()["area_id"] == area_id
    # El departamento es interno: el solicitante no lo recibe
    assert response.json()["department_id"] is None
    assert routing_of(response.json()["id"]) == (area_id, dept)


def test_unsure_goes_to_entry_department(client, make_user, routing_on):
    entry = department()
    area_id = area(department_ids=[entry])
    setting(routing.ENTRY_DEPARTMENT, str(entry))
    try:
        response = _create(client, make_user("requester"), unsure=True)
        assert response.status_code == 200, response.text
        assert routing_of(response.json()["id"]) == (area_id, entry)
    finally:
        setting(routing.ENTRY_DEPARTMENT, None)


def test_ai_suggests_area_and_requester_confirms(client, make_user, monkeypatch):
    area_id = area(description="Internet caído, lentitud, VPN")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    sent = []
    db = SessionLocal()
    try:
        name = db.get(models.ServiceArea, area_id).name
    finally:
        db.close()

    def _fake_post_chat(payload, task=None, timeout=None):
        sent.append(payload)
        return f'{{"area": "{name}", "confidence": "alta"}}'

    monkeypatch.setattr(area_ai.chatbot, "post_chat", _fake_post_chat)
    requester = make_user("requester")
    text = "Mi correo es ana@cliente.example y mi cédula 102340567, teléfono 88881234: la VPN no conecta"
    response = client.post("/api/areas/suggest", headers=requester["headers"],
                           json={"title": "VPN", "description": text})
    assert response.json() == {"area_id": area_id}
    # Nunca correos, cédulas ni teléfonos a la IA
    user_text = sent[0]["messages"][1]["content"]
    for secret in ("ana@cliente.example", "102340567", "88881234"):
        assert secret not in user_text

    monkeypatch.setattr(area_ai.chatbot, "post_chat", lambda payload, task=None, timeout=None:
                        '{"area": "x", "confidence": "baja"}')
    assert client.post("/api/areas/suggest", headers=requester["headers"],
                       json={"title": "VPN", "description": "algo"}).json() == {"area_id": None}


def _setup_transfer(make_user):
    coordinator = make_user("coordinator", "Coordinadora Ana")
    dept_n1, dept_noc, dept_other = department(name="Soporte N1"), department(name="NOC"), department()
    area_a = area(name=None, coordinator_id=coordinator["id"], department_ids=[dept_n1, dept_noc],
                  entry_department_id=dept_n1)
    area_b = area(department_ids=[dept_other], entry_department_id=dept_other)
    db = SessionLocal()
    try:
        ticket = models.Ticket(title="Caída", description="Sin servicio", category_id=1, priority_id=1,
                               created_by=make_user("requester")["id"], source="web")
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
        names = {a.id: a.name for a in db.query(models.ServiceArea).filter(models.ServiceArea.id.in_([area_a, area_b]))}
    finally:
        db.close()
    route(ticket_id, area_a, dept_n1)
    return coordinator, ticket_id, area_a, area_b, dept_n1, dept_noc, dept_other, names


def test_transfer_to_other_area_with_history(client, make_user, routing_on):
    coordinator, ticket, area_a, area_b, _, _, dept_other, names = _setup_transfer(make_user)
    technician = make_user("technician")
    assert client.post(f"/api/tickets/{ticket}/transfer-area", headers=technician["headers"],
                       json={"area_id": area_b, "reason": "No es de mi área"}).status_code == 403
    response = client.post(f"/api/tickets/{ticket}/transfer-area", headers=coordinator["headers"],
                           json={"area_id": area_b, "reason": "Es un tema de facturación"})
    assert response.status_code == 200, response.text
    assert routing_of(ticket) == (area_b, dept_other)
    assert history(ticket, "area") == [
        f"Transferido de {names[area_a]} a {names[area_b]} por Coordinadora Ana: Es un tema de facturación"
    ]
    # Ya no es de su área: ni la ve ni la puede volver a transferir
    assert client.get(f"/api/tickets/{ticket}", headers=coordinator["headers"]).status_code == 403
    assert client.post(f"/api/tickets/{ticket}/transfer-area", headers=coordinator["headers"],
                       json={"area_id": area_a, "reason": "Regresa"}).status_code == 403


def test_requester_does_not_see_route_history(client, make_user, routing_on):
    coordinator, ticket, _, area_b, _, _, _, _ = _setup_transfer(make_user)
    client.post(f"/api/tickets/{ticket}/transfer-area", headers=coordinator["headers"],
                json={"area_id": area_b, "reason": "Motivo interno"})
    db = SessionLocal()
    try:
        owner_id = db.get(models.Ticket, ticket).created_by
    finally:
        db.close()
    from app.security import hash_token, new_token
    token = new_token()
    db = SessionLocal()
    try:
        db.add(models.Session(user_id=owner_id, token=hash_token(token)))
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/api/tickets/{ticket}", headers={"Authorization": f"Bearer {token}"}).json()
    assert all(h["field"] != "area" for h in detail["history"])
    assert "Motivo interno" not in str(detail)


def test_move_department_inside_area_and_reassign(client, make_user, routing_on):
    coordinator, ticket, _, _, dept_n1, dept_noc, dept_other, _ = _setup_transfer(make_user)
    n1 = make_user("technician", "Técnico N1")
    place(n1["id"], dept_n1)
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket).assigned_to = n1["id"]
        db.commit()
    finally:
        db.close()
    assert client.post(f"/api/tickets/{ticket}/move-department", headers=coordinator["headers"],
                       json={"department_id": dept_other, "reason": "Otra área"}).status_code == 400
    response = client.post(f"/api/tickets/{ticket}/move-department", headers=coordinator["headers"],
                           json={"department_id": dept_noc, "reason": "Falla de red troncal"})
    assert response.status_code == 200, response.text
    assert routing_of(ticket)[1] == dept_noc
    assert history(ticket, "department") == [
        "Movido de Soporte N1 a NOC por Coordinadora Ana: Falla de red troncal"
    ]
    # El técnico de N1 no está en NOC: vuelve a la cola y la asignación busca en NOC
    assert response.json()["assigned_to"] is None
    db = SessionLocal()
    try:
        row = db.query(models.TicketAutoAssignment).filter(models.TicketAutoAssignment.ticket_id == ticket).first()
        assert row is not None and row.status in ("waiting", "assigned", "cancelled")
    finally:
        db.close()


def test_technician_opens_internal_ticket_as_requester(client, make_user):
    internal = area(audience="internal")
    external = area()
    technician = make_user("technician")
    assert _create(client, technician, area_id=external).status_code == 403
    response = _create(client, technician, area_id=internal)
    assert response.status_code == 200, response.text
    ticket = response.json()["id"]
    # Lo ve como solicitante: puede comentar, no cambiar el estado
    assert client.get(f"/api/tickets/{ticket}", headers=technician["headers"]).status_code == 200
    assert client.post(f"/api/tickets/{ticket}/comments", headers=technician["headers"],
                       json={"content": "Sigue igual"}).status_code == 200
    assert client.post(f"/api/tickets/{ticket}/comments", headers=technician["headers"],
                       json={"content": "Nota", "internal": True}).status_code == 403
    assert client.patch(f"/api/tickets/{ticket}", headers=technician["headers"],
                        json={"status": "in_progress"}).status_code == 403
