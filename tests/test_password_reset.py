import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.security import verify_password
import re
from urllib.parse import parse_qs

from conftest import code_from

CODE_INVALID = "El código no es válido o venció. Puede pedir uno nuevo."
PORTALS = {"requester": "requester", "technician": "staff", "coordinator": "staff", "admin": "admin"}


def _request(client, email, portal):
    return client.post("/api/auth/password-reset", json={"email": email, "portal": portal})


def _confirm(client, email, portal, code, new_password=None):
    body = {"email": email, "portal": portal, "code": code}
    if new_password is not None:
        body["new_password"] = new_password
    return client.post("/api/auth/password-reset/confirm", json=body)


def _codes_to(sent, email):
    return [code_from(body) for to, _, body in sent if to == email and "código" in body]


def _sessions(user_id):
    db = SessionLocal()
    try:
        return db.query(models.Session).filter(models.Session.user_id == user_id).count()
    finally:
        db.close()


def _set_reset(user_id, **values):
    db = SessionLocal()
    try:
        reset = db.query(models.PasswordReset).filter(models.PasswordReset.user_id == user_id).first()
        for key, value in values.items():
            setattr(reset, key, value)
        db.commit()
    finally:
        db.close()


def test_same_answer_whether_account_exists_or_not(client, make_user, sent):
    requester = make_user("requester")
    technician = make_user("technician")
    inactive = make_user("requester")
    db = SessionLocal()
    try:
        db.get(models.User, inactive["id"]).is_active = False
        db.commit()
    finally:
        db.close()

    cases = [
        (requester["email"], "requester"),                 # existe
        (f"nadie-{uuid.uuid4().hex}@test.com", "requester"),  # no existe
        (technician["email"], "requester"),                # opción equivocada
        (inactive["email"], "requester"),                  # desactivada
    ]
    responses = [_request(client, email, portal) for email, portal in cases]
    assert {r.status_code for r in responses} == {202}
    assert len({tuple(sorted(r.json().keys())) for r in responses}) == 1
    # Solo la cuenta activa con la opción correcta recibe un código
    assert [to for to, _, _ in sent] == [requester["email"]]


def test_requester_chooses_new_password_and_code_works_once(client, make_user, sent):
    requester = make_user("requester")
    _request(client, requester["email"], "requester")
    code = _codes_to(sent, requester["email"])[-1]

    too_short = _confirm(client, requester["email"], "requester", code, "corta")
    assert too_short.status_code == 422
    response = _confirm(client, requester["email"], "requester", code, "nueva-clave-segura")
    assert response.status_code == 204
    assert response.content == b""
    # Sesiones cerradas y la contraseña nueva funciona
    assert _sessions(requester["id"]) == 0
    login = client.post("/api/auth/login", json={
        "email": requester["email"], "password": "nueva-clave-segura", "portal": "requester",
    })
    assert login.status_code == 200
    # El mismo código no sirve otra vez
    again = _confirm(client, requester["email"], "requester", code, "otra-clave-segura")
    assert again.status_code == 400
    assert again.json()["detail"] == CODE_INVALID


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_staff_gets_generated_password(client, make_user, sent, role):
    user = make_user(role)
    _request(client, user["email"], PORTALS[role])
    code = _codes_to(sent, user["email"])[-1]
    # Aunque mande una contraseña, al personal se le genera una
    response = _confirm(client, user["email"], PORTALS[role], code, "elegida-por-mi-123")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    password = response.json()["password"]
    assert len(password) >= 12 and password != "elegida-por-mi-123"
    db = SessionLocal()
    try:
        assert verify_password(password, db.get(models.User, user["id"]).password_hash)
    finally:
        db.close()
    assert _sessions(user["id"]) == 0


def test_wrong_codes_count_toward_lockout(client, make_user, sent):
    requester = make_user("requester")
    _request(client, requester["email"], "requester")
    code = _codes_to(sent, requester["email"])[-1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert _confirm(client, requester["email"], "requester", wrong, "nueva-clave-segura").status_code == 400
    assert _confirm(client, requester["email"], "requester", wrong, "nueva-clave-segura").status_code == 429
    # Bloqueado: ni el código correcto sirve
    assert _confirm(client, requester["email"], "requester", code, "nueva-clave-segura").status_code == 429


def test_expired_code_is_rejected(client, make_user, sent):
    requester = make_user("requester")
    _request(client, requester["email"], "requester")
    code = _codes_to(sent, requester["email"])[-1]
    _set_reset(requester["id"], expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1))
    response = _confirm(client, requester["email"], "requester", code, "nueva-clave-segura")
    assert response.status_code == 400


def test_code_lasts_15_minutes_and_resend_waits_a_minute(client, make_user, sent):
    requester = make_user("requester")
    _request(client, requester["email"], "requester")
    db = SessionLocal()
    try:
        reset = db.query(models.PasswordReset).filter(models.PasswordReset.user_id == requester["id"]).first()
        minutes = (reset.expires_at - reset.sent_at).total_seconds() / 60
    finally:
        db.close()
    assert minutes == 15
    _request(client, requester["email"], "requester")
    client.post("/api/auth/password-reset/resend", json={"email": requester["email"], "portal": "requester"})
    assert len(_codes_to(sent, requester["email"])) == 1


def test_only_hash_is_stored(client, make_user, sent):
    requester = make_user("requester")
    _request(client, requester["email"], "requester")
    code = _codes_to(sent, requester["email"])[-1]
    db = SessionLocal()
    try:
        reset = db.query(models.PasswordReset).filter(models.PasswordReset.user_id == requester["id"]).first()
        assert code not in reset.code_hash
    finally:
        db.close()


@pytest.mark.parametrize("actor_role,target_role", [
    ("coordinator", "technician"), ("admin", "technician"), ("admin", "coordinator"),
])
def test_reset_access_sends_code_to_the_person(client, make_user, sent, actor_role, target_role):
    actor = make_user(actor_role)
    target = make_user(target_role)
    response = client.post(f"/api/accounts/{target['id']}/reset-access", headers=actor["headers"])
    assert response.status_code == 202
    codes = _codes_to(sent, target["email"])
    assert len(codes) == 1
    # Quien lo pidió no recibe el código ni en la respuesta ni por correo
    assert codes[0] not in response.text
    assert all(to != actor["email"] for to, _, _ in sent)
    # La persona lo usa desde "Olvidé mi contraseña"
    confirm = _confirm(client, target["email"], "staff", codes[0])
    assert confirm.status_code == 200
    assert confirm.json()["password"] not in response.text


def test_reset_is_recorded_without_codes_or_passwords(client, make_user, sent):
    admin = make_user("admin")
    technician = make_user("technician")
    client.post(f"/api/accounts/{technician['id']}/reset-access", headers=admin["headers"])
    code = _codes_to(sent, technician["email"])[-1]
    password = _confirm(client, technician["email"], "staff", code).json()["password"]
    db = SessionLocal()
    try:
        rows = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == technician["id"])
            .order_by(models.AccountChange.id)
            .all()
        )
        assert [r.action for r in rows] == ["access_reset_forced", "password_reset"]
        assert rows[0].actor_id == admin["id"]
        text = " ".join(f"{r.old_value} {r.new_value} {r.target_label}" for r in rows)
        assert code not in text and password not in text
    finally:
        db.close()


def _reset_links(sent, email):
    return [m for to, _, body in sent if to == email for m in re.findall(r"\S+/#restablecer\?\S+", body)]


@pytest.mark.parametrize("role,portal", [("requester", "requester"), ("technician", "staff"), ("admin", "admin")])
def test_forgot_email_links_to_code_step(client, make_user, sent, role, portal):
    user = make_user(role)
    _request(client, user["email"], portal)
    links = _reset_links(sent, user["email"])
    assert len(links) == 1
    # El enlace abre el paso del código con la opción de inicio y el correo ya puestos
    params = parse_qs(links[0].split("#restablecer?", 1)[1])
    assert params == {"portal": [portal], "correo": [user["email"]]}


def test_forced_reset_email_links_to_code_step(client, make_user, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    client.post(f"/api/accounts/{technician['id']}/reset-access", headers=coordinator["headers"])
    links = _reset_links(sent, technician["email"])
    assert len(links) == 1
    assert parse_qs(links[0].split("#restablecer?", 1)[1]) == {"portal": ["staff"], "correo": [technician["email"]]}


def test_having_a_code_sends_no_new_email(client, make_user, sent):
    # "Ya tengo un código" solo muestra el paso del código: confirmar no pide otro correo
    requester = make_user("requester")
    _request(client, requester["email"], "requester")
    code = _codes_to(sent, requester["email"])[-1]
    before = len(sent)
    response = _confirm(client, requester["email"], "requester", code, "nueva-clave-456")
    assert response.status_code == 204
    assert len(sent) == before


@pytest.mark.parametrize("actor_role,target_role", [
    ("coordinator", "technician"), ("admin", "technician"), ("admin", "coordinator"),
])
def test_forced_reset_closes_sessions_and_disables_password(client, make_user, sent, actor_role, target_role):
    actor = make_user(actor_role)
    target = make_user(target_role)
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 200

    response = client.post(f"/api/accounts/{target['id']}/reset-access", headers=actor["headers"])
    assert response.status_code == 202
    # La sesión abierta deja de servir de inmediato
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 401
    assert _sessions(target["id"]) == 0
    # La contraseña vieja responde igual que una incorrecta
    old = client.post("/api/auth/login", json={"email": target["email"], "password": "clave-segura-123", "portal": "staff"})
    wrong = client.post("/api/auth/login", json={"email": target["email"], "password": "otra-cualquiera", "portal": "staff"})
    assert old.status_code == wrong.status_code == 401
    assert old.json()["detail"] == wrong.json()["detail"]

    # Con el código se crea la nueva y se puede entrar
    code = _codes_to(sent, target["email"])[-1]
    password = _confirm(client, target["email"], "staff", code).json()["password"]
    login = client.post("/api/auth/login", json={"email": target["email"], "password": password, "portal": "staff"})
    assert login.status_code == 200
    assert "token" in login.json()


def test_forced_reset_is_recorded(client, make_user, sent):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    client.post(f"/api/accounts/{technician['id']}/reset-access", headers=coordinator["headers"])
    response = client.get(
        "/api/admin/account-changes", params={"action": "access_reset_forced", "account": technician["email"]},
        headers=admin["headers"],
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    db = SessionLocal()
    try:
        row = db.query(models.AccountChange).filter(
            models.AccountChange.target_user_id == technician["id"],
            models.AccountChange.action == "access_reset_forced",
        ).one()
        assert row.actor_id == coordinator["id"]
    finally:
        db.close()
