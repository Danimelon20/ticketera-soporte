import uuid
from datetime import datetime, timedelta, timezone

from app import availability, models
from app.database import SessionLocal


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rows(client, admin, **params):
    response = client.get("/api/admin/accounts", params=params, headers=admin["headers"])
    assert response.status_code == 200, response.text
    return response


def _by_id(rows, user_id, kind="user"):
    return next(r for r in rows if r["kind"] == kind and r["id"] == user_id)


def test_only_admin_sees_accounts(make_user, client):
    target = make_user("technician")
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert client.get("/api/admin/accounts", headers=user["headers"]).status_code == 403
        assert client.get(f"/api/admin/accounts/{target['id']}", headers=user["headers"]).status_code == 403


def test_list_masks_email_and_hides_personal_data(make_user, client):
    admin = make_user("admin")
    requester = make_user("requester")
    response = _rows(client, admin, role="requester")
    row = _by_id(response.json(), requester["id"])
    assert row["email"] != requester["email"] and "***@" in row["email"]
    assert row["identity"] == "unverified"
    for secret in (requester["email"], requester["id_number"], requester["phone"]):
        assert secret not in response.text


def test_states_and_filters(make_user, client):
    admin = make_user("admin")
    locked = make_user("technician")
    inactive = make_user("technician")
    away = make_user("technician")
    active = make_user("technician")
    invite_email = f"nuevo-{uuid.uuid4().hex}@test.com"
    db = SessionLocal()
    try:
        email = db.get(models.User, locked["id"]).email.lower()
        db.add(models.LoginThrottle(email=email, failures=0, locked_until=_now() + timedelta(minutes=10),
                                    updated_at=_now()))
        db.get(models.User, inactive["id"]).is_active = False
        today = availability._local_day(_now())
        db.add(models.AvailabilityPeriod(user_id=away["id"], kind="incapacity", start_date=today,
                                         end_date=today + timedelta(days=2), created_at=_now()))
        db.add(models.StaffInvitation(email=invite_email, name="Persona invitada", role="technician",
                                      code_hash="x", expires_at=_now() + timedelta(hours=10), sent_at=_now(),
                                      created_by=admin["id"]))
        db.commit()
    finally:
        db.close()

    rows = _rows(client, admin, role="technician").json()
    assert _by_id(rows, locked["id"])["state"] == "locked"
    assert _by_id(rows, inactive["id"])["state"] == "inactive"
    assert _by_id(rows, away["id"])["state"] == "incapacity"
    assert _by_id(rows, active["id"])["state"] == "active"
    invited = [r for r in rows if r["kind"] == "invitation" and r["name"] == "Persona invitada"]
    assert len(invited) == 1
    assert invited[0]["state"] == "invited" and invited[0]["email"] != invite_email
    assert 9 <= invited[0]["invitation_hours_left"] <= 10

    only_locked = _rows(client, admin, state="locked").json()
    assert all(r["state"] == "locked" for r in only_locked)
    assert locked["id"] in [r["id"] for r in only_locked]
    # El filtro de identidad solo deja solicitantes
    by_identity = _rows(client, admin, identity_code="unverified").json()
    assert by_identity and all(r["role"] == "requester" for r in by_identity)


def test_last_activity_and_sessions(make_user, client):
    admin = make_user("admin")
    technician = make_user("technician")
    rows = _rows(client, admin, role="technician").json()
    assert _by_id(rows, technician["id"])["last_activity"] is not None

    detail = client.get(f"/api/admin/accounts/{technician['id']}", headers=admin["headers"]).json()
    assert detail["active_sessions"] == 1

    db = SessionLocal()
    try:
        session_ids = [s.id for s in db.query(models.Session).filter(models.Session.user_id == technician["id"])]
        db.query(models.SessionActivity).filter(models.SessionActivity.session_id.in_(session_ids)).delete(
            synchronize_session=False)
        db.query(models.Session).filter(models.Session.user_id == technician["id"]).delete()
        db.commit()
    finally:
        db.close()
    rows = _rows(client, admin, role="technician").json()
    assert _by_id(rows, technician["id"])["last_activity"] is None


def test_detail_alerts_and_recent_changes(make_user, client):
    admin = make_user("admin")
    coordinator = make_user("coordinator", name="Coordinadora Ana")
    requester = make_user("requester")
    db = SessionLocal()
    try:
        email = db.get(models.User, requester["id"]).email.lower()
        db.add(models.LoginThrottle(email=email, failures=0, locked_until=_now() + timedelta(minutes=10),
                                    updated_at=_now()))
        db.add(models.AccountRequest(kind="role", target_user_id=requester["id"], created_by=coordinator["id"],
                                     reason="Ahora trabaja con nosotros", new_value="technician",
                                     status="pending", created_at=_now()))
        db.add(models.AccountReport(target_user_id=requester["id"], reporter_id=coordinator["id"],
                                    reason="fake_data", status="open", created_at=_now()))
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/admin/accounts/{requester['id']}", headers=admin["headers"])
    assert response.status_code == 200
    detail = response.json()
    assert detail["state"] == "locked"
    assert detail["alerts"]["pending_role"] == "technician"
    assert detail["alerts"]["pending_role_by"] == "Coordinadora Ana"
    assert detail["alerts"]["locked_until"] is not None
    assert detail["alerts"]["open_reports"] == 1
    assert detail["reported"] is True
    # Correo, teléfono y cédula enmascarados
    for secret in (requester["email"], requester["phone"], requester["id_number"]):
        assert secret not in response.text

    client.post(f"/api/accounts/{requester['id']}/unlock", headers=admin["headers"])
    detail = client.get(f"/api/admin/accounts/{requester['id']}", headers=admin["headers"]).json()
    assert detail["recent_changes"][0]["action"] == "login_unlocked"
    assert detail["alerts"]["locked_until"] is None


def test_close_sessions(make_user, client):
    admin = make_user("admin")
    technician = make_user("technician")
    assert client.get("/api/auth/me", headers=technician["headers"]).status_code == 200

    response = client.post(f"/api/accounts/{technician['id']}/close-sessions", headers=admin["headers"])
    assert response.status_code == 204
    assert client.get("/api/auth/me", headers=technician["headers"]).status_code == 401

    db = SessionLocal()
    try:
        row = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == technician["id"],
                    models.AccountChange.action == "sessions_closed")
            .first()
        )
        assert row is not None and row.actor_id == admin["id"]
    finally:
        db.close()


def test_close_sessions_respects_permissions(make_user, client):
    admin = make_user("admin")
    other_admin = make_user("admin")
    coordinator = make_user("coordinator")
    other_coordinator = make_user("coordinator")
    requester = make_user("requester")

    assert client.post(f"/api/accounts/{other_admin['id']}/close-sessions", headers=admin["headers"]).status_code == 403
    assert client.post(f"/api/accounts/{admin['id']}/close-sessions", headers=admin["headers"]).status_code == 403
    assert client.post(f"/api/accounts/{other_coordinator['id']}/close-sessions",
                       headers=coordinator["headers"]).status_code == 403
    assert client.post(f"/api/accounts/{coordinator['id']}/close-sessions",
                       headers=requester["headers"]).status_code == 403
    # Cerrar sesiones es del administrador: el coordinador ya no lo hace ni con clientes
    assert client.post(f"/api/accounts/{requester['id']}/close-sessions",
                       headers=coordinator["headers"]).status_code == 403
    assert client.post(f"/api/accounts/{requester['id']}/close-sessions",
                       headers=admin["headers"]).status_code == 204
