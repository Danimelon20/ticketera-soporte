def test_create_ticket(client):
    payload = {
        "title": "No funciona la impresora",
        "description": "La impresora del segundo piso no imprime",
        "category_id": 1,
        "priority_id": 2,
    }
    response = client.post("/api/tickets", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "new"
    assert data["assigned_to"] is None


def test_get_ticket(client):
    payload = {
        "title": "Ticket para consultar",
        "description": "Se crea para luego buscarlo",
        "category_id": 1,
        "priority_id": 1,
    }
    created = client.post("/api/tickets", json=payload).json()
    response = client.get(f"/api/tickets/{created['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Ticket para consultar"


def test_create_ticket_invalid_category(client):
    payload = {
        "title": "Categoría inexistente",
        "description": "No debería crearse",
        "category_id": 99,
        "priority_id": 2,
    }
    response = client.post("/api/tickets", json=payload)
    assert response.status_code == 400


def test_get_ticket_not_found(client):
    response = client.get("/api/tickets/999999")
    assert response.status_code == 404
