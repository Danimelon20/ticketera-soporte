"""Áreas y departamentos de prueba, directo en la base. Nombres únicos: la base es compartida entre pruebas."""
import uuid

from app import models
from app.database import SessionLocal


def unique(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def department(name=None, parent_id=None, active=True) -> int:
    db = SessionLocal()
    try:
        dept = models.Department(name=name or unique("Depto"), parent_id=parent_id, is_active=active)
        db.add(dept)
        db.commit()
        return dept.id
    finally:
        db.close()


def area(name=None, audience="external", coordinator_id=None, department_ids=(), entry_department_id=None,
         description="Ejemplos de prueba") -> int:
    db = SessionLocal()
    try:
        row = models.ServiceArea(name=name or unique("Área"), description=description, audience=audience,
                                 coordinator_id=coordinator_id, entry_department_id=entry_department_id)
        db.add(row)
        db.flush()
        for dept_id in department_ids:
            db.add(models.ServiceAreaDepartment(area_id=row.id, department_id=dept_id))
        db.commit()
        return row.id
    finally:
        db.close()


def place(user_id: int, department_id: int | None, tier: int = 1) -> None:
    db = SessionLocal()
    try:
        row = db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == user_id).first()
        if row is None:
            row = models.StaffPlacement(user_id=user_id)
            db.add(row)
        row.department_id = department_id
        row.tier = tier
        db.commit()
    finally:
        db.close()


def route(ticket_id: int, area_id: int, department_id: int | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(models.TicketRouting).filter(models.TicketRouting.ticket_id == ticket_id).first()
        if row is None:
            row = models.TicketRouting(ticket_id=ticket_id, area_id=area_id)
            db.add(row)
        row.area_id, row.department_id = area_id, department_id
        db.commit()
    finally:
        db.close()


def routing_of(ticket_id: int):
    db = SessionLocal()
    try:
        row = db.query(models.TicketRouting).filter(models.TicketRouting.ticket_id == ticket_id).first()
        return (row.area_id, row.department_id) if row else None
    finally:
        db.close()


def history(ticket_id: int, field: str) -> list[str]:
    db = SessionLocal()
    try:
        return [h.new_value for h in db.query(models.History)
                .filter(models.History.ticket_id == ticket_id, models.History.field == field)
                .order_by(models.History.id)]
    finally:
        db.close()


def setting(key: str, value: str | None) -> None:
    from app import routing

    db = SessionLocal()
    try:
        routing.set_setting(db, key, value)
        db.commit()
    finally:
        db.close()


def organization(name=None, domains=(), start=None, end=None, contract="CT-PRUEBA") -> int:
    """Organización cliente; por defecto con contrato vigente (de hace un año a dentro de un año)."""
    from datetime import date, timedelta

    db = SessionLocal()
    try:
        org = models.Organization(
            name=name or unique("Cliente"), kind="client", contract_number=contract,
            contract_start=start or date.today() - timedelta(days=365),
            contract_end=end or date.today() + timedelta(days=365),
        )
        db.add(org)
        db.flush()
        for domain in domains:
            db.add(models.OrganizationDomain(organization_id=org.id, domain=domain))
        db.commit()
        return org.id
    finally:
        db.close()


def unique_domain(prefix="cliente") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}.example"


def link(user_id: int, org_id: int, authorized=False, activation="active") -> None:
    db = SessionLocal()
    try:
        row = db.query(models.RequesterProfile).filter(models.RequesterProfile.user_id == user_id).first()
        if row is None:
            row = models.RequesterProfile(user_id=user_id)
            db.add(row)
        row.organization_id, row.authorized_for_changes, row.activation = org_id, authorized, activation
        db.commit()
    finally:
        db.close()
