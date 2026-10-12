def test_create_user(client):
    payload = {"name": "Test User", "email": "test@example.com", "role": "technician", "password": "clave-segura-123"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "technician"


def test_create_user_duplicate_email(client):
    payload = {"name": "Dup User", "email": "dup@example.com", "role": "requester", "password": "clave-segura-123"}
    first = client.post("/api/auth/register", json=payload)
    assert first.status_code == 201
    second = client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["detail"] == "El correo ya está registrado"


def test_register_correct_returns_token_and_no_password(client):
    payload = {"name": "Token User", "email": "token@example.com", "role": "requester", "password": "clave-segura-123"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert "token" in data
    assert isinstance(data["token"], str)
    assert "password" not in data
    assert "password_hash" not in data
    assert "password" not in data["user"]
    assert "password_hash" not in data["user"]


def test_register_short_password(client):
    payload = {"name": "Short Pwd", "email": "short@example.com", "role": "requester", "password": "123"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


def test_register_without_role(client):
    payload = {"name": "Sin Rol", "email": "sinrol@example.com", "password": "clave-segura-123"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


def test_register_invalid_role(client):
    payload = {"name": "Rol Raro", "email": "rolraro@example.com", "role": "jefe", "password": "clave-segura-123"}
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


def test_login_correct(client):
    email = "loginok@example.com"
    payload = {"name": "Login Ok", "email": email, "role": "technician", "password": "clave-segura-123"}
    client.post("/api/auth/register", json=payload)

    login_payload = {"email": email, "password": "clave-segura-123"}
    response = client.post("/api/auth/login", json=login_payload)
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["user"]["email"] == email


def test_login_incorrect_password(client):
    email = "loginbad@example.com"
    payload = {"name": "Login Bad", "email": email, "role": "technician", "password": "clave-segura-123"}
    client.post("/api/auth/register", json=payload)

    login_payload = {"email": email, "password": "wrong-password"}
    response = client.post("/api/auth/login", json=login_payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Correo o contraseña incorrectos"


def test_login_nonexistent_email(client):
    login_payload = {"email": "noexiste@example.com", "password": "clave-segura-123"}
    response = client.post("/api/auth/login", json=login_payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Correo o contraseña incorrectos"


def test_auth_me_with_token(client, make_user):
    user = make_user("requester", "Me User")
    response = client.get("/api/auth/me", headers=user["headers"])
    assert response.status_code == 200
    assert response.json()["id"] == user["id"]
    assert response.json()["email"] == user["email"]


def test_auth_me_without_token(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Inicia sesión para continuar"


def test_logout_invalidates_token(client, make_user):
    user = make_user("requester", "Logout User")
    res1 = client.get("/api/auth/me", headers=user["headers"])
    assert res1.status_code == 200

    res_logout = client.post("/api/auth/logout", headers=user["headers"])
    assert res_logout.status_code == 204

    res2 = client.get("/api/auth/me", headers=user["headers"])
    assert res2.status_code == 401
    assert res2.json()["detail"] == "Inicia sesión para continuar"
