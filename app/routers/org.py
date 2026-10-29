"""Departamentos, áreas de atención, ubicación del personal y activación (solo el administrador).

El administrador crea, edita y mueve departamentos entre áreas desde aquí, sin tocar código. El archivo
del organigrama solo se lee en memoria para la vista previa: nunca se guarda.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from .. import audit, config, departments, models, org_setup, routing
from ..schemas import (
    AreaCreate, AreaDepartments, AreaOption, AreaRead, AreaUpdate, ChecklistItem, DepartmentCreate,
    DepartmentImportConfirm, DepartmentImportPreview, DepartmentImportResult, DepartmentRead, DepartmentUpdate,
    EntryDepartmentUpdate, InternalRequesterCreate, PlacementRead, PlacementUpdate, StaffAlerts, SwitchRead,
    SwitchUpdate, UnplacedPerson,
)
from ..security import mask_email, unusable_password_hash
from .admin import require_admin
from .auth import _find_user, normalize_email

router = APIRouter(prefix="/api/admin", tags=["org"])

MAX_IMPORT_BYTES = 1_000_000
NOT_FOUND_DEPARTMENT = "El departamento no existe"
NOT_FOUND_AREA = "El área no existe"
CYCLE = "Un departamento no puede quedar dentro de sí mismo"
TAKEN_NAME = "Ya existe un departamento con ese nombre en el mismo lugar"
COORDINATOR_TAKEN = "Ese coordinador ya dirige el área «{area}»"
ENTRY_OUTSIDE = "El departamento de entrada debe ser uno de los departamentos del área"
DEPARTMENT_REQUIRED = "Elija el departamento del técnico"
AREA_REQUIRED_STAFF = "Elija el área que dirige el coordinador"
CHECKLIST_PENDING = "Revise la lista de verificación antes de encender"
PHASE_2 = "Se habilita con las organizaciones y contratos (Fase 2)"


# ----- Departamentos -----

def _department_reads(db: Session) -> list[DepartmentRead]:
    by_id = routing.departments_by_id(db)
    links = dict(db.query(models.ServiceAreaDepartment.department_id, models.ServiceAreaDepartment.area_id))
    areas = {a.id: a.name for a in db.query(models.ServiceArea)}
    counts = {}
    for (dept_id,) in (
        db.query(models.StaffPlacement.department_id)
        .join(models.User, models.User.id == models.StaffPlacement.user_id)
        .filter(models.User.is_active.is_(True), models.User.role == "technician")
    ):
        counts[dept_id] = counts.get(dept_id, 0) + 1
    aliases: dict[int, list[str]] = {}
    for alias in db.query(models.DepartmentAlias).order_by(models.DepartmentAlias.alias):
        aliases.setdefault(alias.department_id, []).append(alias.alias)
    rows = []
    for dept in by_id.values():
        area_id = routing.area_of_department(db, dept.id, by_id) if links else None
        rows.append(DepartmentRead(
            id=dept.id, name=dept.name, parent_id=dept.parent_id, path=routing.path_label(dept, by_id),
            is_active=dept.is_active, active_path=routing.is_active_path(dept, by_id),
            area_id=area_id, area_name=areas.get(area_id), aliases=aliases.get(dept.id, []),
            technicians=counts.get(dept.id, 0),
        ))
    rows.sort(key=lambda r: r.path.casefold())
    return rows


def _department_or_404(db: Session, department_id: int) -> models.Department:
    dept = db.get(models.Department, department_id)
    if dept is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DEPARTMENT)
    return dept


def _check_unique(db: Session, name: str, parent_id: int | None, skip_id: int | None = None) -> None:
    query = db.query(models.Department).filter(models.Department.parent_id.is_(parent_id) if parent_id is None
                                               else models.Department.parent_id == parent_id)
    for other in query:
        if other.id != skip_id and departments.normalize_name(other.name) == departments.normalize_name(name):
            raise HTTPException(status_code=409, detail=TAKEN_NAME)


def _one(db: Session, department_id: int) -> DepartmentRead:
    return next(r for r in _department_reads(db) if r.id == department_id)


@router.get("/departments", response_model=list[DepartmentRead])
def list_departments(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return _department_reads(db)


@router.post("/departments", response_model=DepartmentRead, status_code=201)
def create_department(data: DepartmentCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    name = " ".join(data.name.split())
    if data.parent_id is not None:
        _department_or_404(db, data.parent_id)
    _check_unique(db, name, data.parent_id)
    dept = models.Department(name=name, parent_id=data.parent_id, is_active=True)
    db.add(dept)
    db.commit()
    return _one(db, dept.id)


@router.patch("/departments/{department_id}", response_model=DepartmentRead)
def update_department(department_id: int, data: DepartmentUpdate, db: Session = Depends(get_db),
                      admin=Depends(require_admin)):
    dept = _department_or_404(db, department_id)
    parent_id = dept.parent_id
    if data.parent_id is not None:
        parent_id = data.parent_id or None
        if parent_id is not None:
            _department_or_404(db, parent_id)
            if parent_id in routing.descendants(routing.departments_by_id(db), [dept.id]):
                raise HTTPException(status_code=400, detail=CYCLE)
    name = " ".join(data.name.split()) if data.name is not None else dept.name
    if name != dept.name or parent_id != dept.parent_id:
        _check_unique(db, name, parent_id, skip_id=dept.id)
    dept.name, dept.parent_id = name, parent_id
    if data.is_active is not None:
        # Desactivado: sus técnicos quedan sin departamento (aparecen en la alerta) y no reciben tickets
        dept.is_active = data.is_active
    if data.add_alias:
        if not departments.add_alias(db, dept, data.add_alias):
            raise HTTPException(status_code=409, detail="Ese alias ya existe")
    if data.remove_alias:
        db.query(models.DepartmentAlias).filter(
            models.DepartmentAlias.department_id == dept.id, models.DepartmentAlias.alias == data.remove_alias
        ).delete()
    db.commit()
    return _one(db, dept.id)


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
    try:
        result = departments.confirm(db, data.paths, [m.model_dump() for m in data.merges])
    except departments.ImportError_ as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return result


# ----- Áreas -----

def _area_read(db: Session, area: models.ServiceArea, by_id: dict) -> AreaRead:
    ids = sorted(r[0] for r in db.query(models.ServiceAreaDepartment.department_id)
                 .filter(models.ServiceAreaDepartment.area_id == area.id))
    entry = by_id.get(area.entry_department_id) if area.entry_department_id else None
    return AreaRead(
        id=area.id, name=area.name, description=area.description, audience=area.audience, is_active=area.is_active,
        coordinator_id=area.coordinator_id,
        coordinator_name=(area.coordinator.name or "Sin nombre") if area.coordinator else None,
        entry_department_id=area.entry_department_id, entry_department_name=routing.path_label(entry, by_id),
        department_ids=ids,
    )


def _area_or_404(db: Session, area_id: int) -> models.ServiceArea:
    area = db.get(models.ServiceArea, area_id)
    if area is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_AREA)
    return area


def _check_area_name(db: Session, name: str, skip_id: int | None = None) -> None:
    other = db.query(models.ServiceArea).filter(models.ServiceArea.name == name).first()
    if other is not None and other.id != skip_id:
        raise HTTPException(status_code=409, detail="Ya existe un área con ese nombre")


@router.get("/areas", response_model=list[AreaRead])
def list_areas(db: Session = Depends(get_db), admin=Depends(require_admin)):
    by_id = routing.departments_by_id(db)
    return [_area_read(db, a, by_id) for a in db.query(models.ServiceArea).order_by(models.ServiceArea.id)]


@router.post("/areas", response_model=AreaRead, status_code=201)
def create_area(data: AreaCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    name = " ".join(data.name.split())
    _check_area_name(db, name)
    area = models.ServiceArea(name=name, description=data.description, audience=data.audience)
    db.add(area)
    db.commit()
    return _area_read(db, area, routing.departments_by_id(db))


def set_coordinator(db: Session, admin: models.User, area: models.ServiceArea, user_id: int | None) -> None:
    """Un coordinador por área y un área por coordinador. No hace commit."""
    if user_id is None:
        if area.coordinator_id is not None:
            old = db.get(models.User, area.coordinator_id)
            area.coordinator_id = None
            if old is not None:
                audit.record(db, admin.id, "placement_changed", target=old, old=area.name, new="Sin área")
        return
    user = db.get(models.User, user_id)
    if user is None or user.role != "coordinator" or not user.is_active:
        raise HTTPException(status_code=400, detail="Elija un coordinador activo")
    other = db.query(models.ServiceArea).filter(models.ServiceArea.coordinator_id == user.id).first()
    if other is not None and other.id != area.id:
        raise HTTPException(status_code=409, detail=COORDINATOR_TAKEN.format(area=other.name))
    if area.coordinator_id != user.id:
        area.coordinator_id = user.id
        audit.record(db, admin.id, "placement_changed", target=user, new=area.name)


@router.patch("/areas/{area_id}", response_model=AreaRead)
def update_area(area_id: int, data: AreaUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    area = _area_or_404(db, area_id)
    if data.name is not None:
        name = " ".join(data.name.split())
        _check_area_name(db, name, skip_id=area.id)
        area.name = name
    if data.description is not None:
        area.description = data.description.strip() or None
    if data.audience is not None:
        area.audience = data.audience
    if data.is_active is not None:
        area.is_active = data.is_active
    if data.coordinator_id is not None:
        set_coordinator(db, admin, area, data.coordinator_id or None)
    if data.entry_department_id is not None:
        if data.entry_department_id and data.entry_department_id not in routing.area_department_ids(db, area.id):
            raise HTTPException(status_code=400, detail=ENTRY_OUTSIDE)
        area.entry_department_id = data.entry_department_id or None
    db.commit()
    return _area_read(db, area, routing.departments_by_id(db))


@router.put("/areas/{area_id}/departments", response_model=AreaRead)
def set_area_departments(area_id: int, data: AreaDepartments, db: Session = Depends(get_db),
                         admin=Depends(require_admin)):
    """Departamentos que agrupa el área. Uno que estaba en otra área se mueve a esta."""
    area = _area_or_404(db, area_id)
    by_id = routing.departments_by_id(db)
    wanted = set(data.department_ids)
    if any(d not in by_id for d in wanted):
        raise HTTPException(status_code=400, detail=NOT_FOUND_DEPARTMENT)
    db.query(models.ServiceAreaDepartment).filter(
        models.ServiceAreaDepartment.area_id == area.id, models.ServiceAreaDepartment.department_id.notin_(wanted)
    ).delete(synchronize_session=False)
    for dept_id in wanted:
        row = db.query(models.ServiceAreaDepartment).filter(models.ServiceAreaDepartment.department_id == dept_id).first()
        if row is None:
            db.add(models.ServiceAreaDepartment(area_id=area.id, department_id=dept_id))
        elif row.area_id != area.id:
            moved_from = db.get(models.ServiceArea, row.area_id)
            row.area_id = area.id
            # El área de donde salió ya no puede tenerlo de entrada
            if moved_from is not None and moved_from.entry_department_id in routing.descendants(by_id, [dept_id]):
                moved_from.entry_department_id = None
    db.flush()
    if area.entry_department_id and area.entry_department_id not in routing.area_department_ids(db, area.id, by_id):
        area.entry_department_id = None
    db.commit()
    return _area_read(db, area, by_id)


@router.put("/settings/entry-department", response_model=list[AreaRead])
def set_entry_department(data: EntryDepartmentUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Departamento de entrada ("No estoy seguro", IA sin confianza y tickets que ya existían)."""
    if data.department_id is not None:
        dept = _department_or_404(db, data.department_id)
        if routing.area_of_department(db, dept.id) is None:
            raise HTTPException(status_code=400, detail="Primero agrupe ese departamento en un área")
    routing.set_setting(db, routing.ENTRY_DEPARTMENT, str(data.department_id) if data.department_id else None, admin)
    db.flush()
    # Los tickets que ya existían quedan en el departamento de entrada
    org_setup.migrate_tickets(db)
    db.commit()
    return list_areas(db, admin)


@router.get("/settings/entry-department", response_model=EntryDepartmentUpdate)
def get_entry_department(db: Session = Depends(get_db), admin=Depends(require_admin)):
    entry = routing.entry_department(db)
    return EntryDepartmentUpdate(department_id=entry.id if entry is not None else None)


# ----- Personal -----

@router.get("/staff-alerts", response_model=StaffAlerts)
def staff_alerts(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Personal sin departamento o área (incluye los de un departamento desactivado) y áreas sin coordinador."""
    unplaced = [UnplacedPerson(id=u.id, name=u.name or "Sin nombre", role=u.role) for u in routing.unplaced_staff(db)]
    areas = (
        db.query(models.ServiceArea)
        .filter(models.ServiceArea.is_active.is_(True), models.ServiceArea.coordinator_id.is_(None))
        .order_by(models.ServiceArea.id)
    )
    return StaffAlerts(
        unplaced=unplaced,
        areas_without_coordinator=[AreaOption(id=a.id, name=a.name, description=a.description) for a in areas],
    )


def placement_read(db: Session, user: models.User) -> PlacementRead:
    by_id = routing.departments_by_id(db)
    row = routing.placement(db, user.id) if user.role == "technician" else None
    area = routing.coordinator_area(db, user)
    dept = by_id.get(row.department_id) if row is not None and row.department_id else None
    return PlacementRead(
        user_id=user.id, role=user.role,
        department_id=dept.id if dept else None, department_name=routing.path_label(dept, by_id),
        tier=row.tier if row is not None else None,
        area_id=area.id if area else None, area_name=area.name if area else None,
        placed=routing.is_placed(db, user, by_id),
    )


def _staff_or_404(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, user_id)
    if user is None or user.role not in ("technician", "coordinator"):
        raise HTTPException(status_code=404, detail="La persona no es técnico ni coordinador")
    return user


def apply_placement(db: Session, actor: models.User, user: models.User, data: PlacementUpdate) -> None:
    """Técnico: departamento (obligatorio) y nivel; coordinador: área (obligatoria). No hace commit."""
    if user.role == "technician":
        if data.department_id is None:
            raise HTTPException(status_code=422, detail=DEPARTMENT_REQUIRED)
        by_id = routing.departments_by_id(db)
        dept = by_id.get(data.department_id)
        if dept is None or not routing.is_active_path(dept, by_id):
            raise HTTPException(status_code=400, detail="Elija un departamento activo")
        row = routing.placement(db, user.id)
        old = routing.path_label(by_id.get(row.department_id), by_id) if row is not None and row.department_id else None
        if row is None:
            row = models.StaffPlacement(user_id=user.id)
            db.add(row)
        row.department_id = dept.id
        row.tier = data.tier or row.tier or 1
        row.updated_at = routing._now()
        new = f"{routing.path_label(dept, by_id)} · {routing.TIER_NAMES[row.tier]}"
        audit.record(db, actor.id, "placement_changed", target=user, old=(old or "Sin departamento")[:150], new=new[:150])
        return
    if data.area_id is None:
        raise HTTPException(status_code=422, detail=AREA_REQUIRED_STAFF)
    area = _area_or_404(db, data.area_id)
    current = routing.coordinator_area(db, user)
    if current is not None and current.id != area.id:
        current.coordinator_id = None
        db.flush()
    set_coordinator(db, actor, area, user.id)


@router.get("/users/{user_id}/placement", response_model=PlacementRead)
def get_placement(user_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return placement_read(db, _staff_or_404(db, user_id))


@router.put("/users/{user_id}/placement", response_model=PlacementRead)
def put_placement(user_id: int, data: PlacementUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    user = _staff_or_404(db, user_id)
    apply_placement(db, admin, user, data)
    db.commit()
    return placement_read(db, user)


@router.post("/internal-requesters", status_code=201)
def create_internal_requester(data: InternalRequesterCreate, db: Session = Depends(get_db),
                              admin=Depends(require_admin)):
    """Solo en el entorno de pruebas (sin Microsoft): con Microsoft se crean solos al primer ingreso.

    La cuenta queda sin contraseña utilizable: la persona la define con "Olvidé mi contraseña".
    """
    if config.MS_LOGIN_ENABLED:
        raise HTTPException(status_code=403, detail="Con Microsoft, los empleados se crean al entrar por primera vez")
    email = normalize_email(data.email)
    if not routing.is_internal_domain(email):
        raise HTTPException(status_code=400, detail="Ese correo no es de un dominio interno de ADN")
    if _find_user(db, email) is not None:
        raise HTTPException(status_code=409, detail="Ese correo ya tiene una cuenta")
    user = models.User(name=" ".join(data.name.split()), email=email, role="requester",
                       password_hash=unusable_password_hash())
    db.add(user)
    db.flush()
    routing.link_internal(db, user)
    audit.record(db, admin.id, "internal_requester_created", target=user, new=mask_email(email))
    db.commit()
    return {"id": user.id, "email_hint": mask_email(email)}


# ----- Activación -----

def _switch_read(db: Session, key: str) -> SwitchRead:
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    if key == routing.ROUTING_ENFORCED:
        checklist = routing.routing_checklist(db)
        available = True
    else:
        checklist = [{"key": "phase_2", "label": PHASE_2, "ok": False, "detail": None}]
        available = False
    by = db.get(models.User, row.updated_by) if row is not None and row.updated_by else None
    return SwitchRead(
        key=key, label=routing.SWITCH_LABELS[key], enabled=routing.switch_on(db, key), available=available,
        checklist=[ChecklistItem(**item) for item in checklist],
        updated_at=row.updated_at if row is not None and row.updated_by else None,
        updated_by_name=(by.name or "Sin nombre") if by else None,
    )


@router.get("/activation", response_model=list[SwitchRead])
def list_switches(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return [_switch_read(db, key) for key in routing.SWITCHES]


@router.put("/activation/{key}", response_model=SwitchRead)
def set_switch(key: str, data: SwitchUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Encender pide la lista de verificación completa; apagar siempre se puede. Queda en el registro."""
    if key not in routing.SWITCHES:
        raise HTTPException(status_code=404, detail="Ese interruptor no existe")
    if data.enabled:
        current = _switch_read(db, key)
        if not current.available or not all(item.ok for item in current.checklist):
            raise HTTPException(status_code=409, detail=CHECKLIST_PENDING)
    routing.set_setting(db, key, "true" if data.enabled else "false", admin)
    db.commit()
    return _switch_read(db, key)
