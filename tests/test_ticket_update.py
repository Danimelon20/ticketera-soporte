import uuid


def create_user(client):
    payload = {"name": "Agente", "email": f"agente-{uuid.uuid4().hex}@test.com"}
    return client.post("/api/users", json=payload).json()


def create_ticket(client, priority_id=1):
    payload = {
        "title": "Ticket para cambios",
        "description": "Se usa en pruebas de PATCH",
        "category_id": 1,
        "priority_id": priority_id,
    }
    return client.post("/api/tickets", json=payload).json()


def patch(client, ticket_id, actor_id, **changes):
    return client.patch(
        f"/api/tickets/{ticket_id}", json={"actor_id": actor_id, **changes}
    )


def history(client, ticket_id):
    return client.get(f"/api/tickets/{ticket_id}").json()["history"]


def test_valid_transition_and_assign(client):
    user = create_user(client)
    ticket = create_ticket(client)
    response = patch(client, ticket["id"], user["id"],
                     status="in_progress", assigned_to=user["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["assigned_to"] == user["id"]
    fields = [h["field"] for h in history(client, ticket["id"])]
    assert sorted(fields) == ["assigned_to", "status"]


def test_new_to_closed_rejected(client):
    user = create_user(client)
    ticket = create_ticket(client)
    response = patch(client, ticket["id"], user["id"], status="closed")
    assert response.status_code == 400
    assert history(client, ticket["id"]) == []


def test_cannot_go_back_to_new(client):
    user = create_user(client)
    ticket = create_ticket(client)
    patch(client, ticket["id"], user["id"], status="in_progress")
    response = patch(client, ticket["id"], user["id"], status="new")
    assert response.status_code == 400


def test_full_lifecycle_with_reopen(client):
    user = create_user(client)
    ticket = create_ticket(client)
    for status in ["in_progress", "resolved", "in_progress", "resolved", "closed"]:
        response = patch(client, ticket["id"], user["id"], status=status)
        assert response.status_code == 200
    statuses = [h["new_value"] for h in history(client, ticket["id"])]
    assert statuses == ["in_progress", "resolved", "in_progress", "resolved", "closed"]


def test_closed_ticket_cannot_change(client):
    user = create_user(client)
    ticket = create_ticket(client)
    for status in ["in_progress", "resolved", "closed"]:
        patch(client, ticket["id"], user["id"], status=status)
    response = patch(client, ticket["id"], user["id"], priority_id=4)
    assert response.status_code == 400


def test_invalid_actor(client):
    ticket = create_ticket(client)
    response = patch(client, ticket["id"], 999999, status="in_progress")
    assert response.status_code == 400
    assert history(client, ticket["id"]) == []


def test_invalid_assigned_user(client):
    user = create_user(client)
    ticket = create_ticket(client)
    response = patch(client, ticket["id"], user["id"], assigned_to=999999)
    assert response.status_code == 400


def test_same_value_no_history(client):
    user = create_user(client)
    ticket = create_ticket(client, priority_id=1)
    response = patch(client, ticket["id"], user["id"], priority_id=1)
    assert response.status_code == 200
    assert history(client, ticket["id"]) == []


def test_ticket_not_found(client):
    user = create_user(client)
    response = patch(client, 999999, user["id"], status="in_progress")
    assert response.status_code == 404
