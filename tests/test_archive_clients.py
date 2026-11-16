"""Fase 4 sin portal: cuentas de cliente archivadas y contactos en las pantallas del personal.

scripts.admin_tools archive-clients: por defecto solo muestra; --apply marca las cuentas de cliente (solicitantes
de otro dominio que INTERNAL_DOMAINS) como archivadas, sin borrar nada. Cuentas las muestra como «Archivados»,
sus tickets siguen visibles para el personal y un contacto no tiene acciones de acceso (desbloquear, cerrar
sesiones)."""
import pytest

from app import accounts, config, models
from app.database import SessionLocal
from scripts import admin_tools


@pytest.fixture(autouse=True)
def domains(monkeypatch, client):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])
    # Otras pruebas dejan cuentas sin archivar: aquí solo cuentan las de esta prueba
    with SessionLocal() as db:
        for user in db.query(models.User).filter(models.User.role == "requester"):
            if db.get(models.ArchivedAccount, user.id) is None:
                db.add(models.ArchivedAccount(user_id=user.id))
        db.commit()


def _client(make_user, email):
    user = make_user("requester", "Cliente Externo")
    with SessionLocal() as db:
        db.get(models.User, user["id"]).email = email
        db.commit()
    return user


def _archived(user_id):
    with SessionLocal() as db:
        return db.get(models.ArchivedAccount, user_id) is not None


def test_dry_run_by_default_changes_nothing(client, make_user, make_ticket, capsys):
    outsider = _client(make_user, "cliente.uno@empresa-cliente.com")
    make_ticket(outsider)
    employee = _client(make_user, "ana.mora@data.cr")
    with SessionLocal() as db:
        db.query(models.ArchivedAccount).filter(
            models.ArchivedAccount.user_id.in_([outsider["id"], employee["id"]])).delete()
        db.commit()
    assert admin_tools.main(["archive-clients"]) == 0
    out = capsys.readouterr().out
    assert "MODO DE PRUEBA" in out and "No se cambió nada" in out and "--apply" in out
    assert f"#{outsider['id']}" in out and "1 ticket(s) abierto(s)" in out
    # Correo enmascarado; el empleado (dominio interno) no es un cliente
    assert "cliente.uno@empresa-cliente.com" not in out and f"#{employee['id']}" not in out
    assert not _archived(outsider["id"])


def test_apply_archives_without_deleting(client, make_user, make_ticket, capsys):
    outsider = _client(make_user, "cliente.dos@empresa-cliente.com")
    ticket = make_ticket(outsider)
    with SessionLocal() as db:
        db.query(models.ArchivedAccount).filter(models.ArchivedAccount.user_id == outsider["id"]).delete()
        db.commit()
    assert admin_tools.main(["archive-clients", "--apply"]) == 0
    assert _archived(outsider["id"])
    with SessionLocal() as db:
        user = db.get(models.User, outsider["id"])
        # Nada se borra ni cambia: la cuenta, su correo, su ticket
        assert user.is_active and user.email == "cliente.dos@empresa-cliente.com"
        assert db.get(models.Ticket, ticket["id"]).status == "new"
        change = db.query(models.AccountChange).filter(
            models.AccountChange.target_user_id == outsider["id"],
            models.AccountChange.action == "server_account_archived").one()
        assert change.actor_id is None
    # Otra vez: no repite
    capsys.readouterr()
    assert admin_tools.main(["archive-clients", "--apply"]) == 0
    assert "No hay cuentas de cliente por archivar" in capsys.readouterr().out


def test_accounts_show_archived_and_keep_their_tickets(client, make_user, make_ticket):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    outsider = _client(make_user, "cliente.tres@empresa-cliente.com")
    ticket = make_ticket(outsider)
    assert admin_tools.main(["archive-clients", "--apply"]) == 0
    page = client.get("/api/accounts", params={"filter": "archived"}, headers=admin["headers"]).json()
    row = next(r for r in page["items"] if r["id"] == outsider["id"])
    assert row["archived"] is True and page["counts"]["archived"] >= 1
    summary = client.get(f"/api/accounts/{outsider['id']}", headers=admin["headers"]).json()
    assert summary["archived"] is True
    # Sin acciones de acceso: un contacto no inicia sesión
    assert not {"unlock", "close-sessions", "reset-access"} & set(summary["actions"])
    # El personal sigue viendo su ticket, con la etiqueta de cliente archivado
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["requester_flags"]["archived"] is True


@pytest.mark.parametrize("action", ["unlock", "close-sessions"])
def test_no_access_actions_on_contacts(client, make_user, action):
    admin = make_user("admin")
    contact = make_user("requester")
    assert client.post(f"/api/accounts/{contact['id']}/{action}", headers=admin["headers"]).status_code == 403


def test_contact_without_id_number_is_not_unverified(client, make_user):
    admin = make_user("admin")
    contact = make_user("requester", profile=False)
    page = client.get("/api/accounts", params={"filter": "unverified"}, headers=admin["headers"]).json()
    assert contact["id"] not in [r["id"] for r in page["items"]]
    summary = client.get(f"/api/accounts/{contact['id']}", headers=admin["headers"]).json()
    assert summary["identity"] is None


def test_archived_ids():
    with SessionLocal() as db:
        assert accounts.archived_ids(db, []) == set()
