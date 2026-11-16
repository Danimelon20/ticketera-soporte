from app import chatbot, models
from app.database import SessionLocal

NOTE = "Nota interna: el cliente ya llamó dos veces"


def _follow(client, ticket_id, user):
    """El contacto sigue su ticket (como al llegar por correo: ya no hay botón «Dar seguimiento»)."""
    with SessionLocal() as db:
        db.add(models.TicketFollower(ticket_id=ticket_id, user_id=user["id"]))
        db.commit()


def test_public_reply_still_notifies(make_user, make_ticket, client, sent):
    technician = make_user("technician")
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    client.patch(f"/api/tickets/{ticket_id}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    _follow(client, ticket_id, requester)
    sent.clear()

    response = client.post(f"/api/tickets/{ticket_id}/comments",
                           json={"content": "Respuesta al cliente"}, headers=technician["headers"])
    assert response.status_code == 200
    assert response.json()["internal"] is False
    assert any(to == requester["email"] for to, _subject, _body in sent)


def test_no_internal_note_on_closed_ticket(make_user, make_ticket, client):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket_id).status = "closed"
        db.commit()
    finally:
        db.close()
    response = client.post(f"/api/tickets/{ticket_id}/comments",
                           json={"content": NOTE, "internal": True}, headers=coordinator["headers"])
    assert response.status_code == 400
