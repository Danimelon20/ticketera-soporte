"""Áreas de atención (administrador), alertas, activación y ubicación del personal."""
from app import models, routing
from app.database import SessionLocal
from org_helpers import area, department, place, unique


def test_admin_creates_area_assigns_departments_and_coordinator(client, make_user):
    admin = make_user("admin")
    coordinator = make_user("coordinator", "Coordinadora de prueba")
    dept, other = department(), department()
    created = client.post("/api/admin/areas", headers=admin["headers"],
                          json={"name": unique("Internet"), "description": "Internet caído, VPN"})
    assert created.status_code == 201, created.text
    area_id = created.json()["id"]

    response = client.put(f"/api/admin/areas/{area_id}/departments", headers=admin["headers"],
                          json={"department_ids": [dept, other]})
    assert response.json()["department_ids"] == sorted([dept, other])
    response = client.patch(f"/api/admin/areas/{area_id}", headers=admin["headers"],
                            json={"coordinator_id": coordinator["id"], "entry_department_id": dept})
    assert response.status_code == 200, response.text
    assert response.json()["coordinator_name"] == "Coordinadora de prueba"

    # Mover un departamento a otra área (sin tocar código)
    second = area()
    moved = client.put(f"/api/admin/areas/{second}/departments", headers=admin["headers"], json={"department_ids": [dept]})
    assert moved.json()["department_ids"] == [dept]
    first = next(a for a in client.get("/api/admin/areas", headers=admin["headers"]).json() if a["id"] == area_id)
    assert first["department_ids"] == [other]
    # Ya no puede ser su departamento de entrada
    assert first["entry_department_id"] is None


def test_one_coordinator_per_area(client, make_user):
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    area(coordinator_id=coordinator["id"])
    other = area()
    response = client.patch(f"/api/admin/areas/{other}", headers=admin["headers"],
                            json={"coordinator_id": coordinator["id"]})
    assert response.status_code == 409
    technician = make_user("technician")
    assert client.patch(f"/api/admin/areas/{other}", headers=admin["headers"],
                        json={"coordinator_id": technician["id"]}).status_code == 400


def test_entry_department_must_belong_to_area(client, make_user):
    admin = make_user("admin")
    area_id = area(department_ids=[department()])
    response = client.patch(f"/api/admin/areas/{area_id}", headers=admin["headers"],
                            json={"entry_department_id": department()})
    assert response.status_code == 400


def test_areas_admin_only(client, make_user):
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert client.get("/api/admin/areas", headers=user["headers"]).status_code == 403
        assert client.post("/api/admin/areas", headers=user["headers"], json={"name": unique("X")}).status_code == 403
        assert client.get("/api/admin/staff-alerts", headers=user["headers"]).status_code == 403
        assert client.get("/api/admin/activation", headers=user["headers"]).status_code == 403


def test_staff_alerts_list_unplaced_staff_and_areas_without_coordinator(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician", "Sin departamento")
    placed = make_user("technician", "Con departamento")
    place(placed["id"], department())
    off_dept = department(active=False)
    disabled = make_user("technician", "Departamento desactivado")
    place(disabled["id"], off_dept)
    coordinator = make_user("coordinator", "Coordinador sin área")
    lonely = area()

    data = client.get("/api/admin/staff-alerts", headers=admin["headers"]).json()
    ids = {p["id"] for p in data["unplaced"]}
    assert {technician["id"], disabled["id"], coordinator["id"]} <= ids
    assert placed["id"] not in ids
    assert lonely in {a["id"] for a in data["areas_without_coordinator"]}


def test_placement_requires_department_or_area(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician")
    coordinator = make_user("coordinator")
    assert client.put(f"/api/admin/users/{technician['id']}/placement", headers=admin["headers"],
                      json={}).status_code == 422
    assert client.put(f"/api/admin/users/{coordinator['id']}/placement", headers=admin["headers"],
                      json={}).status_code == 422
    dept = department()
    response = client.put(f"/api/admin/users/{technician['id']}/placement", headers=admin["headers"],
                          json={"department_id": dept, "tier": 2})
    assert response.status_code == 200, response.text
    assert response.json()["placed"] is True and response.json()["tier"] == 2
    area_id = area()
    response = client.put(f"/api/admin/users/{coordinator['id']}/placement", headers=admin["headers"],
                          json={"area_id": area_id})
    assert response.json()["area_id"] == area_id

    db = SessionLocal()
    try:
        changes = db.query(models.AccountChange).filter(
            models.AccountChange.action == "placement_changed",
            models.AccountChange.target_user_id.in_([technician["id"], coordinator["id"]]),
        ).count()
        assert changes == 2
    finally:
        db.close()


def test_invitation_requires_placement_when_routing_on(client, make_user, routing_on, sent):
    admin = make_user("admin")
    payload = {"email": f"{unique('tec').replace(' ', '')}@test.com", "name": "Técnica nueva", "role": "technician"}
    response = client.post("/api/staff-invitations", headers=admin["headers"], json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "Elija el departamento del técnico"
    payload["department_id"] = department()
    payload["tier"] = 3
    assert client.post("/api/staff-invitations", headers=admin["headers"], json=payload).status_code == 201

    coord = {"email": f"{unique('coo').replace(' ', '')}@test.com", "name": "Coordinador nuevo", "role": "coordinator"}
    assert client.post("/api/staff-invitations", headers=admin["headers"], json=coord).status_code == 422


def test_activation_needs_checklist_and_is_recorded(client, make_user, monkeypatch):
    admin = make_user("admin")
    make_user("technician", "Sin ubicación")
    switches = {s["key"]: s for s in client.get("/api/admin/activation", headers=admin["headers"]).json()}
    assert switches[routing.ROUTING_ENFORCED]["enabled"] is False
    assert not all(item["ok"] for item in switches[routing.ROUTING_ENFORCED]["checklist"])
    assert client.put(f"/api/admin/activation/{routing.ROUTING_ENFORCED}", headers=admin["headers"],
                      json={"enabled": True}).status_code == 409

    ok = [{"key": "x", "label": "Listo", "ok": True, "detail": None}]
    monkeypatch.setattr(routing, "routing_checklist", lambda db: ok)
    response = client.put(f"/api/admin/activation/{routing.ROUTING_ENFORCED}", headers=admin["headers"],
                          json={"enabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True
    assert client.put(f"/api/admin/activation/{routing.ROUTING_ENFORCED}", headers=admin["headers"],
                      json={"enabled": False}).json()["enabled"] is False

    db = SessionLocal()
    try:
        rows = db.query(models.AccountChange).filter(
            models.AccountChange.action == "setting_changed", models.AccountChange.actor_id == admin["id"],
        ).order_by(models.AccountChange.id).all()
        assert [(r.old_value, r.new_value) for r in rows] == [("Apagado", "Encendido"), ("Encendido", "Apagado")]
    finally:
        db.close()

    # El de los dominios de clientes llega con la Fase 2
    assert client.put(f"/api/admin/activation/{routing.CLIENT_DOMAIN_ENFORCED}", headers=admin["headers"],
                      json={"enabled": True}).status_code == 409
