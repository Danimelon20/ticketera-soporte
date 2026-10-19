import hashlib
from datetime import datetime, timedelta, timezone

from app import models
from app.database import SessionLocal
from app.security import new_token

PASSWORD = "clave-segura-123"
EXPIRED = "Su sesión venció. Inicie sesión de nuevo."


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _login(client, user, portal="staff"):
    response = client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD, "portal": portal})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _set_times(token, created_at=None, last_used_at=None):
    db = SessionLocal()
    try:
        row = db.query(models.Session).filter(models.Session.token == _sha(token)).first()
        if created_at is not None:
            row.created_at = created_at
        if last_used_at is not None:
            activity = db.query(models.SessionActivity).filter(models.SessionActivity.session_id == row.id).first()
            activity.last_used_at = last_used_at
        db.commit()
    finally:
        db.close()


def _me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_session_expires_after_idle_minutes(client, make_user):
    token = _login(client, make_user("technician"))
    _set_times(token, last_used_at=_now() - timedelta(minutes=239))
    assert _me(client, token).status_code == 200
    _set_times(token, last_used_at=_now() - timedelta(minutes=241))
    response = _me(client, token)
    assert response.status_code == 401
    assert response.json()["detail"] == EXPIRED
    # La sesión se borró: el token ya no sirve
    assert _me(client, token).status_code == 401


def test_session_expires_after_max_hours_even_if_used(client, make_user):
    token = _login(client, make_user("coordinator"))
    _set_times(token, created_at=_now() - timedelta(hours=11, minutes=59), last_used_at=_now())
    assert _me(client, token).status_code == 200
    _set_times(token, created_at=_now() - timedelta(hours=12, minutes=1), last_used_at=_now())
    response = _me(client, token)
    assert response.status_code == 401
    assert response.json()["detail"] == EXPIRED


def test_use_keeps_session_alive(client, make_user):
    token = _login(client, make_user("technician"))
    _set_times(token, last_used_at=_now() - timedelta(minutes=200))
    assert _me(client, token).status_code == 200
    db = SessionLocal()
    try:
        row = db.query(models.Session).filter(models.Session.token == _sha(token)).first()
        activity = db.query(models.SessionActivity).filter(models.SessionActivity.session_id == row.id).first()
        assert _now() - activity.last_used_at < timedelta(minutes=1)
    finally:
        db.close()


def test_logout_invalidates_token_on_server(client, make_user):
    token = _login(client, make_user("technician"))
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert _me(client, token).status_code == 401
    db = SessionLocal()
    try:
        assert db.query(models.Session).filter(models.Session.token == _sha(token)).first() is None
    finally:
        db.close()


def test_token_is_stored_hashed(client, make_user):
    user = make_user("requester")
    token = _login(client, user, "requester")
    db = SessionLocal()
    try:
        tokens = [row.token for row in db.query(models.Session).all()]
    finally:
        db.close()
    assert token not in tokens
    assert all(token not in stored for stored in tokens)
    assert _sha(token) in tokens


def test_session_saved_in_plain_text_no_longer_works(client, make_user):
    user = make_user("technician")
    token = new_token()
    db = SessionLocal()
    try:
        db.add(models.Session(token=token, user_id=user["id"]))
        db.commit()
    finally:
        db.close()
    assert _me(client, token).status_code == 401
