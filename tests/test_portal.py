from datetime import datetime, timedelta

import pytest

from app import comments, models, portal
from app.database import SessionLocal


@pytest.mark.parametrize("status, assigned, step", [
    ("new", None, 1),
    ("new", 5, 2),
    ("in_progress", 5, 3),
    ("resolved", 5, 4),
    ("closed", 5, 4),
    ("closed", None, 4),
])
def test_progress_step(status, assigned, step):
    assert portal.progress_step(models.Ticket(status=status, assigned_to=assigned)) == step


def test_requester_sees_only_own_tickets(make_user, make_ticket, client):
    requester = make_user("requester")
    other = make_user("requester")
    own = make_ticket(requester, title="Mi impresora")["id"]
    foreign = make_ticket(other, title="Ajeno")["id"]

    listed = client.get("/api/tickets", headers=requester["headers"]).json()
    ids = [t["id"] for t in listed]
    assert own in ids and foreign not in ids
    assert all(t["created_by"] == requester["id"] for t in listed)
    mine = next(t for t in listed if t["id"] == own)
    assert mine["progress_step"] == 1 and mine["last_activity_at"]

    assert client.get(f"/api/tickets/{foreign}", headers=requester["headers"]).status_code == 403


def test_requester_detail_without_staff_data(make_user, make_ticket, client):
    requester = make_user("requester")
    technician = make_user("technician")
    coordinator = make_user("coordinator")
    ticket_id = make_ticket(requester)["id"]
    client.post(f"/api/tickets/{ticket_id}/comments", json={"content": "Nota solo del equipo", "internal": True},
                headers=technician["headers"])
    db = SessionLocal()
    try:
        db.add(models.AccountReport(target_user_id=requester["id"], reporter_id=coordinator["id"],
                                    reason="other", status="open", created_at=datetime(2031, 1, 1)))
        db.add(models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=2,
                                       due_at=datetime(2031, 1, 2)))
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"])
    assert response.status_code == 200
    body = response.json()
    assert all(not c["internal"] for c in body["comments"])
    assert "Nota solo del equipo" not in response.text
    for key in ("requester_flags", "ai_analysis", "due_state", "consumed_pct"):
        assert key not in body
    # Ni identidad de Hacienda ni reportes
    for word in ("unverified", "reported", "Reportada", "fake_data"):
        assert word not in response.text

    staff = client.get(f"/api/tickets/{ticket_id}", headers=coordinator["headers"]).json()
    assert staff["requester_flags"]["reported"] is True


def test_last_activity_ignores_internal_notes(make_user, make_ticket, client):
    requester = make_user("requester")
    technician = make_user("technician")
    ticket_id = make_ticket(requester)["id"]
    old = datetime(2020, 1, 1, 12, 0)
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket_id).updated_at = old
        db.commit()
        comments.add_internal(db, ticket_id, technician["id"], "Revisar mañana")
        db.commit()
    finally:
        db.close()

    def last_activity():
        listed = client.get("/api/tickets", headers=requester["headers"]).json()
        return next(t for t in listed if t["id"] == ticket_id)["last_activity_at"]

    assert last_activity().startswith("2020-01-01T12:00")
    client.post(f"/api/tickets/{ticket_id}/comments", json={"content": "Ya lo revisamos"},
                headers=technician["headers"])
    assert not last_activity().startswith("2020-01-01")
    detail = client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"]).json()
    assert not detail["last_activity_at"].startswith("2020-01-01")


def test_progress_follows_status_changes(make_user, make_ticket, client):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    ticket_id = make_ticket(requester)["id"]

    def step():
        return client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"]).json()["progress_step"]

    assert step() == 1
    client.patch(f"/api/tickets/{ticket_id}", json={"assigned_to": technician["id"], "confirm_unavailable": True},
                 headers=coordinator["headers"])
    assert step() == 2
    client.patch(f"/api/tickets/{ticket_id}", json={"status": "in_progress"}, headers=coordinator["headers"])
    assert step() == 3
    client.patch(f"/api/tickets/{ticket_id}", json={"status": "resolved"}, headers=coordinator["headers"])
    assert step() == 4


def test_requester_cannot_use_staff_routes(make_user, make_ticket, client):
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    for url in ("/api/queue", "/api/nav/counts", "/api/system/status", f"/api/tickets/{ticket_id}/suggestion",
                "/api/admin/accounts"):
        assert client.get(url, headers=requester["headers"]).status_code == 403, url
