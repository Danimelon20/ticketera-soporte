import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from conftest import code_from

CODE_INVALID = "El código no es válido o venció. Puede pedir uno nuevo."


def _email():
    return f"personal-{uuid.uuid4().hex}@test.com"


def _invite(client, actor, role="technician", email=None, name="Nueva Persona"):
    email = email or _email()
    response = client.post(
        "/api/staff-invitations", json={"email": email, "name": name, "role": role}, headers=actor["headers"]
    )
    return email, response


def _activate(client, email, code):
    return client.post("/api/auth/activate", json={"email": email, "code": code})


def _invitation(email):
    db = SessionLocal()
    try:
        return (
            db.query(models.StaffInvitation)
            .filter(models.StaffInvitation.email == email)
            .order_by(models.StaffInvitation.id.desc())
            .first()
        )
    finally:
        db.close()


def _set_invitation(email, **values):
    db = SessionLocal()
    try:
        invitation = (
            db.query(models.StaffInvitation)
            .filter(models.StaffInvitation.email == email)
            .order_by(models.StaffInvitation.id.desc())
            .first()
        )
        for key, value in values.items():
            setattr(invitation, key, value)
        db.commit()
    finally:
        db.close()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.parametrize("role", ["technician", "coordinator"])
def test_admin_invites_and_activation_creates_role(client, make_user, sent, role):
    admin = make_user("admin")
    email, response = _invite(client, admin, role, name="Carla Mora")
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["role"] == role and data["status"] == "available" and data["email_sent"] is True
    assert "code" not in data and "code_hash" not in data

    to, subject, body = sent[-1]
    assert to == email
    assert subject == "Active su cuenta de la Ticketera"
    assert "/#activar-cuenta" in body
    assert "72 horas" in body

    response = _activate(client, email, code_from(body))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    password = response.json()["password"]
    assert len(password) >= 16
    # La contraseña nunca va por correo
    assert all(password not in b for (_t, _s, b) in sent)

    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    user = login.json()["user"]
    assert user["role"] == role and user["name"] == "Carla Mora"
    assert _invitation(email).used_by == user["id"]


def test_invitation_expires_in_72_hours(client, make_user, sent):
    email, _ = _invite(client, make_user("admin"))
    invitation = _invitation(email)
    assert (invitation.expires_at - invitation.sent_at) == timedelta(hours=72)


def test_expired_invitation_is_rejected(client, make_user, sent):
    email, _ = _invite(client, make_user("admin"))
    _set_invitation(email, expires_at=_now() - timedelta(minutes=1))
    response = _activate(client, email, code_from(sent[-1][2]))
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID


def test_wrong_code_and_too_many_attempts(client, make_user, sent):
    email, _ = _invite(client, make_user("admin"))
    code = code_from(sent[-1][2])
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    for _ in range(5):
        response = _activate(client, email, wrong)
        assert response.status_code == 400
        assert response.json()["detail"] == CODE_INVALID
    assert _activate(client, email, code).status_code == 400


def test_code_works_only_once(client, make_user, sent):
    email, _ = _invite(client, make_user("admin"))
    code = code_from(sent[-1][2])
    assert _activate(client, email, code).status_code == 200
    second = _activate(client, email, code)
    assert second.status_code == 400
    assert "password" not in second.json()


def test_code_is_tied_to_the_email(client, make_user, sent):
    _email_a, _ = _invite(client, make_user("admin"))
    response = _activate(client, _email(), code_from(sent[-1][2]))
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID


def test_invitation_stores_only_hash(client, make_user, sent):
    email, _ = _invite(client, make_user("admin"))
    code = code_from(sent[-1][2])
    assert code not in _invitation(email).code_hash


def test_coordinator_invites_only_technicians(client, make_user, sent):
    coord = make_user("coordinator")
    _email_t, response = _invite(client, coord, "technician")
    assert response.status_code == 201
    _email_c, response = _invite(client, coord, "coordinator")
    assert response.status_code == 403
    assert response.json()["detail"] == "Solo el administrador puede invitar coordinadores"


@pytest.mark.parametrize("role", ["requester", "technician"])
def test_others_cannot_invite(client, make_user, sent, role):
    _email_x, response = _invite(client, make_user(role))
    assert response.status_code == 403


def test_existing_account_cannot_be_invited(client, make_user, sent):
    requester = make_user("requester")
    _email_x, response = _invite(client, make_user("admin"), email=requester["email"])
    assert response.status_code == 409


def test_list_hides_codes_and_coordinator_sees_own(client, make_user, sent):
    admin = make_user("admin")
    coord = make_user("coordinator")
    admin_email, _ = _invite(client, admin, "coordinator")
    coord_email, _ = _invite(client, coord, "technician")

    mine = client.get("/api/staff-invitations", headers=coord["headers"]).json()
    assert [i["email"] for i in mine] == [coord_email]
    everything = client.get("/api/staff-invitations", headers=admin["headers"]).json()
    emails = [i["email"] for i in everything]
    assert admin_email in emails and coord_email in emails
    for invitation in everything:
        assert "code" not in invitation and "code_hash" not in invitation


def test_resend_gives_new_code_and_72_hours(client, make_user, sent):
    admin = make_user("admin")
    email, response = _invite(client, admin)
    invitation_id = response.json()["id"]
    too_soon = client.post(f"/api/staff-invitations/{invitation_id}/resend", headers=admin["headers"])
    assert too_soon.status_code == 429

    _set_invitation(email, sent_at=_now() - timedelta(hours=10), expires_at=_now() + timedelta(hours=62), attempts=3)
    response = client.post(f"/api/staff-invitations/{invitation_id}/resend", headers=admin["headers"])
    assert response.status_code == 200
    invitation = _invitation(email)
    assert invitation.attempts == 0
    assert invitation.expires_at - invitation.sent_at == timedelta(hours=72)
    assert _activate(client, email, code_from(sent[-1][2])).status_code == 200


def test_resend_from_activation_page(client, make_user, sent):
    email, _ = _invite(client, make_user("admin"))
    count = len(sent)
    assert client.post("/api/auth/activate/resend", json={"email": email}).status_code == 202
    assert len(sent) == count  # antes de 60 s no se envía
    _set_invitation(email, sent_at=_now() - timedelta(seconds=61))
    assert client.post("/api/auth/activate/resend", json={"email": email}).status_code == 202
    assert len(sent) == count + 1
    # Correo sin invitación: misma respuesta
    assert client.post("/api/auth/activate/resend", json={"email": _email()}).status_code == 202


def test_nobody_gets_staff_role_without_invitation(client, sent):
    email = _email()
    assert _activate(client, email, "123456").status_code == 400
    client.post("/api/auth/register", json={"email": email, "password": "clave-segura-123", "role": "technician"})
    data = client.post(
        "/api/auth/register/verify", json={"email": email, "code": code_from(sent[-1][2])}
    ).json()
    assert data["user"]["role"] == "requester"
    assert client.post("/api/invites", json={"role": "technician"}).status_code in (404, 405)
