import logging
from datetime import datetime

import pytest

from app import auto_assign, config, models
from app.database import SessionLocal

# Lunes 10 de marzo de 2031, 10:00 en Costa Rica
MONDAY_10AM = datetime(2031, 3, 10, 16, 0)
NOTICE = "Se le asignó"


def _notices(sent, email):
    return [(subject, body) for to, subject, body in sent if to == email and NOTICE in subject]


def _to(sent, email):
    return [(subject, body) for to, subject, body in sent if to == email]


def _assign(client, actor, ticket_id, user_id):
    response = client.patch(f"/api/tickets/{ticket_id}", json={"assigned_to": user_id}, headers=actor["headers"])
    assert response.status_code == 200, response.text
    return response


def _set_level(ticket_id, level):
    db = SessionLocal()
    try:
        db.add(models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=level))
        db.commit()
    finally:
        db.close()


def test_manual_assignment_notifies_technician(client, make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Ana Rojas")
    ticket = make_ticket(make_user("requester"), title="Impresora sin tinta")
    _set_level(ticket["id"], 3)
    sent.clear()
    _assign(client, coordinator, ticket["id"], technician["id"])
    notices = _notices(sent, technician["email"])
    assert len(notices) == 1
    subject, body = notices[0]
    assert subject == f"[Ticket #{ticket['id']}] Se le asignó: Impresora sin tinta"
    assert f"#{ticket['id']}" in body
    assert "Impresora sin tinta" in body
    assert "Nivel 3" in body
    assert config.APP_URL in body
    assert "Buen día, Ana Rojas." in body


def test_self_assignment_sends_nothing(client, make_user, make_ticket, sent):
    # El técnico ya no se asigna tickets (ni los ve): 403 y ningún correo
    technician = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    sent.clear()
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]},
                            headers=technician["headers"])
    assert response.status_code == 403
    assert _to(sent, technician["email"]) == []
    # El coordinador que se asigna a sí mismo tampoco recibe aviso
    coordinator = make_user("coordinator")
    _assign(client, coordinator, ticket["id"], coordinator["id"])
    assert _to(sent, coordinator["email"]) == []


def test_assigning_coordinator_sends_no_technician_notice(client, make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    other = make_user("coordinator")
    ticket = make_ticket(make_user("requester"))
    sent.clear()
    _assign(client, coordinator, ticket["id"], other["id"])
    assert _notices(sent, other["email"]) == []


def test_follower_technician_gets_one_email(client, make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    other_follower = make_user("coordinator")
    ticket = make_ticket(make_user("requester"))
    assert client.post(f"/api/tickets/{ticket['id']}/follow", headers=other_follower["headers"]).status_code == 204
    # El técnico ya no puede seguir un ticket ajeno: el seguimiento viene de antes de este cambio
    db = SessionLocal()
    try:
        db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=technician["id"]))
        db.commit()
    finally:
        db.close()
    sent.clear()
    _assign(client, coordinator, ticket["id"], technician["id"])
    # Un solo correo: el aviso de asignación, no además el de seguidor
    emails = _to(sent, technician["email"])
    assert len(emails) == 1 and NOTICE in emails[0][0]
    # Los demás seguidores reciben el aviso de siempre
    assert len(_to(sent, other_follower["email"])) == 1


def test_reassignment_notifies_new_technician_only(client, make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    first = make_user("technician")
    second = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    _assign(client, coordinator, ticket["id"], first["id"])
    sent.clear()
    _assign(client, coordinator, ticket["id"], second["id"])
    assert len(_notices(sent, second["email"])) == 1
    assert _notices(sent, first["email"]) == []
    # Guardar la misma asignación otra vez no avisa de nuevo
    sent.clear()
    _assign(client, coordinator, ticket["id"], second["id"])
    assert _notices(sent, second["email"]) == []


def test_notice_has_no_protected_data(client, make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    response = client.post("/api/tickets", json={
        "title": "Llamar al 8765-4321 por la cédula 1-0987-0654",
        "description": "Mi cédula es 109870654 y mi teléfono 8765-4321. Descripción privada.",
        "category_id": 1, "priority_id": 1, "contact_phone": "8765-4321",
    }, headers=requester["headers"])
    ticket = response.json()
    from app import comments

    db = SessionLocal()
    try:
        comments.add_internal(db, ticket["id"], coordinator["id"], "Nota interna secreta")
        db.commit()
    finally:
        db.close()
    sent.clear()
    _assign(client, coordinator, ticket["id"], technician["id"])
    subject, body = _notices(sent, technician["email"])[0]
    text = f"{subject}\n{body}"
    for secret in ("8765", "4321", "109870654", "0987", requester["id_number"], requester["phone"],
                   "Descripción privada", "Nota interna secreta", requester["email"]):
        assert secret not in text


def test_without_email_goes_to_dev_log(client, make_user, make_ticket, monkeypatch, caplog):
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    ticket = make_ticket(make_user("requester"), title="Pantalla azul")
    with caplog.at_level(logging.WARNING):
        _assign(client, coordinator, ticket["id"], technician["id"])
    marks = [r.getMessage() for r in caplog.records if "[SOLO DESARROLLO] Aviso de asignación" in r.getMessage()]
    assert marks == [f"[SOLO DESARROLLO] Aviso de asignación del ticket #{ticket['id']} para {technician['email'][:2]}***@test.com"]
    # El log no lleva el correo completo ni el título
    assert technician["email"] not in caplog.text
    assert "Pantalla azul" not in caplog.text


# ----- Asignación automática -----

@pytest.fixture
def auto(monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    db = SessionLocal()
    try:
        db.query(models.TicketAutoAssignment).delete()
        db.query(models.TechnicianSchedule).delete()
        db.query(models.TechnicianAvailability).delete()
        db.query(models.AvailabilityPeriod).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(models.TechnicianSchedule).delete()
        db.commit()
    finally:
        db.close()


def _on_shift_technician(make_user):
    technician = make_user("technician", "Luis Mora")
    db = SessionLocal()
    try:
        db.add(models.TechnicianSchedule(user_id=technician["id"], weekday=0, start_time="08:00", end_time="17:00"))
        db.commit()
    finally:
        db.close()
    return technician


def _unassigned_ticket(requester, level):
    db = SessionLocal()
    try:
        ticket = models.Ticket(
            title="Correo no sincroniza", description="Descripción", category_id=1, priority_id=1,
            status="new", created_by=requester["id"], source="web",
        )
        db.add(ticket)
        db.flush()
        db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="done", ai_level=level))
        db.commit()
        return ticket.id
    finally:
        db.close()


def test_automatic_assignment_notifies_technician(make_user, sent, auto):
    technician = _on_shift_technician(make_user)
    ticket = _unassigned_ticket(make_user("requester"), level=4)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "assigned"
    notices = _notices(sent, technician["email"])
    assert len(notices) == 1
    subject, body = notices[0]
    assert subject == f"[Ticket #{ticket}] Se le asignó: Correo no sincroniza"
    assert "Nivel 4" in body and config.APP_URL in body


def test_automatic_assignment_follower_gets_one_email(make_user, sent, auto):
    technician = _on_shift_technician(make_user)
    requester = make_user("requester")
    ticket = _unassigned_ticket(requester, level=2)
    db = SessionLocal()
    try:
        for user in (technician, requester):
            db.add(models.TicketFollower(ticket_id=ticket, user_id=user["id"]))
        db.commit()
    finally:
        db.close()
    auto_assign.assign_new(ticket, MONDAY_10AM)
    emails = _to(sent, technician["email"])
    assert len(emails) == 1 and NOTICE in emails[0][0]
    # El solicitante que sigue el ticket recibe el aviso de seguidor, no el del técnico
    requester_emails = _to(sent, requester["email"])
    assert len(requester_emails) == 1 and NOTICE not in requester_emails[0][0]
