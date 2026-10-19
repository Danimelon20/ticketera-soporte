import logging
import uuid

from app import config
from conftest import code_from


def test_no_code_or_password_in_logs(client, make_user, sent, monkeypatch, caplog):
    # Con el correo encendido (simulado) los códigos nunca se escriben en el log
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    caplog.set_level(logging.DEBUG)
    secrets = []

    email = f"cliente-{uuid.uuid4().hex}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "clave-cliente-123"})
    code = code_from(sent[-1][2])
    secrets += ["clave-cliente-123", code]
    assert client.post("/api/auth/register/verify", json={"email": email, "code": code}).status_code == 200

    admin = make_user("admin")
    staff_email = f"personal-{uuid.uuid4().hex}@test.com"
    client.post(
        "/api/staff-invitations",
        json={"email": staff_email, "name": "Persona Nueva", "role": "technician"},
        headers=admin["headers"],
    )
    code = code_from(sent[-1][2])
    activation = client.post("/api/auth/activate", json={"email": staff_email, "code": code}).json()
    secrets += [code, activation["password"]]

    challenge = client.post("/api/auth/login", json={"email": admin["email"], "password": "clave-segura-123", "portal": "admin"}).json()
    code = code_from(sent[-1][2])
    secrets.append(code)
    assert client.post("/api/auth/verify-code", json={"challenge_id": challenge["challenge_id"], "code": code}).status_code == 200

    for secret in secrets:
        assert secret not in caplog.text


def test_generated_password_not_logged_in_development(client, make_user, sent, caplog):
    caplog.set_level(logging.DEBUG)
    admin = make_user("admin")
    staff_email = f"personal-{uuid.uuid4().hex}@test.com"
    client.post(
        "/api/staff-invitations",
        json={"email": staff_email, "name": "Persona Nueva", "role": "coordinator"},
        headers=admin["headers"],
    )
    code = code_from(sent[-1][2])
    password = client.post("/api/auth/activate", json={"email": staff_email, "code": code}).json()["password"]
    assert password not in caplog.text
