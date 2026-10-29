"""Solicitantes internos (empleados de ADN): Microsoft, registro bloqueado, solo áreas internas y aviso de datos."""
import uuid

import pytest

from app import config, models, privacy, routing
from app.database import SessionLocal
from org_helpers import area
from test_microsoft_login import TENANT, _claims, _exchange, _microsoft_login, ms  # noqa: F401

DOMAIN = "adn-pruebas.example"


@pytest.fixture
def internal_domain(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", (DOMAIN,))


def _email():
    return f"empleado-{uuid.uuid4().hex[:8]}@{DOMAIN}"


def _user_by_email(email):
    db = SessionLocal()
    try:
        return db.query(models.User).filter(models.User.email == email).first()
    finally:
        db.close()


def test_microsoft_creates_internal_requester(client, ms, internal_domain):
    email = _email()
    ms.claims = _claims(email)
    fragment = _microsoft_login(client, portal="requester")
    assert "code" in fragment, fragment
    response = _exchange(client, fragment["code"])
    assert response.status_code == 200, response.text
    user = _user_by_email(email)
    assert user.role == "requester"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
    assert me["requester_kind"] == "internal"
    # Rol y departamento del personal nunca salen de aquí
    db = SessionLocal()
    try:
        assert db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == user.id).first() is None
    finally:
        db.close()


def test_guest_from_other_tenant_is_never_internal(client, ms, internal_domain):
    email = _email()
    ms.claims = {**_claims(email), "idp": "https://sts.windows.net/otro-inquilino/"}
    assert _microsoft_login(client, portal="requester") == {"error": "guest", "portal": "requester"}
    ms.claims = {**_claims(email), "acct": 1}
    assert _microsoft_login(client, portal="requester")["error"] == "guest"
    assert _user_by_email(email) is None


def test_other_tenant_rejected(client, ms, internal_domain):
    email = _email()
    ms.claims = _claims(email, tid="otro-inquilino")
    assert _microsoft_login(client, portal="requester")["error"] == "tenant"
    assert _user_by_email(email) is None


def test_external_domain_not_created(client, ms, internal_domain):
    email = f"cliente-{uuid.uuid4().hex[:8]}@cliente.example"
    ms.claims = _claims(email)
    assert _microsoft_login(client, portal="requester")["error"] == "not_internal"
    assert _user_by_email(email) is None


def test_internal_domain_cannot_self_register(client, internal_domain, monkeypatch):
    payload = {"email": _email(), "password": "clave-segura-123"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 403
    assert "administrador" in response.json()["detail"]
    monkeypatch.setattr(config, "MS_LOGIN_ENABLED", True)
    response = client.post("/api/auth/register", json={**payload, "email": _email()})
    assert response.json()["detail"] == "Los correos de ADN entran con su cuenta Microsoft"


def test_admin_creates_internal_requester_without_microsoft(client, make_user, internal_domain):
    admin = make_user("admin")
    email = _email()
    response = client.post("/api/admin/internal-requesters", headers=admin["headers"],
                           json={"email": email, "name": "Empleada de prueba"})
    assert response.status_code == 201, response.text
    assert email not in response.text
    assert client.post("/api/admin/internal-requesters", headers=admin["headers"],
                       json={"email": "alguien@cliente.example", "name": "Externo"}).status_code == 400
    coordinator = make_user("coordinator")
    assert client.post("/api/admin/internal-requesters", headers=coordinator["headers"],
                       json={"email": _email(), "name": "X"}).status_code == 403


def _internal_requester(make_user, client, internal_domain_on=True):
    user = make_user("requester")
    db = SessionLocal()
    try:
        row = db.get(models.User, user["id"])
        row.email = _email()
        routing.link_internal(db, row)
        db.commit()
    finally:
        db.close()
    return user


def _accept(client, user):
    notice = client.get("/api/privacy/notice", headers=user["headers"]).json()
    return client.post("/api/privacy/accept", headers=user["headers"],
                       json={"version": notice["version"], "accepted": True})


def test_internal_requester_only_sees_internal_areas(client, make_user, internal_domain, routing_on):
    internal, external = area(audience="internal"), area()
    user = _internal_requester(make_user, client)
    assert _accept(client, user).status_code == 204
    options = client.get("/api/areas", headers=user["headers"]).json()
    ids = {a["id"] for a in options["areas"]}
    assert internal in ids and external not in ids
    assert options["unsure_allowed"] is False
    payload = {"title": "Laptop", "description": "No enciende", "category_id": 1, "priority_id": 1}
    assert client.post("/api/tickets", headers=user["headers"], json={**payload, "area_id": external}).status_code == 400
    assert client.post("/api/tickets", headers=user["headers"], json={**payload, "unsure": True}).status_code == 400
    assert client.post("/api/tickets", headers=user["headers"], json={**payload, "area_id": internal}).status_code == 200


def test_privacy_notice_required_once_and_again_on_new_version(client, make_user, internal_domain, monkeypatch,
                                                               tmp_path):
    internal = area(audience="internal")
    user = _internal_requester(make_user, client)
    payload = {"title": "Correo", "description": "No sincroniza", "category_id": 1, "priority_id": 1,
               "area_id": internal}
    blocked = client.post("/api/tickets", headers=user["headers"], json=payload)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == privacy.REQUIRED_MESSAGE
    assert client.get("/api/tickets", headers=user["headers"]).status_code == 403
    assert client.get("/api/auth/me", headers=user["headers"]).json()["privacy_pending"] is True

    notice = client.get("/api/privacy/notice", headers=user["headers"]).json()
    assert notice["kind"] == "internal"
    assert "Sin su aceptación no podemos brindarle el servicio" in notice["text"]
    assert client.post("/api/privacy/accept", headers=user["headers"],
                       json={"version": notice["version"], "accepted": False}).status_code == 400
    assert _accept(client, user).status_code == 204
    assert client.post("/api/tickets", headers=user["headers"], json=payload).status_code == 200
    assert client.get("/api/privacy/notice", headers=user["headers"]).json() is None

    db = SessionLocal()
    try:
        row = db.query(models.PrivacyAcceptance).filter(models.PrivacyAcceptance.user_id == user["id"]).one()
        assert row.version == notice["version"] and row.accepted_at and row.via == "login"
    finally:
        db.close()

    # La IP de quien aceptó (el cliente de pruebas no trae una: se revisa directo)
    other = _internal_requester(make_user, client)
    db = SessionLocal()
    try:
        person = db.get(models.User, other["id"])
        assert privacy.accept(db, person, notice["version"], "203.0.113.5")
        db.commit()
        assert db.query(models.PrivacyAcceptance).filter(
            models.PrivacyAcceptance.user_id == other["id"]).one().ip == "203.0.113.5"
    finally:
        db.close()

    # Versión nueva: hay que aceptar de nuevo
    (tmp_path / "aviso_interno.md").write_text("<!-- version: 2 -->\n# Aviso nuevo\n", encoding="utf-8")
    monkeypatch.setattr(privacy, "LEGAL_DIR", str(tmp_path))
    assert client.post("/api/tickets", headers=user["headers"], json=payload).status_code == 403
    assert client.get("/api/privacy/notice", headers=user["headers"]).json()["version"] == "2"


def test_staff_never_see_the_notice(client, make_user):
    for role in ("technician", "coordinator"):
        user = make_user(role)
        assert client.get("/api/privacy/notice", headers=user["headers"]).json() is None


def test_internal_notice_has_article_5_structure():
    version, text = privacy.read_notice("internal")
    for heading in ("Base de datos", "Fines", "Destinatarios", "Datos obligatorios", "Consecuencia de negarse",
                    "Sus derechos", "Responsable y contacto"):
        assert f"## {heading}" in text
    assert "[PENDIENTE: texto oficial de American Data Networks" in text
    assert version
