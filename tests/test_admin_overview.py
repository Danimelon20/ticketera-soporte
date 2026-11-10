"""Panel del administrador: una fila por departamento activo que atiende tickets, con su coordinador y sus
técnicos; abiertos y vencidos llegan en la Fase 2 ("—"). Solo el administrador."""
from org_helpers import department, unique


def _overview(client, user):
    return client.get("/api/admin/overview", headers=user["headers"])


def _place(client, admin, user_id, dept_id, tier=None):
    response = client.put(f"/api/admin/users/{user_id}/placement", headers=admin["headers"],
                          json={"department_id": dept_id, "tier": tier})
    assert response.status_code == 200, response.text


def test_one_row_per_serving_department(client, make_user):
    admin = make_user("admin")
    serving = department(unique("Mesa de ayuda"))
    lonely = department(unique("Sin coordinador"))
    not_serving = department(unique("Finanzas"), handles=False)
    inactive = department(unique("Cerrado"), active=False)
    coordinator = make_user("coordinator", "Coordinadora del Panel")
    _place(client, admin, coordinator["id"], serving)
    for tier in (1, 2):
        _place(client, admin, make_user("technician")["id"], serving, tier)

    response = _overview(client, admin)
    assert response.status_code == 200, response.text
    data = response.json()
    rows = {r["id"]: r for r in data["departments"]}
    assert rows[serving]["coordinator_name"] == "Coordinadora del Panel"
    assert rows[serving]["technicians"] == 2
    assert rows[serving]["open"] is None and rows[serving]["overdue"] is None
    assert rows[lonely]["coordinator_name"] is None and rows[lonely]["technicians"] == 0
    assert not_serving not in rows and inactive not in rows
    assert data["routing_enforced"] is False


def test_overview_lists_unplaced_staff(client, make_user):
    admin = make_user("admin")
    technician = make_user("technician", f"Sin depto {unique('t')}")
    data = _overview(client, admin).json()
    assert technician["id"] in [p["id"] for p in data["unplaced"]]


def test_overview_is_admin_only(client, make_user):
    for role in ("coordinator", "technician", "requester"):
        assert _overview(client, make_user(role)).status_code == 403
