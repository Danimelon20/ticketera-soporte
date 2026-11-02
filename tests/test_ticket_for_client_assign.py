"""Asignación de los tickets creados a nombre de un cliente: en la misma petición, sin el técnico creador
(salvo que sea el único elegible), el aviso sin nivel y una fusión posterior."""
from datetime import datetime

import pytest

from app import audit, auto_assign, config, email_analysis, grouping, models
from app.database import SessionLocal

# Lunes 10 de marzo de 2031, 10:00 en Costa Rica
MONDAY_10AM = datetime(2031, 3, 10, 16, 0)
WEEKDAYS = [(d, "08:00", "17:00") for d in range(5)]
CREATE = "/api/tickets/for-client"


def _clean():
    db = SessionLocal()
    try:
        # Horarios y asignaciones de otras pruebas cambiarían los candidatos
        db.query(models.TicketAutoAssignment).delete()
        db.query(models.TechnicianSchedule).delete()
        db.query(models.TechnicianAvailability).delete()
        db.query(models.AvailabilityPeriod).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "levels")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)
    # Nunca Ollama: el análisis no corre en estas pruebas
    monkeypatch.setattr(email_analysis, "analyze_ticket_by_id", lambda ticket_id: None)
    _clean()
    yield
    _clean()


def _on_shift(user):
    db = SessionLocal()
    try:
        for weekday, start, end in WEEKDAYS:
            db.add(models.TechnicianSchedule(user_id=user["id"], weekday=weekday, start_time=start, end_time=end))
        db.commit()
    finally:
        db.close()
    return user


def _create(client, actor, customer, **extra):
    payload = {"client_email": customer["email"], "channel": "phone", "identity_check": "email",
               "title": "Sin internet", "description": "No hay conexión en la oficina",
               "category_id": 1, "priority_id": 1}
    payload.update(extra)
    response = client.post(CREATE, json=payload, headers=actor["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _ticket(ticket_id):
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, ticket_id)
        row = db.query(models.TicketAutoAssignment).filter_by(ticket_id=ticket_id).first()
        history = [h.field for h in db.query(models.History).filter_by(ticket_id=ticket_id).order_by(models.History.id)]
        return ticket.assigned_to, (row.status if row else None), history
    finally:
        db.close()


# ----- Los tres casos -----

def test_other_eligible_technician_gets_it(client, make_user):
    creator = _on_shift(make_user("technician", "Creador"))
    other = _on_shift(make_user("technician", "Otra Técnica"))
    data = _create(client, creator, make_user("requester"))
    # Asignado antes de responder, y no al creador
    assert data["assigned_name"] == "Otra Técnica"
    assigned_to, status, history = _ticket(data["ticket_id"])
    assert (assigned_to, status) == (other["id"], "assigned")
    assert history == ["created_on_behalf", "assigned_to"]


def test_creator_is_excluded_even_with_more_load_on_others(client, make_user):
    creator = _on_shift(make_user("technician", "Creador"))
    other = _on_shift(make_user("technician", "Otra Técnica"))
    customer = make_user("requester")
    # Otra Técnica ya tiene un ticket: igual no se le asigna al creador, que tiene menos carga
    _create(client, creator, customer)
    data = _create(client, creator, customer)
    assert _ticket(data["ticket_id"])[0] == other["id"]


def test_creator_only_eligible_gets_it(client, make_user):
    creator = _on_shift(make_user("technician", "Creador"))
    make_user("technician", "Sin Turno")  # sin horario: no es elegible
    data = _create(client, creator, make_user("requester"))
    assert data["assigned_name"] == "Creador"
    assert _ticket(data["ticket_id"])[:2] == (creator["id"], "assigned")


def test_nobody_eligible_stays_unassigned(client, make_user):
    creator = make_user("technician", "Creador")
    data = _create(client, creator, make_user("requester"))
    assert data["assigned_name"] is None
    assert _ticket(data["ticket_id"])[:2] == (None, "waiting")


def test_retry_keeps_excluding_creator(client, make_user):
    creator = make_user("technician", "Creador")
    other = make_user("technician", "Otra Técnica")
    data = _create(client, creator, make_user("requester"))
    assert _ticket(data["ticket_id"])[0] is None
    # Entran los dos a su turno: el reintento se lo da al otro
    _on_shift(creator)
    _on_shift(other)
    assert auto_assign.run_pending(MONDAY_10AM) == 1
    assert _ticket(data["ticket_id"])[:2] == (other["id"], "assigned")


def test_retry_gives_it_to_creator_when_only_eligible(client, make_user):
    creator = make_user("technician", "Creador")
    data = _create(client, creator, make_user("requester"))
    _on_shift(creator)
    assert auto_assign.run_pending(MONDAY_10AM) == 1
    assert _ticket(data["ticket_id"])[0] == creator["id"]


def test_creator_cannot_open_ticket_assigned_to_other(client, make_user):
    creator = _on_shift(make_user("technician"))
    other = _on_shift(make_user("technician"))
    data = _create(client, creator, make_user("requester"))
    assert client.get(f"/api/tickets/{data['ticket_id']}", headers=creator["headers"]).status_code == 403
    assert client.get(f"/api/tickets/{data['ticket_id']}", headers=other["headers"]).status_code == 200
    # Tampoco aparece en su bandeja
    response = client.get("/api/queue?view=mine_open", headers=creator["headers"])
    assert data["ticket_id"] not in [item["id"] for item in response.json()["items"]]


def test_coordinator_automatic_assignment(client, make_user):
    coordinator = make_user("coordinator")
    technician = _on_shift(make_user("technician", "Técnica en Turno"))
    data = _create(client, coordinator, make_user("requester"))
    assert data["assigned_name"] == "Técnica en Turno"
    assert _ticket(data["ticket_id"])[0] == technician["id"]


def test_auto_assign_disabled_leaves_it_pending(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    creator = _on_shift(make_user("technician"))
    _on_shift(make_user("technician"))
    data = _create(client, creator, make_user("requester"))
    assert data["assigned_name"] is None
    assert _ticket(data["ticket_id"])[:2] == (None, None)


# ----- Aviso al técnico asignado -----

def _notice(sent, email):
    [body] = [body for to, subject, body in sent if to == email and "Se le asignó" in subject]
    return body


def test_notice_without_analysis_says_pending(client, make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    creator = _on_shift(make_user("technician"))
    other = _on_shift(make_user("technician"))
    _create(client, creator, make_user("requester"))
    assert "Nivel: pendiente de análisis" in _notice(sent, other["email"]).splitlines()


def test_notice_without_ai_keeps_no_level(client, make_user, sent):
    # Con el análisis apagado no hay nada pendiente: sigue "Sin nivel"
    creator = _on_shift(make_user("technician"))
    other = _on_shift(make_user("technician"))
    _create(client, creator, make_user("requester"))
    assert "Nivel: Sin nivel" in _notice(sent, other["email"]).splitlines()


# ----- Fusión posterior -----

def test_later_merge_stays_in_history(client, make_user):
    creator = _on_shift(make_user("technician"))
    other = _on_shift(make_user("technician"))
    customer = make_user("requester")
    first = _create(client, creator, customer, title="No hay internet")
    second = _create(client, creator, customer, title="Sigo sin internet")
    db = SessionLocal()
    try:
        source, target = db.get(models.Ticket, second["ticket_id"]), db.get(models.Ticket, first["ticket_id"])
        grouping.merge_ticket(db, source, target, grouping.Match("merge", target.id, 0.93, "Mismo caso"),
                              actor_id=audit.agent_id(db))
        db.commit()
    finally:
        db.close()
    assigned_to, _, history = _ticket(second["ticket_id"])
    # Creado, asignado y después fusionado: el historial lo cuenta todo y la asignación se conserva
    assert history == ["created_on_behalf", "assigned_to", "merged"]
    assert assigned_to == other["id"]
    assert "merged_from" in _ticket(first["ticket_id"])[2]
