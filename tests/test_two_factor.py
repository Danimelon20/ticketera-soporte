import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import mailer, models
from app.database import SessionLocal

PASSWORD = "clave-segura-123"


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake_send(to, subject, body):
        calls.append((to, subject, body))
        return True

    monkeypatch.setattr(mailer, "send_email", _fake_send)
    return calls


def _code_from(sent):
    match = re.search(r"\b(\d{6})\b", sent[-1][2])
    assert match, "El correo no trae un código de 6 dígitos"
    return match.group(1)


def _login(client, user):
    return client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD})


def _start(client, make_user, role="coordinator"):
    user = make_user(role)
    response = _login(client, user)
    assert response.status_code == 200, response.text
    return user, response.json()


def _verify(client, challenge_id, code):
    return client.post("/api/auth/verify-code", json={"challenge_id": challenge_id, "code": code})


def _wrong(code):
    return f"{(int(code) + 1) % 10**6:06d}"


def test_coordinator_login_requires_code(client, make_user, sent):
    user, data = _start(client, make_user)
    assert data["requires_code"] is True
    assert "token" not in data
    assert data["challenge_id"]
    assert data["email_hint"].startswith(user["email"][:2] + "***@")
    assert len(sent) == 1
    to, subject, _body = sent[0]
    assert to == user["email"]
    assert subject == "Código de verificación de la Ticketera"


def test_correct_code_gives_token(client, make_user, sent):
    user, data = _start(client, make_user)
    response = _verify(client, data["challenge_id"], _code_from(sent))
    assert response.status_code == 200, response.text
    token = response.json()["token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == user["email"]


def test_wrong_code_and_too_many_attempts(client, make_user, sent):
    _user, data = _start(client, make_user)
    wrong = _wrong(_code_from(sent))
    for _ in range(5):
        response = _verify(client, data["challenge_id"], wrong)
        assert response.status_code == 400
        assert response.json()["detail"] == "Error: Intente Nuevamente"

    response = _verify(client, data["challenge_id"], _code_from(sent))
    assert response.status_code == 429
    assert response.json()["detail"] == "Demasiados intentos. Inicia sesión de nuevo."


def test_same_code_twice(client, make_user, sent):
    _user, data = _start(client, make_user)
    code = _code_from(sent)
    assert _verify(client, data["challenge_id"], code).status_code == 200
    second = _verify(client, data["challenge_id"], code)
    assert second.status_code == 400
    assert second.json()["detail"] == "Error: Intente Nuevamente"


def test_expired_code(client, make_user, sent):
    _user, data = _start(client, make_user)
    db = SessionLocal()
    try:
        challenge = db.get(models.LoginChallenge, data["challenge_id"])
        challenge.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    response = _verify(client, data["challenge_id"], _code_from(sent))
    assert response.status_code == 400
    assert response.json()["detail"] == "El código venció. Inicia sesión de nuevo."


def test_unknown_challenge(client, sent):
    response = _verify(client, "no-existe", "123456")
    assert response.status_code == 400
    assert response.json()["detail"] == "Error: Intente Nuevamente"


@pytest.mark.parametrize("role", ["requester", "technician"])
def test_other_roles_get_token_directly(client, make_user, sent, role):
    response = _login(client, make_user(role))
    assert response.status_code == 200
    assert "token" in response.json()
    assert sent == []


def test_admin_requires_code(client, make_user, sent):
    _user, data = _start(client, make_user, "admin")
    assert data["requires_code"] is True
    assert "token" not in data
    assert len(sent) == 1


def test_resend_too_soon(client, make_user, sent):
    _user, data = _start(client, make_user)
    response = client.post("/api/auth/resend-code", json={"challenge_id": data["challenge_id"]})
    assert response.status_code == 429
    assert response.json()["detail"] == "Espera un minuto antes de pedir otro código."
    assert len(sent) == 1


def test_resend_after_a_minute_sends_new_code(client, make_user, sent):
    _user, data = _start(client, make_user)
    old_code = _code_from(sent)
    db = SessionLocal()
    try:
        challenge = db.get(models.LoginChallenge, data["challenge_id"])
        challenge.created_at = challenge.created_at - timedelta(seconds=61)
        db.commit()
    finally:
        db.close()

    response = client.post("/api/auth/resend-code", json={"challenge_id": data["challenge_id"]})
    assert response.status_code == 200
    assert response.json()["challenge_id"] == data["challenge_id"]
    assert len(sent) == 2
    new_code = _code_from(sent)
    if new_code != old_code:
        assert _verify(client, data["challenge_id"], old_code).status_code == 400
    assert _verify(client, data["challenge_id"], new_code).status_code == 200


def test_register_as_coordinator_requires_code(client, make_user, sent):
    admin = make_user("admin")
    invite = client.post("/api/invites", json={"role": "coordinator"}, headers=admin["headers"])
    assert invite.status_code == 201
    payload = {
        "name": "Nueva Coordinadora",
        "email": f"coord-{uuid.uuid4().hex}@test.com",
        "password": PASSWORD,
        "invite_code": invite.json()["code"],
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["requires_code"] is True
    assert "token" not in data
    assert _verify(client, data["challenge_id"], _code_from(sent)).status_code == 200
