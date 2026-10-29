"""Áreas de atención, departamentos y ubicación del personal: una sola fuente para tickets, asignación y permisos.

- Área: la ve el solicitante (externa o interna) y tiene un coordinador.
- Departamento: estructura interna; cada técnico pertenece a uno (StaffPlacement) con su nivel N1–N3.
- "Cerrado por defecto": con routing_enforced encendido, quien no tiene departamento o área no recibe ni ve
  tickets. Apagado (base que ya tenía datos), todo sigue como antes, pero ya se puede configurar.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import audit, config, models

ROUTING_ENFORCED = "routing_enforced"
CLIENT_DOMAIN_ENFORCED = "client_domain_enforced"
ENTRY_DEPARTMENT = "entry_department_id"
SWITCHES = (ROUTING_ENFORCED, CLIENT_DOMAIN_ENFORCED)
SWITCH_LABELS = {
    ROUTING_ENFORCED: "Enrutamiento por áreas y departamentos",
    CLIENT_DOMAIN_ENFORCED: "Solo clientes con contrato (por dominio)",
}

INTERNAL_ORGANIZATION = "American Data Networks"
# Área de los tickets que ya existían antes de las áreas
MIGRATION_AREA = "Internet y conectividad"
INITIAL_AREAS = (
    ("Internet y conectividad", "Internet caído, lentitud, cortes intermitentes, VPN", "external"),
    ("Fibra óptica e instalaciones", "Instalación nueva, traslado, daño en la fibra o en el equipo", "external"),
    ("Inalámbrico y Wi-Fi", "Señal débil, enlaces inalámbricos, puntos de acceso, Wi-Fi", "external"),
    ("Ciberseguridad y desarrollo", "Firewall, correo sospechoso, accesos, sistemas a la medida", "external"),
    ("Facturación y pagos", "Facturas, cobros, comprobantes, formas de pago", "external"),
    ("Contrato y servicios", "Cambios de plan, servicios nuevos, renovación o baja", "external"),
    ("Quejas y calidad", "Inconformidad con el servicio o la atención recibida", "external"),
    ("Soporte interno (TI)", "Computadora, correo, accesos, impresoras y sistemas internos de ADN", "internal"),
)

NO_PLACEMENT = "Su cuenta no tiene departamento asignado. Contacte al administrador"
AREA_NOT_ALLOWED = "Esa área de atención no está disponible para su cuenta"
AREA_REQUIRED = "Elija un área de atención o marque «No estoy seguro»"
TIER_NAMES = {1: "N1", 2: "N2", 3: "N3"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ----- Ajustes -----

def get_setting(db: Session, key: str) -> str | None:
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    return row.value if row is not None else None


def set_setting(db: Session, key: str, value: str | None, actor: models.User | None = None) -> None:
    """Guarda el ajuste. Los interruptores quedan en el Registro de cambios (quién y cuándo). No hace commit."""
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    old = row.value if row is not None else None
    if row is None:
        row = models.AppSetting(key=key)
        db.add(row)
    row.value = value
    row.updated_by = actor.id if actor is not None else None
    row.updated_at = _now()
    if key in SWITCHES and actor is not None and old != value:
        audit.record(db, actor.id, "setting_changed", target_label=key,
                     old=_switch_text(old), new=_switch_text(value))


def _switch_text(value: str | None) -> str:
    return "Encendido" if value == "true" else "Apagado"


def switch_on(db: Session, key: str) -> bool:
    return get_setting(db, key) == "true"


def enforced(db: Session) -> bool:
    return switch_on(db, ROUTING_ENFORCED)


# ----- Correos y tipo de solicitante -----

def email_domain(email: str | None) -> str:
    return (email or "").strip().lower().rpartition("@")[2]


def is_internal_domain(email: str | None) -> bool:
    return bool(config.INTERNAL_DOMAINS) and email_domain(email) in config.INTERNAL_DOMAINS


def internal_organization(db: Session) -> models.Organization | None:
    return db.query(models.Organization).filter(models.Organization.kind == "internal").order_by(
        models.Organization.id).first()


def requester_kind(db: Session, user: models.User) -> str:
    """internal (empleado de ADN, también el personal como solicitante) o external (cliente)."""
    if user.role in ("technician", "coordinator", "admin"):
        return "internal"
    profile = db.query(models.RequesterProfile).filter(models.RequesterProfile.user_id == user.id).first()
    if profile is not None and profile.organization is not None:
        return "internal" if profile.organization.kind == "internal" else "external"
    return "internal" if is_internal_domain(user.email) else "external"


def link_internal(db: Session, user: models.User) -> None:
    """Liga al solicitante con la organización interna. No hace commit."""
    org = internal_organization(db)
    profile = db.query(models.RequesterProfile).filter(models.RequesterProfile.user_id == user.id).first()
    if profile is None:
        profile = models.RequesterProfile(user_id=user.id)
        db.add(profile)
    profile.organization_id = org.id if org is not None else None
    profile.activation = "active"


# ----- Departamentos -----

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
    return " / ".join(path_names(dept, by_id)) if dept is not None else None


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


def area_department_ids(db: Session, area_id: int, by_id: dict | None = None) -> set[int]:
    """Departamentos activos del área, con sus subdepartamentos."""
    by_id = by_id if by_id is not None else departments_by_id(db)
    roots = [r[0] for r in db.query(models.ServiceAreaDepartment.department_id)
             .filter(models.ServiceAreaDepartment.area_id == area_id)]
    return {d for d in descendants(by_id, roots) if d in by_id and is_active_path(by_id[d], by_id)}


def area_of_department(db: Session, department_id: int | None, by_id: dict | None = None) -> int | None:
    """Área que agrupa al departamento (o al primero de sus padres que esté agrupado)."""
    if department_id is None:
        return None
    by_id = by_id if by_id is not None else departments_by_id(db)
    links = dict(db.query(models.ServiceAreaDepartment.department_id, models.ServiceAreaDepartment.area_id))
    seen = set()
    node = by_id.get(department_id)
    while node is not None and node.id not in seen:
        if node.id in links:
            return links[node.id]
        seen.add(node.id)
        node = by_id.get(node.parent_id) if node.parent_id else None
    return None


def entry_department(db: Session) -> models.Department | None:
    value = get_setting(db, ENTRY_DEPARTMENT)
    return db.get(models.Department, int(value)) if value and value.isdigit() else None


# ----- Personal -----

def placement(db: Session, user_id: int) -> models.StaffPlacement | None:
    return db.query(models.StaffPlacement).filter(models.StaffPlacement.user_id == user_id).first()


def coordinator_area(db: Session, user: models.User) -> models.ServiceArea | None:
    if user.role != "coordinator":
        return None
    return db.query(models.ServiceArea).filter(
        models.ServiceArea.coordinator_id == user.id, models.ServiceArea.is_active.is_(True)
    ).first()


def is_placed(db: Session, user: models.User, by_id: dict | None = None) -> bool:
    """Técnico con departamento activo, coordinador con área activa. Los demás roles: siempre."""
    if user.role == "coordinator":
        return coordinator_area(db, user) is not None
    if user.role != "technician":
        return True
    row = placement(db, user.id)
    if row is None or row.department_id is None:
        return False
    by_id = by_id if by_id is not None else departments_by_id(db)
    dept = by_id.get(row.department_id)
    return dept is not None and is_active_path(dept, by_id)


def unplaced_staff(db: Session) -> list[models.User]:
    by_id = departments_by_id(db)
    staff = (
        db.query(models.User)
        .filter(models.User.role.in_(("technician", "coordinator")), models.User.is_active.is_(True))
        .order_by(models.User.name, models.User.id)
        .all()
    )
    return [u for u in staff if not is_placed(db, u, by_id)]


def placement_missing(db: Session, user: models.User) -> bool:
    """Para el aviso del personal: solo cuenta con el enrutamiento encendido (apagado sigue recibiendo)."""
    return user.role in ("technician", "coordinator") and enforced(db) and not is_placed(db, user)


def coordinator_department_ids(db: Session, user: models.User) -> set[int]:
    area = coordinator_area(db, user)
    return area_department_ids(db, area.id) if area is not None else set()


def technician_in_area(db: Session, technician: models.User, area_id: int) -> bool:
    row = placement(db, technician.id)
    return row is not None and row.department_id in area_department_ids(db, area_id)


def coordinator_manages(db: Session, coordinator: models.User, target: models.User) -> bool:
    """El coordinador solo gestiona técnicos de su área (con el enrutamiento encendido). Las cuentas de
    clientes no dependen del área."""
    if not enforced(db) or target.role != "technician":
        return True
    area = coordinator_area(db, coordinator)
    return area is not None and technician_in_area(db, target, area.id)


# ----- Áreas que ve cada solicitante -----

def allowed_areas(db: Session, user: models.User) -> list[models.ServiceArea]:
    audience = requester_kind(db, user)
    return (
        db.query(models.ServiceArea)
        .filter(models.ServiceArea.is_active.is_(True), models.ServiceArea.audience == audience)
        .order_by(models.ServiceArea.id)
        .all()
    )


# ----- Ruta de un ticket -----

def routing_of(db: Session, ticket_id: int) -> models.TicketRouting | None:
    return db.query(models.TicketRouting).filter(models.TicketRouting.ticket_id == ticket_id).first()


def migration_area(db: Session) -> models.ServiceArea | None:
    return db.query(models.ServiceArea).filter(models.ServiceArea.name == MIGRATION_AREA).first()


def unsure_route(db: Session) -> tuple[int | None, int | None]:
    """"No estoy seguro" o la IA sin confianza: departamento de entrada y su área (o la de migración)."""
    entry = entry_department(db)
    area_id = area_of_department(db, entry.id) if entry is not None else None
    if area_id is None:
        fallback = migration_area(db)
        area_id = fallback.id if fallback is not None else None
    return area_id, entry.id if entry is not None else None


def route_for_area(db: Session, area: models.ServiceArea) -> tuple[int, int | None]:
    return area.id, area.entry_department_id


def set_route(db: Session, ticket_id: int, area_id: int | None, department_id: int | None) -> models.TicketRouting | None:
    """Crea o cambia la ruta. Sin área posible (no hay áreas) no guarda nada. No hace commit."""
    if area_id is None:
        return None
    row = routing_of(db, ticket_id)
    if row is None:
        row = models.TicketRouting(ticket_id=ticket_id, area_id=area_id)
        db.add(row)
    row.area_id = area_id
    row.department_id = department_id
    row.migrated = False
    db.flush()
    return row


def ticket_department_ids(db: Session, row: models.TicketRouting | None, by_id: dict | None = None) -> set[int]:
    """Departamentos donde se busca técnico: el del ticket (con sus hijos) o, sin él, los del área."""
    by_id = by_id if by_id is not None else departments_by_id(db)
    if row is None:
        return set()
    if row.department_id is not None and row.department_id in by_id:
        return {d for d in descendants(by_id, [row.department_id]) if is_active_path(by_id[d], by_id)}
    return area_department_ids(db, row.area_id, by_id)


def required_tier(level: int | None) -> int:
    """Gravedad 0–2 (o sin nivel): N1; 3: N2; 4 (crítico): N3."""
    if level is None or level <= 2:
        return 1
    return 2 if level == 3 else 3


def eligible(db: Session, technicians: list[models.User], row: models.TicketRouting | None,
             level: int | None) -> list[models.User]:
    """Técnicos que puede elegir la asignación: con el enrutamiento apagado, todos (como antes).

    Encendido: solo los del departamento del ticket (o de su área) con nivel suficiente; se prefiere el
    nivel exacto y, si no hay nadie, el siguiente. Sin ruta o sin ubicación: nadie.
    """
    if not enforced(db):
        return technicians
    by_id = departments_by_id(db)
    depts = ticket_department_ids(db, row, by_id)
    if not depts:
        return []
    placements = {
        p.user_id: p for p in db.query(models.StaffPlacement)
        .filter(models.StaffPlacement.user_id.in_([u.id for u in technicians]))
    } if technicians else {}
    in_dept = [u for u in technicians if u.id in placements and placements[u.id].department_id in depts]
    need = required_tier(level)
    for tier in range(need, 4):
        chosen = [u for u in in_dept if placements[u.id].tier == tier]
        if chosen:
            return chosen
    return []


def area_label(area: models.ServiceArea | None) -> str:
    return area.name if area is not None else "Sin área"


def record_route_change(db: Session, ticket_id: int, field: str, old: str, new: str, actor: models.User,
                        reason: str) -> None:
    """Historial: "Transferido de X a Y por Z: motivo" (o "Movido de …"). No hace commit."""
    verb = "Transferido" if field == "area" else "Movido"
    db.add(models.History(
        ticket_id=ticket_id, field=field, old_value=old,
        new_value=f"{verb} de {old} a {new} por {actor.name or 'Sin nombre'}: {reason}",
        changed_by=actor.id,
    ))


# ----- Lista de verificación para encender el enrutamiento -----

def routing_checklist(db: Session) -> list[dict]:
    unplaced = unplaced_staff(db)
    without_coordinator = db.query(models.ServiceArea).filter(
        models.ServiceArea.is_active.is_(True), models.ServiceArea.coordinator_id.is_(None)).count()
    return [
        {"key": "unplaced_staff", "label": "Todo el personal tiene departamento o área",
         "ok": not unplaced, "detail": f"{len(unplaced)} sin ubicación" if unplaced else None},
        {"key": "areas_without_coordinator", "label": "Todas las áreas activas tienen coordinador",
         "ok": without_coordinator == 0,
         "detail": f"{without_coordinator} sin coordinador" if without_coordinator else None},
        {"key": "entry_department", "label": "Departamento de entrada configurado",
         "ok": entry_department(db) is not None, "detail": None},
    ]


# ----- Invitaciones del personal -----

def validate_invitation_placement(db: Session, role: str, department_id: int | None, tier: int | None,
                                  area_id: int | None) -> tuple[int | None, int | None, int | None] | str:
    """(departamento, nivel, área) válidos para la invitación, o el mensaje de error.

    Con el enrutamiento encendido es obligatorio; apagado se puede dejar para después.
    """
    if role == "technician":
        if department_id is None:
            return "Elija el departamento del técnico" if enforced(db) else (None, None, None)
        by_id = departments_by_id(db)
        dept = by_id.get(department_id)
        if dept is None or not is_active_path(dept, by_id):
            return "Elija un departamento activo"
        return department_id, tier or 1, None
    if area_id is None:
        return "Elija el área que dirige el coordinador" if enforced(db) else (None, None, None)
    area = db.get(models.ServiceArea, area_id)
    if area is None or not area.is_active:
        return "Elija un área activa"
    if area.coordinator_id is not None:
        return f"El área «{area.name}» ya tiene coordinador"
    return None, None, area_id


def save_invitation_placement(db: Session, invitation: models.StaffInvitation, department_id, tier, area_id) -> None:
    """No hace commit."""
    if department_id is None and area_id is None:
        return
    db.flush()
    db.add(models.StaffInvitationPlacement(invitation_id=invitation.id, department_id=department_id,
                                           tier=tier, area_id=area_id))


def invitation_placement(db: Session, invitation_id: int) -> models.StaffInvitationPlacement | None:
    return db.query(models.StaffInvitationPlacement).filter(
        models.StaffInvitationPlacement.invitation_id == invitation_id).first()


def apply_invitation_placement(db: Session, invitation: models.StaffInvitation, user: models.User) -> None:
    """Al activar la invitación. Si el área ya tomó otro coordinador, la persona queda sin área (alerta). No hace commit."""
    row = invitation_placement(db, invitation.id)
    if row is None:
        return
    if user.role == "technician" and row.department_id is not None:
        db.add(models.StaffPlacement(user_id=user.id, department_id=row.department_id, tier=row.tier or 1,
                                     updated_at=_now()))
    elif user.role == "coordinator" and row.area_id is not None:
        area = db.get(models.ServiceArea, row.area_id)
        if area is not None and area.coordinator_id is None:
            area.coordinator_id = user.id


def fallback_route(db: Session, owner: models.User) -> tuple[int | None, int | None]:
    """Sin área elegida o sin confianza de la IA: externos al departamento de entrada; internos, a la
    primera área interna."""
    if requester_kind(db, owner) == "internal":
        first = next(iter(allowed_areas(db, owner)), None)
        return (first.id, first.entry_department_id) if first is not None else (None, None)
    return unsure_route(db)


def route_for_owner(db: Session, owner: models.User, area_id: int | None) -> tuple[int | None, int | None]:
    """Área sugerida (si es válida para el tipo de solicitante) o la ruta de respaldo."""
    area = db.get(models.ServiceArea, area_id) if area_id else None
    if area is not None and area.is_active and area.audience == requester_kind(db, owner):
        return route_for_area(db, area)
    return fallback_route(db, owner)


def hidden_technician_ids(db: Session, viewer: models.User) -> set[int]:
    """Técnicos que el coordinador no ve (de otras áreas o sin departamento), con el enrutamiento encendido."""
    if viewer.role != "coordinator" or not enforced(db):
        return set()
    area = coordinator_area(db, viewer)
    own = area_department_ids(db, area.id) if area is not None else set()
    placed = {
        p.user_id for p in db.query(models.StaffPlacement)
        .filter(models.StaffPlacement.department_id.in_(own))
    } if own else set()
    technicians = {u for (u,) in db.query(models.User.id).filter(models.User.role == "technician")}
    return technicians - placed
