"""Comando de servidor para cuentas de administrador (scripts.admin_tools)."""
import uuid

import pytest

from app import accounts, models
from app.database import SessionLocal
from conftest import code_from
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


# ----- Solicitantes con correo de dominio interno -----

def _set_email(user_id, email):
    db = SessionLocal()
    try:
        db.get(models.User, user_id).email = email
        db.commit()
    finally:
        db.close()


def _delete(email, typed=None):
    answer = email if typed is None else typed
    return admin_tools.main(["delete-requester", "--email", email], confirm=lambda prompt: answer)


def test_internal_requesters_lists_only_internal_requesters(client, make_user, make_ticket, monkeypatch, capsys):
    from app import config
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])
    inside = make_user("requester", "Cliente Interno")
    sub = make_user("requester", "Cliente Subdominio")
    outside = make_user("requester", "Cliente Externo")
    staff = make_user("technician", "Técnico Interno")
    tag = uuid.uuid4().hex[:8]
    _set_email(inside["id"], f"interno-{tag}@data.cr")
    _set_email(sub["id"], f"sub-{tag}@ti.DATA.cr")
    _set_email(staff["id"], f"tecnico-{tag}@data.cr")
    make_ticket(inside)
    make_ticket(inside)

    assert admin_tools.main(["internal-requesters"]) == 0
    out = capsys.readouterr().out
    lines = {line.split("\t")[0]: line.split("\t") for line in out.splitlines()}
    assert lines[str(inside["id"])][2] == f"interno-{tag}@data.cr"
    assert lines[str(inside["id"])][5] == "2"
    assert str(sub["id"]) in lines
    assert str(outside["id"]) not in lines and str(staff["id"]) not in lines


def test_internal_requesters_without_domains_says_so(client, monkeypatch, capsys):
    from app import config
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", [])
    assert admin_tools.main(["internal-requesters"]) == 0
    assert "INTERNAL_DOMAINS está vacía" in capsys.readouterr().out


def test_delete_requester_refuses_with_tickets(client, make_user, make_ticket, capsys):
    target = make_user("requester")
    make_ticket(target)
    assert _delete(target["email"]) == 1
    assert "Tickets: 1, comentarios: 0, borradores: 0" in capsys.readouterr().err
    assert _db_user(target["id"]) is not None


def test_delete_requester_refuses_with_draft(client, make_user, capsys):
    target = make_user("requester", profile=False)
    db = SessionLocal()
    try:
        db.add(models.EmailDraft(message_id=f"<borrar-{target['id']}@test>", requester_id=target["id"],
                                 subject="Ayuda", body="Texto", status="pending_review"))
        db.commit()
    finally:
        db.close()
    assert _delete(target["email"]) == 1
    assert "borradores: 1" in capsys.readouterr().err
    assert _db_user(target["id"]) is not None


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_delete_requester_refuses_other_roles(client, make_user, role, capsys):
    target = make_user(role)
    assert _delete(target["email"]) == 1
    assert "no de solicitante" in capsys.readouterr().err
    assert _db_user(target["id"]) is not None


def test_delete_requester_needs_the_email_typed(client, make_user):
    target = make_user("requester")
    assert _delete(target["email"], typed="otro@correo.com") == 1
    assert _db_user(target["id"]) is not None


def test_delete_requester_without_activity(client, make_user, sent):
    viewer = make_user("admin")
    target = make_user("requester", "Cliente Equivocado")
    email = target["email"]
    # Un fallo de inicio de sesión (fila de intentos y registro de cambios) y un código pendiente
    client.post("/api/auth/login", json={"email": email, "password": "otra-clave-999", "portal": "requester"})
    assert client.post("/api/auth/password-reset", json={"email": email, "portal": "requester"}).status_code == 202

    assert _delete(email.upper()) == 0
    assert _db_user(target["id"]) is None
    assert _sessions(target["id"]) == 0
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 401
    db = SessionLocal()
    try:
        assert db.query(models.PasswordReset).filter(models.PasswordReset.user_id == target["id"]).count() == 0
        assert db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).count() == 0
        assert db.query(models.AccountChange).filter(models.AccountChange.target_user_id == target["id"]).count() == 0
    finally:
        db.close()

    # Constancia como "Servidor" y el historial anterior se conserva con el nombre de la cuenta
    row = next(c for c in _changes(client, viewer, "server_account_deleted") if "Cliente Equivocado" in c["account"])
    assert row["actor"] == "Servidor"
    assert email not in row["account"]
    assert any("Cliente Equivocado" in c["account"] for c in _changes(client, viewer, "login_failed"))
    # El correo queda libre para registrarse de nuevo
    response = client.post("/api/auth/register", json={"email": email, "password": "clave-segura-123"})
    assert response.status_code == 202


# ----- Archivar un solicitante con actividad -----

def _archive(email, typed=None):
    answer = email if typed is None else typed
    return admin_tools.main(["archive-requester", "--email", email], confirm=lambda prompt: answer)


def _reported_requester(client, make_user, make_ticket, name="Cliente Reportado"):
    """Solicitante con un ticket y un reporte ya cerrado desde la página, como en la instancia real."""
    target = make_user("requester", name)
    ticket = make_ticket(target)
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    response = client.post("/api/account-reports", json={"target_user_id": target["id"], "reason": "fake_data"},
                           headers=coordinator["headers"])
    assert response.status_code in (200, 201), response.text
    report_id = response.json()["id"]
    response = client.post(f"/api/account-reports/{report_id}/dismiss", json={"note": "Revisado"},
                           headers=admin["headers"])
    assert response.status_code == 200, response.text
    return target, ticket, report_id


def test_delete_requester_with_report_suggests_archive(client, make_user, make_ticket, capsys):
    target, _ticket, _report_id = _reported_requester(client, make_user, make_ticket)
    assert _delete(target["email"]) == 1
    err = capsys.readouterr().err
    assert "account_reports.target_user_id: 1" in err
    assert "archive-requester" in err
    assert _db_user(target["id"]) is not None


def test_archive_requester_keeps_activity_and_frees_email(client, make_user, make_ticket, sent):
    admin = make_user("admin")
    target, ticket, report_id = _reported_requester(client, make_user, make_ticket, "Cliente Archivado")
    email = target["email"]
    client.post("/api/auth/login", json={"email": email, "password": "otra-clave-999", "portal": "requester"})

    assert _archive(email.upper()) == 0
    user = _db_user(target["id"])
    archived = f"archivado-{target['id']}@archivo.invalid"
    assert user.email == archived and not user.is_active and user.role == "requester"
    assert _sessions(target["id"]) == 0
    assert client.get("/api/auth/me", headers=target["headers"]).status_code == 401

    db = SessionLocal()
    try:
        # Tickets, comentarios y reportes siguen apuntando a la cuenta archivada
        assert db.get(models.Ticket, ticket["id"]).created_by == target["id"]
        assert db.get(models.AccountReport, report_id).target_user_id == target["id"]
        assert db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).count() == 0
    finally:
        db.close()

    # Constancia como "Servidor" con el correo original
    row = next(c for c in _changes(client, admin, "server_account_archived") if "Cliente Archivado" in c["account"])
    assert row["actor"] == "Servidor"
    assert (row["old_value"], row["new_value"]) == (email, archived)

    # El correo original queda libre para una cuenta de personal
    response = client.post("/api/staff-invitations", json={"email": email, "name": "Persona Interna", "role": "technician"},
                           headers=admin["headers"])
    assert response.status_code == 201, response.text
    response = client.post("/api/auth/activate", json={"email": email, "code": code_from(sent[-1][2])})
    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        staff = db.query(models.User).filter(models.User.email == email).one()
        assert staff.role == "technician" and staff.id != target["id"]
    finally:
        db.close()

    # Archivar otra vez la cuenta archivada no hace nada
    assert _archive(archived) == 1


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_archive_requester_refuses_other_roles(client, make_user, role, capsys):
    target = make_user(role)
    assert _archive(target["email"]) == 1
    assert "no de solicitante" in capsys.readouterr().err
    user = _db_user(target["id"])
    assert user.email == target["email"] and user.is_active
    assert _sessions(target["id"]) == 1


def test_archive_requester_needs_the_email_typed(client, make_user):
    target = make_user("requester")
    assert _archive(target["email"], typed="otro@correo.com") == 1
    user = _db_user(target["id"])
    assert user.email == target["email"] and user.is_active
    assert _sessions(target["id"]) == 1
