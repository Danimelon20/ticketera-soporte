"""Áreas para el solicitante, sugerencia de la IA, transferencias, métricas por área y aviso de datos."""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from .. import alerts, area_ai, auto_assign, models, nav, privacy, routing, ticket_access
from ..schemas import (
    AreaFilters, AreaMetric, AreaOption, AreaOptions, FilterDepartment, AreaSuggestion, AreaSuggestRequest, PrivacyAccept, PrivacyNotice,
    RouteChange, TicketRead,
)
from ..security import get_current_user
from .tickets import OPERATION_ROLES, ticket_read

router = APIRouter(prefix="/api", tags=["areas"])

NOT_COORDINATOR = "Solo el coordinador del área del ticket puede cambiarla"
SAME_AUDIENCE = "El área de destino debe ser del mismo tipo de solicitante"
DEPARTMENT_OUTSIDE = "Ese departamento no pertenece al área del ticket"
RELEASED = "El responsable no pertenece al nuevo destino: el ticket volvió a la cola"


def _owner_for(db: Session, current_user: models.User, requester_id: int | None) -> models.User:
    if requester_id is None:
        return current_user
    if current_user.role not in OPERATION_ROLES:
        raise HTTPException(status_code=403, detail="No puedes crear tickets a nombre de otra persona")
    owner = db.get(models.User, requester_id)
    if owner is None or owner.role != "requester":
        raise HTTPException(status_code=400, detail="El solicitante no existe")
    return owner


@router.get("/areas", response_model=AreaOptions)
def areas_for_me(requester_id: Optional[int] = None, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    """Áreas que puede elegir el solicitante (externas o internas según su tipo)."""
    if current_user.role in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="El administrador no crea tickets")
    owner = _owner_for(db, current_user, requester_id)
    kind = routing.requester_kind(db, owner)
    return AreaOptions(
        areas=[AreaOption(id=a.id, name=a.name, description=a.description) for a in routing.allowed_areas(db, owner)],
        unsure_allowed=kind == "external",
        required=routing.enforced(db),
    )


@router.get("/areas/filters", response_model=AreaFilters)
def area_filters(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role not in ("technician", "coordinator"):
        raise HTTPException(status_code=403, detail="No tiene permiso para ver esta información")
    areas = db.query(models.ServiceArea).filter(models.ServiceArea.is_active.is_(True)).order_by(models.ServiceArea.id).all()
    if current_user.role == "coordinator" and routing.enforced(db):
        own = routing.coordinator_area(db, current_user)
        areas = [a for a in areas if own is not None and a.id == own.id]
    by_id = routing.departments_by_id(db)
    departments = []
    for a in areas:
        for dept_id in sorted(routing.area_department_ids(db, a.id, by_id)):
            departments.append(FilterDepartment(id=dept_id, name=routing.path_label(by_id[dept_id], by_id), area_id=a.id))
    departments.sort(key=lambda d: d.name.casefold())
    return AreaFilters(areas=[AreaOption(id=a.id, name=a.name, description=a.description) for a in areas],
                       departments=departments)


@router.get("/areas/transfer-targets", response_model=AreaFilters)
def transfer_targets(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Áreas activas a las que el coordinador puede transferir (solo nombres)."""
    if current_user.role != "coordinator":
        raise HTTPException(status_code=403, detail=NOT_COORDINATOR)
    areas = db.query(models.ServiceArea).filter(models.ServiceArea.is_active.is_(True)).order_by(models.ServiceArea.id)
    return AreaFilters(areas=[AreaOption(id=a.id, name=a.name, description=a.description) for a in areas], departments=[])


@router.post("/areas/suggest", response_model=AreaSuggestion)
def suggest_area(data: AreaSuggestRequest, requester_id: Optional[int] = None, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    """La IA propone un área; el solicitante confirma o cambia. Sin IA o sin confianza: null."""
    if current_user.role in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="El administrador no crea tickets")
    owner = _owner_for(db, current_user, requester_id)
    return AreaSuggestion(area_id=area_ai.suggest(routing.allowed_areas(db, owner), data.title, data.description))


def _ticket_and_route(db: Session, ticket_id: int, current_user: models.User):
    if current_user.role != "coordinator":
        raise HTTPException(status_code=403, detail=NOT_COORDINATOR)
    ticket = db.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="El ticket no existe")
    if ticket_access.access(db, current_user, ticket) != ticket_access.FULL:
        raise HTTPException(status_code=403, detail=NOT_COORDINATOR)
    if ticket.status == "closed":
        raise HTTPException(status_code=400, detail="No se puede modificar un ticket cerrado")
    row = routing.routing_of(db, ticket.id) or routing.set_route(
        db, ticket.id, *routing.fallback_route(db, db.get(models.User, ticket.created_by)))
    if row is None:
        raise HTTPException(status_code=400, detail="No hay áreas configuradas")
    if routing.enforced(db):
        area = routing.coordinator_area(db, current_user)
        if area is None or area.id != row.area_id:
            raise HTTPException(status_code=403, detail=NOT_COORDINATOR)
    return ticket, row


def _release_if_outside(db: Session, ticket: models.Ticket, row: models.TicketRouting, actor: models.User) -> bool:
    """Con el enrutamiento: si el responsable ya no está en el destino, el ticket vuelve a la cola y la
    asignación automática busca en el nuevo departamento. No hace commit."""
    if not routing.enforced(db) or ticket.assigned_to is None:
        return False
    assignee = db.get(models.User, ticket.assigned_to)
    if assignee is not None and assignee.role == "technician":
        placement = routing.placement(db, assignee.id)
        if placement is not None and placement.department_id in routing.ticket_department_ids(db, row):
            return False
    elif assignee is not None and assignee.role == "coordinator":
        area = routing.coordinator_area(db, assignee)
        if area is not None and area.id == row.area_id:
            return False
    db.add(models.History(ticket_id=ticket.id, field="assigned_to", old_value=str(ticket.assigned_to),
                          new_value=None, changed_by=actor.id))
    ticket.assigned_to = None
    existing = db.query(models.TicketAutoAssignment).filter(models.TicketAutoAssignment.ticket_id == ticket.id).first()
    if existing is None:
        db.add(models.TicketAutoAssignment(ticket_id=ticket.id, status="waiting", created_at=routing._now()))
    else:
        existing.status = "waiting"
    return True


@router.post("/tickets/{ticket_id}/transfer-area", response_model=TicketRead)
def transfer_area(ticket_id: int, data: RouteChange, background_tasks: BackgroundTasks,
                  db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Pasa el ticket a otra área (a su departamento de entrada). Queda en el historial con el motivo."""
    ticket, row = _ticket_and_route(db, ticket_id, current_user)
    target = db.get(models.ServiceArea, data.area_id) if data.area_id else None
    if target is None or not target.is_active:
        raise HTTPException(status_code=400, detail="Elija un área activa")
    if target.id == row.area_id:
        raise HTTPException(status_code=400, detail="El ticket ya está en esa área")
    current = db.get(models.ServiceArea, row.area_id)
    if current is not None and current.audience != target.audience:
        raise HTTPException(status_code=400, detail=SAME_AUDIENCE)
    routing.record_route_change(db, ticket.id, "area", routing.area_label(current), target.name,
                                current_user, data.reason.strip())
    row.area_id, row.department_id, row.migrated = target.id, target.entry_department_id, False
    db.flush()
    released = _release_if_outside(db, ticket, row, current_user)
    db.commit()
    db.refresh(ticket)
    if released:
        background_tasks.add_task(auto_assign.assign_new, ticket.id)
    return ticket_read(ticket, current_user)


@router.post("/tickets/{ticket_id}/move-department", response_model=TicketRead)
def move_department(ticket_id: int, data: RouteChange, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Mueve el ticket entre departamentos de la misma área (por ejemplo, Soporte N1 → NOC)."""
    ticket, row = _ticket_and_route(db, ticket_id, current_user)
    by_id = routing.departments_by_id(db)
    if data.department_id is None or data.department_id not in routing.area_department_ids(db, row.area_id, by_id):
        raise HTTPException(status_code=400, detail=DEPARTMENT_OUTSIDE)
    if data.department_id == row.department_id:
        raise HTTPException(status_code=400, detail="El ticket ya está en ese departamento")
    old = routing.path_label(by_id.get(row.department_id), by_id) if row.department_id else "Sin departamento"
    routing.record_route_change(db, ticket.id, "department", old, routing.path_label(by_id[data.department_id], by_id),
                                current_user, data.reason.strip())
    row.department_id, row.migrated = data.department_id, False
    db.flush()
    released = _release_if_outside(db, ticket, row, current_user)
    db.commit()
    db.refresh(ticket)
    if released:
        background_tasks.add_task(auto_assign.assign_new, ticket.id)
    return ticket_read(ticket, current_user)


@router.get("/reports/areas", response_model=list[AreaMetric])
def area_metrics(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Abiertos, sin asignar, vencidos y resueltos por área y departamento. Solo conteos."""
    if current_user.role not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="No tiene permiso para ver esta información")
    query = db.query(models.Ticket).options(joinedload(models.Ticket.ai_analysis))
    if current_user.role == "coordinator":
        query = ticket_access.filter_visible(db, current_user, query)
    by_id = routing.departments_by_id(db)
    areas = {a.id: a.name for a in db.query(models.ServiceArea)}
    routes = {r.ticket_id: r for r in db.query(models.TicketRouting)}
    now = nav._now()
    metrics: dict[tuple, AreaMetric] = {}
    for ticket in query.all():
        route = routes.get(ticket.id)
        key = (route.area_id if route else None, route.department_id if route else None)
        if key not in metrics:
            dept = by_id.get(key[1]) if key[1] else None
            metrics[key] = AreaMetric(area_id=key[0], area_name=areas.get(key[0], "Sin área"),
                                      department_id=key[1], department_name=routing.path_label(dept, by_id))
        item = metrics[key]
        if ticket.status in nav.OPEN_STATUSES:
            item.open += 1
            item.unassigned += ticket.assigned_to is None
            item.overdue += alerts.due_state(ticket, now)[0] == alerts.OVERDUE
        else:
            item.resolved += 1
    return sorted(metrics.values(), key=lambda m: (m.area_name, m.department_name or ""))


# ----- Aviso de tratamiento de datos -----

@router.get("/privacy/notice", response_model=Optional[PrivacyNotice])
def privacy_notice(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """El aviso que falta aceptar, o null."""
    current = privacy.pending(db, current_user)
    return PrivacyNotice(**current) if current else None


@router.post("/privacy/accept", status_code=204)
def privacy_accept(data: PrivacyAccept, request: Request, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    if not data.accepted:
        raise HTTPException(status_code=400, detail="Marque la casilla para aceptar el aviso")
    ip = request.client.host if request.client else None
    if not privacy.accept(db, current_user, data.version, ip):
        raise HTTPException(status_code=409, detail="El aviso cambió: léalo de nuevo")
    db.commit()
