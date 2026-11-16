"""Departamentos y ubicación del personal: una sola fuente para el árbol, la asignación y los permisos.

Adaptado de la rama areas-y-clientes sin las áreas de atención: el ancla es el departamento que atiende
tickets (casilla "Atiende tickets", que no se hereda a los subdepartamentos).
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import audit, models

SEPARATOR = " / "


# ----- Árbol de departamentos -----

def departments_by_id(db: Session) -> dict[int, models.Department]:
    return {d.id: d for d in db.query(models.Department).all()}


def path_names(dept: models.Department, by_id: dict[int, models.Department]) -> list[str]:
    names, seen = [], set()
    node = dept
    while node is not None and node.id not in seen:
        seen.add(node.id)
        names.append(node.name)
        node = by_id.get(node.parent_id) if node.parent_id else None
    return list(reversed(names))


def path_label(dept: models.Department | None, by_id: dict[int, models.Department]) -> str | None:
    return SEPARATOR.join(path_names(dept, by_id)) if dept is not None else None


def is_active_path(dept: models.Department, by_id: dict[int, models.Department]) -> bool:
    """Activo él y todos sus padres: desactivar un departamento desactiva lo que cuelga de él."""
    seen = set()
    node = dept
    while node is not None and node.id not in seen:
        if not node.is_active:
            return False
        seen.add(node.id)
        node = by_id.get(node.parent_id) if node.parent_id else None
    return True


def descendants(by_id: dict[int, models.Department], root_ids) -> set[int]:
    """Los departamentos indicados y todo lo que cuelga de ellos."""
    children: dict[int, list[int]] = {}
    for d in by_id.values():
        if d.parent_id:
            children.setdefault(d.parent_id, []).append(d.id)
    result, stack = set(), list(root_ids)
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, ()))
    return result


def serves_tickets(dept: models.Department | None, by_id: dict[int, models.Department]) -> bool:
    """Atiende tickets y está activo (él y sus padres)."""
    return dept is not None and dept.handles_tickets and is_active_path(dept, by_id)


# ----- Ubicación del personal -----

PLACED_ROLES = ("technician", "coordinator")
NO_DEPARTMENT_TEXT = "Sin departamento"
DEPARTMENT_REQUIRED = "Elija el departamento"
NOT_SERVING = "Ese departamento no atiende tickets o está desactivado"
COORDINATOR_TAKEN = "Ese departamento ya tiene coordinador: {name}"
COORDINATOR_PENDING = "Ese departamento ya tiene una invitación de coordinador pendiente"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def technician_placement(db: Session, user_id: int) -> models.StaffPlacement | None:
    return db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == user_id).first()


def coordinator_row(db: Session, user_id: int) -> models.DepartmentCoordinator | None:
    return db.query(models.DepartmentCoordinator).filter(models.DepartmentCoordinator.user_id == user_id).first()


def coordinator_of(db: Session, department_id: int) -> models.DepartmentCoordinator | None:
    return db.query(models.DepartmentCoordinator).filter(
        models.DepartmentCoordinator.department_id == department_id).first()


def department_of(db: Session, user: models.User) -> models.Department | None:
    """Departamento del técnico o del coordinador. None si no tiene."""
    if user.role == "technician":
        row = technician_placement(db, user.id)
        # Filas de una versión anterior pueden no tener departamento
        if row is None or row.department_id is None:
            return None
        return db.get(models.Department, row.department_id)
    if user.role == "coordinator":
        row = coordinator_row(db, user.id)
        return db.get(models.Department, row.department_id) if row else None
    return None


def placement_text(dept: models.Department | None, by_id: dict) -> str:
    return path_label(dept, by_id) if dept is not None else NO_DEPARTMENT_TEXT


def _placements(db: Session) -> dict[int, int]:
    """user_id → department_id de técnicos y coordinadores."""
    result = dict(db.query(models.StaffPlacement.user_id, models.StaffPlacement.department_id))
    result.update(dict(db.query(models.DepartmentCoordinator.user_id, models.DepartmentCoordinator.department_id)))
    return result


def placement_labels(db: Session) -> dict[int, str]:
    """user_id → ruta del departamento (técnicos y coordinadores), para listas de Cuentas y Equipo."""
    by_id = departments_by_id(db)
    return {user_id: placement_text(by_id.get(dept_id), by_id) for user_id, dept_id in _placements(db).items()}


def placements(db: Session) -> dict[int, int]:
    """user_id → department_id de técnicos y coordinadores (filtro por departamento de Cuentas)."""
    return _placements(db)


def is_placed(db: Session, user: models.User, by_id: dict | None = None) -> bool:
    """Tiene un departamento activo que atiende tickets ("cerrado por defecto": si no, no recibe tickets)."""
    if user.role not in PLACED_ROLES:
        return True
    by_id = by_id if by_id is not None else departments_by_id(db)
    dept_id = _placements(db).get(user.id)
    return dept_id is not None and serves_tickets(by_id.get(dept_id), by_id)


def unplaced_staff(db: Session) -> list[models.User]:
    """Técnicos y coordinadores activos sin departamento, o en uno desactivado o que ya no atiende tickets."""
    by_id = departments_by_id(db)
    placements = _placements(db)
    staff = db.query(models.User).filter(
        models.User.role.in_(PLACED_ROLES), models.User.is_active.is_(True)
    ).order_by(models.User.name).all()
    return [u for u in staff if not (placements.get(u.id) and serves_tickets(by_id.get(placements[u.id]), by_id))]


def staff_count(db: Session, department_ids) -> int:
    """Personal activo (técnicos y coordinador) ubicado en esos departamentos."""
    ids = list(department_ids)
    if not ids:
        return 0
    technicians = db.query(models.StaffPlacement).join(models.User, models.User.id == models.StaffPlacement.user_id
        ).filter(models.StaffPlacement.department_id.in_(ids), models.User.is_active.is_(True)).count()
    coordinators = db.query(models.DepartmentCoordinator).join(
        models.User, models.User.id == models.DepartmentCoordinator.user_id
    ).filter(models.DepartmentCoordinator.department_id.in_(ids), models.User.is_active.is_(True)).count()
    return technicians + coordinators


def serving_department(db: Session, department_id: int | None) -> models.Department:
    """El departamento elegido para el personal: obligatorio, activo y que atiende tickets (422)."""
    if not department_id:
        raise HTTPException(status_code=422, detail=DEPARTMENT_REQUIRED)
    by_id = departments_by_id(db)
    dept = by_id.get(department_id)
    if not serves_tickets(dept, by_id):
        raise HTTPException(status_code=422, detail=NOT_SERVING)
    return dept


def check_coordinator_free(db: Session, department_id: int, user_id: int | None = None) -> None:
    row = coordinator_of(db, department_id)
    if row is not None and row.user_id != user_id:
        holder = db.get(models.User, row.user_id)
        raise HTTPException(status_code=409, detail=COORDINATOR_TAKEN.format(name=holder.name or "otra persona"))


def clear_placement(db: Session, user: models.User) -> None:
    """Quita la ubicación (técnico o coordinador). No hace commit."""
    db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == user.id).delete()
    db.query(models.DepartmentCoordinator).filter(models.DepartmentCoordinator.user_id == user.id).delete()
    db.flush()


def set_placement(db: Session, actor: models.User | None, user: models.User, department_id: int | None,
                  audit_change: bool = True) -> None:
    """Ubica a un técnico o a un coordinador en su departamento. 422/409 si no se puede.
    Queda en el Registro de cambios. No hace commit."""
    if user.role not in PLACED_ROLES:
        raise HTTPException(status_code=422, detail="Solo técnicos y coordinadores tienen departamento")
    dept = serving_department(db, department_id)
    if user.role == "coordinator":
        check_coordinator_free(db, dept.id, user.id)
    by_id = departments_by_id(db)
    old = placement_text(department_of(db, user), by_id)
    clear_placement(db, user)
    if user.role == "technician":
        # Los niveles son departamentos (Soporte N1, N2…): la columna tier queda sin usarse
        db.add(models.StaffPlacement(user_id=user.id, department_id=dept.id, tier=1, updated_at=_now()))
    else:
        db.add(models.DepartmentCoordinator(department_id=dept.id, user_id=user.id, updated_at=_now()))
    db.flush()
    new = placement_text(dept, by_id)
    if audit_change and old != new:
        audit.record(db, actor.id if actor is not None else None, "placement_changed", target=user,
                     old=old[:150], new=new[:150])


# ----- Invitaciones del personal -----

def validate_invitation_placement(db: Session, role: str, department_id: int | None,
                                  email: str | None = None) -> int:
    """Departamento obligatorio (que atienda tickets). El coordinador necesita un departamento
    sin coordinador ni otra invitación de coordinador pendiente (sin contar la que se reemplaza para el mismo
    correo)."""
    dept = serving_department(db, department_id)
    if role == "coordinator":
        check_coordinator_free(db, dept.id)
        pending = db.query(models.StaffInvitationPlacement).join(
            models.StaffInvitation, models.StaffInvitation.id == models.StaffInvitationPlacement.invitation_id
        ).filter(
            models.StaffInvitationPlacement.department_id == dept.id,
            models.StaffInvitation.role == "coordinator",
            models.StaffInvitation.used_by.is_(None),
            models.StaffInvitation.expires_at > _now(),
        )
        if email is not None:
            pending = pending.filter(models.StaffInvitation.email != email)
        if pending.first() is not None:
            raise HTTPException(status_code=409, detail=COORDINATOR_PENDING)
    return dept.id


def save_invitation_placement(db: Session, invitation: models.StaffInvitation, department_id: int) -> None:
    db.add(models.StaffInvitationPlacement(invitation_id=invitation.id, department_id=department_id))
    db.flush()


def invitation_placement(db: Session, invitation_id: int) -> models.StaffInvitationPlacement | None:
    return db.query(models.StaffInvitationPlacement).filter(
        models.StaffInvitationPlacement.invitation_id == invitation_id).first()


def apply_invitation_placement(db: Session, invitation: models.StaffInvitation, user: models.User) -> None:
    """Al activar la cuenta: el departamento de la invitación. Si ya no se puede (el departamento dejó de
    atender o ya tiene coordinador), la persona queda sin departamento y aparece en la alerta. No hace commit."""
    row = invitation_placement(db, invitation.id)
    if row is None:
        return
    try:
        set_placement(db, None, user, row.department_id, audit_change=False)
    except HTTPException:
        return


# ----- Interruptor de activación -----

ROUTING_ENFORCED = "routing_enforced"
SWITCHES = (ROUTING_ENFORCED,)
SWITCH_LABELS = {ROUTING_ENFORCED: "Tickets por departamento"}
NO_PLACEMENT = "Su cuenta no tiene departamento asignado. Contacte al administrador"


def get_setting(db: Session, key: str) -> str | None:
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    return row.value if row is not None else None


def setting_row(db: Session, key: str) -> models.AppSetting | None:
    return db.query(models.AppSetting).filter(models.AppSetting.key == key).first()


def _switch_text(value: str | None) -> str:
    return "Encendido" if value == "true" else "Apagado"


def set_setting(db: Session, key: str, value: str | None, actor: models.User | None = None) -> None:
    """Guarda el ajuste. Los interruptores cambiados por una persona quedan en el Registro de cambios. No hace
    commit."""
    row = setting_row(db, key)
    old = row.value if row is not None else None
    if row is None:
        row = models.AppSetting(key=key)
        db.add(row)
    row.value = value
    row.updated_by = actor.id if actor is not None else None
    row.updated_at = _now()
    if key in SWITCHES and actor is not None and old != value:
        audit.record(db, actor.id, "setting_changed", target_label=SWITCH_LABELS.get(key, key),
                     old=_switch_text(old), new=_switch_text(value))


def enforced(db: Session) -> bool:
    """Tickets por departamento encendido ("cerrado por defecto"). Apagado: todo funciona como antes."""
    return get_setting(db, ROUTING_ENFORCED) == "true"


def placement_missing(db: Session, user: models.User) -> bool:
    """Con el interruptor encendido: técnico o coordinador sin departamento que atienda tickets."""
    return user.role in PLACED_ROLES and enforced(db) and not is_placed(db, user)


def routing_checklist(db: Session) -> list[dict]:
    """Lo que tiene que estar listo antes de encender. Cada punto con el enlace a la sección que lo corrige."""
    by_id = departments_by_id(db)
    serving = [d for d in by_id.values() if serves_tickets(d, by_id)]
    unplaced = unplaced_staff(db)
    coordinated = {d for (d,) in db.query(models.DepartmentCoordinator.department_id)
                   .join(models.User, models.User.id == models.DepartmentCoordinator.user_id)
                   .filter(models.User.is_active.is_(True))}
    staffed = {d for (d,) in db.query(models.StaffPlacement.department_id)
               .join(models.User, models.User.id == models.StaffPlacement.user_id)
               .filter(models.User.is_active.is_(True), models.User.role == "technician")}
    without_coordinator = sorted(path_label(d, by_id) for d in serving if d.id not in coordinated)
    without_technicians = sorted(path_label(d, by_id) for d in serving if d.id not in staffed)

    def detail(names: list[str]) -> str | None:
        if not names:
            return None
        shown = "; ".join(names[:3])
        return f"{len(names)}: {shown}" + (" y otros" if len(names) > 3 else "")

    unclassified = unclassified_open_tickets(db).count()
    # Importación local: classifier importa este módulo
    from . import classifier
    without_profile = classifier.missing_profiles(db)
    return [
        {"key": "serving_departments", "label": "Hay al menos un departamento que atiende tickets",
         "ok": bool(serving), "detail": None if serving else "ninguno marcado", "link": "admin/departamentos"},
        {"key": "unplaced_staff", "label": "Todo el personal tiene departamento",
         # La lista con el botón Asignar está en la misma pantalla de Activación
         "ok": not unplaced, "detail": f"{len(unplaced)} sin departamento (ver la lista de abajo)" if unplaced else None,
         "link": None},
        {"key": "departments_without_coordinator", "label": "Cada departamento que atiende tickets tiene coordinador",
         "ok": bool(serving) and not without_coordinator, "detail": detail(without_coordinator),
         "link": "admin/departamentos"},
        {"key": "departments_without_technicians",
         "label": "Cada departamento que atiende tickets tiene al menos un técnico",
         "ok": bool(serving) and not without_technicians, "detail": detail(without_technicians),
         "link": "admin/departamentos"},
        # Advertencia, no bloquea: los tickets sin departamento los clasifica el administrador
        {"key": "unclassified_tickets", "label": "Tickets abiertos sin departamento", "warning": True,
         "ok": unclassified == 0, "detail": f"{unclassified} por clasificar" if unclassified else None,
         "link": "admin/sin-departamento"},
        # Advertencia, no bloquea: sin «Qué atiende» no participa en la clasificación (sus correos quedan en
        # «Por clasificar» y el administrador los elige a mano)
        {"key": "departments_without_profile", "label": "Cada departamento que atiende tickets tiene ficha",
         "warning": True, "ok": not without_profile, "detail": detail(without_profile),
         "link": "admin/departamentos"},
    ]


def checklist_ready(checklist: list[dict]) -> bool:
    """Lista lista para encender: las advertencias no bloquean."""
    return all(item["ok"] for item in checklist if not item.get("warning"))


# ----- Departamento del ticket -----

def ticket_department(db: Session, ticket_id: int) -> models.Department | None:
    row = db.query(models.TicketDepartment).filter(models.TicketDepartment.ticket_id == ticket_id).first()
    return db.get(models.Department, row.department_id) if row is not None else None


def ticket_department_ids(db: Session, ticket_ids) -> dict[int, int]:
    """ticket_id → department_id (solo los que tienen departamento)."""
    ids = list(ticket_ids)
    if not ids:
        return {}
    return dict(db.query(models.TicketDepartment.ticket_id, models.TicketDepartment.department_id)
                .filter(models.TicketDepartment.ticket_id.in_(ids)))


def set_ticket_department(db: Session, ticket: models.Ticket, department_id: int) -> None:
    """Fija el departamento del ticket (debe atender tickets). No escribe historial ni hace commit."""
    dept = serving_department(db, department_id)
    row = db.query(models.TicketDepartment).filter(models.TicketDepartment.ticket_id == ticket.id).first()
    if row is None:
        row = models.TicketDepartment(ticket_id=ticket.id, department_id=dept.id, updated_at=_now())
        db.add(row)
    else:
        row.department_id = dept.id
        row.updated_at = _now()
    db.flush()


def unclassified_open_tickets(db: Session):
    """Tickets abiertos sin departamento (los clasifica el administrador)."""
    return db.query(models.Ticket).filter(
        models.Ticket.status.in_(("new", "in_progress")),
        models.Ticket.id.notin_(db.query(models.TicketDepartment.ticket_id)),
    )


def belongs_to(db: Session, user: models.User, department_id: int) -> bool:
    """Técnico ubicado en ese departamento o su coordinador."""
    if user.role == "technician":
        row = technician_placement(db, user.id)
        return row is not None and row.department_id == department_id
    if user.role == "coordinator":
        row = coordinator_row(db, user.id)
        return row is not None and row.department_id == department_id
    return False


def department_technicians(db: Session, department_id: int) -> list[models.User]:
    """Técnicos activos ubicados en el departamento."""
    return db.query(models.User).join(models.StaffPlacement, models.StaffPlacement.user_id == models.User.id).filter(
        models.StaffPlacement.department_id == department_id, models.User.is_active.is_(True),
        models.User.role == "technician",
    ).order_by(models.User.name).all()



# ----- Siguiente nivel (Escalar) -----

ESCALATION_SELF = "El siguiente nivel no puede ser el mismo departamento"
ESCALATION_CYCLE = "Ese siguiente nivel forma un ciclo: el escalamiento volvería a este departamento"
ESCALATION_NOT_SERVING = "Solo un departamento que atiende tickets tiene siguiente nivel"


def escalations(db: Session) -> dict[int, int]:
    """department_id → next_department_id."""
    return dict(db.query(models.DepartmentEscalation.department_id, models.DepartmentEscalation.next_department_id))


def next_level(db: Session, department_id: int, by_id: dict | None = None) -> models.Department | None:
    """Siguiente nivel del departamento, solo si está activo y atiende tickets."""
    row = db.get(models.DepartmentEscalation, department_id)
    if row is None:
        return None
    by_id = by_id if by_id is not None else departments_by_id(db)
    target = by_id.get(row.next_department_id)
    return target if serves_tickets(target, by_id) else None


def set_next_level(db: Session, dept: models.Department, next_id: int | None) -> None:
    """Fija (o quita, con None) el siguiente nivel. 422 si no atiende tickets, 400 si es él mismo o hay ciclo.
    No hace commit."""
    row = db.get(models.DepartmentEscalation, dept.id)
    if next_id is None:
        if row is not None:
            db.delete(row)
            db.flush()
        return
    if not dept.handles_tickets:
        raise HTTPException(status_code=422, detail=ESCALATION_NOT_SERVING)
    if next_id == dept.id:
        raise HTTPException(status_code=400, detail=ESCALATION_SELF)
    target = serving_department(db, next_id)
    chain, seen = escalations(db), set()
    current = target.id
    while current is not None and current not in seen:
        if current == dept.id:
            raise HTTPException(status_code=400, detail=ESCALATION_CYCLE)
        seen.add(current)
        current = chain.get(current)
    if row is None:
        db.add(models.DepartmentEscalation(department_id=dept.id, next_department_id=target.id, updated_at=_now()))
    else:
        row.next_department_id, row.updated_at = target.id, _now()
    db.flush()


# ----- Asignación dentro del departamento -----

def eligible_in_department(db: Session, technicians: list[models.User], department_id: int,
                           creator_id: int | None = None) -> list[models.User]:
    """De los candidatos, los técnicos del departamento (sin nivel: elige la carga). El creador del ticket queda
    fuera, salvo que sea el único elegible."""
    placed = {user_id for (user_id,) in db.query(models.StaffPlacement.user_id)
              .filter(models.StaffPlacement.department_id == department_id)}
    inside = [u for u in technicians if u.id in placed]
    others = [u for u in inside if u.id != creator_id]
    return others or inside


# ----- Bandeja (avisos sin correo) -----

def notify(db: Session, user_id: int, kind: str, ticket_id: int, text: str) -> None:
    """Aviso en la campana de una persona. No hace commit."""
    if kind not in models.INBOX_KINDS:
        raise ValueError(f"Aviso desconocido: {kind}")
    db.add(models.InboxNotification(user_id=user_id, kind=kind, ticket_id=ticket_id, text=text[:500],
                                    created_at=_now()))
