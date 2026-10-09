def patch(client, ticket_id, actor_id, **changes):
    return client.patch(
        f"/api/tickets/{ticket_id}", json={"actor_id": actor_id, **changes}
    )


def history(client, ticket_id, actor):
    response = client.get(f"/api/tickets/{ticket_id}", params={"actor_id": actor["id"]})
    return response.json()["history"]


# ---------- Pruebas de siempre (hechas por un coordinador) ----------

def test_valid_transition_and_assign(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    response = patch(client, ticket["id"], coord["id"],
                     status="in_progress", assigned_to=coord["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["assigned_to"] == coord["id"]
    fields = [h["field"] for h in history(client, ticket["id"], coord)]
    assert sorted(fields) == ["assigned_to", "status"]


def test_new_to_closed_rejected(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    assert patch(client, ticket["id"], coord["id"], status="closed").status_code == 400
    assert history(client, ticket["id"], coord) == []


def test_cannot_go_back_to_new(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    patch(client, ticket["id"], coord["id"], status="in_progress")
    assert patch(client, ticket["id"], coord["id"], status="new").status_code == 400


def test_full_lifecycle_with_reopen(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    steps = ["in_progress", "resolved", "in_progress", "resolved", "closed"]
    for status in steps:
        assert patch(client, ticket["id"], coord["id"], status=status).status_code == 200
    statuses = [h["new_value"] for h in history(client, ticket["id"], coord)]
    assert statuses == steps


def test_closed_ticket_cannot_change(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    for status in ["in_progress", "resolved", "closed"]:
        patch(client, ticket["id"], coord["id"], status=status)
    assert patch(client, ticket["id"], coord["id"], priority_id=4).status_code == 400


def test_invalid_actor(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    assert patch(client, ticket["id"], 999999, status="in_progress").status_code == 400
    assert history(client, ticket["id"], coord) == []


def test_invalid_assigned_user(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    assert patch(client, ticket["id"], coord["id"], assigned_to=999999).status_code == 400


def test_same_value_no_history(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord, priority_id=1)
    assert patch(client, ticket["id"], coord["id"], priority_id=1).status_code == 200
    assert history(client, ticket["id"], coord) == []


def test_ticket_not_found(client, make_user):
    coord = make_user("coordinator")
    assert patch(client, 999999, coord["id"], status="in_progress").status_code == 404


# ---------- Pruebas nuevas de roles ----------

def test_requester_cannot_update(client, make_user, make_ticket):
    # Reglas 13 y 20: el solicitante no cambia nada, ni en su propio ticket
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert patch(client, ticket["id"], solicitante["id"], status="in_progress").status_code == 403
    assert patch(client, ticket["id"], solicitante["id"], priority_id=4).status_code == 403


def test_technician_can_change_status_and_priority(client, make_user, make_ticket):
    # Reglas 14 y 20
    tecnico = make_user("technician")
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert patch(client, ticket["id"], tecnico["id"], status="in_progress").status_code == 200
    assert patch(client, ticket["id"], tecnico["id"], priority_id=4).status_code == 200


def test_assign_to_requester_rejected(client, make_user, make_ticket):
    # Regla 9: un solicitante no puede ser asignado
    coord = make_user("coordinator")
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert patch(client, ticket["id"], coord["id"], assigned_to=solicitante["id"]).status_code == 400


def test_coordinator_assigns_technician(client, make_user, make_ticket):
    # Regla 10: el coordinador asigna a otra persona
    coord = make_user("coordinator")
    tecnico = make_user("technician")
    ticket = make_ticket(coord)
    response = patch(client, ticket["id"], coord["id"], assigned_to=tecnico["id"])
    assert response.status_code == 200
    assert response.json()["assigned_to"] == tecnico["id"]


def test_technician_assigns_self(client, make_user, make_ticket):
    # Regla 11: el técnico se asigna un ticket libre
    tecnico = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    response = patch(client, ticket["id"], tecnico["id"], assigned_to=tecnico["id"])
    assert response.status_code == 200


def test_technician_cannot_assign_other(client, make_user, make_ticket):
    # Regla 10: el técnico no asigna a otra persona
    tecnico = make_user("technician")
    otro_tecnico = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    assert patch(client, ticket["id"], tecnico["id"], assigned_to=otro_tecnico["id"]).status_code == 403


def test_technician_cannot_take_assigned_ticket(client, make_user, make_ticket):
    # Regla 11: solo tickets SIN asignar
    coord = make_user("coordinator")
    tecnico_a = make_user("technician")
    tecnico_b = make_user("technician")
    ticket = make_ticket(coord)
    patch(client, ticket["id"], coord["id"], assigned_to=tecnico_a["id"])
    assert patch(client, ticket["id"], tecnico_b["id"], assigned_to=tecnico_b["id"]).status_code == 403
