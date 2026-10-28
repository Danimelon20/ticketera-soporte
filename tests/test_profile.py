import pytest

from app import hacienda, models
from app.database import SessionLocal

ID_IN_USE = "No es posible usar esta cédula. Si le pertenece, contacte a soporte."
LOCKED = "El nombre y la cédula ya no se pueden cambiar porque ya creó un ticket. Contacte a soporte."


def _profile(name="Daniel Alejandro Sáenz Maroto", phone="8888-1234", id_number="1-0234-0567"):
    return {"name": name, "phone": phone, "id_number": id_number}


def _ticket(**extra):
    payload = {"title": "Sin internet", "description": "No hay red", "category_id": 1, "priority_id": 1}
    payload.update(extra)
    return payload


def _identity(user_id):
    db = SessionLocal()
    try:
        return db.query(models.RequesterIdentity).filter(models.RequesterIdentity.user_id == user_id).first()
    finally:
        db.close()


def test_incomplete_profile_cannot_create_ticket(client, make_user):
    user = make_user("requester", "", profile=False)
    me = client.get("/api/auth/me", headers=user["headers"]).json()
    assert me["profile_complete"] is False
    response = client.post("/api/tickets", json=_ticket(contact_phone="8888-1234"), headers=user["headers"])
    assert response.status_code == 400
    assert response.json()["detail"] == "Complete su perfil (nombre completo, cédula y teléfono) antes de crear tickets"


def test_coordinator_cannot_create_ticket_for_incomplete_profile(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester", profile=False)
    response = client.post("/api/tickets", json=_ticket(requester_id=requester["id"]), headers=coordinator["headers"])
    assert response.status_code == 400
    assert response.json()["detail"].startswith("El perfil del solicitante está incompleto")


def test_complete_profile_then_ticket_uses_profile(client, make_user):
    user = make_user("requester", "", profile=False)
    response = client.put("/api/profile", json=_profile(), headers=user["headers"])
    assert response.status_code == 200, response.text
    me = response.json()
    assert me["profile_complete"] is True and me["identity_locked"] is False
    assert me["name"] == "Daniel Alejandro Sáenz Maroto"

    created = client.post("/api/tickets", json=_ticket(), headers=user["headers"])
    assert created.status_code == 200, created.text
    detail = client.get(f"/api/tickets/{created.json()['id']}", headers=user["headers"]).json()
    assert detail["created_by"] == user["id"]
    assert detail["contact_id_number"] == "102340567"
    assert detail["contact_phone"] == "88881234"


def test_coordinator_ticket_uses_requester_profile(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester", "Laura Mora Vargas")
    response = client.post("/api/tickets", json=_ticket(requester_id=requester["id"]), headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/tickets/{response.json()['id']}", headers=coordinator["headers"]).json()
    assert detail["created_by"] == requester["id"]
    assert detail["contact_id_number"] == requester["id_number"]
    assert client.get("/api/auth/me", headers=requester["headers"]).json()["identity_locked"] is True


def test_repeated_id_number_is_rejected_without_revealing(client, make_user):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    response = client.put("/api/profile", json=_profile(id_number=owner["id_number"]), headers=other["headers"])
    assert response.status_code == 400
    assert response.json()["detail"] == ID_IN_USE
    assert owner["email"] not in response.text and owner["name"] not in response.text


def test_name_and_id_editable_before_first_ticket_and_locked_after(client, make_user):
    user = make_user("requester", profile=False)
    assert client.put("/api/profile", json=_profile(), headers=user["headers"]).status_code == 200
    fixed = _profile(name="Daniel Sáenz Maroto", id_number="1-0987-0654")
    response = client.put("/api/profile", json=fixed, headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["id_number"] == "109870654"

    assert client.post("/api/tickets", json=_ticket(), headers=user["headers"]).status_code == 200
    assert _identity(user["id"]).locked_at is not None
    me = client.get("/api/auth/me", headers=user["headers"]).json()
    assert me["identity_locked"] is True

    response = client.put("/api/profile", json={**fixed, "name": "Otro Nombre Distinto"}, headers=user["headers"])
    assert response.status_code == 403
    assert response.json()["detail"] == LOCKED
    response = client.put("/api/profile", json={**fixed, "id_number": "1-0234-0567"}, headers=user["headers"])
    assert response.status_code == 403

    # El teléfono siempre se puede cambiar
    response = client.put("/api/profile", json={**fixed, "phone": "7777-0000"}, headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["phone"] == "77770000"
    assert response.json()["name"] == "Daniel Sáenz Maroto"


def test_existing_account_with_tickets_is_locked_without_previous_row(client, make_user, make_ticket):
    user = make_user("requester", "Cuenta Antigua Rojas")
    make_ticket(user)
    db = SessionLocal()
    try:
        db.query(models.RequesterIdentity).filter(models.RequesterIdentity.user_id == user["id"]).delete()
        db.commit()
    finally:
        db.close()
    response = client.put("/api/profile", json=_profile(name="Otro Nombre"), headers=user["headers"])
    assert response.status_code == 403
    assert _identity(user["id"]).locked_at is not None


def test_existing_account_without_tickets_can_still_fix_identity(client, make_user):
    user = make_user("requester", "Cuenta Antigua")
    response = client.put("/api/profile", json=_profile(name="Cuenta Antigua Rojas"), headers=user["headers"])
    assert response.status_code == 200, response.text


def test_single_word_name_is_rejected(client, make_user):
    user = make_user("requester", "", profile=False)
    response = client.put("/api/profile", json=_profile(name="Daniel"), headers=user["headers"])
    assert response.status_code == 422


@pytest.mark.parametrize("theirs, expected", [
    ("SAENZ MAROTO DANIEL", "match"),
    ("MORA VARGAS LAURA", "mismatch"),
    (None, "not_found"),
])
def test_hacienda_result_is_saved_and_not_shown(client, make_user, monkeypatch, theirs, expected):
    def _lookup(id_number):
        if theirs is None:
            return None
        return {"tipo": "01", "name_tokens": hacienda.name_tokens(theirs)}

    monkeypatch.setattr(hacienda, "lookup_id", _lookup)
    user = make_user("requester", profile=False)
    response = client.put("/api/profile", json=_profile(), headers=user["headers"])
    assert response.status_code == 200, response.text
    assert _identity(user["id"]).hacienda_result == expected
    assert expected not in response.text
    assert "hacienda" not in response.text.lower()


def test_staff_cannot_use_requester_profile(client, make_user):
    tech = make_user("technician")
    assert client.put("/api/profile", json=_profile(), headers=tech["headers"]).status_code == 403
