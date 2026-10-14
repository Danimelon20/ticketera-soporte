import pytest

from app import models
from app.database import SessionLocal

PASSWORD = "clave-segura-123"


def _directory(client, user, **params):
    return client.get("/api/directory", params=params, headers=user["headers"])


def _row(rows, user):
    return next(r for r in rows if r["id"] == user["id"])


def _patch_user(client, actor, user_id, **data):
    return client.patch(f"/api/admin/users/{user_id}", json=data, headers=actor["headers"])


def _agent_id():
    db = SessionLocal()
    try:
        return db.query(models.User).filter(models.User.role == "agent").first().id
    finally:
        db.close()


def test_coordinator_sees_directory_without_agent(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    response = _directory(client, coordinator)
    assert response.status_code == 200
    rows = response.json()
    assert all(r["role"] != "agent" for r in rows)
    assert any(r["id"] == requester["id"] for r in rows)
    assert all("password_hash" not in r for r in rows)


@pytest.mark.parametrize("role", ["technician", "requester"])
def test_others_cannot_see_directory(client, make_user, role):
    response = _directory(client, make_user(role))
    assert response.status_code == 403
    assert response.json()["detail"] == "No tienes permiso para ver esta información"


def test_directory_counts(client, make_user, make_ticket):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    first = make_ticket(requester, title="Uno")
    second = make_ticket(requester, title="Dos")
    make_ticket(requester, title="Tres")

    for ticket in (first, second):
        response = client.patch(
            f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"]
        )
        assert response.status_code == 200
    # Uno cerrado no cuenta como abierto
    for status in ("in_progress", "resolved", "closed"):
        response = client.patch(f"/api/tickets/{second['id']}", json={"status": status}, headers=coordinator["headers"])
        assert response.status_code == 200

    rows = _directory(client, coordinator).json()
    assert _row(rows, requester)["tickets_created"] == 3
    assert _row(rows, requester)["tickets_assigned_open"] == 0
    assert _row(rows, technician)["tickets_assigned_open"] == 1
    assert _row(rows, technician)["tickets_created"] == 0


def test_directory_filters(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Técnico Buscable Zeta")
    rows = _directory(client, coordinator, role="technician").json()
    assert rows and all(r["role"] == "technician" for r in rows)

    rows = _directory(client, coordinator, q="buscable zeta").json()
    assert [r["id"] for r in rows] == [technician["id"]]
    rows = _directory(client, coordinator, q=technician["email"].upper()).json()
    assert [r["id"] for r in rows] == [technician["id"]]


def test_admin_changes_role_and_coordinator_cannot(client, make_user):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    requester = make_user("requester")

    response = _patch_user(client, coordinator, requester["id"], role="technician")
    assert response.status_code == 403
    assert response.json()["detail"] == "Solo el administrador puede hacer esto"

    response = _patch_user(client, admin, requester["id"], role="technician")
    assert response.status_code == 200
    assert response.json()["role"] == "technician"
    assert _patch_user(client, admin, 999999, role="technician").status_code == 404


def test_deactivated_user_loses_session_and_login(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    assert client.get("/api/auth/me", headers=requester["headers"]).status_code == 200

    response = _patch_user(client, admin, requester["id"], is_active=False)
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    me = client.get("/api/auth/me", headers=requester["headers"])
    assert me.status_code == 401
    assert me.json()["detail"] == "Inicia sesión para continuar"
    login = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD})
    assert login.status_code == 401

    # Activada de nuevo puede entrar
    assert _patch_user(client, admin, requester["id"], is_active=True).status_code == 200
    login = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD})
    assert login.status_code == 200


def test_inactive_user_with_token_gets_401(client, make_user):
    requester = make_user("requester")
    db = SessionLocal()
    try:
        db.get(models.User, requester["id"]).is_active = False
        db.commit()
    finally:
        db.close()
    response = client.get("/api/auth/me", headers=requester["headers"])
    assert response.status_code == 401
    assert response.json()["detail"] == "Inicia sesión para continuar"


def test_admin_cannot_change_self_or_agent(client, make_user):
    admin = make_user("admin")
    response = _patch_user(client, admin, admin["id"], is_active=False)
    assert response.status_code == 400
    assert response.json()["detail"] == "No puedes cambiar tu propio rol ni desactivarte"

    response = _patch_user(client, admin, _agent_id(), is_active=False)
    assert response.status_code == 400
    assert response.json()["detail"] == "El Agente virtual no se puede modificar"


def test_health(client, make_user):
    admin = make_user("admin")
    response = client.get("/api/admin/health", headers=admin["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "ok"
    assert data["users_by_role"]["admin"] >= 1
    assert "email_enabled" in data and "chatbot_enabled" in data

    coordinator = make_user("coordinator")
    assert client.get("/api/admin/health", headers=coordinator["headers"]).status_code == 403


def test_backup(client, make_user):
    admin = make_user("admin")
    response = client.get("/api/admin/backup", headers=admin["headers"])
    assert response.status_code == 200
    assert response.content.startswith(b"SQLite format 3")
    disposition = response.headers["content-disposition"]
    assert "ticketera-respaldo-" in disposition and ".db" in disposition

    coordinator = make_user("coordinator")
    assert client.get("/api/admin/backup", headers=coordinator["headers"]).status_code == 403


def test_logs(client, make_user):
    admin = make_user("admin")
    response = client.get("/api/admin/logs", headers=admin["headers"])
    assert response.status_code == 200
    assert isinstance(response.json()["lines"], list)
    assert len(response.json()["lines"]) <= 200
