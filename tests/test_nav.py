from datetime import datetime, timedelta

import pytest

from app import alerts, config, mailer, models, nav
from app.database import SessionLocal

# Lunes 7 de abril de 2031 en Costa Rica (UTC-6). Oficina de 08:00 a 17:00.
MONDAY_8AM = datetime(2031, 4, 7, 14, 0)
MONDAY_9AM = datetime(2031, 4, 7, 15, 0)
FAR_AWAY = MONDAY_9AM + timedelta(days=30)

COORDINATOR_KEYS = {
    "unassigned", "due_soon", "overdue", "open", "resolved_today",
    "drafts_pending", "reports_open", "invitations_pending", "cancelled",
}
TECHNICIAN_KEYS = {
    "mine_open", "mine_due_soon", "following_open", "mine_resolved_today", "unassigned", "drafts_pending", "shift",
    "overdue",
}


@pytest.fixture(autouse=True)
def office(monkeypatch):
    monkeypatch.setattr(config, "ALERT_WARN_PERCENT", 75)
    monkeypatch.setattr(config, "OFFICE_DAYS", "1-5")
    monkeypatch.setattr(config, "OFFICE_START", "08:00")
    monkeypatch.setattr(config, "OFFICE_END", "17:00")
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    db = SessionLocal()
    try:
        # Los tickets abiertos de otras pruebas cambiarían los conteos
        db.query(models.Ticket).update({"status": "closed"})
        db.query(models.AlertSent).delete()
        db.query(models.EmailDraft).delete()
        db.commit()
    finally:
        db.close()


def _ticket(make_ticket, requester, *, due_at=FAR_AWAY, status="new", assigned_to=None, level=2):
    ticket_id = make_ticket(requester, title="Ticket del menú")["id"]
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, ticket_id)
        ticket.created_at = MONDAY_8AM
        ticket.status = status
        ticket.assigned_to = assigned_to
        if due_at is not None:
            db.add(models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=level, due_at=due_at))
        db.commit()
    finally:
        db.close()
    return ticket_id


def _resolve_at(ticket_id, user_id, when):
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket_id).status = "resolved"
        db.add(models.History(ticket_id=ticket_id, field="status", old_value="in_progress",
                              new_value="resolved", changed_by=user_id, changed_at=when))
        db.commit()
    finally:
        db.close()


def _counts(user_id, now=MONDAY_9AM):
    db = SessionLocal()
    try:
        return nav.counts(db, db.get(models.User, user_id), now)
    finally:
        db.close()


def test_due_state_same_as_alerts(make_user, make_ticket, monkeypatch):
    requester = make_user("requester")
    overdue = _ticket(make_ticket, requester, due_at=MONDAY_8AM + timedelta(minutes=30))
    # Creado 08:00, vence 10:00: a las 09:30 ya consumió el 75 %
    due_soon = _ticket(make_ticket, requester, due_at=MONDAY_8AM + timedelta(hours=2))
    on_time = _ticket(make_ticket, requester)
    no_due = _ticket(make_ticket, requester, due_at=None)
    now = MONDAY_8AM + timedelta(minutes=90)

    db = SessionLocal()
    try:
        states = {tid: alerts.due_state(db.get(models.Ticket, tid), now) for tid in (overdue, due_soon, on_time, no_due)}
    finally:
        db.close()
    assert states[overdue] == ("overdue", 100)
    assert states[due_soon] == ("due_soon", 75)
    assert states[on_time][0] == "on_time"
    assert states[no_due] == (None, None)

    # Las alertas eligen exactamente los mismos tickets
    make_user("coordinator")
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append(subject) or True)
    db = SessionLocal()
    try:
        alerts.run_alerts(db, now)
        rows = db.query(models.AlertSent.kind, models.AlertSent.ticket_id).filter(
            models.AlertSent.ticket_id.isnot(None)
        ).all()
    finally:
        db.close()
    assert {(k, t) for k, t in rows} == {("overdue", overdue), ("due_soon", due_soon)}

    coordinator = make_user("coordinator")
    counts = _counts(coordinator["id"], now)
    assert counts["overdue"] == 1 and counts["due_soon"] == 1


def test_coordinator_counts(make_user, make_ticket):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    _ticket(make_ticket, requester)
    _ticket(make_ticket, requester, due_at=None)
    _ticket(make_ticket, requester, status="in_progress", assigned_to=technician["id"],
            due_at=MONDAY_8AM + timedelta(minutes=30))
    resolved = _ticket(make_ticket, requester, status="in_progress", assigned_to=technician["id"])
    _resolve_at(resolved, technician["id"], MONDAY_9AM - timedelta(minutes=5))
    yesterday = _ticket(make_ticket, requester, status="in_progress", assigned_to=technician["id"])
    _resolve_at(yesterday, technician["id"], MONDAY_9AM - timedelta(days=1))

    counts = _counts(coordinator["id"])
    assert set(counts) == COORDINATOR_KEYS
    assert counts["unassigned"] == 2
    # El vencido se cuenta aparte, en Vencidos
    assert counts["open"] == 2
    assert counts["overdue"] == 1
    assert counts["due_soon"] == 0
    assert counts["resolved_today"] == 1


def test_admin_gets_approvals(make_user, client):
    admin = make_user("admin")
    response = client.get("/api/nav/counts", headers=admin["headers"])
    assert response.status_code == 200
    assert set(response.json()) == COORDINATOR_KEYS | {"approvals_pending"}


def test_technician_counts_only_own_scope(make_user, make_ticket, client, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "levels")
    technician = make_user("technician")
    other = make_user("technician")
    requester = make_user("requester")
    _ticket(make_ticket, requester, assigned_to=technician["id"], due_at=MONDAY_8AM + timedelta(minutes=80))
    _ticket(make_ticket, requester, assigned_to=other["id"], due_at=MONDAY_8AM + timedelta(minutes=80))
    followed = _ticket(make_ticket, requester, assigned_to=other["id"])
    _ticket(make_ticket, requester)
    mine_resolved = _ticket(make_ticket, requester, status="in_progress", assigned_to=technician["id"])
    _resolve_at(mine_resolved, technician["id"], MONDAY_9AM - timedelta(minutes=5))
    other_resolved = _ticket(make_ticket, requester, status="in_progress", assigned_to=other["id"])
    _resolve_at(other_resolved, other["id"], MONDAY_9AM - timedelta(minutes=5))
    db = SessionLocal()
    try:
        db.add(models.TicketFollower(ticket_id=followed, user_id=technician["id"]))
        db.add(models.TechnicianSchedule(user_id=technician["id"], weekday=0, start_time="08:00", end_time="16:00"))
        db.commit()
    finally:
        db.close()

    counts = _counts(technician["id"])
    assert set(counts) == TECHNICIAN_KEYS
    assert counts["mine_open"] == 1
    assert counts["mine_due_soon"] == 1
    assert counts["following_open"] == 1
    assert counts["mine_resolved_today"] == 1
    assert counts["unassigned"] == 1
    assert counts["overdue"] == 0
    assert counts["shift"] == {"start": "08:00", "end": "16:00", "on_shift": True, "load": 2}

    response = client.get("/api/nav/counts", headers=technician["headers"])
    assert response.status_code == 200
    body = response.json()
    assert set(body) == TECHNICIAN_KEYS
    for key in ("reports_open", "approvals_pending", "invitations_pending", "open"):
        assert key not in body


def test_technician_without_shift_today(make_user):
    technician = make_user("technician")
    shift = _counts(technician["id"])["shift"]
    assert shift == {"start": None, "end": None, "on_shift": False, "load": 0}


def test_requester_cannot_read_counts_or_status(make_user, client):
    requester = make_user("requester")
    assert client.get("/api/nav/counts", headers=requester["headers"]).status_code == 403
    assert client.get("/api/system/status", headers=requester["headers"]).status_code == 403


def test_system_status_only_flags_and_counts(make_user, client, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "count")
    technician = make_user("technician")
    response = client.get("/api/system/status", headers=technician["headers"])
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"email", "ai_analysis", "auto_assign", "auto_assign_mode", "technicians_on_shift"}
    assert body["email"] is True and body["auto_assign"] is True
    assert body["auto_assign_mode"] == "count"
    assert isinstance(body["technicians_on_shift"], int)
    text = response.text
    for secret in (config.APP_URL, config.SUPPORT_MAILBOX, config.MS_TENANT_ID, config.MS_CLIENT_ID):
        if secret:
            assert secret not in text


def test_auto_assign_mode_hidden_when_off(make_user, client, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    coordinator = make_user("coordinator")
    body = client.get("/api/system/status", headers=coordinator["headers"]).json()
    assert body["auto_assign"] is False and body["auto_assign_mode"] is None
