"""Administrador único: ninguna pantalla ni endpoint asigna el rol, create_admin se niega si ya hay uno
y las bases con varios administradores avisan sin cambiar a nadie."""
import logging

import pytest

from app import accounts, create_admin, models
from app.database import SessionLocal
from conftest import only_active_admins
from scripts import admin_tools

PASSWORD = "Clave-del-admin-123"


def _db_user(user_id):
    db = SessionLocal()
    try:
        return db.get(models.User, user_id)
    finally:
        db.close()


def _run_tool(argv, typed=None):
    return admin_tools.main(argv, confirm=lambda prompt: typed if typed is not None else argv[-1])


# ----- Ningún endpoint asigna el rol de administrador -----

@pytest.mark.parametrize("target_role", ["technician", "coordinator", "requester"])
def test_admin_patch_cannot_grant_admin(client, make_user, target_role):
    admin = make_user("admin")
    target = make_user(target_role)
    response = client.patch(f"/api/admin/users/{target['id']}", json={"role": "admin"}, headers=admin["headers"])
    assert response.status_code == 422, response.text
    assert _db_user(target["id"]).role == target_role


def test_check_can_manage_rejects_admin_as_new_role(make_user):
    admin = make_user("admin")
    target = make_user("technician")
    db = SessionLocal()
    try:
        with pytest.raises(Exception) as exc:
            accounts.check_can_manage(db.get(models.User, admin["id"]), db.get(models.User, target["id"]), "role", "admin")
        assert exc.value.status_code == 403
        with pytest.raises(Exception) as exc:
            accounts.change_role(db, db.get(models.User, admin["id"]), db.get(models.User, target["id"]), "admin")
        assert exc.value.status_code == 403
    finally:
        db.rollback()
        db.close()
    assert _db_user(target["id"]).role == "technician"


def test_invitation_cannot_be_for_admin(client, make_user, sent):
    admin = make_user("admin")
    response = client.post("/api/staff-invitations", json={
        "email": "nuevo-admin@test.com", "name": "Nuevo", "role": "admin",
    }, headers=admin["headers"])
    assert response.status_code == 422
    assert sent == []


def test_coordinator_request_cannot_ask_for_admin(client, make_user, sent):
    coordinator = make_user("coordinator")
    target = make_user("technician")
    response = client.post("/api/account-requests", json={
        "kind": "role", "target_user_id": target["id"], "new_value": "admin", "reason": "Ascenso",
    }, headers=coordinator["headers"])
    assert response.status_code in (403, 422), response.text
    assert _db_user(target["id"]).role == "technician"


def test_admin_cannot_change_role_of_requester_or_agent(client, make_user):
    """El cambio directo de rol es solo técnico ↔ coordinador."""
    admin = make_user("admin")
    requester = make_user("requester")
    response = client.patch(f"/api/admin/users/{requester['id']}", json={"role": "technician"}, headers=admin["headers"])
    assert response.status_code == 403
    assert _db_user(requester["id"]).role == "requester"


# ----- create_admin -----

def _answers(name, email):
    values = iter([name, email])
    return lambda prompt: next(values)


def test_create_admin_refuses_when_an_admin_is_active(make_user, capsys):
    admin = make_user("admin")
    with only_active_admins(admin["id"]):
        result = create_admin.main(read=_answers("Otro", "otro-admin@test.com"), read_secret=lambda p: PASSWORD)
    assert result == 1
    out = capsys.readouterr().out
    assert "Ya existe un administrador activo" in out
    assert "admin_tools demote" in out and "reset-password" in out
    db = SessionLocal()
    try:
        assert db.query(models.User).filter(models.User.email == "otro-admin@test.com").first() is None
    finally:
        db.close()


def test_create_admin_creates_the_only_admin_and_logs_it(client, make_user):
    email = "unico-admin@test.com"
    with only_active_admins():
        assert create_admin.main(read=_answers("Única", email), read_secret=lambda p: PASSWORD) == 0
        db = SessionLocal()
        try:
            user = db.query(models.User).filter(models.User.email == email).one()
            assert user.role == "admin" and user.is_active
            change = db.query(models.AccountChange).filter(
                models.AccountChange.action == "server_admin_created", models.AccountChange.target_user_id == user.id
            ).one()
            assert change.actor_id is None
            # Para no dejarlo activo en las demás pruebas
            user.is_active = False
            db.commit()
        finally:
            db.close()


def test_create_admin_asks_for_twelve_characters(capsys):
    with only_active_admins():
        result = create_admin.main(read=_answers("Corta", "corta-admin@test.com"), read_secret=lambda p: "12345678")
    assert result == 1
    assert "al menos 12" in capsys.readouterr().out


# ----- admin_tools: list-admins, reactivate, reset-password -----

def test_list_admins_shows_full_email_and_warns_when_many(make_user, capsys):
    first = make_user("admin", "Admin Uno")
    second = make_user("admin", "Admin Dos")
    with only_active_admins(first["id"], second["id"]):
        assert admin_tools.main(["list-admins"]) == 0
    out = capsys.readouterr().out
    assert first["email"] in out and second["email"] in out
    assert "AVISO: hay 2 administradores activos" in out


def test_reactivate_refuses_when_another_admin_is_active(make_user, capsys):
    active = make_user("admin")
    inactive = make_user("admin")
    db = SessionLocal()
    try:
        db.get(models.User, inactive["id"]).is_active = False
        db.commit()
    finally:
        db.close()
    with only_active_admins(active["id"]):
        assert _run_tool(["reactivate", "--email", inactive["email"]]) == 1
    assert not _db_user(inactive["id"]).is_active
    assert "solo puede haber uno" in capsys.readouterr().err


def test_reset_password_recovers_a_locked_admin(client, make_user, capsys):
    admin = make_user("admin")
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta, timezone
        db.add(models.LoginThrottle(email=admin["email"], failures=0, updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                    locked_until=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)))
        db.commit()
    finally:
        db.close()
    assert _run_tool(["reset-password", "--email", admin["email"]]) == 0
    out = capsys.readouterr().out
    password = out.split("una sola vez): ")[1].split()[0]
    # Sesiones cerradas, bloqueo quitado y la contraseña nueva sirve (el administrador recibe el código)
    assert client.get("/api/auth/me", headers=admin["headers"]).status_code == 401
    response = client.post("/api/auth/login", json={"email": admin["email"], "password": password, "portal": "admin"})
    assert response.status_code == 200, response.text
    assert "challenge_id" in response.json()
    db = SessionLocal()
    try:
        assert db.query(models.AccountChange).filter(
            models.AccountChange.action == "server_password_reset", models.AccountChange.target_user_id == admin["id"]
        ).count() == 1
    finally:
        db.close()


def test_reset_password_needs_the_email_typed(make_user):
    admin = make_user("admin")
    before = _db_user(admin["id"]).password_hash
    assert _run_tool(["reset-password", "--email", admin["email"]], typed="otro@test.com") == 1
    assert _db_user(admin["id"]).password_hash == before


# ----- Varios administradores: aviso en el log y en la página -----

def test_many_admins_warning_in_log_and_counts(client, make_user, caplog):
    first = make_user("admin")
    second = make_user("admin")
    with only_active_admins(first["id"], second["id"]):
        db = SessionLocal()
        try:
            with caplog.at_level(logging.WARNING):
                assert accounts.warn_if_many_admins(db) == 2
        finally:
            db.close()
        assert "2 administradores activos" in caplog.text
        counts = client.get("/api/nav/counts", headers=first["headers"]).json()
        assert counts["active_admins"] == 2
        health = client.get("/api/admin/health", headers=first["headers"]).json()
        assert health["active_admins"] == 2
    # Nadie cambió de rol
    assert _db_user(first["id"]).role == "admin" and _db_user(second["id"]).role == "admin"


def test_single_admin_has_no_warning(client, make_user, caplog):
    admin = make_user("admin")
    with only_active_admins(admin["id"]):
        db = SessionLocal()
        try:
            with caplog.at_level(logging.WARNING):
                assert accounts.warn_if_many_admins(db) == 1
        finally:
            db.close()
        assert "administradores activos" not in caplog.text
        assert client.get("/api/nav/counts", headers=admin["headers"]).json()["active_admins"] == 1
