import pytest

from app import accounts, audit, models
from app.database import SessionLocal

PASSWORD = "clave-segura-123"
ACTIONS = ("deactivate", "reactivate", "reset-access", "unlock")


def _post(client, actor, user_id, action):
    return client.post(f"/api/accounts/{user_id}/{action}", headers=actor["headers"])


def _db_user(user_id):
    db = SessionLocal()
    try:
        return db.get(models.User, user_id)
    finally:
        db.close()


def _sessions(user_id):
    db = SessionLocal()
    try:
        return db.query(models.Session).filter(models.Session.user_id == user_id).count()
    finally:
        db.close()


# (rol de quien actúa, rol de la cuenta, acción) que sí están permitidos
ALLOWED = {
    ("admin", target, action)
    for target in ("coordinator", "technician", "requester")
    for action in ("deactivate", "reactivate", "unlock")
} | {
    ("admin", "coordinator", "reset-access"), ("admin", "technician", "reset-access"), ("admin", "admin", "unlock"),
} | {
    ("coordinator", target, action)
    for target in ("technician", "requester")
    for action in ("deactivate", "reactivate", "unlock")
} | {("coordinator", "technician", "reset-access")}


@pytest.mark.parametrize("actor_role", ["admin", "coordinator", "technician", "requester"])
@pytest.mark.parametrize("target_role", ["admin", "coordinator", "technician", "requester"])
@pytest.mark.parametrize("action", ACTIONS)
def test_permission_matrix(client, make_user, sent, actor_role, target_role, action):
    actor = make_user(actor_role)
    target = make_user(target_role)
    response = _post(client, actor, target["id"], action)
    if (actor_role, target_role, action) in ALLOWED:
        assert response.status_code in (202, 204), response.text
    else:
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("role", ["admin", "coordinator", "technician", "requester"])
@pytest.mark.parametrize("action", ACTIONS)
def test_nobody_manages_own_account(client, make_user, sent, role, action):
    user = make_user(role)
    assert _post(client, user, user["id"], action).status_code == 403
    assert _db_user(user["id"]).is_active


def test_nobody_changes_own_role(client, make_user):
    admin = make_user("admin")
    response = client.patch(f"/api/admin/users/{admin['id']}", json={"role": "coordinator"}, headers=admin["headers"])
    assert response.status_code == 403
    assert _db_user(admin["id"]).role == "admin"


@pytest.mark.parametrize("role", ["coordinator", "technician", "requester"])
def test_only_admin_changes_roles_directly(client, make_user, role):
    actor = make_user(role)
    target = make_user("requester")
    response = client.patch(f"/api/admin/users/{target['id']}", json={"role": "technician"}, headers=actor["headers"])
    assert response.status_code == 403
    assert _db_user(target["id"]).role == "requester"


def test_admin_cannot_deactivate_another_admin(client, make_user):
    admin = make_user("admin")
    other = make_user("admin")
    response = client.patch(f"/api/admin/users/{other['id']}", json={"is_active": False}, headers=admin["headers"])
    assert response.status_code == 403
    assert _db_user(other["id"]).is_active


def test_system_keeps_one_active_admin(client, make_user):
    admin = make_user("admin")
    other = make_user("admin")
    db = SessionLocal()
    try:
        # Solo queda la otra cuenta como administradora activa
        for user in db.query(models.User).filter(models.User.role == "admin", models.User.id != other["id"]):
            user.is_active = False
        db.flush()
        actor = db.get(models.User, admin["id"])
        target = db.get(models.User, other["id"])
        with pytest.raises(Exception) as exc:
            accounts.change_role(db, actor, target, "coordinator")
        assert getattr(exc.value, "detail", "") == accounts.LAST_ADMIN
    finally:
        db.rollback()
        db.close()
    assert _db_user(other["id"]).role == "admin"


def test_admin_demotes_another_admin_while_one_remains(client, make_user):
    admin = make_user("admin")
    other = make_user("admin")
    response = client.patch(f"/api/admin/users/{other['id']}", json={"role": "coordinator"}, headers=admin["headers"])
    assert response.status_code == 200
    assert _db_user(other["id"]).role == "coordinator"


def test_role_change_closes_sessions(client, make_user):
    admin = make_user("admin")
    user = make_user("technician")
    assert _sessions(user["id"]) == 1
    client.patch(f"/api/admin/users/{user['id']}", json={"role": "coordinator"}, headers=admin["headers"])
    assert _sessions(user["id"]) == 0
    assert client.get("/api/auth/me", headers=user["headers"]).status_code == 401


def test_coordinator_deactivates_and_reactivates_requester(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    assert _post(client, coordinator, requester["id"], "deactivate").status_code == 204
    assert not _db_user(requester["id"]).is_active
    assert _sessions(requester["id"]) == 0
    login = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD, "portal": "requester"})
    assert login.status_code == 401
    assert _post(client, coordinator, requester["id"], "reactivate").status_code == 204
    login = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD, "portal": "requester"})
    assert login.status_code == 200


def _assign(client, coordinator, ticket, technician):
    response = client.patch(
        f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"]
    )
    assert response.status_code == 200


def test_deactivating_technician_releases_open_tickets(client, make_user, make_ticket, sent):
    coordinator = make_user("coordinator", "Coordinadora Uno")
    technician = make_user("technician", "Técnico Saliente")
    requester = make_user("requester")
    tickets = [make_ticket(requester, title=f"Liberar {i}") for i in range(12)]
    for ticket in tickets:
        _assign(client, coordinator, ticket, technician)
    # Uno en proceso sigue abierto; uno resuelto no se libera
    client.patch(f"/api/tickets/{tickets[0]['id']}", json={"status": "in_progress"}, headers=coordinator["headers"])
    for status in ("in_progress", "resolved"):
        client.patch(f"/api/tickets/{tickets[-1]['id']}", json={"status": status}, headers=coordinator["headers"])
    sent.clear()

    assert _post(client, coordinator, technician["id"], "deactivate").status_code == 204
    assert _sessions(technician["id"]) == 0

    for ticket in tickets[:-1]:
        detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
        assert detail["assigned_to"] is None
        assert [c["content"] for c in detail["comments"] if c["internal"]] == [accounts.RELEASED_COMMENT]
    resolved = client.get(f"/api/tickets/{tickets[-1]['id']}", headers=coordinator["headers"]).json()
    assert resolved["assigned_to"] == technician["id"]

    notices = [(to, body) for to, subject, body in sent if "liberados" in subject]
    db = SessionLocal()
    try:
        expected = {
            u.email for u in db.query(models.User).filter(
                models.User.role.in_(("coordinator", "admin")), models.User.is_active.is_(True)
            )
        }
    finally:
        db.close()
    assert {to for to, _ in notices} == expected
    body = notices[0][1]
    assert body.count("- #") == 10
    assert "… y 1 más" in body
    assert requester["email"] not in {to for to, _ in notices}


def test_deactivated_account_cannot_be_assigned(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    _post(client, coordinator, technician["id"], "deactivate")
    response = client.patch(
        f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"]
    )
    assert response.status_code == 400


def test_unlock_clears_lockout(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    for _ in range(5):
        client.post("/api/auth/login", json={"email": requester["email"], "password": "mala", "portal": "requester"})
    login = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD, "portal": "requester"})
    assert login.status_code == 429
    rows = client.get("/api/directory", params={"role": "requester", "q": requester["email"]},
                      headers=coordinator["headers"]).json()
    assert rows[0]["login_locked"] is True

    assert _post(client, coordinator, requester["id"], "unlock").status_code == 204
    login = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD, "portal": "requester"})
    assert login.status_code == 200


def test_directory_state_filter(client, make_user):
    admin = make_user("admin")
    active = make_user("technician")
    inactive = make_user("technician")
    _post(client, admin, inactive["id"], "deactivate")
    ids_active = {r["id"] for r in client.get("/api/directory", params={"state": "active"}, headers=admin["headers"]).json()}
    ids_inactive = {r["id"] for r in client.get("/api/directory", params={"state": "inactive"}, headers=admin["headers"]).json()}
    assert active["id"] in ids_active and active["id"] not in ids_inactive
    assert inactive["id"] in ids_inactive and inactive["id"] not in ids_active


def test_audit_rejects_unknown_action():
    db = SessionLocal()
    try:
        with pytest.raises(ValueError):
            audit.record(db, None, "accion_inventada")
    finally:
        db.rollback()
        db.close()
