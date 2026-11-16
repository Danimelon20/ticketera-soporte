"""Fase 3 de la clasificación de correos: con «Tickets por departamento» encendido, los borradores son del
coordinador del departamento. Una sola lista (filtrada en el servidor, 403 a otro departamento), sin revisores
ni asignación a técnicos, y al confirmar se elige un técnico activo del departamento (422 si falta o es de
otro). El ticket nace con el departamento del borrador. Apagado, todo sigue como antes."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app import auto_assign, availability, config, mailer, models, nav, routing
from app.database import SessionLocal
from app.due_notices import items_for
from app.routers import drafts as drafts_router
from org_helpers import place, team, unique

CONFIRM = {"title": "Impresora", "description": "No imprime.", "category_id": 1, "priority_id": 1}


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    monkeypatch.setattr(mailer, "send_email", lambda *args, **kwargs: True)
    with SessionLocal() as db:
        db.query(models.EmailDraft).delete()
        db.commit()


def _draft(requester, department_id=None, status="routed"):
    """Borrador pendiente; con department_id, ya clasificado en ese departamento (o «Por clasificar»)."""
    with SessionLocal() as db:
        draft = models.EmailDraft(
            message_id=f"<fase3-{uuid4().hex}@test>", requester_id=requester["id"], status="pending_review",
            subject="Impresora", body="No imprime", title="Impresora", description="No imprime.",
            category_id=1, priority_id=1, ai_level=2,
        )
        db.add(draft)
        db.flush()
        if status is not None:
            db.add(models.DraftClassification(draft_id=draft.id, status=status, department_id=department_id,
                                              created_at=draft.created_at))
        db.commit()
        return draft.id


def _ids(client, user):
    response = client.get("/api/drafts", headers=user["headers"])
    assert response.status_code == 200, response.text
    return [d["id"] for d in response.json()]


def _confirm(client, user, draft_id, **extra):
    return client.post(f"/api/drafts/{draft_id}/confirm", headers=user["headers"], json={**CONFIRM, **extra})


@pytest.fixture
def org(make_user):
    """Dos departamentos con coordinador y técnico."""
    redes, redes_coord, redes_tech = team(make_user)
    soporte, soporte_coord, soporte_tech = team(make_user)
    return {"redes": redes, "soporte": soporte, "redes_coord": redes_coord, "soporte_coord": soporte_coord,
            "redes_tech": redes_tech, "soporte_tech": soporte_tech}


# ----- Una sola lista por departamento -----

def test_coordinator_sees_only_drafts_of_their_department(client, make_user, org, routing_on):
    requester = make_user("requester")
    mine = _draft(requester, org["redes"])
    classified = _draft(requester, org["redes"], status="classified")
    other = _draft(requester, org["soporte"])
    waiting = _draft(requester, None, status="pending")
    unclassified = _draft(requester, None, status=None)
    ids = _ids(client, org["redes_coord"])
    assert mine in ids and classified in ids
    assert other not in ids and waiting not in ids and unclassified not in ids
    # Los filtros de revisor ya no existen: siempre la misma lista
    for assigned in ("mine", "none"):
        assert [d["id"] for d in client.get(f"/api/drafts?assigned={assigned}",
                                            headers=org["redes_coord"]["headers"]).json()] == ids
    # Sin revisor ni reasignar
    row = next(d for d in client.get("/api/drafts", headers=org["redes_coord"]["headers"]).json() if d["id"] == mine)
    assert row["assigned_to"] is None and row["can_reassign"] is False and row["can_review"] is True
    # Contador del menú: los mismos
    with SessionLocal() as db:
        coordinator = db.get(models.User, org["redes_coord"]["id"])
        assert nav.counts(db, coordinator)["drafts_pending"] == 2


def test_coordinator_without_department_sees_nothing(client, make_user, org, routing_on):
    _draft(make_user("requester"), org["redes"])
    loose = make_user("coordinator", unique("Coordinador sin departamento"))
    assert _ids(client, loose) == []


def test_other_department_gets_403_on_every_action(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["soporte"])
    headers = org["redes_coord"]["headers"]
    for path in ("confirm", "discard", "request-details"):
        body = {**CONFIRM, "assigned_to": org["redes_tech"]["id"]} if path == "confirm" else None
        response = client.post(f"/api/drafts/{draft_id}/{path}", headers=headers, json=body)
        assert response.status_code == 403, path
        assert response.json()["detail"] == drafts_router.OTHER_DEPARTMENT
    # «Por clasificar» tampoco: lo decide el administrador
    waiting = _draft(make_user("requester"), None, status="pending")
    assert client.post(f"/api/drafts/{waiting}/discard", headers=headers).status_code == 403
    with SessionLocal() as db:
        assert db.get(models.EmailDraft, draft_id).status == "pending_review"


def test_technicians_no_longer_see_drafts(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["redes"])
    # Asignado a un técnico antes de encender: ya no lo ve, no le cuenta en la carga ni le avisa
    with SessionLocal() as db:
        db.add(models.DraftAssignment(draft_id=draft_id, status="assigned", user_id=org["redes_tech"]["id"],
                                      notify=True, created_at=datetime.now()))
        db.commit()
    headers = org["redes_tech"]["headers"]
    response = client.get("/api/drafts", headers=headers)
    assert response.status_code == 403 and response.json()["detail"] == drafts_router.TECHNICIANS_NO_DRAFTS
    assert client.post(f"/api/drafts/{draft_id}/confirm", headers=headers, json=CONFIRM).status_code == 403
    assert client.get("/api/drafts/technicians", headers=headers).status_code == 403
    with SessionLocal() as db:
        technician = db.get(models.User, org["redes_tech"]["id"])
        assert auto_assign.loads(db, [technician.id])[technician.id] == 0
        assert not [i for i in items_for(db, technician) if i["kind"] == "draft_assigned"]
        assert nav.counts(db, technician)["drafts_pending"] == 0


def test_drafts_are_not_assigned_to_technicians(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["redes"])
    response = client.post(f"/api/drafts/{draft_id}/assign", headers=org["redes_coord"]["headers"],
                           json={"user_id": org["redes_tech"]["id"]})
    assert response.status_code == 403 and response.json()["detail"] == drafts_router.NO_DRAFT_ASSIGNMENT
    with SessionLocal() as db:
        assert auto_assign.draft_row(db, draft_id) is None


# ----- Confirmar con un técnico del departamento -----

def test_confirm_requires_an_active_technician_of_the_department(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["redes"])
    coordinator = org["redes_coord"]
    missing = _confirm(client, coordinator, draft_id)
    assert missing.status_code == 422 and missing.json()["detail"] == drafts_router.TECHNICIAN_REQUIRED
    inactive = make_user("technician", unique("Inactivo"))
    place(inactive["id"], org["redes"])
    with SessionLocal() as db:
        db.get(models.User, inactive["id"]).is_active = False
        db.commit()
    for user_id in (org["soporte_tech"]["id"], inactive["id"], org["soporte_coord"]["id"], 999999):
        response = _confirm(client, coordinator, draft_id, assigned_to=user_id)
        assert response.status_code == 422, user_id
        assert response.json()["detail"] == drafts_router.TECHNICIAN_NOT_IN_DEPARTMENT
    with SessionLocal() as db:
        assert db.get(models.EmailDraft, draft_id).status == "pending_review"
        assert db.get(models.EmailDraft, draft_id).ticket_id is None


def test_confirm_creates_the_ticket_in_the_department_with_the_technician(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["redes"])
    response = _confirm(client, org["redes_coord"], draft_id, assigned_to=org["redes_tech"]["id"])
    assert response.status_code == 200, response.text
    ticket_id = response.json()["ticket_id"]
    with SessionLocal() as db:
        ticket = db.get(models.Ticket, ticket_id)
        assert ticket.assigned_to == org["redes_tech"]["id"]
        assert routing.ticket_department(db, ticket_id).id == org["redes"]
        assert db.query(models.History).filter(models.History.ticket_id == ticket_id,
                                               models.History.field == "assigned_to").count() == 1
        assert db.get(models.EmailDraft, draft_id).reviewed_by == org["redes_coord"]["id"]
    # Ya no está en la lista
    assert draft_id not in _ids(client, org["redes_coord"])


def test_unavailable_technician_needs_confirmation(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["redes"])
    today = availability.office_today()
    with SessionLocal() as db:
        db.add(models.AvailabilityPeriod(user_id=org["redes_tech"]["id"], kind="vacation", created_at=datetime.now(),
                                         start_date=today - timedelta(days=1), end_date=today + timedelta(days=3)))
        db.commit()
    first = _confirm(client, org["redes_coord"], draft_id, assigned_to=org["redes_tech"]["id"])
    assert first.status_code == 409 and "vacaciones" in first.json()["detail"]
    second = _confirm(client, org["redes_coord"], draft_id, assigned_to=org["redes_tech"]["id"],
                      confirm_unavailable=True)
    assert second.status_code == 200, second.text


def test_technician_selector_shows_load_and_availability(client, make_user, org, routing_on):
    other_tech = make_user("technician", unique("Técnico de vacaciones"))
    place(other_tech["id"], org["redes"])
    today = availability.office_today()
    with SessionLocal() as db:
        db.add(models.AvailabilityPeriod(user_id=other_tech["id"], kind="vacation", created_at=datetime.now(),
                                         start_date=today, end_date=today + timedelta(days=2)))
        db.add(models.Ticket(title="Abierto", description="x", category_id=1, priority_id=1, status="new",
                             created_by=org["redes_coord"]["id"], assigned_to=org["redes_tech"]["id"]))
        db.commit()
    response = client.get("/api/drafts/technicians", headers=org["redes_coord"]["headers"])
    assert response.status_code == 200, response.text
    by_id = {t["id"]: t for t in response.json()}
    # Solo los técnicos activos de su departamento
    assert set(by_id) == {org["redes_tech"]["id"], other_tech["id"]}
    assert by_id[org["redes_tech"]["id"]]["load"] >= 1
    assert by_id[org["redes_tech"]["id"]]["availability"] == "available"
    assert by_id[other_tech["id"]]["availability"] == "vacation"
    assert by_id[other_tech["id"]]["availability_text"].startswith("Vacaciones")


# ----- Apagado: como antes -----

def test_switch_off_keeps_reviewers(client, make_user, org, routing_switch):
    requester = make_user("requester")
    draft_id = _draft(requester, status=None)
    technician = org["redes_tech"]
    assert client.post(f"/api/drafts/{draft_id}/assign", headers=org["soporte_coord"]["headers"],
                       json={"user_id": technician["id"]}).status_code == 200
    # El técnico ve el suyo y lo confirma sin elegir a nadie: queda a su cargo
    assert _ids(client, technician) == [draft_id]
    response = _confirm(client, technician, draft_id)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        ticket_id = response.json()["ticket_id"]
        assert db.get(models.Ticket, ticket_id).assigned_to == technician["id"]
        assert routing.ticket_department(db, ticket_id) is None
    assert client.get("/api/drafts/technicians", headers=org["redes_coord"]["headers"]).json() == []
