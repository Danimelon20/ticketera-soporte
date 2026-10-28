"""Cuentas: una sola lista con filtros rápidos para el administrador y el coordinador."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal

FILTERS = ("all", "staff", "clients", "locked", "reported", "unverified", "invitations")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _list(client, viewer, **params):
    response = client.get("/api/accounts", params=params, headers=viewer["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _summary(client, viewer, user_id):
    return client.get(f"/api/accounts/{user_id}", headers=viewer["headers"])


def _lock(email):
    db = SessionLocal()
    try:
        db.add(models.LoginThrottle(
            email=email.lower(), failures=5, locked_until=_now() + timedelta(minutes=15), updated_at=_now(),
        ))
        db.commit()
    finally:
        db.close()


def _identity(user_id, result):
    db = SessionLocal()
    try:
        db.add(models.RequesterIdentity(user_id=user_id, hacienda_result=result, checked_at=_now()))
        db.commit()
    finally:
        db.close()


def _report(client, reporter, target):
    response = client.post("/api/account-reports", json={
        "target_user_id": target["id"], "reason": "fake_data",
    }, headers=reporter["headers"])
    assert response.status_code == 201, response.text


def _invite(client, actor, tag, role="technician"):
    response = client.post("/api/staff-invitations", json={
        "email": f"inv-{uuid.uuid4().hex}@test.com", "name": f"Invitada {tag}", "role": role,
    }, headers=actor["headers"])
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def accounts_set(client, make_user, sent):
    """Cuentas con un nombre único (tag) para que la búsqueda deje solo estas."""
    tag = uuid.uuid4().hex[:10]
    admin = make_user("admin", f"Admin {tag}")
    coordinator = make_user("coordinator", f"Coordinadora {tag}")
    technician = make_user("technician", f"Técnico {tag}")
    locked = make_user("technician", f"Bloqueado {tag}")
    verified = make_user("requester", f"Verificada {tag}")
    unverified = make_user("requester", f"Sinverificar {tag}")
    reported = make_user("requester", f"Reportada {tag}")
    _lock(locked["email"])
    _identity(verified["id"], "match")
    _identity(reported["id"], "mismatch")
    _report(client, admin, reported)
    admin_invitation = _invite(client, admin, tag, role="coordinator")
    # Las invitaciones son solo del administrador
    coordinator_invitation = _invite(client, admin, tag)
    return {
        "tag": tag, "admin": admin, "coordinator": coordinator, "technician": technician, "locked": locked,
        "verified": verified, "unverified": unverified, "reported": reported,
        "admin_invitation": admin_invitation, "coordinator_invitation": coordinator_invitation,
    }


def _ids(page, kind="user"):
    return {row["id"] for row in page["items"] if row["kind"] == kind}


@pytest.mark.parametrize("role", ["technician", "requester"])
def test_technician_and_requester_get_403(client, make_user, role):
    viewer = make_user(role)
    target = make_user("requester")
    assert client.get("/api/accounts", headers=viewer["headers"]).status_code == 403
    assert _summary(client, viewer, target["id"]).status_code == 403


def test_admin_quick_filters_and_counts(client, accounts_set):
    s = accounts_set
    admin = s["admin"]
    pages = {f: _list(client, admin, filter=f, q=s["tag"]) for f in FILTERS}

    staff = {s["admin"]["id"], s["coordinator"]["id"], s["technician"]["id"], s["locked"]["id"]}
    clients = {s["verified"]["id"], s["unverified"]["id"], s["reported"]["id"]}
    assert _ids(pages["staff"]) == staff
    assert _ids(pages["clients"]) == clients
    assert _ids(pages["locked"]) == {s["locked"]["id"]}
    assert _ids(pages["reported"]) == {s["reported"]["id"]}
    assert _ids(pages["unverified"]) == {s["unverified"]["id"]}
    assert _ids(pages["invitations"], "invitation") == {s["admin_invitation"]["id"], s["coordinator_invitation"]["id"]}
    assert _ids(pages["invitations"]) == set()
    assert _ids(pages["all"]) == staff | clients and len(pages["all"]["items"]) == 9

    expected = {"all": 9, "staff": 4, "clients": 3, "locked": 1, "reported": 1, "unverified": 1, "invitations": 2}
    for f in FILTERS:
        # Los contadores no dependen del filtro rápido elegido
        assert pages[f]["counts"] == expected
        assert len(pages[f]["items"]) == expected[f]


def test_search_narrows_counts(client, accounts_set):
    s = accounts_set
    page = _list(client, s["admin"], q=f"Reportada {s['tag']}")
    assert page["counts"] == {
        "all": 1, "staff": 0, "clients": 1, "locked": 0, "reported": 1, "unverified": 0, "invitations": 0,
    }


def test_coordinator_sees_only_technicians_and_clients(client, accounts_set):
    s = accounts_set
    coordinator = s["coordinator"]
    page = _list(client, coordinator, q=s["tag"])
    roles = {row["role"] for row in page["items"] if row["kind"] == "user"}
    assert roles == {"technician", "requester"}
    assert s["admin"]["id"] not in _ids(page) and coordinator["id"] not in _ids(page)
    # Las invitaciones son del administrador: el coordinador no ve ninguna
    assert _ids(page, "invitation") == set()
    assert page["counts"]["staff"] == 2 and page["counts"]["invitations"] == 0

    # Otro coordinador tampoco aparece, ni en la lista ni en el detalle
    other = client.get("/api/accounts", params={"filter": "staff"}, headers=coordinator["headers"]).json()
    assert all(row["role"] == "technician" for row in other["items"])
    assert _summary(client, coordinator, s["admin"]["id"]).status_code == 404
    assert _summary(client, coordinator, coordinator["id"]).status_code == 404


def test_coordinator_actions_stay_within_permissions(client, accounts_set):
    s = accounts_set
    coordinator = s["coordinator"]
    # Cuentas en solo lectura para operar: turnos, disponibilidad y pedir el cambio de rol
    technician = _summary(client, coordinator, s["technician"]["id"]).json()
    assert set(technician["actions"]) == {"change-role", "schedule", "availability"}
    locked = _summary(client, coordinator, s["locked"]["id"]).json()
    assert "unlock" not in locked["actions"]
    # Ve sus tickets y puede reportarla; revisar reportes es del administrador
    requester = _summary(client, coordinator, s["reported"]["id"]).json()
    assert set(requester["actions"]) == {"view-tickets", "report"}
    # Nunca: restablecer a un cliente, ver datos completos ni aprobar solicitudes
    for detail in (technician, locked, requester):
        assert "full-data" not in detail["actions"] and "approve-role" not in detail["actions"]
    assert "reset-access" not in requester["actions"]


def test_admin_actions_never_on_self_or_other_admin(client, accounts_set, make_user):
    s = accounts_set
    admin = s["admin"]
    other_admin = make_user("admin", f"Otro admin {s['tag']}")
    assert _summary(client, admin, admin["id"]).json()["actions"] == []
    assert _summary(client, admin, other_admin["id"]).json()["actions"] == []
    coordinator = _summary(client, admin, s["coordinator"]["id"]).json()
    assert {"change-role", "schedule", "reset-access", "deactivate"} <= set(coordinator["actions"])
    client_detail = _summary(client, admin, s["verified"]["id"]).json()
    assert {"full-data", "deactivate"} <= set(client_detail["actions"])
    # El administrador no entra al flujo de tickets: sin "Ver sus tickets"
    assert "reset-access" not in client_detail["actions"] and "view-tickets" not in client_detail["actions"]
    reported = _summary(client, admin, s["reported"]["id"]).json()
    assert "review-reports" in reported["actions"]


def test_masked_email_and_no_personal_data(client, accounts_set):
    s = accounts_set
    for viewer in (s["admin"], s["coordinator"]):
        page = client.get("/api/accounts", params={"q": s["tag"]}, headers=viewer["headers"])
        text = page.text
        for row in page.json()["items"]:
            assert "***@" in row["email"]
            assert "phone" not in row and "id_number" not in row
        detail = _summary(client, viewer, s["verified"]["id"])
        for body in (text, detail.text):
            assert s["verified"]["email"] not in body
            assert s["verified"]["id_number"] not in body
            assert s["verified"]["phone"] not in body
        assert "phone" not in detail.json() and "id_number" not in detail.json()


def test_open_tickets_and_recent_tickets(client, make_user, make_ticket):
    tag = uuid.uuid4().hex[:10]
    admin = make_user("admin")
    requester = make_user("requester", f"Cliente {tag}")
    tickets = [make_ticket(requester, title=f"Caso {i}") for i in range(7)]
    db = SessionLocal()
    try:
        db.get(models.Ticket, tickets[0]["id"]).status = "resolved"
        db.commit()
    finally:
        db.close()

    row = _list(client, admin, q=tag)["items"][0]
    assert row["open_tickets"] == 6
    recent = _summary(client, admin, requester["id"]).json()["recent_tickets"]
    assert [t["id"] for t in recent] == [t["id"] for t in reversed(tickets)][:5]
    assert set(recent[0]) == {"id", "title", "status"}


def test_staff_recent_changes_limited_to_five(client, make_user, sent):
    admin = make_user("admin")
    technician = make_user("technician")
    for _ in range(3):
        client.post(f"/api/accounts/{technician['id']}/deactivate", headers=admin["headers"])
        client.post(f"/api/accounts/{technician['id']}/reactivate", headers=admin["headers"])
    detail = _summary(client, admin, technician["id"]).json()
    assert len(detail["recent_changes"]) == 5
    assert detail["recent_tickets"] == []


def test_queue_filters_by_requester(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    mine = make_user("requester")
    other = make_user("requester")
    expected = {make_ticket(mine)["id"] for _ in range(2)}
    make_ticket(other)
    page = client.get("/api/queue", params={"view": "all", "requester_id": mine["id"]},
                      headers=coordinator["headers"]).json()
    assert {item["id"] for item in page["items"]} == expected


def test_cancel_invitation(client, make_user, sent):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    tag = uuid.uuid4().hex[:10]
    by_admin = _invite(client, admin, tag)
    second = _invite(client, admin, tag)

    # Solo el administrador cancela invitaciones
    assert client.delete(f"/api/staff-invitations/{by_admin['id']}", headers=coordinator["headers"]).status_code == 403
    assert client.delete(f"/api/staff-invitations/{second['id']}", headers=admin["headers"]).status_code == 204
    assert client.delete(f"/api/staff-invitations/{by_admin['id']}", headers=admin["headers"]).status_code == 204
    assert _list(client, admin, filter="invitations", q=tag)["items"] == []

    technician = make_user("technician")
    third = _invite(client, admin, tag)
    assert client.delete(f"/api/staff-invitations/{third['id']}", headers=technician["headers"]).status_code == 403

    changes = client.get("/api/admin/account-changes", params={"action": "invitation_cancelled"},
                         headers=admin["headers"]).json()
    assert changes["total"] >= 2


def test_cancel_used_invitation_conflicts(client, make_user, sent):
    admin = make_user("admin")
    used_by = make_user("technician")
    invitation = _invite(client, admin, uuid.uuid4().hex[:10])
    db = SessionLocal()
    try:
        row = db.get(models.StaffInvitation, invitation["id"])
        row.used_by = used_by["id"]
        row.used_at = _now()
        db.commit()
    finally:
        db.close()
    assert client.delete(f"/api/staff-invitations/{invitation['id']}", headers=admin["headers"]).status_code == 409


def test_schedule_filter_still_works(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician", f"Turno {uuid.uuid4().hex[:10]}")
    days = [{"weekday": 0, "start": "08:00", "end": "17:00"}]
    assert client.put(f"/api/accounts/{technician['id']}/schedule", json={"days": days},
                      headers=admin["headers"]).status_code == 204
    params = {"schedule_day": 0, "schedule_start": "09:00", "schedule_end": "12:00"}
    page = _list(client, admin, **params)
    row = next(r for r in page["items"] if r["id"] == technician["id"])
    assert row["schedule_day"] == "08:00–17:00"
    assert all(r["role"] in ("technician", "coordinator") for r in page["items"])
    assert page["counts"]["invitations"] == 0
    # Sin turno el martes: no aparece
    tuesday = _list(client, admin, schedule_day=1, schedule_start="09:00", schedule_end="12:00")
    assert technician["id"] not in _ids(tuesday)
