"""Organigrama, ubicación del personal y activación (solo el administrador).

Adaptado de la rama areas-y-clientes sin áreas de atención: el administrador crea, renombra, mueve y
desactiva departamentos y marca los que atienden tickets.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from .. import accounts, audit, auto_assign, config, departments, models, routing
from ..schemas import (
    AdminOverview, DepartmentCreate, DepartmentImportConfirm, DepartmentImportPreview, DepartmentImportResult, DepartmentRead,
    DepartmentUpdate, OverviewDepartment, PlacementRead, PlacementUpdate, StaffAlerts, SwitchRead, SwitchUpdate,
    TicketDepartmentUpdate, UnclassifiedTicket, UnplacedPerson,
)
from .admin import require_admin

router = APIRouter(prefix="/api/admin", tags=["org"])

NOT_FOUND_DEPARTMENT = "El departamento no existe"
CYCLE = "Un departamento no puede quedar dentro de sí mismo"
TAKEN_NAME = "Ya existe un departamento con ese nombre en el mismo lugar"
TAKEN_ALIAS = "Ese alias ya existe"
MAX_IMPORT_BYTES = 1_000_000
HAS_STAFF = "Mueva primero a su personal ({count}): el departamento o uno de sus subdepartamentos lo tiene"
NOT_FOUND_USER = "El usuario no existe"
CHECKLIST_PENDING = "Revise la lista de verificación antes de encender"


# ----- Departamentos -----

def _department_reads(db: Session) -> list[DepartmentRead]:
    by_id = routing.departments_by_id(db)
    aliases: dict[int, list[str]] = {}
    for alias in db.query(models.DepartmentAlias).order_by(models.DepartmentAlias.alias):
        aliases.setdefault(alias.department_id, []).append(alias.alias)
    technicians: dict[int, int] = {}
    for (dept_id,) in (
        db.query(models.StaffPlacement.department_id)
        .join(models.User, models.User.id == models.StaffPlacement.user_id)
        .filter(models.User.is_active.is_(True), models.User.role == "technician")
    ):
        technicians[dept_id] = technicians.get(dept_id, 0) + 1
    # Departamento → (id, nombre) de su coordinador activo
    coordinators = {
        dept_id: (user_id, name)
        for dept_id, user_id, name in db.query(
            models.DepartmentCoordinator.department_id, models.User.id, models.User.name
        ).join(models.User, models.User.id == models.DepartmentCoordinator.user_id)
        .filter(models.User.is_active.is_(True))
    }
    rows = [
        DepartmentRead(
            id=dept.id, name=dept.name, parent_id=dept.parent_id, path=routing.path_label(dept, by_id),
            is_active=dept.is_active, active_path=routing.is_active_path(dept, by_id),
            handles_tickets=dept.handles_tickets, aliases=aliases.get(dept.id, []),
            technicians=technicians.get(dept.id, 0),
            coordinator_id=coordinators[dept.id][0] if dept.id in coordinators else None,
            coordinator_name=(coordinators[dept.id][1] or "Sin nombre") if dept.id in coordinators else None,
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


def _check_no_staff(db: Session, department_ids) -> None:
    count = routing.staff_count(db, department_ids)
    if count:
        raise HTTPException(status_code=409, detail=HAS_STAFF.format(count=count))


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
    # Con personal no se deja a nadie sin departamento por accidente: primero hay que moverlo (409)
    subtree = routing.descendants(by_id, [dept.id])
    if data.is_active is False and dept.is_active:
        _check_no_staff(db, subtree)
    if data.handles_tickets is False and dept.handles_tickets:
        _check_no_staff(db, [dept.id])
    if parent_id != dept.parent_id and parent_id is not None and not routing.is_active_path(by_id[parent_id], by_id):
        # Dentro de un padre desactivado, el subárbol también queda desactivado
        _check_no_staff(db, subtree)
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


# ----- Ubicación del personal -----

def _staff_or_404(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_USER)
    return user


def _placement_read(db: Session, user: models.User) -> PlacementRead:
    by_id = routing.departments_by_id(db)
    dept, tier = routing.department_of(db, user)
    return PlacementRead(user_id=user.id, role=user.role, department_id=dept.id if dept else None,
                         department_name=routing.path_label(dept, by_id), tier=tier,
                         placed=routing.is_placed(db, user, by_id))


@router.get("/users/{user_id}/placement", response_model=PlacementRead)
def get_placement(user_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return _placement_read(db, _staff_or_404(db, user_id))


@router.put("/users/{user_id}/placement", response_model=PlacementRead)
def update_placement(user_id: int, data: PlacementUpdate, db: Session = Depends(get_db),
                     admin=Depends(require_admin)):
    """Mover a un técnico (departamento y nivel) o a un coordinador (su departamento) entre departamentos."""
    user = _staff_or_404(db, user_id)
    accounts.check_can_manage(admin, user, "department")
    routing.set_placement(db, admin, user, data.department_id, data.tier)
    db.commit()
    return _placement_read(db, user)


def _unplaced(db: Session) -> list[UnplacedPerson]:
    by_id = routing.departments_by_id(db)
    result = []
    for user in routing.unplaced_staff(db):
        dept, _tier = routing.department_of(db, user)
        result.append(UnplacedPerson(id=user.id, name=user.name or "Sin nombre", role=user.role,
                                     department_name=routing.path_label(dept, by_id)))
    return result


@router.get("/staff-alerts", response_model=StaffAlerts)
def staff_alerts(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Personal sin departamento (o en uno desactivado o que ya no atiende tickets), con su botón Asignar."""
    return StaffAlerts(unplaced=_unplaced(db))


@router.get("/overview", response_model=AdminOverview)
def overview(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Panel: una fila por departamento activo que atiende tickets. Los tickets abiertos y vencidos se llenan
    cuando los tickets tengan departamento (Fase 2)."""
    rows = [
        OverviewDepartment(id=d.id, path=d.path, coordinator_name=d.coordinator_name, technicians=d.technicians)
        for d in _department_reads(db) if d.handles_tickets and d.active_path
    ]
    return AdminOverview(departments=rows, routing_enforced=routing.enforced(db), unplaced=_unplaced(db))


# ----- Activación: interruptor de tickets por departamento -----

def _switch_read(db: Session, key: str) -> SwitchRead:
    row = routing.setting_row(db, key)
    checklist = routing.routing_checklist(db)
    author = db.get(models.User, row.updated_by) if row is not None and row.updated_by else None
    return SwitchRead(
        key=key, label=routing.SWITCH_LABELS[key], enabled=row is not None and row.value == "true",
        checklist=checklist, ready=routing.checklist_ready(checklist),
        updated_at=row.updated_at if author else None, updated_by_name=(author.name or "Sin nombre") if author else None,
    )


@router.get("/activation", response_model=list[SwitchRead])
def activation(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return [_switch_read(db, key) for key in routing.SWITCHES]


@router.put("/activation/{key}", response_model=SwitchRead)
def set_switch(key: str, data: SwitchUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Encender exige la lista de verificación completa (409); apagar siempre se puede. Las dos quedan en el
    Registro de cambios (quién y cuándo)."""
    if key not in routing.SWITCHES:
        raise HTTPException(status_code=404, detail="Ese interruptor no existe")
    if data.enabled and not routing.checklist_ready(routing.routing_checklist(db)):
        raise HTTPException(status_code=409, detail=CHECKLIST_PENDING)
    routing.set_setting(db, key, "true" if data.enabled else "false", admin)
    db.commit()
    return _switch_read(db, key)


# ----- Tickets sin departamento: los clasifica el administrador -----

@router.get("/tickets/unclassified", response_model=list[UnclassifiedTicket])
def unclassified_tickets(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Tickets abiertos sin departamento (los anteriores a los departamentos y los que llegan del portal)."""
    rows = []
    for ticket in routing.unclassified_open_tickets(db).order_by(models.Ticket.created_at, models.Ticket.id):
        creator = db.get(models.User, ticket.created_by)
        assignee = db.get(models.User, ticket.assigned_to) if ticket.assigned_to else None
        rows.append(UnclassifiedTicket(
            id=ticket.id, title=ticket.title, status=ticket.status, created_at=ticket.created_at,
            requester_name=(creator.name or "Sin nombre") if creator else "Sin nombre",
            assigned_name=(assignee.name or "Sin nombre") if assignee else None,
        ))
    return rows


@router.put("/tickets/{ticket_id}/department", status_code=204)
def classify_ticket(ticket_id: int, data: TicketDepartmentUpdate, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Clasifica un ticket sin departamento. Para cambiar el de un ticket que ya tiene, se transfiere (con motivo)."""
    ticket = db.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    if routing.ticket_department(db, ticket.id) is not None:
        raise HTTPException(status_code=409, detail="El ticket ya tiene departamento: use Transferir")
    routing.set_ticket_department(db, ticket, data.department_id)
    by_id = routing.departments_by_id(db)
    label = routing.path_label(by_id[data.department_id], by_id)
    db.add(models.History(ticket_id=ticket.id, field="department", old_value=None,
                          new_value=f"Clasificado en {label} por {admin.name or 'el administrador'}"[:500],
                          changed_by=admin.id))
    db.commit()
    # Sin responsable: entra a la asignación automática de ese departamento
    if ticket.assigned_to is None and ticket.status in ("new", "in_progress") and config.AUTO_ASSIGN_ENABLED:
        background_tasks.add_task(auto_assign.assign_new, ticket.id)
    return None
