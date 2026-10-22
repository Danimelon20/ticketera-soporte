import itertools
import os
import tempfile
import uuid

# Debe ir ANTES de importar la app: database.py lee esta variable al cargarse
_tmp_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
os.environ["EMAIL_ENABLED"] = "false"
os.environ["CHATBOT_ENABLED"] = "false"
os.environ["AI_ANALYSIS_ENABLED"] = "false"
# Que un .env real no asigne tickets ni arranque el ciclo de asignación durante las pruebas
os.environ["AUTO_ASSIGN_ENABLED"] = "false"
os.environ["MS_TOKEN_CACHE"] = f"{_tmp_dir}/ms_token_cache.json"
os.environ["ATTACHMENTS_DIR"] = f"{_tmp_dir}/attachments"

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
                    from app.security import hash_token, new_token
                    db = SessionLocal()
                    try:
                        user = db.query(models.User).filter(models.User.id == actor_id).first()
                        if user:
                            # La base solo guarda el hash del token: cada vez una sesión nueva
                            token = new_token()
                            db.add(models.Session(user_id=actor_id, token=hash_token(token)))
                            db.commit()
                            headers["Authorization"] = f"Bearer {token}"
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


_id_numbers = itertools.count(1)


def unique_id_number() -> str:
    """Cédula física de 9 dígitos distinta en cada llamada (una cédula no puede estar en dos cuentas)."""
    return f"7{next(_id_numbers) + int(uuid.uuid4().int % 10**4) * 10**4:08d}"


@pytest.fixture
def make_user(client):
    """Fábrica: crea un usuario directamente en la base de datos de prueba.

    Los solicitantes vienen con el perfil completo (nombre, teléfono y cédula única),
    salvo con profile=False.
    """
    def _make(role="coordinator", name="Usuario de prueba", profile=True):
        from app.database import SessionLocal
        from app import models
        from app.security import hash_password, hash_token, new_token
        
        db = SessionLocal()
        try:
            with_profile = role == "requester" and profile
            user = models.User(
                name=name,
                email=f"{role}-{uuid.uuid4().hex}@test.com",
                role=role,
                password_hash=hash_password("clave-segura-123"),
                phone="88881234" if with_profile else None,
                id_number=unique_id_number() if with_profile else None,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            token = new_token()
            db_session = models.Session(token=hash_token(token), user_id=user.id)
            db.add(db_session)
            db.commit()

            result = {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "phone": user.phone,
                "id_number": user.id_number,
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
        # Sin nombre: la comparación da "mismatch" salvo que la prueba lo cambie
        return {"tipo": "01" if len(id_number) == 9 else "03", "name_tokens": frozenset()}

    monkeypatch.setattr(hacienda, "lookup_id", _fake_lookup)
    # Que un .env real (cargado por docker compose) no cambie el resultado de las pruebas
    monkeypatch.setenv("HACIENDA_CHECK", "true")
    monkeypatch.delenv("HACIENDA_STRICT", raising=False)


# Cédulas fijas que usan muchas pruebas para cuentas distintas
SHARED_TEST_ID_NUMBERS = ("102340567", "109870654", "123456789012", "310123456")


@pytest.fixture(autouse=True)
def free_shared_id_numbers():
    """Cada prueba empieza sin ninguna cuenta dueña de las cédulas fijas de las pruebas."""
    from sqlalchemy import bindparam, text
    from sqlalchemy.exc import OperationalError
    from app.database import engine

    statement = text("UPDATE users SET id_number = NULL WHERE id_number IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    try:
        with engine.begin() as conn:
            conn.execute(statement, {"ids": list(SHARED_TEST_ID_NUMBERS)})
    except OperationalError:
        # Todavía no existen las tablas (la app no ha arrancado)
        pass


@pytest.fixture
def sent(monkeypatch):
    """Correos simulados: (destinatario, asunto, cuerpo). Nunca se conecta a Microsoft Graph.

    Los adjuntos de cada correo quedan en sent.attachments (misma posición; None si no llevaba).
    """
    from app import mailer

    class SentList(list):
        attachments: list

    calls = SentList()
    calls.attachments = []

    def _fake_send(to, subject, body, attachments=None):
        calls.append((to, subject, body))
        calls.attachments.append(attachments)
        return True

    monkeypatch.setattr(mailer, "send_email", _fake_send)
    return calls


def code_from(body: str) -> str:
    import re

    match = re.search(r"\b(\d{6})\b", body)
    assert match, "El correo no trae un código de 6 dígitos"
    return match.group(1)
