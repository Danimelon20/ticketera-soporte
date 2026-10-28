"""Comando de servidor para cuentas de administrador (scripts.admin_tools)."""
import pytest

from app import accounts, models
from app.database import SessionLocal
from scripts import admin_tools


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


def _run(command, email, typed=None, extra=()):
    """Corre el comando como desde la consola; typed es lo que escribe la persona al confirmar."""
    answer = email if typed is None else typed
    return admin_tools.main([command, "--email", email, *extra], confirm=lambda prompt: answer)


def _changes(client, viewer, action):
    response = client.get("/api/admin/account-changes", params={"action": action}, headers=viewer["headers"])
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_deactivate_admin_closes_sessions_and_logs_server(client, make_user):
    viewer = make_user("admin")
    target = make_user("admin", "Admin Saliente")
    assert _run("deactivate", target["email"].upper()) == 0
    user = _db_user(target["id"])
    assert not user.is_active and user.role == "admin"
    assert _sessions(target["id"]) == 0
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 401

    row = next(c for c in _changes(client, viewer, "server_account_deactivated") if "Admin Saliente" in c["account"])
    assert row["actor"] == "Servidor"


def test_reactivate_admin(client, make_user):
    viewer = make_user("admin")
    target = make_user("admin", "Admin Vuelve")
    assert _run("deactivate", target["email"]) == 0
    assert _run("reactivate", target["email"]) == 0
    assert _db_user(target["id"]).is_active
    row = next(c for c in _changes(client, viewer, "server_account_activated") if "Admin Vuelve" in c["account"])
    assert row["actor"] == "Servidor"


@pytest.mark.parametrize("new_role, label", [(None, "Coordinador"), ("technician", "Técnico")])
def test_demote_admin(client, make_user, new_role, label):
    viewer = make_user("admin")
    target = make_user("admin", f"Admin Baja {label}")
    extra = ("--to", new_role) if new_role else ()
    assert _run("demote", target["email"], extra=extra) == 0
    user = _db_user(target["id"])
    assert user.role == (new_role or "coordinator") and user.is_active
    assert _sessions(target["id"]) == 0
    row = next(c for c in _changes(client, viewer, "server_role_changed") if f"Admin Baja {label}" in c["account"])
    assert row["actor"] == "Servidor"
    assert (row["old_value"], row["new_value"]) == ("Administrador", label)


@pytest.mark.parametrize("command", ["deactivate", "demote"])
def test_wrong_confirmation_changes_nothing(make_user, command):
    target = make_user("admin")
    assert _run(command, target["email"], typed="otro@correo.com") == 1
    user = _db_user(target["id"])
    assert user.is_active and user.role == "admin"
    assert _sessions(target["id"]) == 1


@pytest.mark.parametrize("command", ["deactivate", "demote"])
def test_never_leaves_system_without_active_admin(make_user, command):
    target = make_user("admin")
    db = SessionLocal()
    others = [u.id for u in db.query(models.User).filter(
        models.User.role == "admin", models.User.is_active.is_(True), models.User.id != target["id"]
    )]
    try:
        db.query(models.User).filter(models.User.id.in_(others)).update({"is_active": False}, synchronize_session=False)
        db.commit()
        assert accounts.active_admins(db) == 1
        assert _run(command, target["email"]) == 1
        user = _db_user(target["id"])
        assert user.is_active and user.role == "admin"
        assert _sessions(target["id"]) == 1
    finally:
        db.query(models.User).filter(models.User.id.in_(others)).update({"is_active": True}, synchronize_session=False)
        db.commit()
        db.close()


def test_only_admin_accounts(make_user):
    technician = make_user("technician")
    assert _run("deactivate", technician["email"]) == 1
    assert _db_user(technician["id"]).is_active
    assert _run("deactivate", "nadie@test.com") == 1


def test_rejects_unknown_command(make_user):
    target = make_user("admin")
    with pytest.raises(SystemExit):
        admin_tools.main(["delete", "--email", target["email"]], confirm=lambda prompt: target["email"])
    assert _db_user(target["id"]).is_active
