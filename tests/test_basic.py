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
