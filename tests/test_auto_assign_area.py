"""Asignación automática restringida al área (o departamento) del ticket y al nivel del técnico."""
from datetime import datetime

import pytest

from app import auto_assign, config, mailer, models, routing
from app.database import SessionLocal
from org_helpers import area, department, place, route

MONDAY_10AM = datetime(2031, 3, 10, 16, 0)
ALL_DAY = [(d, "00:00", "23:59") for d in range(7)]


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "levels")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: True)
    _clean()
    yield
    _clean()


def _clean():
    # Igual que test_auto_assign: horarios de otras pruebas cambiarían los candidatos
    db = SessionLocal()
    try:
        db.query(models.TicketAutoAssignment).delete()
        db.query(models.TechnicianSchedule).delete()
        db.query(models.AvailabilityPeriod).delete()
        db.commit()
    finally:
        db.close()


def _tech(make_user, department_id, tier=1, name="Técnico"):
    user = make_user("technician", name)
    db = SessionLocal()
    try:
        for weekday, start, end in ALL_DAY:
            db.add(models.TechnicianSchedule(user_id=user["id"], weekday=weekday, start_time=start, end_time=end))
        db.commit()
    finally:
        db.close()
    if department_id is not None:
        place(user["id"], department_id, tier)
    return user


def _ticket(requester, level=1):
    db = SessionLocal()
    try:
        ticket = models.Ticket(title="Ticket", description="Descripción", category_id=1, priority_id=1,
                               created_by=requester["id"], source="web")
        db.add(ticket)
        db.flush()
        db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="done", ai_level=level))
        db.commit()
        return ticket.id
    finally:
        db.close()


def _assigned_to(ticket_id):
    db = SessionLocal()
    try:
        return db.get(models.Ticket, ticket_id).assigned_to
    finally:
        db.close()


def test_only_technicians_of_the_ticket_area(make_user, routing_on):
    dept_a, dept_b = department(), department()
    area_a = area(department_ids=[dept_a])
    area(department_ids=[dept_b])
    inside = _tech(make_user, dept_a, name="Del área")
    _tech(make_user, dept_b, name="De otra área")
    _tech(make_user, None, name="Sin departamento")
    ticket = _ticket(make_user("requester"))
    route(ticket, area_a)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "assigned"
    assert _assigned_to(ticket) == inside["id"]


def test_ticket_department_and_subdepartments(make_user, routing_on):
    parent = department()
    child = department(parent_id=parent)
    sibling = department()
    area_id = area(department_ids=[parent, sibling])
    in_child = _tech(make_user, child, name="Subdepartamento")
    _tech(make_user, sibling, name="Hermano")
    ticket = _ticket(make_user("requester"))
    route(ticket, area_id, parent)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "assigned"
    assert _assigned_to(ticket) == in_child["id"]


def test_unplaced_technician_never_receives_tickets(make_user, routing_on):
    dept = department()
    area_id = area(department_ids=[dept])
    _tech(make_user, None, name="Sin departamento")
    ticket = _ticket(make_user("requester"))
    route(ticket, area_id)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "waiting"
    assert _assigned_to(ticket) is None


def test_deactivated_department_stops_assignments(make_user, routing_on):
    dept = department(active=False)
    area_id = area(department_ids=[dept])
    _tech(make_user, dept)
    ticket = _ticket(make_user("requester"))
    route(ticket, area_id)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "waiting"


def test_level_picks_matching_tier_and_climbs(make_user, routing_on):
    dept = department()
    area_id = area(department_ids=[dept])
    n1 = _tech(make_user, dept, tier=1, name="N1")
    n3 = _tech(make_user, dept, tier=3, name="N3")
    easy, hard, mid = (_ticket(make_user("requester"), level) for level in (1, 4, 3))
    for ticket in (easy, hard, mid):
        route(ticket, area_id)
    assert auto_assign.assign_new(easy, MONDAY_10AM) == "assigned"
    assert _assigned_to(easy) == n1["id"]
    assert auto_assign.assign_new(hard, MONDAY_10AM) == "assigned"
    assert _assigned_to(hard) == n3["id"]
    # Nivel 3 pide N2: no hay, sube a N3
    assert auto_assign.assign_new(mid, MONDAY_10AM) == "assigned"
    assert _assigned_to(mid) == n3["id"]


def test_required_tier():
    assert [routing.required_tier(level) for level in (None, 0, 1, 2, 3, 4)] == [1, 1, 1, 1, 2, 3]


def test_switch_off_keeps_current_behavior(make_user):
    """Apagado: cualquier técnico disponible, aunque no tenga departamento (como antes)."""
    _tech(make_user, None, name="Sin departamento")
    ticket = _ticket(make_user("requester"))
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "assigned"
