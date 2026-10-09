import os
import tempfile
import uuid

# Debe ir ANTES de importar la app: database.py lee esta variable al cargarse
_tmp_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def make_user(client):
    """Fábrica: crea un usuario con el rol indicado y un correo único."""
    def _make(role="coordinator", name="Usuario de prueba"):
        payload = {
            "name": name,
            "email": f"{role}-{uuid.uuid4().hex}@test.com",
            "role": role,
        }
        response = client.post("/api/users", json=payload)
        assert response.status_code == 200, response.text
        return response.json()
    return _make


@pytest.fixture
def make_ticket(client):
    """Fábrica: crea un ticket a nombre del usuario indicado (actor)."""
    def _make(actor, title="Ticket de prueba", priority_id=1, category_id=1):
        payload = {
            "title": title,
            "description": f"Descripción de {title}",
            "category_id": category_id,
            "priority_id": priority_id,
            "actor_id": actor["id"],
        }
        response = client.post("/api/tickets", json=payload)
        assert response.status_code == 200, response.text
        return response.json()
    return _make
