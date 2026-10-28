"""El administrador no entra al flujo de tickets: no lo ve ni lo asignan ni le avisan. Si necesita abrir un
ticket usa "Ver conversación", que pide un motivo corto y queda en el Registro de cambios."""
from datetime import datetime, timedelta, timezone

import pytest

from app import auto_assign, daily_summary, models
from app.database import SessionLocal

from test_attachments import PNG, upload

REASON = "Queja del cliente por el trato"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _view(client, admin, ticket_id, reason=REASON):
    body = {} if reason is None else {"reason": reason}
    return client.post(f"/api/tickets/{ticket_id}/view-conversation", json=body, headers=admin["headers"])


def _views(ticket_id):
    db = SessionLocal()
    try:
        return db.query(models.AccountChange).filter(
            models.AccountChange.action == "conversation_viewed",
            models.AccountChange.target_label == f"Ticket #{ticket_id}",
        ).order_by(models.AccountChange.id).all()
    finally:
        db.close()


@pytest.fixture
def ticket(make_user, make_ticket):
    return make_ticket(make_user("requester"), title="Conversación con el cliente")


def test_admin_cannot_open_ticket_without_reason(client, make_user, ticket):
    admin = make_user("admin")
    assert client.get(f"/api/tickets/{ticket['id']}", headers=admin["headers"]).status_code == 403
    for reason in (None, "", "   ", "ok"):
        assert _view(client, admin, ticket["id"], reason).status_code == 422, reason
    assert _view(client, admin, ticket["id"], "x" * 151).status_code == 422
    assert _views(ticket["id"]) == []
    assert _view(client, admin, 999999).status_code == 404


@pytest.mark.parametrize("role", ["technician", "coordinator", "requester"])
def test_only_admin_uses_view_conversation(client, make_user, ticket, role):
    assert _view(client, make_user(role), ticket["id"]).status_code == 403
    assert _views(ticket["id"]) == []


def test_view_is_recorded_with_who_ticket_reason_and_date(client, make_user, ticket):
    admin = make_user("admin")
    before = _now()
    response = _view(client, admin, ticket["id"], "  Queja   del cliente, llamar al 8888-1234  ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == ticket["id"] and body["read_only"] is True
    assert body["title"] == "Conversación con el cliente"

    [row] = _views(ticket["id"])
    assert row.actor_id == admin["id"]
    # Espacios de más fuera y el teléfono enmascarado: el motivo queda en el registro
    assert row.new_value == "Queja del cliente, llamar al [dato oculto]"
    assert row.created_at >= before - timedelta(seconds=1)
    listed = client.get("/api/admin/account-changes", params={"action": "conversation_viewed"},
                        headers=admin["headers"]).json()
    # En el Registro de cambios: quién, ticket, motivo y fecha
    item = next(i for i in listed["items"] if i["account"] == f"Ticket #{ticket['id']}")
    assert item["new_value"] == row.new_value and item["created_at"]
    assert item["actor"] == admin["name"]
    # Cada vez queda registrada; el detalle normal sigue cerrado
    _view(client, admin, ticket["id"])
    assert len(_views(ticket["id"])) == 2
    assert client.get(f"/api/tickets/{ticket['id']}", headers=admin["headers"]).status_code == 403


def test_admin_cannot_act_on_the_ticket(client, make_user, ticket):
    admin = make_user("admin")
    assert _view(client, admin, ticket["id"]).status_code == 200
    tid = ticket["id"]
    assert client.post(f"/api/tickets/{tid}/comments", json={"content": "Hola"}, headers=admin["headers"]).status_code == 403
    assert client.patch(f"/api/tickets/{tid}", json={"priority_id": 2}, headers=admin["headers"]).status_code == 403
    assert client.post(f"/api/tickets/{tid}/follow", headers=admin["headers"]).status_code == 403


def test_admin_attachments_only_one_hour_after_view(client, make_user, make_ticket, ticket):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    response = upload(client, ticket["id"], coordinator, [("captura.png", PNG)])
    assert response.status_code == 200, response.text
    attachment_id = response.json()["attachments"][0]["id"]
    other = make_ticket(make_user("requester"))
    other_attachment = upload(client, other["id"], coordinator, [("otra.png", PNG)]).json()["attachments"][0]["id"]
    url = f"/api/attachments/{attachment_id}"

    assert client.get(url, headers=admin["headers"]).status_code == 403
    _view(client, admin, ticket["id"])
    assert client.get(url, headers=admin["headers"]).status_code == 200
    # Solo los de ese ticket
    assert client.get(f"/api/attachments/{other_attachment}", headers=admin["headers"]).status_code == 403
    # Cada descarga sigue en el registro
    db = SessionLocal()
    try:
        row = db.query(models.AccountChange).filter(models.AccountChange.action == "attachment_downloaded") \
            .order_by(models.AccountChange.id.desc()).first()
        assert row.actor_id == admin["id"] and row.target_label == f"Ticket #{ticket['id']}"
        # Pasó más de una hora desde "Ver conversación"
        for view in db.query(models.AccountChange).filter(models.AccountChange.action == "conversation_viewed",
                                                          models.AccountChange.actor_id == admin["id"]):
            view.created_at = _now() - timedelta(hours=1, minutes=1)
        db.commit()
    finally:
        db.close()
    assert client.get(url, headers=admin["headers"]).status_code == 403
    # Otro administrador no aprovecha la ventana de este
    assert client.get(url, headers=make_user("admin")["headers"]).status_code == 403


def test_admin_is_never_a_candidate_nor_assignable(client, make_user, ticket):
    admin = make_user("admin")
    db = SessionLocal()
    try:
        for day in range(7):
            db.add(models.TechnicianSchedule(user_id=admin["id"], weekday=day, start_time="00:00", end_time="23:59"))
        db.commit()
        assert admin["id"] not in {u.id for u in auto_assign.candidates(db, _now())}
    finally:
        db.close()
    coordinator = make_user("coordinator")
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": admin["id"]},
                            headers=coordinator["headers"])
    assert response.status_code == 400


def test_admin_gets_no_ticket_notices(client, make_user, make_ticket, sent):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    # Primera revisión de avisos antes del vencimiento (la primera solo registra lo que ya había)
    client.get("/api/notifications/overdue", headers=coordinator["headers"])
    db = SessionLocal()
    try:
        # Seguía el ticket desde antes de este cambio, y el ticket está vencido
        db.add(models.TicketFollower(ticket_id=ticket_id, user_id=admin["id"]))
        db.add(models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=2,
                                       due_at=_now() - timedelta(hours=2)))
        db.get(models.Ticket, ticket_id).created_at = _now() - timedelta(days=2)
        db.commit()
        assert admin["email"] not in daily_summary.recipients(db)
    finally:
        db.close()
    sent.clear()
    client.post(f"/api/tickets/{ticket_id}/comments", json={"content": "Seguimos revisando"},
                headers=coordinator["headers"])
    client.patch(f"/api/tickets/{ticket_id}", json={"priority_id": 2}, headers=coordinator["headers"])
    assert all(to != admin["email"] for to, _, _ in sent)

    bell = client.get("/api/notifications/overdue", headers=admin["headers"]).json()
    assert bell == {"items": [], "unread": 0}
    coordinator_bell = client.get("/api/notifications/overdue", headers=coordinator["headers"]).json()
    assert any(i["ticket_id"] == ticket_id and i["kind"] == "report" for i in coordinator_bell["items"])
