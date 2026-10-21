from app import chatbot, models
from app.database import SessionLocal

NOTE = "Nota interna: el cliente ya llamó dos veces"


def _follow(client, ticket_id, user):
    assert client.post(f"/api/tickets/{ticket_id}/follow", headers=user["headers"]).status_code == 204


def test_internal_note_is_hidden_from_requester(make_user, make_ticket, client, sent):
    technician = make_user("technician")
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    # Seguidores: el solicitante y otra persona del personal
    _follow(client, ticket_id, requester)
    _follow(client, ticket_id, coordinator)
    sent.clear()

    response = client.post(f"/api/tickets/{ticket_id}/comments",
                           json={"content": NOTE, "internal": True}, headers=technician["headers"])
    assert response.status_code == 200
    assert response.json()["internal"] is True

    # Ningún correo a los seguidores, ni al solicitante ni al personal
    assert sent == []

    own = client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"]).json()
    assert all(NOTE not in c["content"] for c in own["comments"])

    staff = client.get(f"/api/tickets/{ticket_id}", headers=coordinator["headers"]).json()
    notes = [c for c in staff["comments"] if c["content"] == NOTE]
    assert len(notes) == 1 and notes[0]["internal"] is True

    db = SessionLocal()
    try:
        context = chatbot._requester_context(db, db.get(models.User, requester["id"]))
    finally:
        db.close()
    assert all(NOTE not in line for line in context)


def test_public_reply_still_notifies(make_user, make_ticket, client, sent):
    technician = make_user("technician")
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    _follow(client, ticket_id, coordinator)
    sent.clear()

    response = client.post(f"/api/tickets/{ticket_id}/comments",
                           json={"content": "Respuesta al cliente"}, headers=technician["headers"])
    assert response.status_code == 200
    assert response.json()["internal"] is False
    assert any(to == coordinator["email"] for to, _subject, _body in sent)


def test_requester_cannot_add_internal_note(make_user, make_ticket, client):
    requester = make_user("requester")
    ticket_id = make_ticket(requester)["id"]
    response = client.post(f"/api/tickets/{ticket_id}/comments",
                           json={"content": NOTE, "internal": True}, headers=requester["headers"])
    assert response.status_code == 403
    db = SessionLocal()
    try:
        assert db.query(models.Comment).filter(models.Comment.content == NOTE,
                                               models.Comment.ticket_id == ticket_id).count() == 0
    finally:
        db.close()


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
