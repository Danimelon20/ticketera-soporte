import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"
PORTALS = {"admin": "admin", "coordinator": "staff", "technician": "staff", "requester": "requester"}
WRONG_PASSWORD = "La contraseña actual no es correcta"
CODE_INVALID = "El código no es válido o venció. Puede pedir uno nuevo."
OLD_EMAIL_NOTICE = "El correo de su cuenta cambió; si no fue usted, contacte a soporte."


def _email():
    return f"nuevo-{uuid.uuid4().hex}@example.com"


def _login(client, email, role, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password, "portal": PORTALS[role]})


def _second_session(client, user):
    response = _login(client, user["email"], user["role"])
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers)


def _change(user_id):
    db = SessionLocal()
    try:
        return db.query(models.EmailChange).filter(models.EmailChange.user_id == user_id).first()
    finally:
        db.close()


def _set_change(user_id, **values):
    db = SessionLocal()
    try:
        row = db.query(models.EmailChange).filter(models.EmailChange.user_id == user_id).first()
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()
    finally:
        db.close()


def _failures(email):
    db = SessionLocal()
    try:
        row = db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).first()
        return row.failures if row else 0
    finally:
        db.close()


def _request_email(client, user, new_email, password=PASSWORD):
    return client.post(
        "/api/account/email", json={"current_password": password, "new_email": new_email}, headers=user["headers"]
    )


# ----- Contraseña -----

def test_password_change_requires_current_password(client, make_user):
    user = make_user("requester")
    response = client.put(
        "/api/account/password", json={"current_password": "otra-clave-999", "new_password": "nueva-clave-1"},
        headers=user["headers"],
    )
    assert response.status_code == 400
    assert response.json()["detail"] == WRONG_PASSWORD
    response = client.put("/api/account/password", json={"new_password": "nueva-clave-1"}, headers=user["headers"])
    assert response.status_code == 422
    assert _login(client, user["email"], "requester").status_code == 200


@pytest.mark.parametrize("role, password, ok", [
    ("requester", "a" * 7, False),
    ("requester", "a" * 8, True),
    ("technician", "a" * 11, False),
    ("technician", "a" * 12, True),
    ("coordinator", "a" * 11, False),
    ("coordinator", "a" * 12, True),
    ("admin", "a" * 11, False),
    ("admin", "a" * 12, True),
])
def test_password_minimum_depends_on_role(client, make_user, sent, role, password, ok):
    user = make_user(role)
    response = client.put(
        "/api/account/password", json={"current_password": PASSWORD, "new_password": password}, headers=user["headers"]
    )
    if ok:
        assert response.status_code == 204, response.text
    else:
        assert response.status_code == 422
        minimum = 8 if role == "requester" else 12
        assert response.json()["detail"] == f"La contraseña debe tener al menos {minimum} caracteres"


def test_password_change_closes_other_sessions(client, make_user):
    user = make_user("technician")
    other = _second_session(client, user)
    response = client.put(
        "/api/account/password", json={"current_password": PASSWORD, "new_password": "nueva-clave-larga"},
        headers=user["headers"],
    )
    assert response.status_code == 204, response.text
    assert _me(client, other).status_code == 401
    assert _me(client, user["headers"]).status_code == 200
    assert _login(client, user["email"], "technician").status_code == 401
    assert _login(client, user["email"], "technician", "nueva-clave-larga").status_code == 200


# ----- Teléfono -----

def test_staff_changes_phone_without_password(client, make_user):
    user = make_user("coordinator")
    response = client.put("/api/account/phone", json={"phone": "8777-6655"}, headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["phone"] == "87776655"
    bad = client.put("/api/account/phone", json={"phone": "123"}, headers=user["headers"])
    assert bad.status_code == 422


# ----- Correo -----

def test_email_change_needs_code_from_new_email(client, make_user, sent):
    user = make_user("requester")
    other = _second_session(client, user)
    new_email = _email()
    response = _request_email(client, user, new_email)
    assert response.status_code == 202, response.text
    assert response.json() == {"email_hint": new_email[:2] + "***@example.com"}
    assert [to for to, _s, _b in sent] == [new_email]
    code = code_from(sent[-1][2])
    assert code not in response.text

    # Sin confirmar, nada cambia
    assert _me(client, user["headers"]).json()["email"] == user["email"]
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    response = client.post("/api/account/email/verify", json={"code": wrong}, headers=user["headers"])
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID
    assert _me(client, user["headers"]).json()["email"] == user["email"]

    response = client.post("/api/account/email/verify", json={"code": code}, headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["email"] == new_email
    # Aviso al correo anterior
    to, _subject, body = sent[-1]
    assert to == user["email"]
    assert OLD_EMAIL_NOTICE in body
    # Las demás sesiones se cierran; esta sigue
    assert _me(client, other).status_code == 401
    assert _me(client, user["headers"]).status_code == 200
    assert _login(client, new_email, "requester").status_code == 200
    assert _login(client, user["email"], "requester").status_code == 401
    assert _change(user["id"]) is None


def test_email_change_wrong_password_sends_nothing_and_counts(client, make_user, sent):
    user = make_user("technician")
    response = _request_email(client, user, _email(), password="otra-clave-999")
    assert response.status_code == 400
    assert response.json()["detail"] == WRONG_PASSWORD
    assert sent == []
    assert _change(user["id"]) is None
    assert _failures(user["email"]) == 1


def test_email_in_use_is_not_revealed(client, make_user, sent):
    owner = make_user("coordinator")
    user = make_user("requester")
    free = _request_email(client, user, _email())
    taken = _request_email(client, make_user("requester"), owner["email"])
    assert taken.status_code == free.status_code == 202
    assert taken.json() == {"email_hint": owner["email"][:2] + "***@test.com"}
    # Solo se envió el código al correo libre
    assert len(sent) == 1 and sent[0][0] != owner["email"]

    # Ningún código sirve para un correo en uso
    for code in ("000000", "123456"):
        response = client.post("/api/account/email/verify", json={"code": code}, headers=user["headers"])
        assert response.status_code == 400


def test_email_code_expires_in_15_minutes_and_allows_5_attempts(client, make_user, sent):
    user = make_user("requester")
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    _request_email(client, user, _email())
    change = _change(user["id"])
    assert timedelta(minutes=14, seconds=59) <= change.expires_at - before <= timedelta(minutes=15, seconds=5)
    code = code_from(sent[-1][2])

    _set_change(user["id"], expires_at=before - timedelta(seconds=1))
    response = client.post("/api/account/email/verify", json={"code": code}, headers=user["headers"])
    assert response.status_code == 400

    _set_change(user["id"], expires_at=before + timedelta(minutes=15), attempts=5)
    response = client.post("/api/account/email/verify", json={"code": code}, headers=user["headers"])
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID


def test_email_code_resend_waits_60_seconds(client, make_user, sent):
    user = make_user("requester")
    _request_email(client, user, _email())
    first = code_from(sent[-1][2])
    response = client.post("/api/account/email/resend", headers=user["headers"])
    assert response.status_code == 429
    assert len(sent) == 1

    _set_change(user["id"], sent_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=61))
    response = client.post("/api/account/email/resend", headers=user["headers"])
    assert response.status_code == 202
    assert len(sent) == 2
    second = code_from(sent[-1][2])
    if second != first:
        response = client.post("/api/account/email/verify", json={"code": first}, headers=user["headers"])
        assert response.status_code == 400
    response = client.post("/api/account/email/verify", json={"code": second}, headers=user["headers"])
    assert response.status_code == 200, response.text
