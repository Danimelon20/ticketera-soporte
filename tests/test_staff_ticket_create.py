"""Crear tickets desde el personal (interruptor encendido): mismo formulario para técnico, coordinador y
administrador, asignación solo dentro del departamento (por carga y disponibilidad, sin nivel), el creador fuera
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


def priority_id(name="Media"):
    db = SessionLocal()
    try:
        return db.query(models.Priority).filter(models.Priority.name == name).one().id
    finally:
        db.close()


def _body(dept, area, **extra):
    return {"title": "No imprime la impresora del segundo piso", "description": "Sale la hoja en blanco desde ayer.",
            "department_id": dept, "reporter_name": "María Rojas", "reporter_email": "maria@empresa.test",
            "reporter_area_id": area, "reporter_phone": "Ext. 204", "channel": "phone",
            "priority_id": priority_id(), **extra}


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
    ({"priority_id": None}, "Elija la prioridad"),
    ({"priority_id": 999999}, "Elija la prioridad"),
    ({"category_id": 999999}, "categoría no existe"),
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
    place(inside["id"], dept)
    place(outside["id"], other)
    data = _create(client, make_user("coordinator"), _body(dept, area)).json()
    assert data["assigned_to"] == inside["id"] and data["assigned_name"] == inside["name"]


def test_creator_is_excluded_unless_only_one(client, make_user, routing_on, auto_on):
    dept, area = department(unique("Mesa")), department(handles=False)
    creator, colleague = _on_duty(make_user, "Creador"), _on_duty(make_user, "Colega")
    place(creator["id"], dept)
    place(colleague["id"], dept)
    assert _create(client, creator, _body(dept, area)).json()["assigned_to"] == colleague["id"]
    # Solo él es elegible: se le asigna
    alone = department(unique("Uno solo"))
    place(creator["id"], alone)
    assert _create(client, creator, _body(alone, area)).json()["assigned_to"] == creator["id"]


def test_any_level_goes_to_the_least_loaded_and_unplaced_never_gets_tickets(client, make_user, routing_on, auto_on):
    """Sin nivel del técnico: un ticket Urgente lo toma el de menor carga del departamento."""
    dept, area = department(unique("Sin niveles")), department(handles=False)
    busy, free = _on_duty(make_user, "Ocupado"), _on_duty(make_user, "Libre")
    place(busy["id"], dept)
    place(free["id"], dept)
    _on_duty(make_user, "Sin depto")
    admin = make_user("admin")
    # El primero (empate en carga) se lo lleva uno; el Urgente va al otro, el de menor carga
    first = _create(client, admin, _body(dept, area)).json()["assigned_to"]
    second = _create(client, admin, _body(dept, area, priority_id=priority_id("Urgente"))).json()["assigned_to"]
    assert {first, second} == {busy["id"], free["id"]}


def test_unavailable_or_off_shift_technician_is_skipped(client, make_user, routing_on, auto_on):
    from datetime import date, datetime, timedelta, timezone
    dept, area = department(unique("Disponibilidad")), department(handles=False)
    away, no_shift, ready = (_on_duty(make_user, "Vacaciones"), make_user("technician", unique("Sin turno")),
                             _on_duty(make_user, "Disponible"))
    for user in (away, no_shift, ready):
        place(user["id"], dept)
    db = SessionLocal()
    try:
        # De vacaciones no recibe; sin turno, tampoco: el disponible es el único aunque sume carga
        db.add(models.AvailabilityPeriod(user_id=away["id"], kind="vacation", start_date=date.today() - timedelta(days=1),
                                         end_date=date.today() + timedelta(days=5), created_by=None,
                                         created_at=datetime.now(timezone.utc)))
        db.commit()
    finally:
        db.close()
    for _ in range(2):
        data = _create(client, make_user("admin"), _body(dept, area, priority_id=priority_id("Urgente"))).json()
        assert data["assigned_to"] == ready["id"]


def test_priority_sets_level_and_due_date(client, make_user, routing_on, auto_on):
    dept, area = department(unique("Prioridad")), department(handles=False)
    technician = _on_duty(make_user, "Técnico")
    place(technician["id"], dept)
    creator = make_user("coordinator")
    data = _create(client, creator, _body(dept, area, priority_id=priority_id("Alta"))).json()
    assert data["assigned_to"] == technician["id"]
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, data["id"])
        analysis = ticket.ai_analysis
        # Nivel puesto por una persona: la IA no lo cambia
        assert analysis.human_level == 3 and analysis.human_level_by == creator["id"]
        assert analysis.due_at is not None and analysis.due_at > ticket.created_at
        assert ticket.priority_id == priority_id("Alta")
    finally:
        db.close()


def test_category_is_optional(client, make_user, routing_on):
    dept, area = department(), department(handles=False)
    creator = make_user("technician")
    db = SessionLocal()
    try:
        red = db.query(models.Category).filter(models.Category.name == "Red").one().id
    finally:
        db.close()
    loose = _create(client, creator, _body(dept, area)).json()["id"]
    chosen = _create(client, creator, _body(dept, area, category_id=red)).json()["id"]
    db = SessionLocal()
    try:
        assert db.get(models.Ticket, loose).category.name == "Sin categoría"
        assert db.get(models.Ticket, chosen).category_id == red
    finally:
        db.close()


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
    # Prioridades con su plazo (definen el vencimiento) y categorías sin "Sin categoría"
    due = {p["name"]: p["due_text"] for p in options["priorities"]}
    assert due["Alta"] == "16 horas hábiles" and due["Urgente"] == "4 horas hábiles"
    assert "Sin categoría" not in [c["name"] for c in options["categories"]]
    assert client.get("/api/departments/options", headers=make_user("requester")["headers"]).status_code == 403
