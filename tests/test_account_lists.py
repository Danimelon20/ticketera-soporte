"""Listas de cuentas: el solicitante no recibe ninguna y el técnico no ve correos ni solicitantes."""
import re

import pytest

from app import models
from app.database import SessionLocal

from conftest import assign_draft_to

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Rutas que listan cuentas (o datos de cuentas ajenas)
ACCOUNT_LISTS = (
    "/api/users",
    "/api/directory",
    "/api/account-requests",
    "/api/staff-invitations",
    "/api/admin/account-changes",
)


@pytest.mark.parametrize("path", ACCOUNT_LISTS)
def test_requester_gets_403_on_every_account_list(client, make_user, path):
    response = client.get(path, headers=make_user("requester")["headers"])
    assert response.status_code == 403
    assert not EMAIL_RE.search(response.text)


@pytest.mark.parametrize("path", [p for p in ACCOUNT_LISTS if p != "/api/users"])
def test_technician_gets_403_on_management_lists(client, make_user, path):
    assert client.get(path, headers=make_user("technician")["headers"]).status_code == 403


def test_technician_gets_only_active_staff_without_emails(client, make_user):
    technician = make_user("technician", "Técnico Que Mira")
    requester = make_user("requester", "Solicitante Oculto")
    coordinator = make_user("coordinator", "Coordinadora Visible")
    inactive = make_user("technician", "Técnico Inactivo")
    db = SessionLocal()
    try:
        db.get(models.User, inactive["id"]).is_active = False
        db.commit()
    finally:
        db.close()

    response = client.get("/api/users", headers=technician["headers"])
    assert response.status_code == 200
    rows = response.json()
    assert not EMAIL_RE.search(response.text)
    # La disponibilidad no es un dato personal: sirve para elegir a quién asignar
    assert all(set(r) == {"id", "name", "role", "availability"} for r in rows)
    assert all(r["role"] in ("technician", "coordinator", "admin") for r in rows)
    ids = {r["id"] for r in rows}
    assert coordinator["id"] in ids and technician["id"] in ids
    assert requester["id"] not in ids and inactive["id"] not in ids


def test_admin_still_gets_full_list(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    rows = client.get("/api/users", headers=admin["headers"]).json()
    row = next(r for r in rows if r["id"] == requester["id"])
    assert row["email"] == requester["email"]


def test_coordinator_list_has_no_clients(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    response = client.get("/api/users", headers=coordinator["headers"])
    assert response.status_code == 200
    rows = response.json()
    assert all(r["role"] != "requester" for r in rows)
    assert requester["email"] not in response.text
    # Sigue recibiendo al personal: lo usa para asignar y para los nombres del historial
    assert technician["id"] in [r["id"] for r in rows]


def test_coordinator_gets_client_name_with_the_ticket(client, make_user, make_ticket):
    # Sin la lista, el detalle del ticket sigue trayendo el nombre del cliente (y no su correo)
    coordinator = make_user("coordinator")
    requester = make_user("requester", "Cliente Con Nombre")
    ticket = make_ticket(requester)
    response = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"])
    assert response.status_code == 200
    assert {"id": requester["id"], "name": "Cliente Con Nombre", "role": "requester"} in response.json()["people"]


@pytest.mark.parametrize("role", ["coordinator", "admin"])
def test_requesters_partial_search_stays_gone(client, make_user, role):
    viewer = make_user(role)
    requester = make_user("requester")
    # Ya no hay búsqueda de solicitantes por coincidencia parcial: solo el correo exacto (POST /api/requesters/lookup)
    response = client.get("/api/requesters?q=a", headers=viewer["headers"])
    assert response.status_code in (404, 405)
    assert requester["email"] not in response.text


def test_ticket_brings_names_without_emails(client, make_user, make_ticket):
    requester = make_user("requester", "Ana Solicitante")
    coordinator = make_user("coordinator", "Carla Coordinadora")
    technician = make_user("technician", "Tomás Técnico")
    ticket = make_ticket(requester)
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    client.post(f"/api/tickets/{ticket['id']}/comments", json={"content": "Revisando"}, headers=technician["headers"])

    for viewer in (requester, technician):
        detail = client.get(f"/api/tickets/{ticket['id']}", headers=viewer["headers"]).json()
        people = {p["id"]: p for p in detail["people"]}
        assert people[technician["id"]] == {"id": technician["id"], "name": "Tomás Técnico", "role": "technician"}
        assert people[coordinator["id"]]["name"] == "Carla Coordinadora"
        assert all(set(p) == {"id", "name", "role"} for p in detail["people"])
        assert detail["assigned_name"] == "Tomás Técnico"
        for user in (requester, coordinator, technician):
            assert user["email"] not in str(detail["people"])

    listing = client.get("/api/tickets", headers=requester["headers"]).json()
    assert [t["assigned_name"] for t in listing if t["id"] == ticket["id"]] == ["Tomás Técnico"]


def test_technician_never_sees_requester_email_in_drafts(client, make_user):
    technician = make_user("technician")
    requester = make_user("requester", name="", profile=False)
    db = SessionLocal()
    try:
        draft = models.EmailDraft(
            message_id=f"<lista-{requester['id']}@test>", requester_id=requester["id"],
            subject="Ayuda", body="Texto", status="pending_review",
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id
    finally:
        db.close()
    # El técnico solo ve los borradores que tiene asignados
    assign_draft_to(draft_id, technician["id"])

    drafts = client.get("/api/drafts", headers=technician["headers"]).json()
    mine = [d for d in drafts if d["requester_id"] == requester["id"]]
    assert mine
    assert requester["email"] not in str(mine)
    assert mine[0]["requester_name"].startswith(requester["email"][:2]) and "***@" in mine[0]["requester_name"]

    coordinator = make_user("coordinator")
    drafts = client.get("/api/drafts", headers=coordinator["headers"]).json()
    assert [d["requester_name"] for d in drafts if d["requester_id"] == requester["id"]] == [requester["email"]]


def _account_lookup(client, viewer, email):
    return client.post("/api/accounts/lookup", json={"email": email}, headers=viewer["headers"])


@pytest.mark.parametrize("params", [{"filter": "clients"}, {"filter": "clients", "q": "Ficha Oculta"},
                                    {"filter": "all", "q": "Ficha Oculta"}, {"filter": "reported"},
                                    {"filter": "unverified"}, {"filter": "locked"}])
def test_coordinator_gets_no_clients_from_accounts_list(client, make_user, params):
    coordinator = make_user("coordinator")
    customer = make_user("requester", "Clara Ficha Oculta")
    response = client.get("/api/accounts", params=params, headers=coordinator["headers"])
    assert response.status_code == 200
    assert customer["id"] not in [row["id"] for row in response.json()["items"]]
    assert response.json()["counts"]["clients"] == 0


def test_admin_keeps_clients_list_and_partial_search(client, make_user):
    admin = make_user("admin")
    customer = make_user("requester", "Clara Ficha Administrada")
    response = client.get("/api/accounts", params={"filter": "clients", "q": "Ficha Administrada"},
                          headers=admin["headers"])
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == [customer["id"]]
    assert response.json()["counts"]["clients"] == 1
    # Sin tickets también: el administrador gestiona todas las cuentas
    assert client.get(f"/api/accounts/{customer['id']}", headers=admin["headers"]).status_code == 200


def test_coordinator_finds_client_only_by_exact_email(client, make_user):
    coordinator = make_user("coordinator")
    customer = make_user("requester", "Clara Correo Exacto")
    email = customer["email"]
    found = _account_lookup(client, coordinator, email)
    assert found.status_code == 200
    assert found.json()["id"] == customer["id"] and found.json()["name"] == "Clara Correo Exacto"
    # Sin importar mayúsculas ni espacios, como al crear tickets
    assert _account_lookup(client, coordinator, f"  {email.upper()} ").json()["id"] == customer["id"]
    # El correo completo no vuelve en la respuesta
    assert email not in found.text
    for partial in (email[:-1], email.split("@")[0], "Clara Correo Exacto"):
        response = _account_lookup(client, coordinator, partial)
        assert response.status_code == 404
        assert response.json()["detail"] == "No hay ningún cliente con ese correo"


def test_account_lookup_finds_only_clients(client, make_user):
    coordinator = make_user("coordinator")
    assert _account_lookup(client, coordinator, make_user("technician")["email"]).status_code == 404
    assert _account_lookup(client, coordinator, make_user("admin")["email"]).status_code == 404


def test_account_lookup_shows_inactive_client(client, make_user):
    coordinator = make_user("coordinator")
    customer = make_user("requester")
    db = SessionLocal()
    try:
        db.get(models.User, customer["id"]).is_active = False
        db.commit()
    finally:
        db.close()
    response = _account_lookup(client, coordinator, customer["email"])
    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.parametrize("role", ["technician", "requester"])
def test_account_lookup_forbidden_for_others(client, make_user, role):
    customer = make_user("requester")
    assert _account_lookup(client, make_user(role), customer["email"]).status_code == 403


def test_coordinator_opens_client_by_id_only_with_tickets(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    without = make_user("requester", "Clara Sin Tickets")
    response = client.get(f"/api/accounts/{without['id']}", headers=coordinator["headers"])
    assert response.status_code == 404
    assert "Clara Sin Tickets" not in response.text
    # Igual que un id que no existe: la respuesta no confirma que la cuenta exista
    assert response.json() == client.get("/api/accounts/999999", headers=coordinator["headers"]).json()

    # Con correo exacto sí llega a su ficha, aunque no tenga tickets
    assert _account_lookup(client, coordinator, without["email"]).status_code == 200

    # "Ver ficha del cliente" desde un ticket
    make_ticket(without)
    detail = client.get(f"/api/accounts/{without['id']}", headers=coordinator["headers"])
    assert detail.status_code == 200
    assert detail.json()["name"] == "Clara Sin Tickets"


def test_coordinator_still_opens_technician_by_id(client, make_user):
    # La restricción es solo para clientes: Equipo › Personas sigue igual
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    assert client.get(f"/api/accounts/{technician['id']}", headers=coordinator["headers"]).status_code == 200
