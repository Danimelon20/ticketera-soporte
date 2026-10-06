from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


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
    data = response.json()
    levels = [p["level"] for p in data]
    assert levels == sorted(levels)


def test_create_user(client):
    response = client.post(
        "/api/users",
        json={"name": "Test User", "email": "test@example.com"},
    )
    assert response.status_code == 200


def test_create_user_duplicate_email(client):
    payload = {"name": "Dup User", "email": "dup@example.com"}
    first = client.post("/api/users", json=payload)
    assert first.status_code == 200
    second = client.post("/api/users", json=payload)
    assert second.status_code == 409
