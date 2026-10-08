import uuid


def create_user(client):
    payload = {"name": "Agente", "email": f"agente-{uuid.uuid4().hex}@test.com"}
    return client.post("/api/users", json=payload).json()


def create_ticket(client):
    payload = {
        "title": "Ticket para comentarios",
        "description": "Se usa en pruebas de comentarios",
        "category_id": 1,
        "priority_id": 1,
    }
    return client.post("/api/tickets", json=payload).json()


def comment(client, ticket_id, actor_id, content):
    return client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"actor_id": actor_id, "content": content},
    )


def close_ticket(client, ticket_id, actor_id):
    for status in ["in_progress", "resolved", "closed"]:
        client.patch(
            f"/api/tickets/{ticket_id}",
            json={"actor_id": actor_id, "status": status},
        )


def test_create_comment(client):
    user = create_user(client)
    ticket = create_ticket(client)
    response = comment(client, ticket["id"], user["id"], "Revisé el cable")
    assert response.status_code == 200
    data = response.json()
    assert data["ticket_id"] == ticket["id"]
    assert data["user_id"] == user["id"]
    assert data["content"] == "Revisé el cable"


def test_comment_in_detail_not_in_history(client):
    user = create_user(client)
    ticket = create_ticket(client)
    comment(client, ticket["id"], user["id"], "Nota de prueba")
    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert [c["content"] for c in detail["comments"]] == ["Nota de prueba"]
    assert detail["history"] == []


def test_comments_oldest_first(client):
    user = create_user(client)
    ticket = create_ticket(client)
    comment(client, ticket["id"], user["id"], "Primero")
    comment(client, ticket["id"], user["id"], "Segundo")
    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert [c["content"] for c in detail["comments"]] == ["Primero", "Segundo"]


def test_empty_comment_rejected(client):
    user = create_user(client)
    ticket = create_ticket(client)
    assert comment(client, ticket["id"], user["id"], "").status_code == 422
    assert comment(client, ticket["id"], user["id"], "     ").status_code == 422


def test_comment_is_trimmed(client):
    user = create_user(client)
    ticket = create_ticket(client)
    response = comment(client, ticket["id"], user["id"], "   hola   ")
    assert response.json()["content"] == "hola"


def test_comment_closed_ticket_rejected(client):
    user = create_user(client)
    ticket = create_ticket(client)
    close_ticket(client, ticket["id"], user["id"])
    response = comment(client, ticket["id"], user["id"], "Después de cerrar")
    assert response.status_code == 400


def test_comment_invalid_actor(client):
    ticket = create_ticket(client)
    response = comment(client, ticket["id"], 999999, "Nadie")
    assert response.status_code == 400


def test_comment_ticket_not_found(client):
    user = create_user(client)
    response = comment(client, 999999, user["id"], "Sin ticket")
    assert response.status_code == 404
