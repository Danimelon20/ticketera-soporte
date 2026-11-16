"""Invitar como personal a un contacto (quien reportó por correo): la invitación lo convierte en técnico o
coordinador al activarla y conserva su historial (los tickets que reportó siguen siendo suyos). Un cliente
archivado o un contacto desactivado no se invita. Todo se valida en el servidor."""
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from app import config, entra_login, models
from app.accounts import ARCHIVED_NOT_INVITABLE, INACTIVE_CONTACT
from app.database import SessionLocal
from app.security import verify_password
from conftest import code_from
from org_helpers import placement_for


def _invite(client, admin, email, role="technician", name="Laura Vargas"):
    return client.post("/api/staff-invitations", json={"email": email, "name": name, "role": role,
                                                       **placement_for(role)}, headers=admin["headers"])


def _contact(make_user, make_ticket):
    contact = make_user("requester", "Laura Contacto")
    ticket = make_ticket(contact)
    return contact, ticket


def _set(user_id, **values):
    with SessionLocal() as db:
        user = db.get(models.User, user_id)
        for key, value in values.items():
            setattr(user, key, value)
        db.commit()


@pytest.mark.parametrize("role", ["technician", "coordinator"])
def test_invited_contact_becomes_staff_and_keeps_tickets(client, make_user, make_ticket, sent, role):
    admin = make_user("admin")
    contact, ticket = _contact(make_user, make_ticket)
    response = _invite(client, admin, contact["email"].upper(), role)
    assert response.status_code == 201, response.text
    # En la lista aparece pendiente, no como «ya registrado»
    listed = client.get("/api/staff-invitations", headers=admin["headers"]).json()
    assert next(i for i in listed if i["id"] == response.json()["id"])["status"] == "available"
    # Hasta activarla sigue siendo contacto (sin acceso)
    with SessionLocal() as db:
        assert db.get(models.User, contact["id"]).role == "requester"

    activated = client.post("/api/auth/activate", json={"email": contact["email"], "code": code_from(sent[-1][2])})
    assert activated.status_code == 200, activated.text
    password = activated.json()["password"]
    with SessionLocal() as db:
        user = db.get(models.User, contact["id"])
        # La misma cuenta: el mismo id, ahora con el rol invitado y una contraseña
        assert user.role == role and user.name == "Laura Vargas" and verify_password(password, user.password_hash)
        assert db.query(models.User).filter(models.User.email == contact["email"]).count() == 1
        # Su historial sigue siendo suyo
        assert db.get(models.Ticket, ticket["id"]).created_by == contact["id"]
        change = db.query(models.AccountChange).filter(
            models.AccountChange.target_user_id == contact["id"],
            models.AccountChange.action == "invitation_used").one()
        # El registro guarda el rol anterior
        assert change.old_value == "Solicitante" and change.new_value
    # Ya entra como personal
    login = client.post("/api/auth/login", json={"email": contact["email"], "password": password})
    assert login.status_code == 200, login.text


def test_archived_client_cannot_be_invited(client, make_user, make_ticket, sent):
    admin = make_user("admin")
    contact, _ticket = _contact(make_user, make_ticket)
    with SessionLocal() as db:
        db.add(models.ArchivedAccount(user_id=contact["id"]))
        db.commit()
    response = _invite(client, admin, contact["email"])
    assert response.status_code == 409 and response.json()["detail"] == ARCHIVED_NOT_INVITABLE
    assert sent == []


def test_inactive_contact_cannot_be_invited(client, make_user, make_ticket, sent):
    admin = make_user("admin")
    contact, _ticket = _contact(make_user, make_ticket)
    _set(contact["id"], is_active=False)
    response = _invite(client, admin, contact["email"])
    assert response.status_code == 409 and response.json()["detail"] == INACTIVE_CONTACT


def test_archived_after_inviting_cannot_activate(client, make_user, make_ticket, sent):
    """Si se archiva entre la invitación y la activación, el código ya no sirve y nada cambia."""
    admin = make_user("admin")
    contact, _ticket = _contact(make_user, make_ticket)
    assert _invite(client, admin, contact["email"]).status_code == 201
    code = code_from(sent[-1][2])
    invitation_id = client.get("/api/staff-invitations", headers=admin["headers"]).json()[0]["id"]
    with SessionLocal() as db:
        db.add(models.ArchivedAccount(user_id=contact["id"]))
        db.commit()
    response = client.post("/api/auth/activate", json={"email": contact["email"], "code": code})
    assert response.status_code == 400
    with SessionLocal() as db:
        assert db.get(models.User, contact["id"]).role == "requester"
    # Tampoco se reenvía
    resend = client.post(f"/api/staff-invitations/{invitation_id}/resend", headers=admin["headers"])
    assert resend.status_code == 409 and resend.json()["detail"] == ARCHIVED_NOT_INVITABLE


def test_staff_email_still_cannot_be_invited(client, make_user, sent):
    admin = make_user("admin")
    technician = make_user("technician")
    response = _invite(client, admin, technician["email"])
    assert response.status_code == 409 and "ya está registrada" in response.json()["detail"]


def test_contact_activates_with_microsoft(client, make_user, make_ticket, sent, monkeypatch):
    from test_microsoft_login import CLIENT_ID, TENANT, FakeMsalApp, MsState, _callback, _claims, _exchange

    admin = make_user("admin")
    contact, ticket = _contact(make_user, make_ticket)
    assert _invite(client, admin, contact["email"]).status_code == 201
    code = code_from(sent[-1][2])
    state = MsState()
    monkeypatch.setattr(config, "MS_LOGIN_ENABLED", True)
    monkeypatch.setattr(config, "MS_LOGIN_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "MS_LOGIN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(entra_login, "build_app", lambda: FakeMsalApp(state))
    client.cookies.clear()
    state.claims = _claims(contact["email"])
    start = client.post("/api/auth/activate/microsoft", json={"email": contact["email"], "code": code})
    assert start.status_code == 200, start.text
    result = _callback(client, parse_qs(urlsplit(start.json()["auth_url"]).query)["state"][0])
    response = _exchange(client, result["code"])
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == contact["id"] and response.json()["user"]["role"] == "technician"
    with SessionLocal() as db:
        assert db.get(models.Ticket, ticket["id"]).created_by == contact["id"]
