"""Organigrama, ubicación del personal y activación (solo el administrador).

Adaptado de la rama areas-y-clientes sin áreas de atención: el administrador crea, renombra, mueve y
desactiva departamentos y marca los que atienden tickets.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from .. import audit, departments, models, routing
from ..schemas import (
    DepartmentCreate, DepartmentImportConfirm, DepartmentImportPreview, DepartmentImportResult, DepartmentRead,
    DepartmentUpdate,
)
from .admin import require_admin

router = APIRouter(prefix="/api/admin", tags=["org"])

NOT_FOUND_DEPARTMENT = "El departamento no existe"
CYCLE = "Un departamento no puede quedar dentro de sí mismo"
TAKEN_NAME = "Ya existe un departamento con ese nombre en el mismo lugar"
TAKEN_ALIAS = "Ese alias ya existe"
MAX_IMPORT_BYTES = 1_000_000


# ----- Departamentos -----

def _department_reads(db: Session) -> list[DepartmentRead]:
    by_id = routing.departments_by_id(db)
    aliases: dict[int, list[str]] = {}
    for alias in db.query(models.DepartmentAlias).order_by(models.DepartmentAlias.alias):
        aliases.setdefault(alias.department_id, []).append(alias.alias)
    rows = [
        DepartmentRead(
            id=dept.id, name=dept.name, parent_id=dept.parent_id, path=routing.path_label(dept, by_id),
            is_active=dept.is_active, active_path=routing.is_active_path(dept, by_id),
            handles_tickets=dept.handles_tickets, aliases=aliases.get(dept.id, []),
        )
        for dept in by_id.values()
    ]
    rows.sort(key=lambda r: r.path.casefold())
    return rows


def _department_or_404(db: Session, department_id: int) -> models.Department:
    dept = db.get(models.Department, department_id)
    if dept is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DEPARTMENT)
    return dept


def _check_unique(db: Session, name: str, parent_id: int | None, skip_id: int | None = None) -> None:
    """Mismo nombre (sin tildes, mayúsculas ni abreviaturas) entre hermanos: 409."""
    query = db.query(models.Department).filter(models.Department.parent_id.is_(None) if parent_id is None
                                               else models.Department.parent_id == parent_id)
    for other in query:
        if other.id != skip_id and departments.normalize_name(other.name) == departments.normalize_name(name):
            raise HTTPException(status_code=409, detail=TAKEN_NAME)


def _one(db: Session, department_id: int) -> DepartmentRead:
    return next(r for r in _department_reads(db) if r.id == department_id)


def _yes_no(value: bool) -> str:
    return "Sí" if value else "No"


@router.get("/departments", response_model=list[DepartmentRead])
def list_departments(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return _department_reads(db)


@router.post("/departments", response_model=DepartmentRead, status_code=201)
def create_department(data: DepartmentCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    name = " ".join(data.name.split())
    if data.parent_id is not None:
        _department_or_404(db, data.parent_id)
    _check_unique(db, name, data.parent_id)
    dept = models.Department(name=name, parent_id=data.parent_id, is_active=True,
                             handles_tickets=data.handles_tickets)
    db.add(dept)
    db.flush()
    label = routing.path_label(dept, routing.departments_by_id(db))
    audit.record(db, admin.id, "department_created", target_label=label[:150],
                 new="Atiende tickets" if dept.handles_tickets else None)
    db.commit()
    return _one(db, dept.id)


@router.patch("/departments/{department_id}", response_model=DepartmentRead)
def update_department(department_id: int, data: DepartmentUpdate, db: Session = Depends(get_db),
                      admin=Depends(require_admin)):
    dept = _department_or_404(db, department_id)
    by_id = routing.departments_by_id(db)
    before = routing.path_label(dept, by_id)
    changes: list[tuple[str | None, str | None]] = []

    parent_id = dept.parent_id
    if data.parent_id is not None:
        parent_id = data.parent_id or None
        if parent_id is not None:
            _department_or_404(db, parent_id)
            if parent_id in routing.descendants(by_id, [dept.id]):
                raise HTTPException(status_code=400, detail=CYCLE)
    name = " ".join(data.name.split()) if data.name is not None else dept.name
    if name != dept.name or parent_id != dept.parent_id:
        _check_unique(db, name, parent_id, skip_id=dept.id)
        dept.name, dept.parent_id = name, parent_id
        db.flush()
        changes.append((before, routing.path_label(dept, routing.departments_by_id(db))))
    if data.is_active is not None and data.is_active != dept.is_active:
        dept.is_active = data.is_active
        changes.append(("Activo" if not data.is_active else "Desactivado",
                        "Activo" if data.is_active else "Desactivado"))
    if data.handles_tickets is not None and data.handles_tickets != dept.handles_tickets:
        dept.handles_tickets = data.handles_tickets
        changes.append((f"Atiende tickets: {_yes_no(not data.handles_tickets)}",
                        f"Atiende tickets: {_yes_no(data.handles_tickets)}"))
    if data.add_alias:
        if not departments.add_alias(db, dept, data.add_alias):
            raise HTTPException(status_code=409, detail=TAKEN_ALIAS)
        changes.append((None, f"Alias: {data.add_alias}"))
    if data.remove_alias:
        removed = db.query(models.DepartmentAlias).filter(
            models.DepartmentAlias.department_id == dept.id, models.DepartmentAlias.alias == data.remove_alias
        ).delete()
        if removed:
            changes.append((f"Alias: {data.remove_alias}", None))
    label = routing.path_label(dept, routing.departments_by_id(db))
    for old, new in changes:
        audit.record(db, admin.id, "department_changed", target_label=label[:150],
                     old=old[:150] if old else None, new=new[:150] if new else None)
    db.commit()
    return _one(db, dept.id)


# ----- Importación del organigrama (el archivo solo se lee en memoria: nunca se guarda) -----

@router.post("/departments/import/preview", response_model=DepartmentImportPreview)
async def preview_import(file: UploadFile = File(...), db: Session = Depends(get_db), admin=Depends(require_admin)):
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="El archivo pasa de 1 MB")
    try:
        paths = departments.read_paths(content)
    except departments.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return departments.preview(db, paths)


@router.post("/departments/import/confirm", response_model=DepartmentImportResult)
def confirm_import(data: DepartmentImportConfirm, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Crea las rutas nuevas sin "Atiende tickets" (se marca después, departamento por departamento)."""
    try:
        result = departments.confirm(db, data.paths, [m.model_dump() for m in data.merges])
    except departments.ImportError_ as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    if result["created"] or result["aliases"]:
        audit.record(db, admin.id, "departments_imported", target_label="Organigrama",
                     new=f"{result['created']} nuevo(s), {result['aliases']} alias")
    db.commit()
    return result
