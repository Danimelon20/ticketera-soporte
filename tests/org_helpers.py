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
    """Departamento (y nivel) válidos para invitar o cambiar de rol: técnicos en un departamento compartido que
    atiende tickets; cada coordinador en uno nuevo (un coordinador por departamento)."""
    global _shared_department
    if role == "coordinator":
        return {"department_id": department(unique("Coordinación"))}
    if _shared_department is None:
        _shared_department = department(unique("Soporte compartido"))
    return {"department_id": _shared_department, "tier": 1}
