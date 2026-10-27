"""Inicio de sesión del personal con Microsoft (MSAL simulado: nunca se conecta a Microsoft)."""
import logging
import os
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import msal
import pytest

from app import config, entra_login, models
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"
TENANT = "inquilino-de-prueba"
CLIENT_ID = "app-de-prueba"
STAFF_USE_MICROSOFT = "El personal entra con su cuenta Microsoft"


class FakeMsalApp:
    """Lo mínimo de ConfidentialClientApplication: anota los scopes y devuelve los claims de la prueba."""

    def __init__(self, ms):
        self.ms = ms

    def initiate_auth_code_flow(self, scopes, redirect_uri=None, **kwargs):
        self.ms.scopes.append(list(scopes))
        state = uuid.uuid4().hex
        return {
            "state": state, "nonce": "nonce", "code_verifier": "verifier", "redirect_uri": redirect_uri,
            "auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={state}",
        }

    def acquire_token_by_auth_code_flow(self, flow, params):
        if params.get("state") != flow["state"]:
            raise ValueError("state mismatch")
        return {"id_token_claims": dict(self.ms.claims)}


class MsState:
    def __init__(self):
        self.scopes = []
        self.claims = {}


@pytest.fixture
def ms(client, monkeypatch):
    state = MsState()
    monkeypatch.setattr(config, "MS_LOGIN_ENABLED", True)
    monkeypatch.setattr(config, "MS_LOGIN_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "MS_LOGIN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(entra_login, "build_app", lambda: FakeMsalApp(state))
    client.cookies.clear()
    return state


def _claims(email, oid=None, tid=TENANT, aud=CLIENT_ID):
    return {"oid": oid or uuid.uuid4().hex, "preferred_username": email, "tid": tid, "aud": aud}


def _fragment(response):
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith("/#microsoft?")
    return {k: v[0] for k, v in parse_qs(location.split("?", 1)[1]).items()}


def _go_to_microsoft(client, portal="staff"):
    response = client.get("/auth/microsoft/login", params={"portal": portal}, follow_redirects=False)
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def _callback(client, state):
    response = client.get(
        "/auth/microsoft/callback", params={"code": "codigo-de-microsoft", "state": state}, follow_redirects=False
    )
    return _fragment(response)


def _microsoft_login(client, portal="staff"):
    return _callback(client, _go_to_microsoft(client, portal))


def _exchange(client, code):
    return client.post("/api/auth/microsoft/exchange", json={"code": code})


def _last_change(action):
    db = SessionLocal()
    try:
        return (
            db.query(models.AccountChange)
            .filter(models.AccountChange.action == action)
            .order_by(models.AccountChange.id.desc())
            .first()
        )
    finally:
        db.close()


def _link_for(user_id):
    db = SessionLocal()
    try:
        return db.query(models.UserMicrosoftLink).filter(models.UserMicrosoftLink.user_id == user_id).first()
    finally:
        db.close()


def _set_user(user_id, **values):
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        for key, value in values.items():
            setattr(user, key, value)
        db.commit()
    finally:
        db.close()


# ----- Ingreso correcto -----

def test_first_login_links_by_email_then_by_oid(client, make_user, ms):
    user = make_user("technician")
    oid = uuid.uuid4().hex
    ms.claims = _claims(user["email"].upper(), oid=oid)

    result = _microsoft_login(client)
    assert "error" not in result
    response = _exchange(client, result["code"])
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["id"] == user["id"]

    link = _link_for(user["id"])
    assert link is not None and link.oid == oid
    assert _last_change("microsoft_linked").target_user_id == user["id"]
    assert _last_change("microsoft_login").target_user_id == user["id"]

    # Segundo ingreso: por oid, aunque Microsoft ya traiga otro correo
    ms.claims = _claims("otro-correo@ejemplo.com", oid=oid)
    result = _microsoft_login(client)
    response = _exchange(client, result["code"])
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == user["id"]


@pytest.mark.parametrize("role,portal", [("coordinator", "staff"), ("admin", "admin")])
def test_coordinator_and_admin_enter_with_microsoft(client, make_user, ms, role, portal):
    user = make_user(role)
    ms.claims = _claims(user["email"])
    response = _exchange(client, _microsoft_login(client, portal)["code"])
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == role


# ----- Rechazos -----

def _assert_rejected(result, reason, label_must_not_contain=None):
    assert result["error"] == reason
    assert "code" not in result
    change = _last_change("microsoft_login_rejected")
    assert change.new_value == entra_login.REJECT_REASONS[reason]
    if label_must_not_contain:
        assert label_must_not_contain not in (change.target_label or "")
    return change


def test_invalid_state_is_rejected(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    _go_to_microsoft(client)
    _assert_rejected(_callback(client, "state-inventado"), "state")
    assert _link_for(user["id"]) is None


def test_state_from_another_browser_is_rejected(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    state = _go_to_microsoft(client)
    client.cookies.clear()
    _assert_rejected(_callback(client, state), "state")


def test_state_works_only_once(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    state = _go_to_microsoft(client)
    assert "code" in _callback(client, state)
    _assert_rejected(_callback(client, state), "state")


def test_other_tenant_is_rejected(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"], tid="otro-inquilino")
    _assert_rejected(_microsoft_login(client), "tenant", label_must_not_contain=user["email"])
    assert _link_for(user["id"]) is None


def test_other_audience_is_rejected(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"], aud="otra-app")
    _assert_rejected(_microsoft_login(client), "failed")


def test_unknown_email_is_rejected_without_full_email(client, ms):
    email = f"desconocido-{uuid.uuid4().hex}@ejemplo.com"
    ms.claims = _claims(email)
    change = _assert_rejected(_microsoft_login(client), "not_staff", label_must_not_contain=email)
    assert change.target_label and "***" in change.target_label


def test_requester_account_is_rejected(client, make_user, ms):
    user = make_user("requester")
    ms.claims = _claims(user["email"])
    change = _assert_rejected(_microsoft_login(client), "requester")
    assert change.target_user_id == user["id"]
    assert _link_for(user["id"]) is None


def test_deactivated_account_is_rejected(client, make_user, ms):
    user = make_user("technician")
    _set_user(user["id"], is_active=False)
    ms.claims = _claims(user["email"])
    _assert_rejected(_microsoft_login(client), "inactive")


def test_staff_portal_does_not_let_admin_in(client, make_user, ms):
    user = make_user("admin")
    ms.claims = _claims(user["email"])
    _assert_rejected(_microsoft_login(client, "staff"), "wrong_portal")


def test_account_linked_to_another_oid_is_rejected(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    assert "code" in _microsoft_login(client)
    ms.claims = _claims(user["email"])  # mismo correo, otro oid
    _assert_rejected(_microsoft_login(client), "already_linked")


def test_microsoft_cancelled_is_rejected(client, make_user, ms, monkeypatch):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    monkeypatch.setattr(
        FakeMsalApp, "acquire_token_by_auth_code_flow", lambda self, flow, params: {"error": "access_denied"}
    )
    _assert_rejected(_microsoft_login(client), "failed")


# ----- Código de canje -----

def test_exchange_code_is_single_use(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    code = _microsoft_login(client)["code"]
    browser = client.cookies.get(entra_login.COOKIE_NAME)
    assert _exchange(client, code).status_code == 200
    # Mismo navegador (la cookie se borra al canjear): falla solo porque el código ya se usó
    client.cookies.set(entra_login.COOKIE_NAME, browser)
    second = _exchange(client, code)
    assert second.status_code == 400
    assert "token" not in second.json()


def test_exchange_code_expires_after_60_seconds(client, make_user, ms, monkeypatch):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    code = _microsoft_login(client)["code"]
    real_now = entra_login._now
    monkeypatch.setattr(entra_login, "_now", lambda: real_now() + timedelta(seconds=61))
    assert _exchange(client, code).status_code == 400


def test_exchange_code_from_another_browser_fails(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    code = _microsoft_login(client)["code"]
    client.cookies.clear()
    assert _exchange(client, code).status_code == 400


def test_only_the_code_hash_is_stored(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    code = _microsoft_login(client)["code"]
    db = SessionLocal()
    try:
        stored = [row.code_hash for row in db.query(models.MicrosoftLoginHandoff)]
    finally:
        db.close()
    assert code not in stored


# ----- Permisos que pide y aislamiento del buzón -----

def test_login_never_asks_for_mail_permissions(client, make_user, ms):
    user = make_user("technician")
    ms.claims = _claims(user["email"])
    _microsoft_login(client)
    assert ms.scopes
    for scopes in ms.scopes:
        assert set(scopes) == {"User.Read", "email"}
        assert not any(scope.lower().startswith("mail.") for scope in scopes)


def test_msal_instance_is_separate_from_mailbox(monkeypatch):
    created = []

    class Recorder:
        def __init__(self, client_id, **kwargs):
            created.append((client_id, kwargs))

    monkeypatch.setattr(config, "MS_LOGIN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(config, "MS_LOGIN_TENANT_ID", TENANT)
    monkeypatch.setattr(msal, "ConfidentialClientApplication", Recorder)
    entra_login.build_app()
    entra_login.build_app()

    assert len(created) == 2
    (client_id, first), (_, second) = created
    assert client_id == CLIENT_ID
    assert first["exclude_scopes"] == ["offline_access"]
    # Caché solo en memoria, una por instancia; nunca la del buzón
    assert type(first["token_cache"]) is msal.TokenCache
    assert first["token_cache"] is not second["token_cache"]
    assert not os.path.exists(config.MS_TOKEN_CACHE)


def test_callback_query_is_not_in_access_log():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/auth/microsoft/callback?code=secreto&state=abc", "1.1", 302), None,
    )
    entra_login.RedactCallbackQuery().filter(record)
    message = record.getMessage()
    assert "secreto" not in message and "abc" not in message
    assert "/auth/microsoft/callback" in message


# ----- Contraseñas del personal con la bandera encendida -----

@pytest.mark.parametrize("role", ["technician", "coordinator"])
def test_staff_password_login_is_blocked(client, make_user, ms, role):
    user = make_user(role)
    response = client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD, "portal": "staff"})
    assert response.status_code == 403
    assert response.json()["detail"] == STAFF_USE_MICROSOFT


def test_staff_password_reset_is_blocked(client, make_user, ms, sent):
    user = make_user("technician")
    for path in ("/api/auth/password-reset", "/api/auth/password-reset/resend"):
        response = client.post(path, json={"email": user["email"], "portal": "staff"})
        assert response.status_code == 403
    response = client.post(
        "/api/auth/password-reset/confirm", json={"email": user["email"], "portal": "staff", "code": "123456"}
    )
    assert response.status_code == 403
    assert sent == []


def test_admin_cannot_force_staff_password_reset(client, make_user, ms, sent):
    admin = make_user("admin")
    technician = make_user("technician")
    response = client.post(f"/api/accounts/{technician['id']}/reset-access", headers=admin["headers"])
    assert response.status_code == 403
    assert response.json()["detail"] == STAFF_USE_MICROSOFT
    assert sent == []


def test_staff_cannot_change_password(client, make_user, ms):
    user = make_user("coordinator")
    response = client.put(
        "/api/account/password",
        json={"current_password": PASSWORD, "new_password": "otra-clave-muy-segura"},
        headers=user["headers"],
    )
    assert response.status_code == 403


def test_requester_login_unchanged(client, make_user, ms):
    user = make_user("requester")
    response = client.post(
        "/api/auth/login", json={"email": user["email"], "password": PASSWORD, "portal": "requester"}
    )
    assert response.status_code == 200
    assert "token" in response.json()


def test_admin_emergency_access_is_recorded_and_notified(client, make_user, ms, sent):
    admin = make_user("admin", name="Admin Emergencia")
    other = make_user("admin", name="Otro Admin")
    response = client.post("/api/auth/login", json={"email": admin["email"], "password": PASSWORD, "portal": "admin"})
    assert response.status_code == 200, response.text
    challenge = response.json()["challenge_id"]
    response = client.post("/api/auth/verify-code", json={"challenge_id": challenge, "code": code_from(sent[-1][2])})
    assert response.status_code == 200, response.text
    assert "token" in response.json()

    change = _last_change("emergency_access")
    assert change.actor_id == admin["id"] and change.target_user_id == admin["id"]
    notices = [(to, body) for to, subject, body in sent if subject == "Acceso de emergencia a la Ticketera"]
    recipients = [to for to, _ in notices]
    assert other["email"] in recipients
    assert admin["email"] not in recipients
    # El correo del administrador va enmascarado
    assert all(admin["email"] not in body for _, body in notices)


def test_admin_login_without_flag_is_not_emergency(client, make_user, sent):
    admin = make_user("admin")
    make_user("admin")
    before = _last_change("emergency_access")
    response = client.post("/api/auth/login", json={"email": admin["email"], "password": PASSWORD, "portal": "admin"})
    challenge = response.json()["challenge_id"]
    response = client.post("/api/auth/verify-code", json={"challenge_id": challenge, "code": code_from(sent[-1][2])})
    assert response.status_code == 200
    after = _last_change("emergency_access")
    assert (before.id if before else None) == (after.id if after else None)
    assert not any(subject == "Acceso de emergencia a la Ticketera" for _, subject, _ in sent)


# ----- Invitación: activar con Microsoft -----

def _invite(client, admin, sent, role="technician"):
    email = f"nuevo-{uuid.uuid4().hex}@test.com"
    response = client.post(
        "/api/staff-invitations", json={"email": email, "name": "Persona Nueva", "role": role}, headers=admin["headers"]
    )
    assert response.status_code == 201, response.text
    return email, code_from(sent[-1][2])


def _start_activation(client, email, code):
    response = client.post("/api/auth/activate/microsoft", json={"email": email, "code": code})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["auth_url"]).query)["state"][0]


def test_activation_with_microsoft_creates_linked_account(client, make_user, ms, sent):
    admin = make_user("admin")
    email, code = _invite(client, admin, sent)
    assert "cuenta Microsoft" in sent[-1][2]
    oid = uuid.uuid4().hex
    ms.claims = _claims(email.upper(), oid=oid)

    result = _callback(client, _start_activation(client, email, code))
    response = _exchange(client, result["code"])
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["email"] == email and user["role"] == "technician"
    assert _link_for(user["id"]).oid == oid

    db = SessionLocal()
    try:
        invitation = db.query(models.StaffInvitation).filter(models.StaffInvitation.email == email).first()
        assert invitation.used_by == user["id"]
    finally:
        db.close()


def test_activation_with_another_microsoft_email_is_rejected(client, make_user, ms, sent):
    admin = make_user("admin")
    email, code = _invite(client, admin, sent)
    ms.claims = _claims(f"otra-{uuid.uuid4().hex}@test.com")
    _assert_rejected(_callback(client, _start_activation(client, email, code)), "invitation_email")
    db = SessionLocal()
    try:
        assert db.query(models.User).filter(models.User.email == email).first() is None
    finally:
        db.close()


def test_activation_wrong_code_is_rejected(client, make_user, ms, sent):
    admin = make_user("admin")
    email, code = _invite(client, admin, sent)
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    response = client.post("/api/auth/activate/microsoft", json={"email": email, "code": wrong})
    assert response.status_code == 400


def test_password_activation_is_blocked(client, make_user, ms, sent):
    admin = make_user("admin")
    email, code = _invite(client, admin, sent)
    response = client.post("/api/auth/activate", json={"email": email, "code": code})
    assert response.status_code == 403


# ----- Con la bandera apagada todo sigue como hoy -----

def test_flag_off_disables_microsoft_routes(client):
    assert config.MS_LOGIN_ENABLED is False
    assert client.get("/api/auth/microsoft/config").json() == {"enabled": False}
    assert client.get("/auth/microsoft/login", follow_redirects=False).status_code == 404
    assert client.get("/auth/microsoft/callback", follow_redirects=False).status_code == 404
    assert client.post("/api/auth/microsoft/exchange", json={"code": "x"}).status_code == 404
    assert client.post("/api/auth/activate/microsoft", json={"email": "a@b.com", "code": "1"}).status_code == 404


def test_flag_off_staff_password_login_works(client, make_user):
    user = make_user("technician")
    response = client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD, "portal": "staff"})
    assert response.status_code == 200
    assert "token" in response.json()
