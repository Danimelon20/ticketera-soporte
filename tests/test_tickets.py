def test_create_ticket(make_user, make_ticket):
    user = make_user("requester")
    ticket = make_ticket(user, title="No funciona la impresora", priority_id=2)
    assert ticket["status"] == "new"
    assert ticket["assigned_to"] is None
    assert ticket["created_by"] == user["id"]


def test_get_ticket(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user, title="Ticket para consultar")
    response = client.get(f"/api/tickets/{ticket['id']}", params={"actor_id": user["id"]})
    assert response.status_code == 200
    assert response.json()["title"] == "Ticket para consultar"


def test_create_ticket_invalid_category(client, make_user):
    user = make_user()
    payload = {
        "title": "Categoría inexistente",
        "description": "No debería crearse",
        "category_id": 99,
        "priority_id": 2,
        "actor_id": user["id"],
    }
    response = client.post("/api/tickets", json=payload)
    assert response.status_code == 400


def test_create_ticket_without_actor(client):
    payload = {
        "title": "Sin actor",
        "description": "No debería crearse",
        "category_id": 1,
        "priority_id": 1,
    }
    response = client.post("/api/tickets", json=payload)
    assert response.status_code == 422


def test_create_ticket_invalid_actor(client):
    payload = {
        "title": "Actor inexistente",
        "description": "No debería crearse",
        "category_id": 1,
        "priority_id": 1,
        "actor_id": 999999,
    }
    response = client.post("/api/tickets", json=payload)
    assert response.status_code == 400


def test_get_ticket_not_found(client, make_user):
    user = make_user()
    response = client.get("/api/tickets/999999", params={"actor_id": user["id"]})
    assert response.status_code == 404
