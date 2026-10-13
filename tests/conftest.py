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
    with TestClient(app) as test_client:
        class WrappedClient:
            def __init__(self, c):
                self.c = c

            def _inject_auth(self, kwargs):
                headers = kwargs.get("headers", {})
                if "Authorization" in headers:
                    return kwargs
                
                actor_id = None
                params = kwargs.get("params")
                if isinstance(params, dict) and "actor_id" in params:
                    actor_id = params.pop("actor_id")
                
                json_data = kwargs.get("json")
                if isinstance(json_data, dict) and "actor_id" in json_data:
                    actor_id = json_data.pop("actor_id")

                if actor_id is not None:
                    from app.database import SessionLocal
                    from app import models
                    from app.security import new_token
                    db = SessionLocal()
                    try:
                        user = db.query(models.User).filter(models.User.id == actor_id).first()
                        if user:
                            db_session = db.query(models.Session).filter(models.Session.user_id == actor_id).first()
                            if not db_session:
                                token = new_token()
                                db_session = models.Session(user_id=actor_id, token=token)
                                db.add(db_session)
                                db.commit()
                            headers["Authorization"] = f"Bearer {db_session.token}"
                    finally:
                        db.close()
                kwargs["headers"] = headers
                return kwargs

            def get(self, url, **kwargs):
                return self.c.get(url, **self._inject_auth(kwargs))
            def post(self, url, **kwargs):
                return self.c.post(url, **self._inject_auth(kwargs))
            def patch(self, url, **kwargs):
                return self.c.patch(url, **self._inject_auth(kwargs))
            def put(self, url, **kwargs):
                return self.c.put(url, **self._inject_auth(kwargs))
            def delete(self, url, **kwargs):
                return self.c.delete(url, **self._inject_auth(kwargs))
            def __getattr__(self, name):
                return getattr(self.c, name)

        yield WrappedClient(test_client)


@pytest.fixture
def make_user(client):
    """Fábrica: crea un usuario directamente en la base de datos de prueba."""
    def _make(role="coordinator", name="Usuario de prueba"):
        from app.database import SessionLocal
        from app import models
        from app.security import hash_password, new_token
        
        db = SessionLocal()
        try:
            user = models.User(
                name=name,
                email=f"{role}-{uuid.uuid4().hex}@test.com",
                role=role,
                password_hash=hash_password("clave-segura-123")
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            token = new_token()
            db_session = models.Session(token=token, user_id=user.id)
            db.add(db_session)
            db.commit()

            result = {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "token": token,
                "headers": {"Authorization": f"Bearer {token}"}
            }
            return result
        finally:
            db.close()
    return _make


@pytest.fixture
def make_ticket(client):
    """Fábrica: crea un ticket a nombre del usuario indicado (headers)."""
    def _make(actor, title="Ticket de prueba", priority_id=1, category_id=1):
        payload = {
            "title": title,
            "description": f"Descripción de {title}",
            "category_id": category_id,
            "priority_id": priority_id,
            "contact_phone": "8888-1234",
            "contact_id_number": "1-0234-0567",
        }
        response = client.post("/api/tickets", json=payload, headers=actor["headers"])
        assert response.status_code == 200, response.text
        return response.json()
    return _make


@pytest.fixture(autouse=True)
def fake_hacienda(monkeypatch):
    """Reemplaza la consulta a Hacienda: las pruebas nunca llaman a internet."""
    from app import hacienda

    def _fake_lookup(id_number):
        return {"tipo": "01"} if len(id_number) == 9 else {"tipo": "03"}

    monkeypatch.setattr(hacienda, "lookup_id", _fake_lookup)
