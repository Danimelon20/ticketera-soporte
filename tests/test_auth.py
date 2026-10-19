import uuid
from datetime import datetime, timedelta, timezone

from app import models
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"
CODE_INVALID = "El código no es válido o venció. Puede pedir uno nuevo."


def _email():
    return f"cliente-{uuid.uuid4().hex}@example.com"


def _register(client, email, password=PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _verify(client, email, code):
    return client.post("/api/auth/register/verify", json={"email": email, "code": code})


def _login(client, email, password=PASSWORD, portal="requester"):
    return client.post("/api/auth/login", json={"email": email, "password": password, "portal": portal})


def _wrong(code):
    return f"{(int(code) + 1) % 10**6:06d}"


def _pending(email):
    db = SessionLocal()
    try:
        return db.query(models.PendingSignup).filter(models.PendingSignup.email == email).first()
    finally:
        db.close()


def _set_pending(email, **values):
    db = SessionLocal()
    try:
        pending = db.query(models.PendingSignup).filter(models.PendingSignup.email == email).first()
        for key, value in values.items():
            setattr(pending, key, value)
        db.commit()
    finally:
        db.close()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_register_sends_code_and_creates_no_account(client, sent):
    email = _email()
    response = _register(client, email)
    assert response.status_code == 202
    assert response.json() == {"email_hint": email[:2] + "***@example.com"}
    assert "token" not in response.json()
    assert len(sent) == 1 and sent[0][0] == email
    assert len(code_from(sent[0][2])) == 6
    assert PASSWORD not in sent[0][2]

    pending = _pending(email)
    code = code_from(sent[0][2])
    # Código y contraseña solo con hash
    assert code not in pending.code_hash and PASSWORD not in pending.password_hash
    db = SessionLocal()
    try:
        assert db.query(models.User).filter(models.User.email == email).first() is None
    finally:
        db.close()


def test_code_expires_in_15_minutes(client, sent):
    email = _email()
    _register(client, email)
    pending = _pending(email)
    minutes = (pending.expires_at - pending.sent_at).total_seconds() / 60
    assert minutes == 15


def test_unconfirmed_account_cannot_login(client, sent):
    email = _email()
    _register(client, email)
    response = _login(client, email)
    assert response.status_code == 403
    assert response.json()["detail"] == "Confirme su correo para iniciar sesión"
    # Con otra contraseña no se entera de nada
    response = _login(client, email, "otra-clave-123")
    assert response.status_code == 401
    assert response.json()["detail"] == "Correo o contraseña incorrectos"


def test_correct_code_creates_requester_and_session(client, sent):
    email = _email()
    _register(client, email)
    response = _verify(client, email, code_from(sent[0][2]))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["user"]["role"] == "requester"
    assert "password" not in data and "password_hash" not in data["user"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['token']}"}).json()
    assert me["profile_complete"] is False
    assert _pending(email) is None
    assert _login(client, email).status_code == 200


def test_wrong_code_is_rejected(client, sent):
    email = _email()
    _register(client, email)
    response = _verify(client, email, _wrong(code_from(sent[0][2])))
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID


def test_expired_code_is_rejected(client, sent):
    email = _email()
    _register(client, email)
    _set_pending(email, expires_at=_now() - timedelta(minutes=1))
    response = _verify(client, email, code_from(sent[0][2]))
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID


def test_more_than_five_attempts_is_rejected(client, sent):
    email = _email()
    _register(client, email)
    code = code_from(sent[0][2])
    for _ in range(5):
        assert _verify(client, email, _wrong(code)).status_code == 400
    response = _verify(client, email, code)
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID
    assert _login(client, email).status_code == 403


def test_unknown_email_same_message(client, sent):
    response = _verify(client, _email(), "123456")
    assert response.status_code == 400
    assert response.json()["detail"] == CODE_INVALID


def test_resend_waits_60_seconds_and_gives_new_code(client, sent):
    email = _email()
    _register(client, email)
    assert client.post("/api/auth/register/resend", json={"email": email}).status_code == 202
    assert len(sent) == 1

    _set_pending(email, sent_at=_now() - timedelta(seconds=61), attempts=4)
    assert client.post("/api/auth/register/resend", json={"email": email}).status_code == 202
    assert len(sent) == 2
    old_code, new_code = code_from(sent[0][2]), code_from(sent[1][2])
    assert _pending(email).attempts == 0
    if old_code != new_code:
        assert _verify(client, email, old_code).status_code == 400
    assert _verify(client, email, new_code).status_code == 200


def test_existing_email_gets_same_answer(client, make_user, sent):
    user = make_user("requester")
    new = _register(client, _email())
    existing = _register(client, user["email"])
    assert existing.status_code == new.status_code == 202
    assert existing.json().keys() == new.json().keys()
    # Al dueño le llega un aviso sin código y no se puede confirmar nada
    to, subject, body = sent[-1]
    assert to == user["email"] and subject == "Intento de crear una cuenta con su correo"
    assert _verify(client, user["email"], "000000").json()["detail"] == CODE_INVALID
    assert _login(client, user["email"], "clave-segura-123").status_code == 200


def test_register_short_password(client):
    response = _register(client, _email(), "123")
    assert response.status_code == 422


def test_register_never_gives_staff_role(client, sent):
    email = _email()
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "role": "coordinator", "invite_code": "COO-AAAA-BBBB"},
    )
    assert response.status_code == 202
    data = _verify(client, email, code_from(sent[-1][2])).json()
    assert data["user"]["role"] == "requester"


def test_login_incorrect_password(client, make_user):
    user = make_user("requester")
    response = _login(client, user["email"], "wrong-password")
    assert response.status_code == 401
    assert response.json()["detail"] == "Correo o contraseña incorrectos"


def test_login_nonexistent_email(client):
    response = _login(client, "noexiste@example.com")
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
