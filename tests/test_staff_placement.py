"""Personal por departamento: invitación con departamento, ubicación, cambio de rol con departamento, alerta del
personal sin departamento y departamentos con personal que no se pueden desmarcar ni desactivar."""
import uuid

import pytest

from app import entra_login, models, routing
from app.database import SessionLocal
from conftest import code_from
from org_helpers import changes, department, unique


def _email():
    return f"ubicado-{uuid.uuid4().hex}@test.com"


def _invite(client, admin, role="technician", **body):
    return client.post("/api/staff-invitations", headers=admin["headers"],
                       json={"email": _email(), "name": "Persona Nueva", "role": role, **body})


def _place(client, admin, user_id, department_id):
    return client.put(f"/api/admin/users/{user_id}/placement", headers=admin["headers"],
                      json={"department_id": department_id})


def _db_user(user_id):
    db = SessionLocal()
    try:
        return db.get(models.User, user_id)
    finally:
        db.close()


def _department_of(user_id):
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        dept = routing.department_of(db, user)
        return dept.id if dept else None
    finally:
        db.close()


# ----- Invitación -----

def test_invitation_requires_a_serving_department_without_tier(client, make_user, sent):
    admin = make_user("admin")
    serving = department(handles=True)
    not_serving = department(handles=False)
    inactive = department(handles=True, active=False)
    assert _invite(client, admin).json()["detail"] == routing.DEPARTMENT_REQUIRED
    for dept_id in (not_serving, inactive, 999999):
        response = _invite(client, admin, department_id=dept_id)
        assert response.status_code == 422 and response.json()["detail"] == routing.NOT_SERVING
    assert sent == []
    # Sin nivel: los niveles de soporte son departamentos
    ok = _invite(client, admin, department_id=serving)
    assert ok.status_code == 201, ok.text
    assert "tier" not in ok.json() and ok.json()["department_name"].endswith(_dept_name(serving))
    # Un nivel enviado por una versión anterior de la página se ignora
    old_page = _invite(client, admin, department_id=serving, tier=3)
    assert old_page.status_code == 201 and "tier" not in old_page.json()


def _dept_name(dept_id):
    db = SessionLocal()
    try:
        return db.get(models.Department, dept_id).name
    finally:
        db.close()


def test_one_coordinator_per_department_counts_pending_invitations(client, make_user, sent):
    admin = make_user("admin")
    dept = department()
    first = _invite(client, admin, "coordinator", department_id=dept)
    assert first.status_code == 201, first.text
    # Ya hay una invitación de coordinador pendiente para ese departamento
    second = _invite(client, admin, "coordinator", department_id=dept)
    assert second.status_code == 409 and second.json()["detail"] == routing.COORDINATOR_PENDING
    # Con coordinador ya activo
    other = department()
    coordinator = make_user("coordinator", "Coordinadora Activa")
    assert _place(client, admin, coordinator["id"], other).status_code == 200
    taken = _invite(client, admin, "coordinator", department_id=other)
    assert taken.status_code == 409 and "Coordinadora Activa" in taken.json()["detail"]


def test_password_activation_applies_the_placement(client, make_user, sent):
    admin = make_user("admin")
    dept = department()
    email = _email()
    response = client.post("/api/staff-invitations", headers=admin["headers"], json={
        "email": email, "name": "Técnica Nueva", "role": "technician", "department_id": dept})
    assert response.status_code == 201, response.text
    activated = client.post("/api/auth/activate", json={"email": email, "code": code_from(sent[-1][2])})
    assert activated.status_code == 200, activated.text
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == email).one()
        assert routing.department_of(db, user).id == dept
    finally:
        db.close()


def test_microsoft_activation_applies_the_placement(client, make_user, sent):
    admin = make_user("admin")
    dept = department()
    email = _email()
    created = client.post("/api/staff-invitations", headers=admin["headers"], json={
        "email": email, "name": "Coordinador Microsoft", "role": "coordinator", "department_id": dept}).json()
    db = SessionLocal()
    try:
        identity = entra_login.Identity(oid=uuid.uuid4().hex, email=email, purpose="activate", portal=None,
                                        invitation_id=created["id"])
        user = entra_login.activate(db, identity)
        db.commit()
        assert routing.coordinator_of(db, dept).user_id == user.id
    finally:
        db.close()


# ----- Ubicación (administrador) -----

def test_admin_moves_staff_between_departments_with_audit(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician", f"Técnico {uuid.uuid4().hex[:6]}")
    first, second = department(unique("Soporte")), department(unique("Redes"))
    assert _place(client, admin, technician["id"], first).status_code == 200
    response = _place(client, admin, technician["id"], second)
    assert response.status_code == 200 and response.json()["placed"] is True
    assert "tier" not in response.json()
    assert _department_of(technician["id"]) == second
    moves = [(c.old_value, c.new_value) for c in changes("placement_changed")
             if c.target_user_id == technician["id"]]
    # Solo la ruta del departamento, sin "· N1"
    assert moves[0][0] == "Sin departamento" and moves[0][1].endswith(_dept_name(first))
    assert moves[1][1].endswith(_dept_name(second)) and "· N" not in moves[1][1]
    # Un técnico pertenece a un solo departamento
    db = SessionLocal()
    try:
        assert db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == technician["id"]).count() == 1
    finally:
        db.close()


def test_coordinator_placement_rules(client, make_user):
    admin = make_user("admin")
    first, second = department(), department()
    one, two = make_user("coordinator", "Coordinadora Uno"), make_user("coordinator", "Coordinador Dos")
    assert _place(client, admin, one["id"], first).status_code == 200
    # Un coordinador por departamento
    taken = _place(client, admin, two["id"], first)
    assert taken.status_code == 409 and "Coordinadora Uno" in taken.json()["detail"]
    # Un departamento por coordinador: al moverlo, el anterior queda libre
    assert _place(client, admin, one["id"], second).status_code == 200
    assert _place(client, admin, two["id"], first).status_code == 200
    assert _department_of(one["id"]) == second


def test_placement_validation_and_permissions(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician")
    assert _place(client, admin, technician["id"], department(handles=False)).status_code == 422
    assert _place(client, admin, technician["id"], None).status_code == 422
    # Sin nivel: con el departamento basta
    assert _place(client, admin, technician["id"], department()).status_code == 200
    assert _place(client, admin, make_user("requester")["id"], department()).status_code == 403
    assert _place(client, admin, make_user("admin")["id"], department()).status_code == 403
    for role in ("coordinator", "technician"):
        user = make_user(role)
        assert _place(client, user, technician["id"], department()).status_code == 403


# ----- Cambio de rol técnico ↔ coordinador con departamento -----

def test_technician_to_coordinator_with_department(client, make_user):
    admin = make_user("admin")
    user = make_user("technician")
    old, new = department(), department()
    _place(client, admin, user["id"], old)
    response = client.patch(f"/api/admin/users/{user['id']}", headers=admin["headers"],
                            json={"role": "coordinator", "department_id": new})
    assert response.status_code == 200, response.text
    assert _db_user(user["id"]).role == "coordinator"
    assert _department_of(user["id"]) == new
    db = SessionLocal()
    try:
        # Ya no figura como técnico de su departamento anterior
        assert routing.technician_placement(db, user["id"]) is None
    finally:
        db.close()
    actions = [c.action for c in changes("role_changed") + changes("placement_changed") if c.target_user_id == user["id"]]
    assert "role_changed" in actions and actions.count("placement_changed") == 2


def test_technician_to_coordinator_of_a_taken_department_changes_nothing(client, make_user):
    admin = make_user("admin")
    holder = make_user("coordinator", "Coordinadora Actual")
    user = make_user("technician")
    taken, own = department(), department()
    _place(client, admin, holder["id"], taken)
    _place(client, admin, user["id"], own)
    response = client.patch(f"/api/admin/users/{user['id']}", headers=admin["headers"],
                            json={"role": "coordinator", "department_id": taken})
    assert response.status_code == 409 and "Coordinadora Actual" in response.json()["detail"]
    assert _db_user(user["id"]).role == "technician"
    assert _department_of(user["id"]) == own


def test_coordinator_to_technician_frees_the_department(client, make_user):
    admin = make_user("admin")
    user = make_user("coordinator")
    led, target = department(), department()
    _place(client, admin, user["id"], led)
    response = client.patch(f"/api/admin/users/{user['id']}", headers=admin["headers"],
                            json={"role": "technician", "department_id": target})
    # Sin nivel: con el departamento basta
    assert response.status_code == 200, response.text
    assert _department_of(user["id"]) == target
    db = SessionLocal()
    try:
        assert routing.coordinator_of(db, led) is None
    finally:
        db.close()


@pytest.mark.parametrize("body", [
    {"role": "coordinator"},
    {"role": "technician"},
])
def test_role_change_without_department_is_422(client, make_user, body):
    admin = make_user("admin")
    start = "coordinator" if body["role"] == "technician" else "technician"
    user = make_user(start)
    body = {k: (department() if v == "DEPT" else v) for k, v in body.items()}
    response = client.patch(f"/api/admin/users/{user['id']}", headers=admin["headers"], json=body)
    assert response.status_code == 422
    assert _db_user(user["id"]).role == start


# ----- Alerta del personal sin departamento -----

def test_staff_alert_lists_unplaced_and_badly_placed(client, make_user):
    admin = make_user("admin")
    alone = make_user("technician", f"Sin depto {uuid.uuid4().hex[:6]}")
    placed = make_user("technician", f"Ubicado {uuid.uuid4().hex[:6]}")
    later_off = make_user("technician", f"Depto apagado {uuid.uuid4().hex[:6]}")
    dept, off = department(), department()
    _place(client, admin, placed["id"], dept)
    _place(client, admin, later_off["id"], off)
    db = SessionLocal()
    try:
        db.get(models.Department, off).handles_tickets = False
        db.commit()
    finally:
        db.close()
    alerts = client.get("/api/admin/staff-alerts", headers=admin["headers"])
    assert alerts.status_code == 200
    unplaced = {p["id"]: p for p in alerts.json()["unplaced"]}
    assert alone["id"] in unplaced and unplaced[alone["id"]]["department_name"] is None
    assert later_off["id"] in unplaced and unplaced[later_off["id"]]["department_name"] is not None
    assert placed["id"] not in unplaced
    assert client.get("/api/admin/staff-alerts", headers=make_user("coordinator")["headers"]).status_code == 403


def test_me_shows_department_without_tier(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician")
    dept = department(unique("Mesa de ayuda"))
    _place(client, admin, technician["id"], dept)
    me = client.get("/api/auth/me", headers=technician["headers"]).json()
    assert me["department_name"].startswith("Mesa de ayuda") and "tier" not in me


# ----- Departamentos con personal -----

def test_department_with_staff_cannot_be_unmarked_or_deactivated(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician")
    parent = department(handles=False)
    child = department(parent_id=parent)
    _place(client, admin, technician["id"], child)
    for dept_id, body in ((child, {"handles_tickets": False}), (child, {"is_active": False}),
                          (parent, {"is_active": False})):
        response = client.patch(f"/api/admin/departments/{dept_id}", headers=admin["headers"], json=body)
        assert response.status_code == 409, body
        assert "Mueva primero a su personal (1)" in response.json()["detail"]
    # Mover el departamento con su personal sí se permite
    other = department(handles=False)
    assert client.patch(f"/api/admin/departments/{child}", headers=admin["headers"],
                        json={"parent_id": other}).status_code == 200
    # Sin personal, sí se desmarca
    _place(client, admin, technician["id"], department())
    assert client.patch(f"/api/admin/departments/{child}", headers=admin["headers"],
                        json={"handles_tickets": False}).status_code == 200
