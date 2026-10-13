def patch(client, ticket_id, actor, **changes):
    return client.patch(
        f"/api/tickets/{ticket_id}", json=changes, headers=actor["headers"]
    )


def history(client, ticket_id, actor):
    response = client.get(f"/api/tickets/{ticket_id}", headers=actor["headers"])
    return response.json()["history"]


# ---------- Pruebas de siempre (hechas por un coordinador) ----------

def test_valid_transition_and_assign(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    response = patch(client, ticket["id"], coord,
                     status="in_progress", assigned_to=coord["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["assigned_to"] == coord["id"]
    fields = [h["field"] for h in history(client, ticket["id"], coord)]
    assert sorted(fields) == ["assigned_to", "status"]


def test_new_to_closed_rejected(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    assert patch(client, ticket["id"], coord, status="closed").status_code == 400
    assert history(client, ticket["id"], coord) == []


def test_cannot_go_back_to_new(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    patch(client, ticket["id"], coord, assigned_to=coord["id"], status="in_progress")
    assert patch(client, ticket["id"], coord, status="new").status_code == 400


def test_full_lifecycle_with_reopen(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    patch(client, ticket["id"], coord, assigned_to=coord["id"])
    steps = ["in_progress", "resolved", "in_progress", "resolved", "closed"]
    for status in steps:
        assert patch(client, ticket["id"], coord, status=status).status_code == 200
    statuses = [h["new_value"] for h in history(client, ticket["id"], coord) if h["field"] == "status"]
    assert statuses == steps


def test_closed_ticket_cannot_change(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    patch(client, ticket["id"], coord, assigned_to=coord["id"])
    for status in ["in_progress", "resolved", "closed"]:
        patch(client, ticket["id"], coord, status=status)
    assert patch(client, ticket["id"], coord, priority_id=4).status_code == 400


def test_invalid_actor(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"status": "in_progress"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Inicia sesión para continuar"
    assert history(client, ticket["id"], coord) == []


def test_invalid_assigned_user(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    assert patch(client, ticket["id"], coord, assigned_to=999999).status_code == 400


def test_same_value_no_history(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req, priority_id=1)
    assert patch(client, ticket["id"], coord, priority_id=1).status_code == 200
    assert history(client, ticket["id"], coord) == []


def test_ticket_not_found(client, make_user):
    coord = make_user("coordinator")
    assert patch(client, 999999, coord, status="in_progress").status_code == 404


# ---------- Pruebas nuevas de roles ----------

def test_requester_cannot_update(client, make_user, make_ticket):
    # Reglas 13 y 20: el solicitante no cambia nada, ni en su propio ticket
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert patch(client, ticket["id"], solicitante, status="in_progress").status_code == 403
    assert patch(client, ticket["id"], solicitante, priority_id=4).status_code == 403


def test_technician_can_change_status_and_priority(client, make_user, make_ticket):
    # Reglas 14 y 20
    tecnico = make_user("technician")
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert patch(client, ticket["id"], tecnico, assigned_to=tecnico["id"]).status_code == 200
    assert patch(client, ticket["id"], tecnico, status="in_progress").status_code == 200
    assert patch(client, ticket["id"], tecnico, priority_id=4).status_code == 200


def test_assign_to_requester_rejected(client, make_user, make_ticket):
    # Regla 9: un solicitante no puede ser asignado
    coord = make_user("coordinator")
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert patch(client, ticket["id"], coord, assigned_to=solicitante["id"]).status_code == 400


def test_coordinator_assigns_technician(client, make_user, make_ticket):
    # Regla 10: el coordinador asigna a otra persona
    coord = make_user("coordinator")
    tecnico = make_user("technician")
    req = make_user("requester")
    ticket = make_ticket(req)
    response = patch(client, ticket["id"], coord, assigned_to=tecnico["id"])
    assert response.status_code == 200
    assert response.json()["assigned_to"] == tecnico["id"]


def test_technician_assigns_self(client, make_user, make_ticket):
    # Regla 11: el técnico se asigna un ticket libre
    tecnico = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    response = patch(client, ticket["id"], tecnico, assigned_to=tecnico["id"])
    assert response.status_code == 200


def test_technician_cannot_assign_other(client, make_user, make_ticket):
    # Regla 10: el técnico no asigna a otra persona
    tecnico = make_user("technician")
    otro_tecnico = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    assert patch(client, ticket["id"], tecnico, assigned_to=otro_tecnico["id"]).status_code == 403


def test_technician_cannot_take_assigned_ticket(client, make_user, make_ticket):
    # Regla 11: solo tickets SIN asignar
    coord = make_user("coordinator")
    req = make_user("requester")
    tecnico_a = make_user("technician")
    tecnico_b = make_user("technician")
    ticket = make_ticket(req)
    patch(client, ticket["id"], coord, assigned_to=tecnico_a["id"])
    assert patch(client, ticket["id"], tecnico_b, assigned_to=tecnico_b["id"]).status_code == 403


# Pruebas adicionales requeridas por las nuevas reglas:
# - un técnico que intenta crear un ticket recibe 403 "Los técnicos no pueden crear tickets";
def test_technician_cannot_create_ticket(client, make_user):
    tecnico = make_user("technician")
    response = client.post(
        "/api/tickets",
        json={"title": "Ticket de técnico", "description": "Prueba", "category_id": 1, "priority_id": 1, "contact_phone": "8888-1234", "contact_id_number": "1-0234-0567"},
        headers=tecnico["headers"]
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Los técnicos no pueden crear tickets"


# - pasar a "in_progress" un ticket sin asignar da 400 "Ticket sin asignar. Asigne para iniciar";
def test_in_progress_unassigned_rejected(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    response = patch(client, ticket["id"], coord, status="in_progress")
    assert response.status_code == 400
    assert response.json()["detail"] == "Ticket sin asignar. Asigne para iniciar"


# - un PATCH con assigned_to y status "in_progress" juntos funciona (200);
def test_patch_assigned_and_in_progress_together(client, make_user, make_ticket):
    coord = make_user("coordinator")
    tecnico = make_user("technician")
    req = make_user("requester")
    ticket = make_ticket(req)
    response = patch(client, ticket["id"], coord, status="in_progress", assigned_to=tecnico["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["assigned_to"] == tecnico["id"]


# - un coordinador reasigna un ticket que ya está "in_progress" a otro técnico (200) y queda en el historial.
def test_coordinator_reassigns_in_progress_ticket(client, make_user, make_ticket):
    coord = make_user("coordinator")
    tecnico1 = make_user("technician")
    tecnico2 = make_user("technician")
    req = make_user("requester")
    ticket = make_ticket(req)
    # Asignar e iniciar con técnico 1
    patch(client, ticket["id"], coord, status="in_progress", assigned_to=tecnico1["id"])
    
    # Reasignar a técnico 2 estando en in_progress
    response = patch(client, ticket["id"], coord, assigned_to=tecnico2["id"])
    assert response.status_code == 200
    assert response.json()["assigned_to"] == tecnico2["id"]
    assert response.json()["status"] == "in_progress"
    
    hist = history(client, ticket["id"], coord)
    assigned_histories = [h for h in hist if h["field"] == "assigned_to"]
    assert len(assigned_histories) == 2
    assert assigned_histories[-1]["old_value"] == str(tecnico1["id"])
    assert assigned_histories[-1]["new_value"] == str(tecnico2["id"])

