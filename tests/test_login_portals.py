import uuid

import pytest

from conftest import code_from

PASSWORD = "clave-segura-123"
GENERIC = "Correo o contraseña incorrectos"
PORTALS = ("admin", "staff", "requester")
OWN_PORTAL = {"admin": "admin", "coordinator": "staff", "technician": "staff", "requester": "requester"}


def _login(client, email, portal, password=PASSWORD):
    payload = {"email": email, "password": password}
    if portal is not None:
        payload["portal"] = portal
    return client.post("/api/auth/login", json=payload)


@pytest.mark.parametrize("role", ["requester", "technician", "coordinator"])
def test_each_role_enters_by_its_option(client, make_user, sent, role):
    user = make_user(role)
    response = _login(client, user["email"], OWN_PORTAL[role])
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == role
    assert sent == []


def test_admin_enters_by_its_option_and_keeps_code(client, make_user, sent):
    admin = make_user("admin")
    response = _login(client, admin["email"], "admin")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requires_code"] is True and "token" not in data
    assert len(sent) == 1


@pytest.mark.parametrize("role, portal", [
    (role, portal)
    for role in ("admin", "coordinator", "technician", "requester")
    for portal in PORTALS
    if portal != OWN_PORTAL[role]
])
def test_wrong_option_is_rejected_like_a_wrong_password(client, make_user, sent, role, portal):
    user = make_user(role)
    response = _login(client, user["email"], portal)
    assert response.status_code == 401
    assert response.json() == {"detail": GENERIC}
    # Sin sesión, sin reto y sin correo: nada indica que la cuenta existe
    assert sent == []

    wrong_password = _login(client, user["email"], OWN_PORTAL[role], "otra-clave-999")
    missing = _login(client, f"nadie-{uuid.uuid4().hex}@test.com", portal)
    assert response.json() == wrong_password.json() == missing.json()
    assert response.status_code == wrong_password.status_code == missing.status_code


@pytest.mark.parametrize("portal", [None, "", "superuser", "ADMIN", "agent"])
def test_login_without_valid_option_is_rejected(client, make_user, sent, portal):
    for role in ("admin", "coordinator", "requester"):
        user = make_user(role)
        response = _login(client, user["email"], portal)
        assert response.status_code == 401
        assert response.json() == {"detail": GENERIC}
    assert sent == []


def test_agent_cannot_enter_by_any_option(client, make_user, sent):
    agent = make_user("agent")
    for portal in PORTALS:
        response = _login(client, agent["email"], portal)
        assert response.status_code == 401
        assert response.json() == {"detail": GENERIC}


def test_unconfirmed_account_only_hears_about_it_as_requester(client, sent):
    email = f"cliente-{uuid.uuid4().hex}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert code_from(sent[-1][2])

    response = _login(client, email, "requester")
    assert response.status_code == 403
    assert response.json()["detail"] == "Confirme su correo para iniciar sesión"
    for portal in ("staff", "admin", None):
        response = _login(client, email, portal)
        assert response.status_code == 401
        assert response.json() == {"detail": GENERIC}
