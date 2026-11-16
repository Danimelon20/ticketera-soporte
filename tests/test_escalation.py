"""Escalar = transferir al siguiente nivel del departamento (Soporte N1 → Soporte N2 → Ingeniería N3).
El administrador configura el siguiente nivel; el detalle trae el destino ya elegido y el motivo es obligatorio."""
from app import routing
from org_helpers import changes, department, history, set_department, team, unique

REASON = "No se resuelve en este nivel: requiere acceso al servidor"


def _next(client, admin, dept_id, next_id):
    return client.patch(f"/api/admin/departments/{dept_id}", headers=admin["headers"],
                        json={"next_department_id": next_id})


def _read(client, admin, dept_id):
    return next(d for d in client.get("/api/admin/departments", headers=admin["headers"]).json() if d["id"] == dept_id)


def test_admin_sets_and_clears_the_next_level_with_audit(client, make_user):
    admin = make_user("admin")
    first, second = department(unique("Soporte N1")), department(unique("Soporte N2"))
    response = _next(client, admin, first, second)
    assert response.status_code == 200, response.text
    data = _read(client, admin, first)
    assert data["next_department_id"] == second and "Soporte N2" in data["next_department_path"]
    assert any(c.new_value and c.new_value.startswith("Siguiente nivel:") for c in changes("department_changed"))
    # 0: sin siguiente nivel
    assert _next(client, admin, first, 0).status_code == 200
    assert _read(client, admin, first)["next_department_id"] is None


def test_next_level_validation(client, make_user):
    admin = make_user("admin")
    one, two, three = (department(unique(f"Nivel {n}")) for n in (1, 2, 3))
    not_serving = department(handles=False)
    assert _next(client, admin, one, not_serving).json()["detail"] == routing.NOT_SERVING
    assert _next(client, admin, one, one).status_code == 400
    # Solo un departamento que atiende tickets tiene siguiente nivel
    assert _next(client, admin, not_serving, one).json()["detail"] == routing.ESCALATION_NOT_SERVING
    # Sin ciclos: 1 → 2 → 3 → 1 no se permite
    assert _next(client, admin, one, two).status_code == 200
    assert _next(client, admin, two, three).status_code == 200
    cycle = _next(client, admin, three, one)
    assert cycle.status_code == 400 and cycle.json()["detail"] == routing.ESCALATION_CYCLE
    # Solo el administrador
    assert _next(client, make_user("coordinator"), one, three).status_code == 403


def test_detail_offers_escalate_and_it_transfers_with_reason(client, make_user, make_ticket, routing_on):
    admin = make_user("admin")
    dept_a, coordinator_a, technician_a = team(make_user)
    dept_b, coordinator_b, _ = team(make_user)
    ticket = make_ticket(make_user("requester"))
    set_department(ticket["id"], dept_a)
    url = f"/api/tickets/{ticket['id']}"
    # Sin siguiente nivel: no hay botón Escalar
    assert client.get(url, headers=coordinator_a["headers"]).json().get("escalate_to") is None
    assert _next(client, admin, dept_a, dept_b).status_code == 200
    detail = client.get(url, headers=coordinator_a["headers"]).json()
    assert detail["escalate_to"]["id"] == dept_b and detail["escalate_to"]["path"]
    # Motivo obligatorio (15 caracteres), como la transferencia
    short = client.post(f"{url}/transfer", headers=coordinator_a["headers"],
                        json={"department_id": detail["escalate_to"]["id"], "reason": "muy corto"})
    assert short.status_code == 422
    done = client.post(f"{url}/transfer", headers=coordinator_a["headers"],
                       json={"department_id": detail["escalate_to"]["id"], "reason": REASON})
    assert done.status_code == 200, done.text
    [entry] = history(ticket["id"], "department")
    assert REASON in entry.new_value
    # Ya en el siguiente nivel (sin siguiente propio): no ofrece Escalar
    assert client.get(url, headers=coordinator_b["headers"]).json().get("escalate_to") is None


def test_no_escalate_when_switch_off(client, make_user, make_ticket):
    admin = make_user("admin")
    dept_a, coordinator_a, _ = team(make_user)
    dept_b, _, _ = team(make_user)
    _next(client, admin, dept_a, dept_b)
    ticket = make_ticket(make_user("requester"))
    set_department(ticket["id"], dept_a)
    # Interruptor apagado
    assert client.get(f"/api/tickets/{ticket['id']}", headers=coordinator_a["headers"]).json().get("escalate_to") is None
