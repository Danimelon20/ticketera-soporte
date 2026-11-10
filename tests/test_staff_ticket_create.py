"""Crear tickets desde el personal (interruptor encendido): mismo formulario para técnico, coordinador y
administrador, asignación solo dentro del departamento (según nivel, carga y disponibilidad), el creador fuera
salvo que sea el único elegible y el historial "Creado por X en nombre de Y vía Z"."""
import pytest

from app import config, models
from app.database import SessionLocal
from org_helpers import coordinate, department, history, place, unique

ALL_DAYS = range(7)


@pytest.fixture
def auto_on(monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "count")


def _on_duty(make_user, name="Técnico"):
    user = make_user("technician", unique(name))
    db = SessionLocal()
    try:
        for weekday in ALL_DAYS:
            db.add(models.TechnicianSchedule(user_id=user["id"], weekday=weekday, start_time="00:00", end_time="23:59"))
        db.commit()
    finally:
        db.close()
    return user


def _body(dept, area, **extra):
    return {"title": "No imprime la impresora del segundo piso", "description": "Sale la hoja en blanco desde ayer.",
            "department_id": dept, "reporter_name": "María Rojas", "reporter_email": "maria@empresa.test",
            "reporter_area_id": area, "reporter_phone": "Ext. 204", "channel": "phone", **extra}


def _create(client, user, body):
    return client.post("/api/tickets", headers=user["headers"], json=body)


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_staff_creates_with_the_same_form(client, make_user, routing_on, role):
    dept = department(unique("Atención"))
    area = department(unique("Contabilidad"), handles=False)
    creator = make_user(role, unique("Creador"))
    response = _create(client, creator, _body(dept, area))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["department_name"].startswith("Atención") and data["assigned_to"] is None
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, data["id"])
        assert ticket.created_by == creator["id"] and ticket.status == "new"
        reporter = db.query(models.TicketReporter).filter(models.TicketReporter.ticket_id == ticket.id).one()
        assert (reporter.name, reporter.channel, reporter.area_department_id) == ("María Rojas", "phone", area)
    finally:
        db.close()
    [entry] = history(data["id"], "created_on_behalf")
    assert entry.new_value == f"Creado por {creator['name']} en nombre de María Rojas vía teléfono"


def test_switch_off_keeps_the_old_rules(client, make_user):
    dept, area = department(), department(handles=False)
    for role in ("technician", "admin"):
        assert _create(client, make_user(role), _body(dept, area)).status_code == 403


@pytest.mark.parametrize("change, message", [
    ({"reporter_name": "  "}, "nombre de quien reporta"),
    ({"channel": None}, "canal"),
    ({"reporter_area_id": None}, "área de quien reporta"),
    ({"reporter_email": "sin-arroba"}, "correo válido"),
    ({"department_id": None}, "Elija el departamento"),
])
def test_required_fields(client, make_user, routing_on, change, message):
    dept, area = department(), department(handles=False)
    response = _create(client, make_user("coordinator"), _body(dept, area, **change))
    assert response.status_code == 422 and message in response.json()["detail"]


def test_department_must_handle_tickets(client, make_user, routing_on):
    area = department(handles=False)
    response = _create(client, make_user("technician"), _body(area, area))
    assert response.status_code == 422


def test_assignment_stays_inside_the_department(client, make_user, routing_on, auto_on):
    dept, other = department(unique("Soporte")), department(unique("Redes"))
    area = department(handles=False)
    inside, outside = _on_duty(make_user, "Adentro"), _on_duty(make_user, "Afuera")
    place(inside["id"], dept, 1)
    place(outside["id"], other, 1)
    data = _create(client, make_user("coordinator"), _body(dept, area)).json()
    assert data["assigned_to"] == inside["id"] and data["assigned_name"] == inside["name"]


def test_creator_is_excluded_unless_only_one(client, make_user, routing_on, auto_on):
    dept, area = department(unique("Mesa")), department(handles=False)
    creator, colleague = _on_duty(make_user, "Creador"), _on_duty(make_user, "Colega")
    place(creator["id"], dept, 1)
    place(colleague["id"], dept, 1)
    assert _create(client, creator, _body(dept, area)).json()["assigned_to"] == colleague["id"]
    # Solo él es elegible: se le asigna
    alone = department(unique("Uno solo"))
    place(creator["id"], alone, 1)
    assert _create(client, creator, _body(alone, area)).json()["assigned_to"] == creator["id"]


def test_tier_matching_climbs_and_unplaced_never_gets_tickets(client, make_user, routing_on, auto_on):
    dept, area = department(unique("Niveles")), department(handles=False)
    senior = _on_duty(make_user, "N2")
    place(senior["id"], dept, 2)
    _on_duty(make_user, "Sin depto")
    # Ticket sin nivel: pide N1; no hay, sube a N2
    assert _create(client, make_user("admin"), _body(dept, area)).json()["assigned_to"] == senior["id"]


def test_nobody_eligible_stays_pending_in_the_department(client, make_user, routing_on, auto_on):
    dept, area = department(unique("Vacío")), department(handles=False)
    coordinator = make_user("coordinator")
    coordinate(coordinator["id"], dept)
    data = _create(client, coordinator, _body(dept, area)).json()
    assert data["assigned_to"] is None and data["assigned_name"] is None
    queue = client.get("/api/queue", headers=coordinator["headers"], params={"view": "unassigned"}).json()
    assert data["id"] in [i["id"] for i in queue["items"]]
    # En la lista se ve a quien reportó
    item = next(i for i in queue["items"] if i["id"] == data["id"])
    assert item["requester_name"] == "María Rojas"


def test_department_options_for_staff(client, make_user):
    dept, area = department(unique("Opción")), department(unique("Área"), handles=False)
    options = client.get("/api/departments/options", headers=make_user("technician")["headers"]).json()
    assert dept in [o["id"] for o in options["serving"]] and area not in [o["id"] for o in options["serving"]]
    assert {dept, area} <= {o["id"] for o in options["areas"]}
    assert client.get("/api/departments/options", headers=make_user("requester")["headers"]).status_code == 403
