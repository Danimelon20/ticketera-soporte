"""Cancelar tickets (coordinador y administrador) y reabrirlos."""
import pytest

from app import models
from app.database import SessionLocal


@pytest.fixture
def requester(make_user):
    return make_user("requester", name="Cliente Cancelación")


def _cancel(client, actor, ids, reason="duplicate", notify=True, note=None):
    payload = {"ticket_ids": ids, "reason": reason, "notify_requester": notify}
    if note is not None:
        payload["note"] = note
    return client.post("/api/tickets/cancel", json=payload, headers=actor["headers"])


def _queue_ids(client, actor, view):
    response = client.get(f"/api/queue?view={view}", headers=actor["headers"])
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _cancellation(ticket_id):
    db = SessionLocal()
    try:
        return db.query(models.TicketCancellation).filter(models.TicketCancellation.ticket_id == ticket_id).first()
    finally:
        db.close()


# Cancelar es del coordinador: el administrador no entra al flujo de tickets
@pytest.mark.parametrize("role", ["coordinator"])
def test_staff_with_permission_cancels(client, make_user, make_ticket, requester, sent, role):
    actor = make_user(role)
    ticket = make_ticket(requester, title="Ya no se necesita")

    response = _cancel(client, actor, [ticket["id"]], reason="no_longer_applies", note="Lo resolvió solo")
    assert response.status_code == 200, response.text
    body = response.json()[0]
    assert body["status"] == "closed"
    assert body["cancellation"]["reason"] == "no_longer_applies"
    assert body["cancellation"]["reason_label"] == "Ya no aplica"
    assert body["cancellation"]["note"] == "Lo resolvió solo"

    row = _cancellation(ticket["id"])
    assert row is not None and row.cancelled_by == actor["id"]
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=actor["headers"]).json()
    entry = [h for h in detail["history"] if h["field"] == "cancelled"]
    assert entry and entry[0]["new_value"] == "no_longer_applies" and entry[0]["old_value"] == "new"
    # El correo al cliente no lleva la nota interna
    to_requester = [s for s in sent if s[0] == requester["email"]]
    assert len(to_requester) == 1
    assert "Ya no aplica" in to_requester[0][2]
    assert "Lo resolvió solo" not in to_requester[0][2]


@pytest.mark.parametrize("role", ["technician", "requester", "admin"])
def test_others_cannot_cancel_or_reopen(client, make_user, make_ticket, requester, role):
    actor = requester if role == "requester" else make_user(role)
    ticket = make_ticket(requester)

    assert _cancel(client, actor, [ticket["id"]]).status_code == 403
    assert _cancellation(ticket["id"]) is None

    coordinator = make_user("coordinator")
    assert _cancel(client, coordinator, [ticket["id"]]).status_code == 200
    assert client.post(f"/api/tickets/{ticket['id']}/reopen", headers=actor["headers"]).status_code == 403
    assert _cancellation(ticket["id"]) is not None


def test_cancel_several_with_one_reason(client, make_user, make_ticket, requester):
    coordinator = make_user("coordinator")
    ids = [make_ticket(requester, title=f"Vencido {n}")["id"] for n in range(3)]

    response = _cancel(client, coordinator, ids, reason="no_response")
    assert response.status_code == 200, response.text
    assert [t["id"] for t in response.json()] == ids
    assert all(_cancellation(i).reason == "no_response" for i in ids)


def test_cancel_is_all_or_nothing(client, make_user, make_ticket, requester):
    coordinator = make_user("coordinator")
    first = make_ticket(requester)
    already = make_ticket(requester)
    assert _cancel(client, coordinator, [already["id"]]).status_code == 200

    response = _cancel(client, coordinator, [first["id"], already["id"]])
    assert response.status_code == 400
    assert _cancellation(first["id"]) is None
    assert client.get(f"/api/tickets/{first['id']}", headers=coordinator["headers"]).json()["status"] == "new"


def test_invalid_reason_is_rejected(client, make_user, make_ticket, requester):
    coordinator = make_user("coordinator")
    ticket = make_ticket(requester)
    assert _cancel(client, coordinator, [ticket["id"]], reason="otro").status_code == 422


def test_no_email_to_requester_when_unchecked(client, make_user, make_ticket, requester, sent):
    coordinator = make_user("coordinator")
    ticket = make_ticket(requester)
    assert _cancel(client, coordinator, [ticket["id"]], reason="test_spam", notify=False).status_code == 200
    assert not [s for s in sent if s[0] == requester["email"]]


def test_reopen_returns_to_queue_unassigned(client, make_user, make_ticket, requester):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    ticket = make_ticket(requester)
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket["id"]).assigned_to = technician["id"]
        db.commit()
    finally:
        db.close()
    assert _cancel(client, coordinator, [ticket["id"]]).status_code == 200

    response = client.post(f"/api/tickets/{ticket['id']}/reopen", headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "new"
    assert body["assigned_to"] is None
    assert body["cancellation"] is None
    assert _cancellation(ticket["id"]) is None
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert "reopened" in [h["field"] for h in detail["history"]]
    assert ticket["id"] in _queue_ids(client, coordinator, "unassigned")
    # Solo se reabre un cancelado
    assert client.post(f"/api/tickets/{ticket['id']}/reopen", headers=coordinator["headers"]).status_code == 400


def test_cancelled_leaves_work_queues_and_counts(client, make_user, make_ticket, requester):
    coordinator = make_user("coordinator")
    ticket = make_ticket(requester)
    before = client.get("/api/nav/counts", headers=coordinator["headers"]).json()
    assert ticket["id"] in _queue_ids(client, coordinator, "open")

    assert _cancel(client, coordinator, [ticket["id"]]).status_code == 200

    for view in ("open", "unassigned", "due_soon", "overdue", "resolved_today"):
        assert ticket["id"] not in _queue_ids(client, coordinator, view), view
    response = client.get("/api/queue?view=cancelled", headers=coordinator["headers"])
    row = [item for item in response.json()["items"] if item["id"] == ticket["id"]]
    assert row and row[0]["cancelled"] is True

    after = client.get("/api/nav/counts", headers=coordinator["headers"]).json()
    assert after["open"] == before["open"] - 1
    assert after["cancelled"] == before["cancelled"] + 1


def test_technician_cannot_see_cancelled_view(client, make_user):
    technician = make_user("technician")
    assert client.get("/api/queue?view=cancelled", headers=technician["headers"]).status_code == 403
    assert "cancelled" not in client.get("/api/nav/counts", headers=technician["headers"]).json()


def test_admin_health_counts_cancelled_apart(client, make_user, make_ticket, requester):
    admin = make_user("admin")
    ticket = make_ticket(requester)
    before = client.get("/api/admin/health", headers=admin["headers"]).json()["tickets_by_status"]
    # Cancela el coordinador; el administrador lo ve en Estado del sistema
    assert _cancel(client, make_user("coordinator"), [ticket["id"]]).status_code == 200

    after = client.get("/api/admin/health", headers=admin["headers"]).json()["tickets_by_status"]
    assert after["cancelled"] == before.get("cancelled", 0) + 1
    assert after.get("closed", 0) == before.get("closed", 0)


def test_requester_does_not_see_cancellation_reason(client, make_user, make_ticket, requester):
    coordinator = make_user("coordinator")
    ticket = make_ticket(requester)
    assert _cancel(client, coordinator, [ticket["id"]], note="Nota interna").status_code == 200
    body = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"]).json()
    assert body["status"] == "closed"
    assert body["cancellation"] is None
