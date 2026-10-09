def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200


def test_get_categories(client):
    response = client.get("/api/categories")
    assert response.status_code == 200
    assert len(response.json()) == 4


def test_get_priorities_ordered(client):
    response = client.get("/api/priorities")
    assert response.status_code == 200
    levels = [p["level"] for p in response.json()]
    assert levels == sorted(levels)


def test_create_user(client):
    payload = {"name": "Test User", "email": "test@example.com", "role": "technician"}
    response = client.post("/api/users", json=payload)
    assert response.status_code == 200
    assert response.json()["role"] == "technician"


def test_create_user_duplicate_email(client):
    payload = {"name": "Dup User", "email": "dup@example.com", "role": "requester"}
    first = client.post("/api/users", json=payload)
    assert first.status_code == 200
    second = client.post("/api/users", json=payload)
    assert second.status_code == 409


def test_create_user_without_role(client):
    payload = {"name": "Sin Rol", "email": "sinrol@example.com"}
    response = client.post("/api/users", json=payload)
    assert response.status_code == 422


def test_create_user_invalid_role(client):
    payload = {"name": "Rol Raro", "email": "rolraro@example.com", "role": "jefe"}
    response = client.post("/api/users", json=payload)
    assert response.status_code == 422
