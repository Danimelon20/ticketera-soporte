import uuid
from datetime import datetime, timedelta, timezone

from app import models
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"
LOCKED = "Demasiados intentos. Intente de nuevo en unos minutos."
GENERIC = "Correo o contraseña incorrectos"
PORTALS = {"admin": "admin", "coordinator": "staff", "technician": "staff", "requester": "requester"}


def _login(client, email, portal, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password, "portal": portal})


def _fail(client, email, portal, times):
    return [_login(client, email, portal, "otra-clave-999") for _ in range(times)]


def _unlock_now(email):
    db = SessionLocal()
    try:
        row = db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).first()
        row.locked_until = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()


def _locked_until(email):
    db = SessionLocal()
    try:
        return db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).first().locked_until
    finally:
        db.close()


def test_five_wrong_passwords_lock_for_15_minutes(client, make_user, sent):
    admin = make_user("admin")
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    responses = _fail(client, admin["email"], "admin", 5)
    assert [r.status_code for r in responses] == [401, 401, 401, 401, 429]
    assert responses[-1].json() == {"detail": LOCKED}
    lock = _locked_until(admin["email"])
    assert timedelta(minutes=14, seconds=59) <= lock - before <= timedelta(minutes=15, seconds=5)

    # Con la contraseña correcta sigue bloqueado: sin reto y sin correo
    response = _login(client, admin["email"], "admin")
    assert response.status_code == 429
    assert response.json() == {"detail": LOCKED}
    assert sent == []

    _unlock_now(admin["email"])
    response = _login(client, admin["email"], "admin")
    assert response.status_code == 200
    assert response.json()["requires_code"] is True


def test_lock_message_is_the_same_whether_account_exists_or_not(client, make_user):
    user = make_user("requester")
    missing = f"nadie-{uuid.uuid4().hex}@test.com"
    existing_responses = _fail(client, user["email"], "requester", 6)
    missing_responses = _fail(client, missing, "requester", 6)
    for a, b in zip(existing_responses, missing_responses):
        assert a.status_code == b.status_code
        assert a.json() == b.json()
    assert existing_responses[-1].json() == {"detail": LOCKED}
    assert _login(client, user["email"], "requester").json() == _login(client, missing, "requester").json()


def test_wrong_option_counts_as_failure(client, make_user):
    user = make_user("technician")
    for _ in range(4):
        assert _login(client, user["email"], "requester").status_code == 401
    assert _login(client, user["email"], "admin").status_code == 429
    assert _login(client, user["email"], "staff").status_code == 429


def test_successful_login_resets_counter(client, make_user):
    user = make_user("coordinator")
    assert all(r.status_code == 401 for r in _fail(client, user["email"], "staff", 4))
    assert _login(client, user["email"], "staff").status_code == 200
    assert all(r.status_code == 401 for r in _fail(client, user["email"], "staff", 4))
    assert _login(client, user["email"], "staff").status_code == 200


def test_admin_code_failures_share_the_counter(client, make_user, sent):
    admin = make_user("admin")
    assert all(r.status_code == 401 for r in _fail(client, admin["email"], "admin", 3))
    challenge = _login(client, admin["email"], "admin").json()["challenge_id"]
    wrong = f"{(int(code_from(sent[-1][2])) + 1) % 10**6:06d}"
    first = client.post("/api/auth/verify-code", json={"challenge_id": challenge, "code": wrong})
    assert first.status_code == 400
    second = client.post("/api/auth/verify-code", json={"challenge_id": challenge, "code": wrong})
    assert second.status_code == 429
    assert second.json() == {"detail": LOCKED}
    # Ni con el código correcto ni con la contraseña
    right = client.post("/api/auth/verify-code", json={"challenge_id": challenge, "code": code_from(sent[-1][2])})
    assert right.status_code == 429
    assert _login(client, admin["email"], "admin").status_code == 429


def test_email_change_code_failures_share_the_counter(client, make_user, sent):
    user = make_user("requester")
    assert all(r.status_code == 401 for r in _fail(client, user["email"], "requester", 3))
    client.post(
        "/api/account/email", json={"current_password": PASSWORD, "new_email": f"n-{uuid.uuid4().hex}@example.com"},
        headers=user["headers"],
    )
    wrong = f"{(int(code_from(sent[-1][2])) + 1) % 10**6:06d}"
    assert client.post("/api/account/email/verify", json={"code": wrong}, headers=user["headers"]).status_code == 400
    response = client.post("/api/account/email/verify", json={"code": wrong}, headers=user["headers"])
    assert response.status_code == 429
    assert response.json() == {"detail": LOCKED}
    assert _login(client, user["email"], "requester").status_code == 429


def test_email_change_wrong_password_counts_toward_lock(client, make_user, sent):
    user = make_user("technician")
    assert all(r.status_code == 401 for r in _fail(client, user["email"], "staff", 4))
    response = client.post(
        "/api/account/email", json={"current_password": "otra-clave-999", "new_email": "x@example.com"},
        headers=user["headers"],
    )
    assert response.status_code == 429
    assert sent == []
    assert _login(client, user["email"], "staff").status_code == 429
