"""Estructura inicial y migración: base nueva (interruptores encendidos) y base con datos (apagados)."""
import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, models, org_setup, routing


def _fresh_session():
    # Base temporal en archivo, aparte de la de las pruebas (nunca :memory:)
    path = os.path.join(tempfile.mkdtemp(), "nueva.db")
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _agent(db):
    db.add(models.User(name="Agente virtual", email="agente@ticketera.local", role="agent", password_hash="!"))
    db.commit()


def test_new_database_starts_with_switches_on():
    db = _fresh_session()
    try:
        _agent(db)
        org_setup.ensure_structure(db)
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "true"
        assert routing.get_setting(db, routing.CLIENT_DOMAIN_ENFORCED) == "true"
    finally:
        db.close()


def test_database_with_data_starts_with_switches_off_and_migrates_tickets(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ("adn.example",))
    db = _fresh_session()
    try:
        _agent(db)
        db.add_all([models.Category(id=1, name="Red"), models.Priority(id=1, name="Baja", level=1)])
        requester = models.User(name="Cliente", email="c@cliente.example", role="requester", password_hash="!")
        tech = models.User(name="Técnico", email="t@adn.example", role="technician", password_hash="!")
        db.add_all([requester, tech])
        db.flush()
        ticket = models.Ticket(title="Viejo", description="Antes de las áreas", category_id=1, priority_id=1,
                               created_by=requester.id, assigned_to=tech.id)
        db.add(ticket)
        db.commit()

        org_setup.ensure_structure(db)
        org_setup.ensure_structure(db)  # Idempotente: no duplica nada

        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "false"
        assert routing.get_setting(db, routing.CLIENT_DOMAIN_ENFORCED) == "false"
        names = [a.name for a in db.query(models.ServiceArea)]
        assert sorted(names) == sorted(name for name, _, _ in routing.INITIAL_AREAS)
        internal = [a.name for a in db.query(models.ServiceArea).filter(models.ServiceArea.audience == "internal")]
        assert internal == ["Soporte interno (TI)"]
        org = routing.internal_organization(db)
        assert [d.domain for d in org.domains] == ["adn.example"]

        row = routing.routing_of(db, ticket.id)
        assert row.area.name == routing.MIGRATION_AREA and row.department_id is None and row.migrated
        db.refresh(ticket)
        assert ticket.assigned_to == tech.id
        # Personal existente: sin departamento (en la alerta del administrador)
        assert [u.id for u in routing.unplaced_staff(db)] == [tech.id]

        # Al configurar el departamento de entrada, los migrados quedan ahí
        dept = models.Department(name="Soporte N1")
        db.add(dept)
        db.flush()
        db.add(models.ServiceAreaDepartment(area_id=row.area_id, department_id=dept.id))
        routing.set_setting(db, routing.ENTRY_DEPARTMENT, str(dept.id))
        org_setup.migrate_tickets(db)
        db.commit()
        assert routing.routing_of(db, ticket.id).department_id == dept.id
    finally:
        db.close()


def test_switch_values_are_kept_on_restart():
    db = _fresh_session()
    try:
        _agent(db)
        org_setup.ensure_structure(db)
        routing.set_setting(db, routing.ROUTING_ENFORCED, "false")
        db.commit()
        db.add(models.User(name="Alguien", email="a@b.example", role="requester", password_hash="!"))
        db.commit()
        org_setup.ensure_structure(db)
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "false"
        assert routing.get_setting(db, routing.CLIENT_DOMAIN_ENFORCED) == "true"
    finally:
        db.close()
