"""Departamentos del organigrama: jerarquía, casilla "Atiende tickets" y Registro de cambios (solo el admin)."""
from org_helpers import changes, unique


def _create(client, admin, name, parent_id=None, handles=False):
    return client.post("/api/admin/departments", headers=admin["headers"],
                       json={"name": name, "parent_id": parent_id, "handles_tickets": handles})


def _patch(client, admin, dept_id, **body):
    return client.patch(f"/api/admin/departments/{dept_id}", headers=admin["headers"], json=body)


def test_crud_move_cycle_and_root(client, make_user):
    admin = make_user("admin")
    parent = _create(client, admin, unique("Gerencia")).json()
    noc = unique("NOC")
    child = _create(client, admin, noc, parent["id"]).json()
    assert child["path"] == f"{parent['name']} / {noc}"
    # Mismo nombre (sin tildes ni mayúsculas) en el mismo lugar
    assert _create(client, admin, noc.upper(), parent["id"]).status_code == 409
    # Un departamento no puede quedar dentro de su propio hijo
    assert _patch(client, admin, parent["id"], parent_id=child["id"]).status_code == 400
    moved = _patch(client, admin, child["id"], parent_id=0)
    assert moved.status_code == 200 and moved.json()["parent_id"] is None
    renamed = _patch(client, admin, child["id"], name=f"{noc} Central")
    assert renamed.json()["path"] == f"{noc} Central"


def test_deactivating_parent_deactivates_the_subtree(client, make_user):
    admin = make_user("admin")
    parent = _create(client, admin, unique("Operaciones")).json()
    child = _create(client, admin, unique("Fibra"), parent["id"]).json()
    assert _patch(client, admin, parent["id"], is_active=False).json()["is_active"] is False
    listed = {d["id"]: d for d in client.get("/api/admin/departments", headers=admin["headers"]).json()}
    assert listed[child["id"]]["is_active"] is True and listed[child["id"]]["active_path"] is False


def test_handles_tickets_is_per_department_and_not_inherited(client, make_user):
    admin = make_user("admin")
    parent = _create(client, admin, unique("Tecnología"), handles=True).json()
    child = _create(client, admin, unique("Desarrollo"), parent["id"]).json()
    assert parent["handles_tickets"] is True
    # No se hereda: el hijo no atiende tickets
    assert child["handles_tickets"] is False
    assert _patch(client, admin, child["id"], handles_tickets=True).json()["handles_tickets"] is True
    assert _patch(client, admin, parent["id"], handles_tickets=False).json()["handles_tickets"] is False


def test_aliases(client, make_user):
    admin = make_user("admin")
    dept = _create(client, admin, unique("Soporte N2")).json()
    alias = f"Soporte Nivel 2 {dept['id']}"
    assert _patch(client, admin, dept["id"], add_alias=alias).json()["aliases"] == [alias]
    other = _create(client, admin, unique("Otro")).json()
    assert _patch(client, admin, other["id"], add_alias=alias).status_code == 409
    assert _patch(client, admin, dept["id"], remove_alias=alias).json()["aliases"] == []


def test_changes_are_audited(client, make_user):
    admin = make_user("admin")
    name = unique("Auditoría")
    dept = _create(client, admin, name, handles=True).json()
    _patch(client, admin, dept["id"], handles_tickets=False)
    _patch(client, admin, dept["id"], is_active=False)
    created = changes("department_created", name)
    assert len(created) == 1 and created[0].actor_id == admin["id"] and created[0].new_value == "Atiende tickets"
    changed = [(c.old_value, c.new_value) for c in changes("department_changed", name)]
    assert ("Atiende tickets: Sí", "Atiende tickets: No") in changed
    assert ("Activo", "Desactivado") in changed


def test_departments_are_admin_only(client, make_user):
    admin = make_user("admin")
    dept = _create(client, admin, unique("Privado")).json()
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert client.get("/api/admin/departments", headers=user["headers"]).status_code == 403
        assert _create(client, user, unique("X")).status_code == 403
        assert _patch(client, user, dept["id"], name="Y").status_code == 403


def test_unknown_parent_or_department_is_404(client, make_user):
    admin = make_user("admin")
    assert _create(client, admin, unique("Huérfano"), parent_id=999999).status_code == 404
    assert _patch(client, admin, 999999, name="Nada").status_code == 404
