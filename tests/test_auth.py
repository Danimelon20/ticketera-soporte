"""Inicio de sesión: un solo formulario para el personal. El registro de clientes ya no existe y un contacto (rol
requester: quien reporta por correo y los clientes archivados) nunca entra: recibe el mismo error que una
contraseña incorrecta o un correo sin cuenta."""
from app import models
from app.database import SessionLocal
from app.security import hash_password, hash_token, new_token

PASSWORD = "clave-segura-123"
LOGIN_ERROR = "Correo o contraseña incorrectos"


def _login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_registration_no_longer_exists(client):
    for path in ("/api/auth/register", "/api/auth/register/verify", "/api/auth/register/resend"):
        response = client.post(path, json={"email": "cliente@example.com", "password": PASSWORD, "code": "123456"})
        assert response.status_code in (404, 405), path


def test_login_incorrect_password(client, make_user):
    user = make_user("technician")
    response = _login(client, user["email"], "wrong-password")
    assert response.status_code == 401
    assert response.json()["detail"] == LOGIN_ERROR


def test_login_nonexistent_email(client):
    response = _login(client, "noexiste@example.com")
    assert response.status_code == 401
    assert response.json()["detail"] == LOGIN_ERROR


def test_contact_with_the_right_password_gets_the_same_error(client, make_user):
    """No revela que la cuenta existe: el mismo 401 que un correo sin cuenta, también con la contraseña correcta."""
    contact = make_user("requester")
    with SessionLocal() as db:
        db.get(models.User, contact["id"]).password_hash = hash_password(PASSWORD)
        db.commit()
    for password in (PASSWORD, "otra-cualquiera"):
        response = _login(client, contact["email"], password)
        assert response.status_code == 401
        assert response.json()["detail"] == LOGIN_ERROR


def test_open_contact_session_stops_working(client, make_user):
    """Una sesión de cliente abierta antes de instalar deja de servir."""
    contact = make_user("requester")
    with SessionLocal() as db:
        token = new_token()
        db.add(models.Session(token=hash_token(token), user_id=contact["id"]))
        db.commit()
    for path in ("/api/auth/me", "/api/tickets"):
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Inicia sesión para continuar"


def test_auth_me_with_token(client, make_user):
    user = make_user("technician", "Me User")
    response = client.get("/api/auth/me", headers=user["headers"])
    assert response.status_code == 200
    assert response.json()["id"] == user["id"]
    assert response.json()["email"] == user["email"]


def test_auth_me_without_token(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Inicia sesión para continuar"


def test_logout_invalidates_token(client, make_user):
    user = make_user("technician", "Logout User")
    res1 = client.get("/api/auth/me", headers=user["headers"])
    assert res1.status_code == 200

    res_logout = client.post("/api/auth/logout", headers=user["headers"])
    assert res_logout.status_code == 204

    res2 = client.get("/api/auth/me", headers=user["headers"])
    assert res2.status_code == 401
    assert res2.json()["detail"] == "Inicia sesión para continuar"
