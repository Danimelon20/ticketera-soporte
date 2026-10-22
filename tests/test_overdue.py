"""Vencidos aparte, avisos de la campana (50 %, 80 %, vencido y reporte), reporte de vencimiento,
su PDF y la causa."""
from datetime import datetime, timedelta

import pytest

from app import config, due_notices, models, nav
from app.database import SessionLocal
from app.routers import overdue as overdue_router

# Lunes 14 de abril de 2031 en Costa Rica (UTC-6). Oficina de 08:00 a 17:00.
MONDAY_8AM = datetime(2031, 4, 14, 14, 0)
# Plazo de 4 horas hábiles: vence a las 12:00
DUE = MONDAY_8AM + timedelta(hours=4)
AT_50 = MONDAY_8AM + timedelta(hours=2)  # 10:00
AT_85 = MONDAY_8AM + timedelta(hours=3, minutes=24)  # 11:24
AFTER_DUE = MONDAY_8AM + timedelta(hours=5)  # 13:00
PHONE = "88881234"
ID_NUMBER = "102340567"


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
        # Los tickets abiertos de otras pruebas cambiarían las colas y los avisos
        db.query(models.Ticket).update({"status": "closed"})
        db.commit()
        # Primera revisión ya hecha: lo que se cree en cada prueba sí avisa
        due_notices.sync(db, MONDAY_8AM)
    finally:
        db.close()


def _at(monkeypatch, now):
    monkeypatch.setattr(overdue_router, "_now", lambda: now)


def _ticket(requester, *, assigned_to=None, due_at=DUE, title="La impresora no imprime"):
    db = SessionLocal()
    try:
        ticket = models.Ticket(
            title=title, description="Llamar al 8888-1234", category_id=1, priority_id=1, status="new",
            assigned_to=assigned_to, created_by=requester["id"], source="web",
            contact_phone=PHONE, contact_id_number=ID_NUMBER, created_at=MONDAY_8AM,
        )
        db.add(ticket)
        db.flush()
        db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="done", ai_level=2, due_at=due_at))
        db.commit()
        return ticket.id
    finally:
        db.close()


def _all_day(user):
    db = SessionLocal()
    try:
        for day in range(7):
            db.add(models.TechnicianSchedule(user_id=user["id"], weekday=day, start_time="00:00", end_time="23:59"))
        db.commit()
    finally:
        db.close()


def _queue_ids(user, now, **params):
    db = SessionLocal()
    try:
        return {item["id"] for item in nav.queue(db, db.get(models.User, user["id"]), now=now, **params)["items"]}
    finally:
        db.close()


def _counts(user, now):
    db = SessionLocal()
    try:
        return nav.counts(db, db.get(models.User, user["id"]), now=now)
    finally:
        db.close()


def _notices(client, user):
    response = client.get("/api/notifications/overdue", headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _kinds(data, ticket_id):
    return {item["kind"] for item in data["items"] if item["ticket_id"] == ticket_id}


@pytest.fixture
def people(make_user):
    return {
        "requester": make_user("requester"),
        "technician": make_user("technician", name="Técnica Asignada"),
        "other_technician": make_user("technician"),
        "coordinator": make_user("coordinator"),
        "admin": make_user("admin"),
    }


# ----- Vistas y contadores -----

def test_overdue_only_in_overdue_view(people):
    coordinator, technician = people["coordinator"], people["technician"]
    late = _ticket(people["requester"], assigned_to=technician["id"])
    late_free = _ticket(people["requester"])
    on_time = _ticket(people["requester"], due_at=AFTER_DUE + timedelta(days=2))

    for view in ("open", "all", "unassigned"):
        ids = _queue_ids(coordinator, AFTER_DUE, view=view)
        assert late not in ids and late_free not in ids, view
    assert on_time in _queue_ids(coordinator, AFTER_DUE, view="open")
    assert _queue_ids(coordinator, AFTER_DUE, view="overdue") == {late, late_free}
    assert late not in _queue_ids(technician, AFTER_DUE, view="mine_open")
    # Buscar sí lo encuentra
    assert late in _queue_ids(coordinator, AFTER_DUE, view="all", search="impresora")


def test_technician_sees_only_own_overdue(people):
    technician = people["technician"]
    mine = _ticket(people["requester"], assigned_to=technician["id"])
    _ticket(people["requester"], assigned_to=people["other_technician"]["id"])
    _ticket(people["requester"])
    assert _queue_ids(technician, AFTER_DUE, view="overdue") == {mine}


def test_counts_put_overdue_apart(people):
    coordinator, technician = people["coordinator"], people["technician"]
    _ticket(people["requester"], assigned_to=technician["id"])
    _ticket(people["requester"])
    _ticket(people["requester"], due_at=AFTER_DUE + timedelta(days=2))

    counts = _counts(coordinator, AFTER_DUE)
    assert counts["overdue"] == 2
    assert counts["open"] == 1
    # Solo el que está a tiempo
    assert counts["unassigned"] == 1
    tech = _counts(technician, AFTER_DUE)
    assert tech["overdue"] == 1
    assert tech["mine_open"] == 0


# ----- Avisos de la campana -----

def test_requester_cannot_see_notices(client, people):
    requester = people["requester"]
    assert client.get("/api/notifications/overdue", headers=requester["headers"]).status_code == 403
    response = client.post("/api/notifications/overdue/read", json={"all": True}, headers=requester["headers"])
    assert response.status_code == 403


def test_notices_at_50_and_80_once(client, people, monkeypatch):
    technician, coordinator = people["technician"], people["coordinator"]
    _all_day(technician)
    ticket = _ticket(people["requester"], assigned_to=technician["id"])

    _at(monkeypatch, AT_50)
    data = _notices(client, technician)
    assert _kinds(data, ticket) == {"due_50"}
    # El coordinador no asignado no recibe 50 %
    assert _kinds(_notices(client, coordinator), ticket) == set()

    _at(monkeypatch, AT_85)
    assert _kinds(_notices(client, technician), ticket) == {"due_80"}
    # El responsable está en turno: el coordinador no lo recibe
    assert _kinds(_notices(client, coordinator), ticket) == set()
    _notices(client, technician)

    db = SessionLocal()
    try:
        kinds = [r.kind for r in db.query(models.TicketDueNotice).filter(models.TicketDueNotice.ticket_id == ticket)]
    finally:
        db.close()
    assert sorted(kinds) == ["due_50", "due_80"]


def test_80_with_unavailable_assignee_reaches_coordinators(client, people, make_user, monkeypatch):
    technician, coordinator = people["technician"], people["coordinator"]
    _all_day(make_user("technician", name="Técnico en turno"))
    # Su turno del lunes empieza a las 14:00: a las 11:24 no está en turno
    db = SessionLocal()
    try:
        db.add(models.TechnicianSchedule(user_id=technician["id"], weekday=0, start_time="14:00", end_time="17:00"))
        db.commit()
    finally:
        db.close()
    ticket = _ticket(people["requester"], assigned_to=technician["id"])

    _at(monkeypatch, AT_85)
    item = [i for i in _notices(client, coordinator)["items"] if i["ticket_id"] == ticket]
    assert item and item[0]["kind"] == "due_80"
    assert item[0]["suggestion"] is not None
    # El responsable también, pero sin la sugerencia (no puede reasignar)
    own = [i for i in _notices(client, technician)["items"] if i["ticket_id"] == ticket]
    assert own and own[0]["suggestion"] is None
    # El administrador no recibe avisos de vencimiento
    assert _kinds(_notices(client, people["admin"]), ticket) == set()


def test_overdue_notice_and_report_by_role(client, people, monkeypatch):
    technician = people["technician"]
    ticket = _ticket(people["requester"], assigned_to=technician["id"])

    _at(monkeypatch, AFTER_DUE)
    assert _kinds(_notices(client, technician), ticket) == {"overdue"}
    assert _kinds(_notices(client, people["other_technician"]), ticket) == set()
    # Coordinadores y administradores: solo el reporte, pendiente de revisar
    for role in ("coordinator", "admin"):
        items = [i for i in _notices(client, people[role])["items"] if i["ticket_id"] == ticket]
        assert [i["kind"] for i in items] == ["report"], role
        assert items[0]["needs_review"] is True


def test_assigned_coordinator_gets_own_notices(client, people, monkeypatch):
    coordinator = people["coordinator"]
    ticket = _ticket(people["requester"], assigned_to=coordinator["id"])
    _at(monkeypatch, AFTER_DUE)
    assert _kinds(_notices(client, coordinator), ticket) == {"overdue", "report"}


def test_mark_one_and_all_read(client, people, monkeypatch):
    technician = people["technician"]
    first = _ticket(people["requester"], assigned_to=technician["id"])
    second = _ticket(people["requester"], assigned_to=technician["id"])
    _at(monkeypatch, AFTER_DUE)
    data = _notices(client, technician)
    assert data["unread"] >= 2

    response = client.post("/api/notifications/overdue/read", json={"ticket_id": first}, headers=technician["headers"])
    assert response.status_code == 204
    read = {i["ticket_id"]: i["read"] for i in _notices(client, technician)["items"]}
    assert read[first] is True and read[second] is False

    client.post("/api/notifications/overdue/read", json={"all": True}, headers=technician["headers"])
    data = _notices(client, technician)
    assert data["unread"] == 0


def test_first_sync_registers_without_notifying(client, people, monkeypatch):
    technician = people["technician"]
    db = SessionLocal()
    try:
        db.query(models.OverdueNoticeSeen).delete()
        db.query(models.TicketDueNotice).delete()
        db.query(models.DueNoticesInstall).delete()
        db.commit()
    finally:
        db.close()
    old = _ticket(people["requester"], assigned_to=technician["id"], due_at=AT_50)
    new = _ticket(people["requester"], assigned_to=technician["id"])

    # Primera revisión: el que ya venció y el que va por el 50 % no avisan
    _at(monkeypatch, AT_50 + timedelta(minutes=30))
    assert _notices(client, technician)["items"] == []
    assert old in _queue_ids(people["coordinator"], AT_50 + timedelta(minutes=30), view="overdue")

    # Lo que se cruza después sí avisa
    _at(monkeypatch, AT_85)
    assert _kinds(_notices(client, technician), new) == {"due_80"}
    assert _kinds(_notices(client, technician), old) == set()


# ----- Reporte de vencimiento -----

def test_report_permissions(client, people, monkeypatch):
    technician = people["technician"]
    ticket = _ticket(people["requester"], assigned_to=technician["id"])
    not_due = _ticket(people["requester"], due_at=AFTER_DUE + timedelta(days=2))
    _at(monkeypatch, AFTER_DUE)
    url = f"/api/tickets/{ticket}/overdue-report"

    for role in ("coordinator", "admin", "technician"):
        response = client.get(url, headers=people[role]["headers"])
        assert response.status_code == 200, (role, response.text)
    assert client.get(url, headers=people["other_technician"]["headers"]).status_code == 403
    assert client.get(url, headers=people["requester"]["headers"]).status_code == 403
    response = client.get(f"/api/tickets/{not_due}/overdue-report", headers=people["coordinator"]["headers"])
    assert response.status_code == 400

    body = client.get(url, headers=people["coordinator"]["headers"]).json()
    assert body["overdue_seconds"] == 3600
    assert body["assigned_name"] == "Técnica Asignada"
    assert [e["kind"] for e in body["timeline"]][0] == "created"
    assert body["timeline"][-1]["kind"] == "overdue"
    assert body["cause"] is None and body["can_record_cause"] is True
    # Técnico asignado: cédula enmascarada, como en el detalle
    tech = client.get(url, headers=technician["headers"]).json()
    assert tech["contact_id_number"] != ID_NUMBER and tech["can_record_cause"] is False


def test_report_pdf(client, people, monkeypatch):
    coordinator = people["coordinator"]
    ticket = _ticket(people["requester"], assigned_to=people["technician"]["id"])
    not_due = _ticket(people["requester"], due_at=AFTER_DUE + timedelta(days=2))
    _at(monkeypatch, AFTER_DUE)
    url = f"/api/tickets/{ticket}/overdue-report.pdf"

    response = client.get(url, headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert f'filename="vencimiento-ticket-{ticket}-2031-04-14.pdf"' in response.headers["content-disposition"]
    content = response.content
    assert content.startswith(b"%PDF")
    assert b"Reporte de vencimiento" in content
    # Siempre enmascarados, aunque el coordinador los vea completos en pantalla
    assert PHONE.encode() not in content and ID_NUMBER.encode() not in content
    assert b"8888-1234" not in content

    assert client.get(url, headers=people["other_technician"]["headers"]).status_code == 403
    assert client.get(url, headers=people["requester"]["headers"]).status_code == 403
    response = client.get(f"/api/tickets/{not_due}/overdue-report.pdf", headers=coordinator["headers"])
    assert response.status_code == 400

    db = SessionLocal()
    try:
        row = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.action == "overdue_report_downloaded",
                    models.AccountChange.target_label == f"Ticket #{ticket}")
            .all()
        )
    finally:
        db.close()
    assert len(row) == 1 and row[0].actor_id == coordinator["id"]


def test_cause_permissions_and_review(client, people, monkeypatch):
    ticket = _ticket(people["requester"], assigned_to=people["technician"]["id"])
    _at(monkeypatch, AFTER_DUE)
    url = f"/api/tickets/{ticket}/overdue-cause"
    payload = {"cause": "high_load"}

    for role in ("technician", "requester"):
        assert client.post(url, json=payload, headers=people[role]["headers"]).status_code == 403, role
    other = client.post(url, json={"cause": "other"}, headers=people["coordinator"]["headers"])
    assert other.status_code == 422

    response = client.post(url, json=payload, headers=people["coordinator"]["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["cause"]["label"] == "Carga alta"
    response = client.post(url, json={"cause": "other", "note": "Falla eléctrica"}, headers=people["admin"]["headers"])
    assert response.status_code == 200
    assert response.json()["cause"]["note"] == "Falla eléctrica"

    items = [i for i in _notices(client, people["coordinator"])["items"] if i["ticket_id"] == ticket]
    assert items and items[0]["needs_review"] is False
