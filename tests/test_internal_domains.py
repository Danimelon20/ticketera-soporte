"""Correos de dominios internos (INTERNAL_DOMAINS): no pueden ser cuentas de solicitante."""
import uuid

import pytest

from app import config, models
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"
INTERNAL = "Los correos de ADN no se registran como cliente. Si necesita acceso, solicítelo al administrador."


@pytest.fixture
def internal(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])


def _local():
    return f"persona-{uuid.uuid4().hex[:10]}"


def _register(client, email):
    return client.post("/api/auth/register", json={"email": email, "password": PASSWORD})


def _accounts(email):
    db = SessionLocal()
    try:
        return db.query(models.User).filter(models.User.email == email.lower()).count()
    finally:
        db.close()


@pytest.mark.parametrize("domain", ["data.cr", "soporte.data.cr", "DATA.CR", "Soporte.Data.Cr"])
def test_register_with_internal_domain_is_rejected(client, sent, internal, domain):
    email = f"{_local()}@{domain}"
    response = _register(client, email)
    assert response.status_code == 400
    assert response.json()["detail"] == INTERNAL
    assert sent == []
    assert _accounts(email) == 0


@pytest.mark.parametrize("domain", ["otradata.cr", "data.cr.example.com", "example.com"])
def test_register_with_other_domain_is_allowed(client, sent, internal, domain):
    email = f"{_local()}@{domain}"
    assert _register(client, email).status_code == 202
    code = code_from(sent[-1][2])
    response = client.post("/api/auth/register/verify", json={"email": email, "code": code})
    assert response.status_code == 200, response.text
    assert _accounts(email) == 1


def test_empty_internal_domains_blocks_nothing(client, sent, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", [])
    assert _register(client, f"{_local()}@data.cr").status_code == 202


def test_signup_started_before_the_rule_cannot_be_confirmed(client, sent, monkeypatch):
    email = f"{_local()}@data.cr"
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", [])
    assert _register(client, email).status_code == 202
    code = code_from(sent[-1][2])

    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])
    response = client.post("/api/auth/register/resend", json={"email": email})
    assert response.status_code == 400 and response.json()["detail"] == INTERNAL
    response = client.post("/api/auth/register/verify", json={"email": email, "code": code})
    assert response.status_code == 400 and response.json()["detail"] == INTERNAL
    assert _accounts(email) == 0


def _request_email(client, user, new_email):
    return client.post(
        "/api/account/email", json={"current_password": PASSWORD, "new_email": new_email}, headers=user["headers"]
    )


@pytest.mark.parametrize("domain", ["data.cr", "ti.data.cr", "Data.CR"])
def test_requester_cannot_change_email_to_internal_domain(client, make_user, sent, internal, domain):
    user = make_user("requester")
    response = _request_email(client, user, f"{_local()}@{domain}")
    assert response.status_code == 400
    assert response.json()["detail"] == INTERNAL
    assert sent == []


def test_requester_can_change_email_to_other_domain(client, make_user, sent, internal):
    user = make_user("requester")
    assert _request_email(client, user, f"{_local()}@otradata.cr").status_code == 202


def test_staff_can_change_email_to_internal_domain(client, make_user, sent, internal):
    user = make_user("technician")
    assert _request_email(client, user, f"{_local()}@data.cr").status_code == 202


def test_pending_email_change_to_internal_domain_cannot_be_confirmed(client, make_user, sent, monkeypatch):
    user = make_user("requester")
    new_email = f"{_local()}@data.cr"
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", [])
    assert _request_email(client, user, new_email).status_code == 202
    code = code_from(sent[-1][2])

    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])
    response = client.post("/api/account/email/verify", json={"code": code}, headers=user["headers"])
    assert response.status_code == 400 and response.json()["detail"] == INTERNAL
    me = client.get("/api/auth/me", headers=user["headers"]).json()
    assert me["email"] == user["email"]
