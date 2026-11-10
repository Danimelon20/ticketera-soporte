"""Invitaciones del personal: reconoce a quien ya está registrado, no reenvía a cuentas activadas
y deja filtrar a los usuarios existentes por rol, nombre o correo."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from conftest import code_from


def _email():
    return f"invitado-{uuid.uuid4().hex}@test.com"


def _invite(client, admin, email, role="technician"):
    return client.post("/api/staff-invitations", json={"email": email, "name": "Persona", "role": role},
                       headers=admin["headers"])


def _resend(client, admin, invitation_id):
    return client.post(f"/api/staff-invitations/{invitation_id}/resend", headers=admin["headers"])


def _set_sent_at(invitation_id, **changes):
    db = SessionLocal()
    try:
        row = db.get(models.StaffInvitation, invitation_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.commit()
    finally:
        db.close()


def _ago(**kwargs):
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**kwargs)


@pytest.mark.parametrize("existing_role, invited_role, expected", [
    ("technician", "technician", "ya está registrada como Técnico"),
    ("coordinator", "coordinator", "ya está registrada como Coordinador"),
    ("technician", "coordinator", "registrada con el rol Técnico. Cambie su rol desde Acciones"),
    ("coordinator", "technician", "registrada con el rol Coordinador"),
    ("admin", "technician", "registrada con el rol Administrador"),
    ("requester", "technician", "cuenta de cliente"),
])
def test_invite_recognizes_registered_people(client, make_user, sent, existing_role, invited_role, expected):
    admin = make_user("admin")
    person = make_user(existing_role)
    response = _invite(client, admin, person["email"].upper(), invited_role)
    assert response.status_code == 409
    assert expected in response.json()["detail"]
    assert sent == []


def test_cannot_resend_an_activated_invitation(client, make_user, sent):
    admin = make_user("admin")
    email = _email()
    invitation = _invite(client, admin, email).json()
    activated = client.post("/api/auth/activate", json={"email": email, "code": code_from(sent[-1][2])})
    assert activated.status_code == 200, activated.text
    sent.clear()
    response = _resend(client, admin, invitation["id"])
    assert response.status_code == 409
    assert sent == []
    listed = next(i for i in client.get("/api/staff-invitations", headers=admin["headers"]).json()
                  if i["id"] == invitation["id"])
    assert listed["status"] == "used"


def test_cannot_resend_when_the_email_already_has_an_account(client, make_user, sent):
    """La invitación quedó pendiente, pero el correo ya tiene cuenta (p. ej., otra vía o cambio de rol)."""
    admin = make_user("admin")
    email = _email()
    invitation = _invite(client, admin, email).json()
    _set_sent_at(invitation["id"], sent_at=_ago(minutes=5))
    db = SessionLocal()
    try:
        db.add(models.User(name="Ya Existe", email=email, role="coordinator", password_hash="x"))
        db.commit()
    finally:
        db.close()
    sent.clear()
    response = _resend(client, admin, invitation["id"])
    assert response.status_code == 409
    assert "Coordinador" in response.json()["detail"]
    assert sent == []
    listed = next(i for i in client.get("/api/staff-invitations", headers=admin["headers"]).json()
                  if i["id"] == invitation["id"])
    assert listed["status"] == "registered"


@pytest.mark.parametrize("expired", [False, True])
def test_resend_pending_invitation(client, make_user, sent, expired):
    admin = make_user("admin")
    email = _email()
    invitation = _invite(client, admin, email).json()
    changes = {"sent_at": _ago(minutes=5)}
    if expired:
        changes["expires_at"] = _ago(minutes=1)
    _set_sent_at(invitation["id"], **changes)
    sent.clear()
    response = _resend(client, admin, invitation["id"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "available"
    assert [to for to, _, _ in sent] == [email]
    # El código nuevo activa la cuenta
    activated = client.post("/api/auth/activate", json={"email": email, "code": code_from(sent[-1][2])})
    assert activated.status_code == 200, activated.text


def test_existing_users_filter_by_role_name_and_email(client, make_user):
    admin = make_user("admin")
    tag = uuid.uuid4().hex[:8]
    technician = make_user("technician", f"Técnica {tag}")
    coordinator = make_user("coordinator", f"Coordinador {tag}")
    requester = make_user("requester", f"Cliente {tag}")

    def ids(**params):
        response = client.get("/api/directory", params=params, headers=admin["headers"])
        assert response.status_code == 200, response.text
        return {u["id"] for u in response.json()}

    found = ids(role="staff", q=tag)
    assert {technician["id"], coordinator["id"]} <= found and requester["id"] not in found
    assert ids(role="technician", q=tag) == {technician["id"]}
    assert ids(role="coordinator", q=tag.upper()) == {coordinator["id"]}
    assert ids(role="staff", q=coordinator["email"]) == {coordinator["id"]}
