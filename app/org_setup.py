"""Estructura inicial de áreas y migración de los datos que ya existían. Corre en cada arranque y no duplica nada.

- Interruptores: en una base nueva arrancan encendidos; en una que ya tenía tickets o cuentas, apagados
  (todo sigue como antes hasta que el administrador revise la lista de verificación y los encienda).
- Áreas iniciales sin departamentos (se asignan después de importar el organigrama).
- Organización interna de ADN con los dominios de INTERNAL_DOMAINS.
- Tickets sin ruta: área "Internet y conectividad" en el departamento de entrada (si ya existe); conservan
  su responsable. Personal existente: sin departamento ni área (aparece en la alerta del administrador).
"""
from sqlalchemy.orm import Session

from . import config, models, routing


def _has_data(db: Session) -> bool:
    return (
        db.query(models.Ticket.id).first() is not None
        or db.query(models.User.id).filter(models.User.role != "agent").first() is not None
    )


def _ensure_switches(db: Session) -> None:
    missing = [key for key in routing.SWITCHES if routing.get_setting(db, key) is None]
    if not missing:
        return
    value = "false" if _has_data(db) else "true"
    for key in missing:
        routing.set_setting(db, key, value)


def _ensure_areas(db: Session) -> None:
    existing = {name for (name,) in db.query(models.ServiceArea.name)}
    for name, description, audience in routing.INITIAL_AREAS:
        if name not in existing:
            db.add(models.ServiceArea(name=name, description=description, audience=audience))
    db.flush()


def _ensure_internal_organization(db: Session) -> None:
    org = routing.internal_organization(db)
    if org is None:
        org = models.Organization(name=routing.INTERNAL_ORGANIZATION, kind="internal")
        db.add(org)
        db.flush()
    taken = {
        d.domain: d for d in db.query(models.OrganizationDomain)
        .filter(models.OrganizationDomain.domain.in_(config.INTERNAL_DOMAINS))
    } if config.INTERNAL_DOMAINS else {}
    for domain in config.INTERNAL_DOMAINS:
        row = taken.get(domain)
        if row is None:
            db.add(models.OrganizationDomain(organization_id=org.id, domain=domain))
        elif row.organization_id != org.id:
            # El dominio de ADN nunca queda en una organización cliente
            row.organization_id = org.id
    # Un dominio que se quitó de INTERNAL_DOMAINS deja de ser interno
    for row in list(org.domains):
        if row.domain not in config.INTERNAL_DOMAINS:
            db.delete(row)


def migrate_tickets(db: Session) -> int:
    """Tickets sin ruta y migrados que todavía esperan el departamento de entrada. Devuelve cuántos tocó."""
    area = routing.migration_area(db)
    if area is None:
        return 0
    entry = routing.entry_department(db)
    entry_id = entry.id if entry is not None else None
    routed = db.query(models.TicketRouting.ticket_id)
    count = 0
    for (ticket_id,) in db.query(models.Ticket.id).filter(models.Ticket.id.notin_(routed)):
        db.add(models.TicketRouting(ticket_id=ticket_id, area_id=area.id, department_id=entry_id, migrated=True))
        count += 1
    if entry_id is not None:
        count += (
            db.query(models.TicketRouting)
            .filter(models.TicketRouting.migrated.is_(True), models.TicketRouting.department_id.is_(None))
            .update({models.TicketRouting.department_id: entry_id}, synchronize_session=False)
        )
    return count


def ensure_structure(db: Session) -> None:
    """Hace commit."""
    _ensure_switches(db)
    _ensure_areas(db)
    _ensure_internal_organization(db)
    migrate_tickets(db)
    db.commit()
