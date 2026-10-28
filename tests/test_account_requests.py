import pytest

from app import models
from app.database import SessionLocal


def _create(client, actor, target, kind, new_value, reason="Lo pidió por teléfono"):
    return client.post("/api/account-requests", json={
        "kind": kind, "target_user_id": target["id"], "new_value": new_value, "reason": reason,
    }, headers=actor["headers"])


def _user(user_id):
    db = SessionLocal()
    try:
        return db.get(models.User, user_id)
    finally:
        db.close()


def _locked_requester(make_user, make_ticket):
    requester = make_user("requester", "Ana Pérez Mora")
    make_ticket(requester)  # Con el primer ticket nombre y cédula quedan fijos
    return requester


def test_role_request_applies_only_after_approval(client, make_user, sent):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    technician = make_user("technician")
    response = _create(client, coordinator, technician, "role", "coordinator")
    assert response.status_code == 201
    request_id = response.json()["id"]
    assert _user(technician["id"]).role == "technician"
    # Aviso a los administradores
    assert any(to == admin["email"] and "solicitud" in subject.lower() for to, subject, _ in sent)

    pending = client.get("/api/account-requests", params={"status": "pending"}, headers=admin["headers"]).json()
    assert request_id in [r["id"] for r in pending]

    approved = client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"])
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert _user(technician["id"]).role == "coordinator"
    # El rol cambió: sus sesiones se cerraron
    assert client.get("/api/auth/me", headers=technician["headers"]).status_code == 401
    assert any(to == coordinator["email"] and "resuelta" in subject for to, subject, _ in sent)
    # Ya resuelta no se puede volver a resolver
    assert client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"]).status_code == 409


def test_rejection_changes_nothing(client, make_user, sent):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    technician = make_user("technician")
    request_id = _create(client, coordinator, technician, "role", "requester").json()["id"]
    assert client.post(f"/api/account-requests/{request_id}/reject", json={"reason": " "},
                       headers=admin["headers"]).status_code == 422
    response = client.post(f"/api/account-requests/{request_id}/reject", json={"reason": "No corresponde"},
                           headers=admin["headers"])
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejection_reason"] == "No corresponde"
    assert _user(technician["id"]).role == "technician"
    mine = client.get("/api/account-requests", headers=coordinator["headers"]).json()
    assert [r["status"] for r in mine if r["id"] == request_id] == ["rejected"]


def test_reason_is_required(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    assert _create(client, coordinator, technician, "role", "coordinator", reason="   ").status_code == 422


def test_only_coordinator_creates_and_only_admin_resolves(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    for role in ("admin", "technician", "requester"):
        assert _create(client, make_user(role), technician, "role", "coordinator").status_code == 403
    request_id = _create(client, coordinator, technician, "role", "coordinator").json()["id"]
    for actor in (coordinator, make_user("coordinator"), make_user("technician"), make_user("requester")):
        assert client.post(f"/api/account-requests/{request_id}/approve", headers=actor["headers"]).status_code == 403
        assert client.post(f"/api/account-requests/{request_id}/reject", json={"reason": "x"},
                           headers=actor["headers"]).status_code == 403
    for role in ("technician", "requester"):
        assert client.get("/api/account-requests", headers=make_user(role)["headers"]).status_code == 403


def test_coordinator_cannot_request_for_coordinators_admins_or_self(client, make_user):
    coordinator = make_user("coordinator")
    assert _create(client, coordinator, make_user("coordinator"), "role", "technician").status_code == 403
    assert _create(client, coordinator, make_user("admin"), "role", "technician").status_code == 403
    assert _create(client, coordinator, coordinator, "role", "technician").status_code == 403
    # Nunca se pide el rol de administrador
    assert _create(client, coordinator, make_user("technician"), "role", "admin").status_code == 422


def test_nobody_approves_own_request(client, make_user):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    request_id = _create(client, coordinator, technician, "role", "coordinator").json()["id"]
    # El coordinador pasa a ser administrador: aun así no aprueba la suya
    db = SessionLocal()
    try:
        db.get(models.User, coordinator["id"]).role = "admin"
        db.commit()
    finally:
        db.close()
    response = client.post(f"/api/account-requests/{request_id}/approve", headers=coordinator["headers"])
    assert response.status_code == 403
    assert _user(technician["id"]).role == "technician"


def test_no_two_pending_requests_of_same_kind(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    other = make_user("coordinator")
    technician = make_user("technician")
    assert _create(client, coordinator, technician, "role", "coordinator").status_code == 201
    assert _create(client, other, technician, "role", "requester").status_code == 409

    requester = _locked_requester(make_user, make_ticket)
    assert _create(client, coordinator, requester, "name", "Ana Pérez Rojas").status_code == 201
    # Otro tipo sí se puede
    assert _create(client, coordinator, requester, "id_number", "1-0987-0654").status_code == 201
    assert _create(client, coordinator, requester, "name", "Ana Pérez Solano").status_code == 409


def test_name_or_id_request_only_when_locked(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")  # Sin tickets: lo corrige en su perfil
    response = _create(client, coordinator, requester, "name", "Otro Nombre Completo")
    assert response.status_code == 400
    assert _create(client, coordinator, make_user("technician"), "name", "Otro Nombre").status_code == 403


def test_name_correction_is_applied(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = _locked_requester(make_user, make_ticket)
    assert _create(client, coordinator, requester, "name", "Ana").status_code == 422
    request_id = _create(client, coordinator, requester, "name", "Ana  Pérez   Rojas").json()["id"]
    assert _user(requester["id"]).name == "Ana Pérez Mora"
    assert client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"]).status_code == 200
    assert _user(requester["id"]).name == "Ana Pérez Rojas"


def test_id_number_request_is_masked_and_validated(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = _locked_requester(make_user, make_ticket)
    assert _create(client, coordinator, requester, "id_number", "12345").status_code == 422
    owner = make_user("requester")
    assert _create(client, coordinator, requester, "id_number", owner["id_number"]).status_code == 400

    response = _create(client, coordinator, requester, "id_number", "1-0234-0567")
    assert response.status_code == 201
    request_id = response.json()["id"]
    # El coordinador nunca ve la cédula nueva completa
    assert response.json()["new_value"] == "****0567"
    mine = client.get("/api/account-requests", headers=coordinator["headers"]).json()
    assert "102340567" not in str(mine)
    # El administrador sí, mientras está pendiente
    pending = client.get("/api/account-requests", headers=admin["headers"]).json()
    assert [r["new_value"] for r in pending if r["id"] == request_id] == ["102340567"]

    assert client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"]).status_code == 200
    assert _user(requester["id"]).id_number == "102340567"
    db = SessionLocal()
    try:
        row = db.get(models.AccountRequest, request_id)
        assert row.new_value == "****0567"
    finally:
        db.close()
    resolved = client.get("/api/account-requests", headers=admin["headers"]).json()
    assert "102340567" not in str(resolved)


def test_id_number_masked_after_rejection(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = _locked_requester(make_user, make_ticket)
    request_id = _create(client, coordinator, requester, "id_number", "109870654").json()["id"]
    client.post(f"/api/account-requests/{request_id}/reject", json={"reason": "Sin respaldo"}, headers=admin["headers"])
    db = SessionLocal()
    try:
        assert db.get(models.AccountRequest, request_id).new_value == "****0654"
        assert db.get(models.User, requester["id"]).id_number != "109870654"
    finally:
        db.close()


def test_approval_fails_if_id_number_was_taken_meanwhile(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = _locked_requester(make_user, make_ticket)
    request_id = _create(client, coordinator, requester, "id_number", "123456789012").json()["id"]
    other = make_user("requester")
    db = SessionLocal()
    try:
        db.get(models.User, other["id"]).id_number = "123456789012"
        db.commit()
    finally:
        db.close()
    assert client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"]).status_code == 400
    db = SessionLocal()
    try:
        assert db.get(models.AccountRequest, request_id).status == "pending"
    finally:
        db.close()


def test_hacienda_tolerant_on_approval(client, make_user, make_ticket, monkeypatch):
    from app import hacienda

    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = _locked_requester(make_user, make_ticket)
    request_id = _create(client, coordinator, requester, "id_number", "310123456").json()["id"]
    # Hacienda no la encuentra y está en modo estricto: igual se aprueba
    monkeypatch.setattr(hacienda, "lookup_id", lambda _id: None)
    monkeypatch.setenv("HACIENDA_STRICT", "true")
    assert client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"]).status_code == 200
    assert _user(requester["id"]).id_number == "310123456"


def test_requests_are_recorded_without_id_number(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = _locked_requester(make_user, make_ticket)
    request_id = _create(client, coordinator, requester, "id_number", "102340567").json()["id"]
    client.post(f"/api/account-requests/{request_id}/approve", headers=admin["headers"])
    db = SessionLocal()
    try:
        rows = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == requester["id"])
            .order_by(models.AccountChange.id)
            .all()
        )
        actions = [r.action for r in rows]
        assert actions[-3:] == ["request_created", "id_number_changed", "request_approved"]
        assert "102340567" not in " ".join(f"{r.old_value} {r.new_value}" for r in rows)
    finally:
        db.close()
