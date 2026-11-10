"""Un solo formulario de inicio de sesión: el rol sale de la cuenta, sin pestañas.

Las protecciones siguen: bloqueo de 15 minutos tras 5 fallos, 401 igual en todos los casos de error,
código por correo del administrador y, con Microsoft, el personal sin contraseña."""
import uuid

import pytest

from app import config, models, throttle
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"
GENERIC = "Correo o contraseña incorrectos"


def _login(client, email, password=PASSWORD, **extra):
    return client.post("/api/auth/login", json={"email": email, "password": password, **extra})


def _me(client, token):
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("role", ["technician", "coordinator", "requester"])
def test_each_role_enters_by_the_same_form(client, make_user, sent, role):
    user = make_user(role)
    response = _login(client, user["email"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["user"]["role"] == role
    # Con el rol de /auth/me la página decide la pantalla de inicio
    assert _me(client, data["token"])["role"] == role
    assert sent == []


def test_admin_enters_by_the_same_form_with_code(client, make_user, sent):
    admin = make_user("admin")
    response = _login(client, admin["email"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requires_code"] is True and "token" not in data
    assert [to for to, _, _ in sent] == [admin["email"]]

    verified = client.post("/api/auth/verify-code", json={"challenge_id": data["challenge_id"],
                                                          "code": code_from(sent[-1][2])})
    assert verified.status_code == 200, verified.text
    assert _me(client, verified.json()["token"])["role"] == "admin"


@pytest.mark.parametrize("legacy_portal", ["admin", "staff", "requester", "", "cualquiera"])
def test_old_page_sending_portal_is_ignored(client, make_user, sent, legacy_portal):
    """Compatibilidad: una página vieja en caché que todavía envía la pestaña entra igual."""
    for role in ("technician", "coordinator", "requester"):
        user = make_user(role)
        response = _login(client, user["email"], portal=legacy_portal)
        assert response.status_code == 200, (role, response.text)
        assert response.json()["user"]["role"] == role


def test_errors_answer_the_same(client, make_user, sent):
    user = make_user("technician")
    inactive = make_user("coordinator")
    db = SessionLocal()
    try:
        db.get(models.User, inactive["id"]).is_active = False
        db.commit()
        # El Agente virtual que crea la app al arrancar (no se crea otro: debe haber uno solo)
        agent_email = db.query(models.User).filter(models.User.role == "agent").one().email
    finally:
        db.close()
    responses = [
        _login(client, user["email"], "otra-clave-999"),
        _login(client, f"nadie-{uuid.uuid4().hex}@test.com"),
        _login(client, inactive["email"]),
        _login(client, agent_email),
    ]
    assert {r.status_code for r in responses} == {401}
    assert all(r.json() == {"detail": GENERIC} for r in responses)
    assert sent == []


def test_five_failures_lock_for_fifteen_minutes(client, make_user, sent):
    user = make_user("coordinator")
    for _ in range(throttle.MAX_FAILURES - 1):
        assert _login(client, user["email"], "otra-clave-999").status_code == 401
    assert _login(client, user["email"], "otra-clave-999").status_code == 429
    # Bloqueado: ni la contraseña correcta entra
    locked = _login(client, user["email"])
    assert locked.status_code == 429
    assert locked.json()["detail"] == throttle.LOCK_MESSAGE
    assert throttle.LOCK_MINUTES == 15


def test_unconfirmed_signup_hears_it_only_with_the_right_password(client, sent):
    email = f"cliente-{uuid.uuid4().hex}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert code_from(sent[-1][2])
    response = _login(client, email)
    assert response.status_code == 403
    assert response.json()["detail"] == "Confirme su correo para iniciar sesión"
    wrong = _login(client, email, "otra-clave-999")
    assert wrong.status_code == 401 and wrong.json() == {"detail": GENERIC}


# ----- Con MS_LOGIN_ENABLED: el personal entra solo con Microsoft -----

@pytest.mark.parametrize("role", ["technician", "coordinator"])
def test_staff_password_blocked_with_microsoft(client, make_user, sent, monkeypatch, role):
    monkeypatch.setattr(config, "MS_LOGIN_ENABLED", True)
    user = make_user(role)
    right = _login(client, user["email"])
    assert right.status_code == 403
    assert right.json()["detail"] == "El personal entra con su cuenta Microsoft"
    # Con una contraseña incorrecta no se entera de nada más
    wrong = _login(client, user["email"], "otra-clave-999")
    assert wrong.status_code == 401 and wrong.json() == {"detail": GENERIC}
    assert sent == []


def test_requester_and_admin_still_use_password_with_microsoft(client, make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "MS_LOGIN_ENABLED", True)
    requester = make_user("requester")
    assert _login(client, requester["email"]).status_code == 200
    # Acceso de emergencia del administrador: contraseña y código
    admin = make_user("admin")
    response = _login(client, admin["email"])
    assert response.status_code == 200 and response.json()["requires_code"] is True


# ----- Restablecimiento: 202 igual para todos y 60 segundos entre envíos -----

def test_reset_same_answer_and_resend_wait(client, make_user, sent):
    user = make_user("technician")
    responses = [
        client.post("/api/auth/password-reset", json={"email": user["email"]}),
        client.post("/api/auth/password-reset", json={"email": f"nadie-{uuid.uuid4().hex}@test.com"}),
    ]
    assert {r.status_code for r in responses} == {202}
    assert len({tuple(sorted(r.json())) for r in responses}) == 1
    assert [to for to, _, _ in sent] == [user["email"]]
    # Antes de 60 segundos no sale otro código
    assert client.post("/api/auth/password-reset", json={"email": user["email"]}).status_code == 202
    assert client.post("/api/auth/password-reset/resend", json={"email": user["email"]}).status_code == 202
    assert len(sent) == 1


def test_reset_link_has_no_tab(client, make_user, sent):
    user = make_user("coordinator")
    client.post("/api/auth/password-reset", json={"email": user["email"]})
    body = sent[-1][2]
    assert "#restablecer?correo=" in body and "portal=" not in body


def test_reset_for_staff_sends_nothing_with_microsoft(client, make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "MS_LOGIN_ENABLED", True)
    user = make_user("technician")
    for path in ("/api/auth/password-reset", "/api/auth/password-reset/resend"):
        assert client.post(path, json={"email": user["email"]}).status_code == 202
    confirm = client.post("/api/auth/password-reset/confirm", json={"email": user["email"], "code": "123456"})
    assert confirm.status_code == 400
    assert sent == []
