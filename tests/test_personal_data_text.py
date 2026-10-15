from app import models
from app.database import SessionLocal

DESCRIPTION = "No abre el sistema. Mi cédula es 1-0234-0567."
COMMENT = "Pueden llamarme al 8888-1234"


def _ticket_with_personal_data(client, make_user):
    """Ticket de un solicitante con la cédula en la descripción y el teléfono en un comentario."""
    requester = make_user("requester")
    payload = {
        "title": "No abre el sistema",
        "description": DESCRIPTION,
        "category_id": 1,
        "priority_id": 1,
        "contact_phone": "8888-1234",
        "contact_id_number": "1-0234-0567",
    }
    response = client.post("/api/tickets", json=payload, headers=requester["headers"])
    assert response.status_code == 200, response.text
    ticket = response.json()
    response = client.post(f"/api/tickets/{ticket['id']}/comments", json={"content": COMMENT},
                           headers=requester["headers"])
    assert response.status_code == 200, response.text
    return requester, ticket


def _assign(client, ticket_id, technician, make_user):
    coordinator = make_user("coordinator")
    response = client.patch(f"/api/tickets/{ticket_id}", json={"assigned_to": technician["id"]},
                            headers=coordinator["headers"])
    assert response.status_code == 200, response.text


def _detail(client, ticket_id, user):
    response = client.get(f"/api/tickets/{ticket_id}", headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _listed(client, ticket_id, user):
    response = client.get("/api/tickets", headers=user["headers"])
    assert response.status_code == 200, response.text
    return next(t for t in response.json() if t["id"] == ticket_id)


def test_unassigned_technician_sees_hidden_data(client, make_user):
    _requester, ticket = _ticket_with_personal_data(client, make_user)
    technician = make_user("technician")

    detail = _detail(client, ticket["id"], technician)
    assert detail["description"] == "No abre el sistema. Mi cédula es [dato oculto]."
    assert detail["comments"][0]["content"] == "Pueden llamarme al [dato oculto]"
    assert "0567" not in detail["description"] and "1234" not in detail["comments"][0]["content"]
    assert _listed(client, ticket["id"], technician)["description"] == detail["description"]


def test_technician_assigned_to_another_sees_hidden_data(client, make_user):
    _requester, ticket = _ticket_with_personal_data(client, make_user)
    _assign(client, ticket["id"], make_user("technician"), make_user)
    other = make_user("technician")

    detail = _detail(client, ticket["id"], other)
    assert "[dato oculto]" in detail["description"]
    assert "[dato oculto]" in detail["comments"][0]["content"]


def test_assigned_technician_sees_original_text(client, make_user):
    _requester, ticket = _ticket_with_personal_data(client, make_user)
    technician = make_user("technician")
    _assign(client, ticket["id"], technician, make_user)

    detail = _detail(client, ticket["id"], technician)
    assert detail["description"] == DESCRIPTION
    assert detail["comments"][0]["content"] == COMMENT
    assert _listed(client, ticket["id"], technician)["description"] == DESCRIPTION


def test_coordinator_admin_and_owner_see_original_text(client, make_user):
    requester, ticket = _ticket_with_personal_data(client, make_user)
    for user in (make_user("coordinator"), make_user("admin"), requester):
        detail = _detail(client, ticket["id"], user)
        assert detail["description"] == DESCRIPTION
        assert detail["comments"][0]["content"] == COMMENT
        assert _listed(client, ticket["id"], user)["description"] == DESCRIPTION


def test_database_keeps_original_text(client, make_user):
    _requester, ticket = _ticket_with_personal_data(client, make_user)
    _detail(client, ticket["id"], make_user("technician"))

    db = SessionLocal()
    try:
        assert db.get(models.Ticket, ticket["id"]).description == DESCRIPTION
        comment = db.query(models.Comment).filter(models.Comment.ticket_id == ticket["id"]).one()
        assert comment.content == COMMENT
    finally:
        db.close()
