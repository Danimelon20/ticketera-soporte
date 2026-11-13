import pytest

from app import mailer, models
from app.database import SessionLocal


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake_send(to, subject, body):
        calls.append((to, subject, body))
        return True

    monkeypatch.setattr(mailer, "send_email", _fake_send)
    return calls


def _patch(client, ticket_id, user, **data):
    response = client.patch(f"/api/tickets/{ticket_id}", json=data, headers=user["headers"])
    assert response.status_code == 200, response.text
    return response


def _followers_count(ticket_id, user_id):
    db = SessionLocal()
    try:
        return (
            db.query(models.TicketFollower)
            .filter(models.TicketFollower.ticket_id == ticket_id, models.TicketFollower.user_id == user_id)
            .count()
        )
    finally:
        db.close()


def _to(sent, user):
    return [call for call in sent if call[0] == user["email"]]


def _contact_follows(ticket_id, user_id):
    """El contacto sigue su ticket (como al llegar por correo: se agrega solo al crearlo)."""
    _old_follower(ticket_id, user_id)


def _old_follower(ticket_id, user_id):
    """Seguimiento del personal que quedó de antes del cambio (ya no se puede crear)."""
    db = SessionLocal()
    try:
        db.add(models.TicketFollower(ticket_id=ticket_id, user_id=user_id))
        db.commit()
    finally:
        db.close()


def test_status_change_notifies_requester_follower_only(client, make_user, make_ticket, sent):
    requester = make_user("requester", "Carla Solicitante")
    old_staff_follower = make_user("coordinator")
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Tomás Técnico")
    ticket = make_ticket(requester)

    _contact_follows(ticket["id"], requester["id"])
    _old_follower(ticket["id"], old_staff_follower["id"])
    _patch(client, ticket["id"], coordinator, assigned_to=technician["id"])
    _old_follower(ticket["id"], technician["id"])
    sent.clear()

    _patch(client, ticket["id"], technician, status="in_progress")

    mails = _to(sent, requester)
    assert len(mails) == 1
    _to_addr, subject, body = mails[0]
    assert subject == f"[Ticket #{ticket['id']}] Cambió de estado: En proceso"
    assert "Hola Carla Solicitante." in body
    assert "Tomás Técnico cambió el estado de Nuevo a En proceso." in body
    assert f"Título: {ticket['title']}" in body
    assert "Recibes este correo porque das seguimiento a este ticket." in body
    # Nunca datos personales del contacto
    assert "8888" not in body
    assert "0234" not in body

    # Las filas de seguimiento del personal que quedaron de antes ya no envían correo
    assert _to(sent, old_staff_follower) == []
    assert _to(sent, technician) == []
    assert _to(sent, coordinator) == []


def test_comment_notifies_with_text(client, make_user, make_ticket, sent):
    requester = make_user("requester", "Rosa")
    coordinator = make_user("coordinator", "Cora")
    ticket = make_ticket(requester)
    _contact_follows(ticket["id"], requester["id"])

    response = client.post(
        f"/api/tickets/{ticket['id']}/comments",
        json={"content": "Ya revisamos el equipo y quedó funcionando."},
        headers=coordinator["headers"],
    )
    assert response.status_code == 200

    mails = _to(sent, requester)
    assert len(mails) == 1
    assert mails[0][1] == f"[Ticket #{ticket['id']}] Nuevo comentario de Cora"
    assert "Ya revisamos el equipo y quedó funcionando." in mails[0][2]
    assert _to(sent, coordinator) == []


def test_status_and_assignment_send_one_email(client, make_user, make_ticket, sent):
    requester = make_user("requester")
    other_requester = make_user("requester")
    coordinator = make_user("coordinator", "Cora")
    technician = make_user("technician", "Teo")
    ticket = make_ticket(requester)
    _contact_follows(ticket["id"], requester["id"])

    _patch(client, ticket["id"], coordinator, status="in_progress", assigned_to=technician["id"])

    mails = _to(sent, requester)
    assert len(mails) == 1
    _to_addr, subject, body = mails[0]
    assert subject == f"[Ticket #{ticket['id']}] Cambió de estado: En proceso"
    assert "Cora cambió el estado de Nuevo a En proceso." in body
    assert "Cora asignó el ticket a Teo." in body
    assert _to(sent, other_requester) == []


def test_assignment_only_subject(client, make_user, make_ticket, sent):
    requester = make_user("requester")
    coordinator = make_user("coordinator", "Cora")
    technician = make_user("technician", "Teo")
    ticket = make_ticket(requester)
    _contact_follows(ticket["id"], requester["id"])

    _patch(client, ticket["id"], coordinator, assigned_to=technician["id"])

    mails = _to(sent, requester)
    assert len(mails) == 1
    assert mails[0][1] == f"[Ticket #{ticket['id']}] Nueva asignación"
    assert "Cora asignó el ticket a Teo." in mails[0][2]


def test_inactive_follower_is_skipped(client, make_user, make_ticket, sent):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    ticket = make_ticket(requester)
    _contact_follows(ticket["id"], requester["id"])

    db = SessionLocal()
    try:
        db.get(models.User, requester["id"]).is_active = False
        db.commit()
    finally:
        db.close()

    _patch(client, ticket["id"], coordinator, assigned_to=coordinator["id"])
    assert _to(sent, requester) == []


def test_following_view_no_longer_exists(client, make_user):
    technician = make_user("technician")
    response = client.get("/api/queue", params={"view": "following"}, headers=technician["headers"])
    assert response.status_code == 422


def test_follow_routes_no_longer_exist(client, make_user, make_ticket):
    """«Dar seguimiento» era del cliente con sesión: quien reporta por correo lo sigue desde que se crea el ticket."""
    coordinator = make_user("coordinator")
    ticket = make_ticket(make_user("requester"))
    for method in ("post", "delete"):
        response = getattr(client, method)(f"/api/tickets/{ticket['id']}/follow", headers=coordinator["headers"])
        assert response.status_code in (404, 405)
