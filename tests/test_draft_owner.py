"""Borradores con dueño: el revisor se elige por carga (tickets abiertos + borradores pendientes) entre los
técnicos en turno; sin nadie en turno queda "Sin revisor" hasta que el ciclo lo asigna. Solo el revisor o un
coordinador lo confirma o descarta, y quien confirma queda como responsable del ticket."""
from datetime import date, datetime

import pytest

from app import auto_assign, config, models
from app.database import SessionLocal

# Lunes 10 de marzo de 2031 en Costa Rica (UTC-6): 10:00 en turno; domingo, nadie
MONDAY_10AM = datetime(2031, 3, 10, 16, 0)
SUNDAY_10AM = datetime(2031, 3, 9, 16, 0)
WEEKDAYS = [(d, "08:00", "17:00") for d in range(5)]
ALL_DAY = [(d, "00:00", "23:59") for d in range(7)]
CONFIRM = {"title": "Impresora", "description": "No imprime.", "category_id": 1, "priority_id": 1}


def _clean(installed=True):
    db = SessionLocal()
    try:
        # Horarios, asignaciones y borradores de otras pruebas cambiarían los candidatos y la carga
        db.query(models.TicketAutoAssignment).delete()
        db.query(models.TechnicianSchedule).delete()
        db.query(models.TechnicianAvailability).delete()
        db.query(models.AvailabilityPeriod).delete()
        db.query(models.EmailDraft).delete()
        db.query(models.DraftAssignmentInstall).delete()
        if installed:
            db.add(models.DraftAssignmentInstall(installed_at=SUNDAY_10AM))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "count")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    _clean()
    yield
    _clean()


def _tech(make_user, name="Técnico", days=WEEKDAYS):
    user = make_user("technician", name)
    db = SessionLocal()
    try:
        for weekday, start, end in days:
            db.add(models.TechnicianSchedule(user_id=user["id"], weekday=weekday, start_time=start, end_time=end))
        db.commit()
    finally:
        db.close()
    return user


def _draft(requester, level=2):
    db = SessionLocal()
    try:
        draft = models.EmailDraft(
            message_id=f"<dueño-{datetime.now().timestamp()}-{requester['id']}@test>", requester_id=requester["id"],
            status="pending_review", subject="Impresora", body="No imprime", title="Impresora",
            description="No imprime.", category_id=1, priority_id=1, ai_level=level,
        )
        db.add(draft)
        db.commit()
        return draft.id
    finally:
        db.close()


def _open_ticket(requester, assigned_to):
    db = SessionLocal()
    try:
        ticket = models.Ticket(title="Abierto", description="Carga", category_id=1, priority_id=1, status="new",
                               created_by=requester["id"], assigned_to=assigned_to, source="web")
        db.add(ticket)
        db.commit()
        return ticket.id
    finally:
        db.close()


def _row(draft_id):
    db = SessionLocal()
    try:
        return auto_assign.draft_row(db, draft_id)
    finally:
        db.close()


def _reviewer(draft_id):
    row = _row(draft_id)
    return row.user_id if row else None


def _bell(client, user):
    response = client.get("/api/notifications/overdue", headers=user["headers"])
    assert response.status_code == 200, response.text
    return [i for i in response.json()["items"] if i["kind"] == "draft_assigned"]


def _loads(user_id):
    db = SessionLocal()
    try:
        return auto_assign.loads(db, [user_id])[user_id]
    finally:
        db.close()


def test_draft_goes_to_least_loaded_and_counts_as_load(make_user):
    requester = make_user("requester")
    busy = _tech(make_user, "Ocupada")
    free = _tech(make_user, "Libre")
    for _ in range(2):
        _open_ticket(requester, busy["id"])

    first, second, third = (_draft(requester) for _ in range(3))
    assert auto_assign.assign_draft(first, MONDAY_10AM) == "assigned"
    assert _reviewer(first) == free["id"]
    # Carga = tickets abiertos + borradores pendientes: 1 contra 2
    assert auto_assign.assign_draft(second, MONDAY_10AM) == "assigned"
    assert _reviewer(second) == free["id"]
    assert _loads(free["id"]) == 2 == _loads(busy["id"])
    # Empate: el de id menor
    auto_assign.assign_draft(third, MONDAY_10AM)
    assert _reviewer(third) == busy["id"]


def test_levels_mode_counts_the_draft_level(make_user, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "levels")
    technician = _tech(make_user)
    draft = _draft(make_user("requester"), level=4)
    auto_assign.assign_draft(draft, MONDAY_10AM)
    assert _loads(technician["id"]) == 4


def test_without_anyone_on_shift_it_waits_and_the_cycle_assigns_it(client, make_user):
    coordinator = make_user("coordinator")
    technician = _tech(make_user)
    draft = _draft(make_user("requester"))

    assert auto_assign.assign_draft(draft, SUNDAY_10AM) == "waiting"
    without = client.get("/api/drafts?assigned=none", headers=coordinator["headers"]).json()
    item = next(d for d in without if d["id"] == draft)
    assert item["assigned_to"] is None and item["assigned_name"] is None
    assert item["can_review"] is True and item["can_reassign"] is True
    # El técnico no ve los que no son suyos
    assert all(d["id"] != draft for d in client.get("/api/drafts", headers=technician["headers"]).json())

    # Entra alguien en turno: el mismo ciclo de la asignación automática lo asigna y avisa
    assert auto_assign.run_pending_drafts(MONDAY_10AM) == 1
    assert _reviewer(draft) == technician["id"]
    mine = client.get("/api/drafts", headers=technician["headers"]).json()
    item = next(d for d in mine if d["id"] == draft)
    assert item["assigned_name"] == "Técnico" and item["can_review"] is True and item["can_reassign"] is False
    notices = _bell(client, technician)
    assert [(n["draft_id"], n["read"], n["ticket_id"]) for n in notices] == [(draft, False, None)]


def test_first_run_assigns_existing_drafts_without_notices(client, make_user):
    _clean(installed=False)
    technician = _tech(make_user)
    requester = make_user("requester")
    existing = _draft(requester)

    assert auto_assign.run_pending_drafts(MONDAY_10AM) == 1
    assert _reviewer(existing) == technician["id"]
    assert _row(existing).notify is False
    assert _bell(client, technician) == []
    # Los que llegan después sí avisan
    new = _draft(requester)
    auto_assign.assign_draft(new, MONDAY_10AM)
    assert [n["draft_id"] for n in _bell(client, technician)] == [new]


def test_auto_assign_off_leaves_drafts_without_reviewer(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    _tech(make_user, days=ALL_DAY)
    coordinator = make_user("coordinator")
    draft = _draft(make_user("requester"))
    assert auto_assign.assign_draft(draft) == "disabled"
    assert auto_assign.run_pending_drafts() == 0
    without = client.get("/api/drafts?assigned=none", headers=coordinator["headers"]).json()
    assert draft in {d["id"] for d in without}


def test_other_technician_cannot_open_someone_elses_draft(client, make_user):
    technician = _tech(make_user)
    other = _tech(make_user, "Otro")
    draft = _draft(make_user("requester"))
    auto_assign.assign_draft(draft, MONDAY_10AM)
    owner, stranger = (technician, other) if _reviewer(draft) == technician["id"] else (other, technician)

    assert all(d["id"] != draft for d in client.get("/api/drafts", headers=stranger["headers"]).json())
    for path, body in ((f"/api/drafts/{draft}/confirm", CONFIRM), (f"/api/drafts/{draft}/discard", None),
                       (f"/api/drafts/{draft}/request-details", None)):
        response = client.post(path, json=body, headers=stranger["headers"])
        assert response.status_code == 403, path
        assert response.json()["detail"] == "Este borrador está asignado a otra persona"
    assert client.post(f"/api/drafts/{draft}/discard", headers=owner["headers"]).status_code == 200


def test_confirming_technician_stays_responsible(client, make_user):
    technician = _tech(make_user, days=ALL_DAY)
    # Otra técnica en turno y sin carga: la asignación automática la elegiría a ella
    idle = _tech(make_user, "Sin carga", days=ALL_DAY)
    requester = make_user("requester")
    for _ in range(3):
        _open_ticket(requester, technician["id"])
    draft = _draft(requester)
    db = SessionLocal()
    try:
        db.add(models.DraftAssignment(draft_id=draft, status="assigned", user_id=technician["id"],
                                      assigned_at=MONDAY_10AM, notify=True, created_at=MONDAY_10AM))
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/drafts/{draft}/confirm", json=CONFIRM, headers=technician["headers"])
    assert response.status_code == 200, response.text
    ticket_id = response.json()["ticket_id"]
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, ticket_id)
        assert ticket.assigned_to == technician["id"]
        entry = db.query(models.History).filter(models.History.ticket_id == ticket_id,
                                                models.History.field == "assigned_to").one()
        assert entry.changed_by == technician["id"]
    finally:
        db.close()
    # Ni el primer intento ni el ciclo lo cambian
    assert auto_assign.assign_new(ticket_id) == "skipped"
    auto_assign.run_pending()
    assert _get_assignee(ticket_id) == technician["id"] != idle["id"]
    assert _row(draft).status == "done"
    # Ya no cuenta como carga
    assert _loads(technician["id"]) == 4


def _get_assignee(ticket_id):
    db = SessionLocal()
    try:
        return db.get(models.Ticket, ticket_id).assigned_to
    finally:
        db.close()


def test_coordinator_confirm_gives_ticket_to_the_reviewer(client, make_user, sent):
    reviewer = _tech(make_user, "Revisora", days=ALL_DAY)
    coordinator = make_user("coordinator")
    draft = _draft(make_user("requester"))
    auto_assign.assign_draft(draft)
    assert _reviewer(draft) == reviewer["id"]

    response = client.post(f"/api/drafts/{draft}/confirm", json=CONFIRM, headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    ticket_id = response.json()["ticket_id"]
    assert _get_assignee(ticket_id) == reviewer["id"]
    # Le llega el aviso de asignación de siempre
    assert any(to == reviewer["email"] and "Se le asignó" in subject for to, subject, _ in sent)


def test_coordinator_confirm_without_reviewer_uses_auto_assign(client, make_user, sent):
    candidate = _tech(make_user, "Candidata", days=ALL_DAY)
    coordinator = make_user("coordinator")
    draft = _draft(make_user("requester"))
    response = client.post(f"/api/drafts/{draft}/confirm", json=CONFIRM, headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    assert _get_assignee(response.json()["ticket_id"]) == candidate["id"]


def test_coordinator_reassigns_and_new_reviewer_gets_bell_notice(client, make_user):
    first = _tech(make_user, "Primera")
    second = _tech(make_user, "Segunda")
    coordinator = make_user("coordinator")
    draft = _draft(make_user("requester"))
    auto_assign.assign_draft(draft, MONDAY_10AM)
    assert _reviewer(draft) == first["id"]

    for actor in (first, second, make_user("admin")):
        response = client.post(f"/api/drafts/{draft}/assign", json={"user_id": second["id"]}, headers=actor["headers"])
        assert response.status_code == 403
    # Solo técnicos: ni coordinadores, ni el administrador, ni solicitantes
    for target in (coordinator, make_user("admin"), make_user("requester")):
        response = client.post(f"/api/drafts/{draft}/assign", json={"user_id": target["id"]},
                               headers=coordinator["headers"])
        assert response.status_code == 400
    response = client.post(f"/api/drafts/{draft}/assign",
                           json={"user_id": second["id"], "confirm_unavailable": True}, headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to"] == second["id"]
    assert response.json()["assigned_name"] == "Segunda"

    notices = _bell(client, second)
    assert [(n["draft_id"], n["read"]) for n in notices] == [(draft, False)]
    assert client.post("/api/notifications/overdue/read", json={"draft_id": draft},
                       headers=second["headers"]).status_code == 204
    assert [n["read"] for n in _bell(client, second)] == [True]
    # El anterior ya no lo ve ni lo decide
    assert _bell(client, first) == []
    assert client.post(f"/api/drafts/{draft}/discard", headers=first["headers"]).status_code == 403


def test_reassigning_to_unavailable_technician_needs_confirmation(client, make_user):
    away = _tech(make_user, "De vacaciones")
    coordinator = make_user("coordinator")
    today = date.today()
    db = SessionLocal()
    try:
        db.add(models.AvailabilityPeriod(user_id=away["id"], kind="vacation", start_date=today, end_date=today,
                                         created_at=MONDAY_10AM))
        db.commit()
    finally:
        db.close()
    draft = _draft(make_user("requester"))
    url = f"/api/drafts/{draft}/assign"
    assert client.post(url, json={"user_id": away["id"]}, headers=coordinator["headers"]).status_code == 409
    assert client.post(url, json={"user_id": away["id"], "confirm_unavailable": True},
                       headers=coordinator["headers"]).status_code == 200
