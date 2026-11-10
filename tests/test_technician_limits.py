"""Técnico: en Vencidos no cambia estado, prioridad, categoría ni nivel (el coordinador sí).
La separación de incidentes que conserva al técnico se prueba en test_grouping.py."""
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ticket(requester_id, technician_id, *, overdue=True, status="new"):
    """Ticket asignado con análisis; vencido si su fecha límite ya pasó."""
    db = SessionLocal()
    try:
        ticket = models.Ticket(
            title="El correo no sincroniza", description="Desde ayer", category_id=1, priority_id=1, status=status,
            assigned_to=technician_id, created_by=requester_id, source="web", created_at=_now() - timedelta(days=3),
        )
        db.add(ticket)
        db.flush()
        due = _now() - timedelta(hours=1) if overdue else _now() + timedelta(days=30)
        db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="done", ai_level=2, due_at=due))
        db.commit()
        return ticket.id
    finally:
        db.close()


def _status(ticket_id):
    db = SessionLocal()
    try:
        return db.get(models.Ticket, ticket_id).status
    finally:
        db.close()


@pytest.fixture
def people(make_user):
    return {"technician": make_user("technician"), "coordinator": make_user("coordinator"),
            "requester": make_user("requester")}


@pytest.mark.parametrize("change", [{"status": "in_progress"}, {"priority_id": 2}, {"category_id": 2}])
def test_technician_cannot_change_overdue_ticket(client, people, change):
    ticket_id = _ticket(people["requester"]["id"], people["technician"]["id"])
    response = client.patch(f"/api/tickets/{ticket_id}", json=change, headers=people["technician"]["headers"])
    assert response.status_code == 403
    assert "vencido" in response.json()["detail"]
    assert _status(ticket_id) == "new"


def test_technician_cannot_change_level_of_overdue_ticket(client, people):
    ticket_id = _ticket(people["requester"]["id"], people["technician"]["id"])
    response = client.patch(f"/api/tickets/{ticket_id}/ai-level", json={"level": 4},
                            headers=people["technician"]["headers"])
    assert response.status_code == 403
    db = SessionLocal()
    try:
        assert db.get(models.Ticket, ticket_id).ai_analysis.human_level is None
    finally:
        db.close()


def test_technician_still_comments_on_overdue_ticket(client, people):
    ticket_id = _ticket(people["requester"]["id"], people["technician"]["id"])
    response = client.post(f"/api/tickets/{ticket_id}/comments", json={"content": "Revisando con el proveedor"},
                           headers=people["technician"]["headers"])
    assert response.status_code in (200, 201), response.text


def test_coordinator_changes_overdue_ticket(client, people):
    ticket_id = _ticket(people["requester"]["id"], people["technician"]["id"])
    headers = people["coordinator"]["headers"]
    assert client.patch(f"/api/tickets/{ticket_id}", json={"status": "in_progress"}, headers=headers).status_code == 200
    assert client.patch(f"/api/tickets/{ticket_id}/ai-level", json={"level": 3}, headers=headers).status_code == 200


def test_technician_changes_ticket_on_time(client, people):
    ticket_id = _ticket(people["requester"]["id"], people["technician"]["id"], overdue=False)
    headers = people["technician"]["headers"]
    assert client.patch(f"/api/tickets/{ticket_id}", json={"status": "in_progress"}, headers=headers).status_code == 200
    assert client.patch(f"/api/tickets/{ticket_id}/ai-level", json={"level": 3}, headers=headers).status_code == 200
