"""Transferir a otro departamento: motivo obligatorio (15+), historial, el técnico de afuera sale de su bandeja,
el vencimiento no se reinicia y el coordinador destino recibe el aviso en su campana."""
from datetime import datetime, timedelta, timezone

from app import models
from app.database import SessionLocal
from org_helpers import history, set_department, team

REASON = "Es un problema de red, no de impresoras"


def _setup(client, make_user, make_ticket):
    dept_a, coordinator_a, technician_a = team(make_user)
    dept_b, coordinator_b, _ = team(make_user)
    ticket = make_ticket(make_user("requester"))
    set_department(ticket["id"], dept_a)
    due = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)
    db = SessionLocal()
    try:
        db.add(models.TicketAIAnalysis(ticket_id=ticket["id"], status="done", ai_level=2, due_at=due))
        db.commit()
    finally:
        db.close()
    client.patch(f"/api/tickets/{ticket['id']}", headers=coordinator_a["headers"],
                 json={"assigned_to": technician_a["id"], "status": "in_progress"})
    return ticket, dept_b, coordinator_b, technician_a, due


def test_transfer_with_reason_history_clock_and_notice(client, make_user, make_ticket, routing_on):
    ticket, dept_b, coordinator_b, technician_a, due = _setup(client, make_user, make_ticket)
    short = client.post(f"/api/tickets/{ticket['id']}/transfer", headers=technician_a["headers"],
                        json={"department_id": dept_b, "reason": "corto"})
    assert short.status_code == 422
    response = client.post(f"/api/tickets/{ticket['id']}/transfer", headers=technician_a["headers"],
                           json={"department_id": dept_b, "reason": REASON})
    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        row = db.get(models.Ticket, ticket["id"])
        # Sale de la bandeja del técnico (no es del destino) y el reloj no se reinicia
        assert row.assigned_to is None and row.status == "new"
        assert row.ai_analysis.due_at == due
    finally:
        db.close()
    [entry] = history(ticket["id"], "department")
    assert entry.new_value.startswith("Transferido de ") and f"por {technician_a['name']}: {REASON}" in entry.new_value
    bell = client.get("/api/notifications/overdue", headers=coordinator_b["headers"]).json()
    assert any(i["kind"] == "ticket_transferred" and i["ticket_id"] == ticket["id"] for i in bell["items"])
    # El coordinador destino lo ve
    assert client.get(f"/api/tickets/{ticket['id']}", headers=coordinator_b["headers"]).status_code == 200


def test_transfer_needs_the_switch(client, make_user, make_ticket):
    dept_a, coordinator_a, _ = team(make_user)
    dept_b, _, _ = team(make_user)
    ticket = make_ticket(make_user("requester"))
    set_department(ticket["id"], dept_a)
    response = client.post(f"/api/tickets/{ticket['id']}/transfer", headers=coordinator_a["headers"],
                           json={"department_id": dept_b, "reason": REASON})
    assert response.status_code == 409
