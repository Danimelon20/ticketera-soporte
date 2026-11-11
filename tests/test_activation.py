"""Interruptor de tickets por departamento: siembra al arrancar, lista de verificación, Registro de cambios y
"cerrado por defecto" (con el interruptor encendido, el personal sin departamento no recibe tickets)."""
import os
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auto_assign, models, org_setup, routing
from app.database import SessionLocal
from org_helpers import changes, department

ALL_DAYS = range(7)


# ----- Siembra al arrancar (base temporal en archivo, aparte de la de las pruebas) -----

def _fresh_session():
    path = os.path.join(tempfile.mkdtemp(), "nueva.db")
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _agent(db):
    db.add(models.User(name="Agente virtual", email="agente@ticketera.local", role="agent", password_hash="!"))
    db.commit()


def test_new_database_starts_on():
    db = _fresh_session()
    try:
        _agent(db)
        org_setup.ensure_structure(db)
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "true"
    finally:
        db.close()


def test_database_with_data_starts_off_and_restart_keeps_the_value():
    db = _fresh_session()
    try:
        _agent(db)
        db.add(models.User(name="Técnica previa", email="previa@test.com", role="technician", password_hash="x"))
        db.commit()
        org_setup.ensure_structure(db)
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "false"
        # Existía: el personal previo queda sin departamento (aparece en la alerta)
        assert [u.email for u in routing.unplaced_staff(db)] == ["previa@test.com"]
        # Un reinicio no pisa lo que eligió el administrador
        routing.set_setting(db, routing.ROUTING_ENFORCED, "true")
        db.commit()
        org_setup.ensure_structure(db)
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "true"
    finally:
        db.close()


# ----- Lista de verificación y encendido -----

def _activation(client, admin):
    response = client.get("/api/admin/activation", headers=admin["headers"])
    assert response.status_code == 200, response.text
    [switch] = response.json()
    return switch


def _set(client, admin, enabled):
    return client.put(f"/api/admin/activation/{routing.ROUTING_ENFORCED}", headers=admin["headers"],
                      json={"enabled": enabled})


def _items(switch):
    return {item["key"]: item for item in switch["checklist"]}


def _isolated_staff():
    """La base es compartida: el resto del personal activo y de los departamentos que atienden se apagan durante la
    prueba y se restauran al final."""
    db = SessionLocal()
    try:
        users = [u.id for u in db.query(models.User).filter(
            models.User.role.in_(("technician", "coordinator")), models.User.is_active.is_(True))]
        depts = [d.id for d in db.query(models.Department).filter(models.Department.handles_tickets.is_(True))]
        db.query(models.User).filter(models.User.id.in_(users)).update({"is_active": False}, synchronize_session=False)
        db.query(models.Department).filter(models.Department.id.in_(depts)).update(
            {"handles_tickets": False}, synchronize_session=False)
        db.commit()
        return users, depts
    finally:
        db.close()


def _restore(users, depts):
    db = SessionLocal()
    try:
        db.query(models.User).filter(models.User.id.in_(users)).update({"is_active": True}, synchronize_session=False)
        db.query(models.Department).filter(models.Department.id.in_(depts)).update(
            {"handles_tickets": True}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def clean_org():
    users, depts = _isolated_staff()
    yield
    _restore(users, depts)


def _place(client, admin, user_id, dept_id):
    response = client.put(f"/api/admin/users/{user_id}/placement", headers=admin["headers"],
                          json={"department_id": dept_id})
    assert response.status_code == 200, response.text


def test_checklist_blocks_turning_on_item_by_item(client, make_user, clean_org):
    admin = make_user("admin")
    # 1. Ningún departamento atiende tickets
    switch = _activation(client, admin)
    assert switch["enabled"] is False and switch["ready"] is False
    assert _items(switch)["serving_departments"]["ok"] is False
    assert _set(client, admin, True).status_code == 409

    dept = department()
    technician = make_user("technician")
    switch = _activation(client, admin)
    items = _items(switch)
    assert items["serving_departments"]["ok"] is True
    # 2. Personal sin departamento, 3. sin coordinador, 4. sin técnicos
    assert items["unplaced_staff"]["ok"] is False
    assert items["departments_without_coordinator"]["ok"] is False
    assert items["departments_without_technicians"]["ok"] is False
    assert _set(client, admin, True).status_code == 409

    _place(client, admin, technician["id"], dept)
    items = _items(_activation(client, admin))
    assert items["unplaced_staff"]["ok"] and items["departments_without_technicians"]["ok"]
    assert items["departments_without_coordinator"]["ok"] is False
    assert _set(client, admin, True).status_code == 409

    coordinator = make_user("coordinator")
    _place(client, admin, coordinator["id"], dept)
    switch = _activation(client, admin)
    assert switch["ready"] is True
    response = _set(client, admin, True)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True and response.json()["updated_by_name"] == admin["name"]

    # Apagar siempre se puede, aunque la lista se rompa
    make_user("technician")
    assert _set(client, admin, False).json()["enabled"] is False
    audited = [(c.actor_id, c.old_value, c.new_value) for c in changes("setting_changed")]
    assert (admin["id"], "Apagado", "Encendido") in audited
    assert (admin["id"], "Encendido", "Apagado") in audited


def test_activation_is_admin_only(client, make_user):
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert client.get("/api/admin/activation", headers=user["headers"]).status_code == 403
        assert _set(client, user, False).status_code == 403
    assert client.put("/api/admin/activation/otro", headers=make_user("admin")["headers"],
                      json={"enabled": True}).status_code == 404


# ----- Cerrado por defecto -----

def _on_duty(make_user, name):
    """Técnico con horario de todos los días (siempre en turno)."""
    user = make_user("technician", name)
    db = SessionLocal()
    try:
        for weekday in ALL_DAYS:
            db.add(models.TechnicianSchedule(user_id=user["id"], weekday=weekday, start_time="00:00", end_time="23:59"))
        db.commit()
    finally:
        db.close()
    return user


def _candidate_ids():
    db = SessionLocal()
    try:
        return {u.id for u in auto_assign.candidates(db, datetime.now(timezone.utc).replace(tzinfo=None))}
    finally:
        db.close()


def test_switch_off_keeps_old_behaviour(client, make_user):
    unplaced = _on_duty(make_user, "Sin depto, apagado")
    assert unplaced["id"] in _candidate_ids()
    me = client.get("/api/auth/me", headers=unplaced["headers"]).json()
    assert me["placement_missing"] is False


def test_switch_on_unplaced_staff_gets_no_tickets_and_sees_the_notice(client, make_user, routing_on):
    admin = make_user("admin")
    unplaced = _on_duty(make_user, "Sin depto, encendido")
    placed = _on_duty(make_user, "Con depto, encendido")
    _place(client, admin, placed["id"], department())
    ids = _candidate_ids()
    assert unplaced["id"] not in ids and placed["id"] in ids
    # Puede entrar y ve el aviso
    me = client.get("/api/auth/me", headers=unplaced["headers"])
    assert me.status_code == 200 and me.json()["placement_missing"] is True
    assert client.get("/api/auth/me", headers=placed["headers"]).json()["placement_missing"] is False
    coordinator = make_user("coordinator")
    assert client.get("/api/auth/me", headers=coordinator["headers"]).json()["placement_missing"] is True


def test_old_version_tables_get_their_columns_and_switch_starts_off():
    """Base que ya tenía departamentos de la rama areas-y-clientes: tabla sin handles_tickets e interruptor
    encendido. Al arrancar esta versión: se agrega la columna y el interruptor queda apagado (hay datos)."""
    path = os.path.join(tempfile.mkdtemp(), "anterior.db")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE departments (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, "
                             "parent_id INTEGER, is_active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)")
        conn.exec_driver_sql("INSERT INTO departments (name, parent_id, is_active, created_at) "
                             "VALUES ('Soporte N1', NULL, 1, '2026-10-29 10:00:00')")
    models.Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        _agent(db)
        db.add(models.User(name="Técnico previo", email="previo@test.com", role="technician", password_hash="x"))
        db.add(models.AppSetting(key=routing.ROUTING_ENFORCED, value="true", updated_at=datetime(2026, 10, 29)))
        db.commit()
        org_setup.ensure_structure(db)
        dept = db.query(models.Department).one()
        assert dept.name == "Soporte N1" and dept.handles_tickets is False
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "false"
        # Después de la primera vez, la elección del administrador se respeta
        routing.set_setting(db, routing.ROUTING_ENFORCED, "true")
        db.commit()
        org_setup.ensure_structure(db)
        assert routing.get_setting(db, routing.ROUTING_ENFORCED) == "true"
    finally:
        db.close()
