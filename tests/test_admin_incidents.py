"""El administrador ve los incidentes en solo lectura: la lista y el ticket principal, sin teléfono ni cédula."""
from datetime import datetime, timezone

from app import models
from app.database import SessionLocal


def _incident(make_user, make_ticket):
    requester = make_user("requester")
    principal = make_ticket(requester, title="Se cayó el sistema de facturación")
    other = make_ticket(make_user("requester"), title="Otro problema sin incidente")
    db = SessionLocal()
    try:
        db.add(models.TicketIncident(principal_ticket_id=principal["id"],
                                     marked_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()
    finally:
        db.close()
    return principal, other


def test_admin_lists_incidents(client, make_user, make_ticket):
    admin = make_user("admin")
    principal, _other = _incident(make_user, make_ticket)
    response = client.get("/api/incidents", headers=admin["headers"])
    assert response.status_code == 200, response.text
    assert principal["id"] in [item["principal_id"] for item in response.json()]


def test_admin_opens_incident_principal_read_only_without_contact(client, make_user, make_ticket):
    admin = make_user("admin")
    principal, _other = _incident(make_user, make_ticket)
    response = client.get(f"/api/tickets/{principal['id']}", headers=admin["headers"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["read_only"] is True
    assert data.get("contact_phone") is None and data.get("contact_id_number") is None
    # Sin "Ver conversación": no queda registro de lectura con motivo
    db = SessionLocal()
    try:
        assert db.query(models.AccountChange).filter(
            models.AccountChange.action == "conversation_viewed",
            models.AccountChange.target_label == f"Ticket #{principal['id']}",
        ).count() == 0
    finally:
        db.close()


def test_admin_cannot_change_incident_principal(client, make_user, make_ticket):
    admin = make_user("admin")
    principal, _other = _incident(make_user, make_ticket)
    tid = principal["id"]
    assert client.post(f"/api/tickets/{tid}/comments", json={"content": "Hola"},
                       headers=admin["headers"]).status_code == 403
    assert client.patch(f"/api/tickets/{tid}", json={"status": "in_progress"},
                        headers=admin["headers"]).status_code == 403


def test_admin_still_cannot_open_other_tickets(client, make_user, make_ticket):
    admin = make_user("admin")
    _principal, other = _incident(make_user, make_ticket)
    assert client.get(f"/api/tickets/{other['id']}", headers=admin["headers"]).status_code == 403
    assert client.get("/api/tickets/999999", headers=admin["headers"]).status_code == 404
    assert client.get("/api/queue", headers=admin["headers"]).status_code == 403


def test_technician_still_cannot_list_incidents(client, make_user):
    assert client.get("/api/incidents", headers=make_user("technician")["headers"]).status_code == 403
