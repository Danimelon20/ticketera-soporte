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


def test_coordinator_cannot_create_ticket_for_incomplete_profile(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester", profile=False)
    response = client.post("/api/tickets", json=_ticket(requester_id=requester["id"]), headers=coordinator["headers"])
    assert response.status_code == 400
    assert response.json()["detail"].startswith("El perfil del solicitante está incompleto")


def test_staff_cannot_use_requester_profile(client, make_user):
    tech = make_user("technician")
    assert client.put("/api/profile", json=_profile(), headers=tech["headers"]).status_code == 403
