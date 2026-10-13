import pytest
from datetime import datetime, timezone, timedelta
from app import models
from app.database import SessionLocal

@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_coordinator_generates_technician_code(client, make_user):
    coord = make_user("coordinator", "Coord")
    
    response = client.post("/api/invites", json={"role": "technician"}, headers=coord["headers"])
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "technician"
    assert data["code"].startswith("TEC-")
    # Formato esperado: TEC-XXXX-XXXX
    assert len(data["code"]) == 13

def test_register_with_valid_invite_code(client, make_user):
    coord = make_user("coordinator", "Coord")
    
    # Generar código
    res_invite = client.post("/api/invites", json={"role": "technician"}, headers=coord["headers"])
    code = res_invite.json()["code"]
    
    # Registrarse
    reg_payload = {
        "name": "Nuevo Tech",
        "email": "tech@test.com",
        "password": "clave-segura-123",
        "invite_code": code
    }
    response = client.post("/api/auth/register", json=reg_payload)
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "technician"
    
    # Intentar usar de nuevo
    reg_payload["email"] = "otro-tech@test.com"
    response2 = client.post("/api/auth/register", json=reg_payload)
    assert response2.status_code == 400
    assert response2.json()["detail"] == "Código de invitación inválido o vencido"

def test_expired_code_fails(client, make_user, db):
    coord = make_user("coordinator", "Coord")
    
    # Generar código
    res_invite = client.post("/api/invites", json={"role": "technician"}, headers=coord["headers"])
    code = res_invite.json()["code"]
    
    # Vencer código en base
    from app.security import hash_code
    code_hash = hash_code(code)
    invite = db.query(models.InviteCode).filter(models.InviteCode.code_hash == code_hash).first()
    invite.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()
    
    # Registrarse
    reg_payload = {
        "name": "Tech Expired",
        "email": "exp@test.com",
        "password": "clave-segura-123",
        "invite_code": code
    }
    response = client.post("/api/auth/register", json=reg_payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Código de invitación inválido o vencido"

def test_invalid_code_fails(client):
    reg_payload = {
        "name": "Tech Fake",
        "email": "fake@test.com",
        "password": "clave-segura-123",
        "invite_code": "TEC-INVALID-CODE"
    }
    response = client.post("/api/auth/register", json=reg_payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Código de invitación inválido o vencido"

def test_coordinator_cannot_create_coordinator_invite(client, make_user):
    coord = make_user("coordinator", "Coord")
    response = client.post("/api/invites", json={"role": "coordinator"}, headers=coord["headers"])
    assert response.status_code == 403
    assert response.json()["detail"] == "Solo el administrador puede invitar coordinadores"

def test_admin_can_create_coordinator_invite(client, make_user):
    admin = make_user("admin", "Admin")
    response = client.post("/api/invites", json={"role": "coordinator"}, headers=admin["headers"])
    assert response.status_code == 201
    assert response.json()["role"] == "coordinator"

def test_unauthorized_user_cannot_create_invite(client, make_user):
    requester = make_user("requester", "Req")
    response = client.post("/api/invites", json={"role": "technician"}, headers=requester["headers"])
    assert response.status_code == 403
    assert response.json()["detail"] == "No tienes permiso para generar códigos"

def test_get_invites_hides_sensitive_data(client, make_user):
    coord = make_user("coordinator", "Coord")
    client.post("/api/invites", json={"role": "technician"}, headers=coord["headers"])
    
    response = client.get("/api/invites", headers=coord["headers"])
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    for invite in data:
        assert "code" not in invite
        assert "code_hash" not in invite
