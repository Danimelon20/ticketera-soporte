"""Departamentos y ubicaciones de prueba, directo en la base. Nombres únicos: la base es compartida."""
import uuid

from app import models
from app.database import SessionLocal


def unique(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def department(name=None, parent_id=None, active=True, handles=True) -> int:
    db = SessionLocal()
    try:
        dept = models.Department(name=name or unique("Depto"), parent_id=parent_id, is_active=active,
                                 handles_tickets=handles)
        db.add(dept)
        db.commit()
        return dept.id
    finally:
        db.close()


def changes(action: str, label_contains: str = "") -> list[models.AccountChange]:
    db = SessionLocal()
    try:
        rows = db.query(models.AccountChange).filter(models.AccountChange.action == action).all()
        return [r for r in rows if label_contains in (r.target_label or "")]
    finally:
        db.close()


_shared_department: int | None = None


def placement_for(role: str) -> dict:
    """Departamento válido para invitar o cambiar de rol: técnicos en un departamento compartido que
    atiende tickets; cada coordinador en uno nuevo (un coordinador por departamento)."""
    global _shared_department
    if role == "coordinator":
        return {"department_id": department(unique("Coordinación"))}
    if _shared_department is None:
        _shared_department = department(unique("Soporte compartido"))
    return {"department_id": _shared_department}


def place(user_id: int, department_id: int) -> None:
    """Técnico en un departamento (directo en la base)."""
    db = SessionLocal()
    try:
        db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == user_id).delete()
        db.add(models.StaffPlacement(user_id=user_id, department_id=department_id))
        db.commit()
    finally:
        db.close()


def coordinate(user_id: int, department_id: int) -> None:
    """Coordinador de un departamento (directo en la base)."""
    db = SessionLocal()
    try:
        db.query(models.DepartmentCoordinator).filter(models.DepartmentCoordinator.user_id == user_id).delete()
        db.add(models.DepartmentCoordinator(department_id=department_id, user_id=user_id))
        db.commit()
    finally:
        db.close()


def set_department(ticket_id: int, department_id: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(models.TicketDepartment).filter(models.TicketDepartment.ticket_id == ticket_id).first()
        if row is None:
            db.add(models.TicketDepartment(ticket_id=ticket_id, department_id=department_id))
        else:
            row.department_id = department_id
        db.commit()
    finally:
        db.close()


def history(ticket_id: int, field: str) -> list[models.History]:
    db = SessionLocal()
    try:
        return db.query(models.History).filter(models.History.ticket_id == ticket_id,
                                               models.History.field == field).order_by(models.History.id).all()
    finally:
        db.close()


def team(make_user):
    """Departamento que atiende tickets con su coordinador y un técnico. Devuelve (dept_id, coordinador, técnico)."""
    dept = department(unique("Equipo"))
    coordinator = make_user("coordinator", unique("Coordinador"))
    technician = make_user("technician", unique("Técnico"))
    coordinate(coordinator["id"], dept)
    place(technician["id"], dept)
    return dept, coordinator, technician
