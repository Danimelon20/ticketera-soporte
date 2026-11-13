"""Escenarios de prueba para desarrollo, sin usar la página.

Uso:
    docker compose exec app python -m scripts.demo_data cargar
    docker compose exec app python -m scripts.demo_data verificar
    docker compose exec app python -m scripts.demo_data limpiar

Todo lo que crea lleva "[DEMO]" en el nombre o el título y las cuentas usan @demo.invalid (un dominio que
no existe: mailer nunca envía a .invalid). Mientras corre, este proceso no envía correos ni usa la IA,
la agrupación ni la asignación automática (no cambia el .env). "limpiar" borra solo lo DEMO y sus adjuntos;
a las cuentas y tickets reales solo les quita la referencia a una cuenta DEMO (por ejemplo, el responsable).
"""
import argparse
import os
import re
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, select, update

from app import config, departments, email_analysis, grouping, models, routing, suspicion
from app.business_hours import add_business_hours, business_hours_between
from app.database import SessionLocal
from app.main import init_db
from app.security import hash_password

DEMO_TAG = "[DEMO]"
DEMO_DOMAIN = "demo.invalid"
PASSWORD = "Demo-Ticketera-2026"
# Cédulas de prueba (provincia 9 no se asigna a personas físicas); se salta cualquiera que ya use una cuenta real
DEMO_ID_PREFIX = "9990"
# Banderas que este proceso apaga mientras corre
QUIET_FLAGS = (
    "EMAIL_ENABLED", "MAILBOX_POLL_ENABLED", "AI_ANALYSIS_ENABLED", "CHATBOT_ENABLED", "GROUPING_ENABLED",
    "AUTO_ASSIGN_ENABLED", "ALERTS_ENABLED", "DAILY_SUMMARY_ENABLED",
)
# Tablas principales: una referencia opcional a algo DEMO se borra (queda en NULL), la fila real se conserva
CORE_TABLES = ("users", "tickets", "email_drafts")
# Columnas opcionales que sí hacen DEMO a toda la fila
OWNING_COLUMNS = {
    ("account_changes", "target_user_id"),
    ("ticket_merges", "source_ticket_id"),
    ("ticket_merges", "source_draft_id"),
    ("alerts_sent", "ticket_id"),
    ("alerts_sent", "draft_id"),
}
ALERT_KINDS = ("critical", "due_soon", "overdue")

# Cuentas y títulos de los escenarios (verificar los busca por aquí: los números cambian en cada base)
MORNING, EVENING, AWAY, COORDINATOR = "tecnica.manana", "tecnico.tarde", "tecnico.vacaciones", "coordinadora"
# Departamentos: coordinador de Redes y un técnico sin departamento (con turno siempre)
NET_COORDINATOR, UNPLACED = "coordinador.redes", "tecnico.sindepto"
# Organigrama DEMO: (padre, nombre, atiende tickets). Los nombres llevan [DEMO] para limpiarlos
# Fase 2: ticket sin departamento, ticket creado por el personal en nombre de alguien y transferido
UNCLASSIFIED = "Pregunta por el horario de la oficina"
TRANSFERRED = "No hay internet en la sala de reuniones"
TRANSFER_REASON = "Es una falla de la red del edificio, no del equipo"
DEMO_DEPARTMENTS = (
    (None, "Gerencia General", False),
    ("Gerencia General", "Soporte N1", True),
    ("Gerencia General", "Redes", True),
    (None, "Recursos Humanos", False),
)
# Fichas de la clasificación de los dos departamentos que atienden tickets (sin datos personales). Los ejemplos
# no repiten ni parafrasean los correos de scripts.eval_classifier: si se parecieran, la evaluación acertaría por
# copia (tests/test_demo_data.py lo revisa)
DEMO_PROFILES = {
    "Redes": {
        "handles": "Conectividad: internet, wifi, cableado y puntos de red, VPN y acceso remoto, telefonía IP.",
        "examples": "\n".join((
            "El puerto de red de mi escritorio no enciende la luz aunque cambié el cable",
            "En la sala de reuniones la señal inalámbrica es muy débil y se corta",
            "Necesitamos instalar un punto de red nuevo en la bodega",
            "El teléfono IP de recepción no tiene tono",
            "El escritorio remoto al servidor de archivos se desconecta cada pocos minutos",
        )),
        "not_handles": "Computadoras, impresoras, programas y cuentas de usuario: van a Soporte N1.",
    },
    "Soporte N1": {
        "handles": "Equipos y periféricos (computadoras, monitores, teclados, impresoras, escáneres), programas de "
                   "oficina y su instalación, cuentas de usuario y contraseñas.",
        "examples": "\n".join((
            "El monitor nuevo no muestra imagen cuando lo conecto",
            "El mouse inalámbrico dejó de responder aunque le puse baterías nuevas",
            "El escáner no envía los documentos escaneados a mi correo",
            "Necesito que configuren el correo de la empresa en mi celular nuevo",
            "Word no me deja guardar documentos en la carpeta compartida",
            "La computadora tarda muchísimo en encender y hace un ruido extraño",
        )),
        "not_handles": "Internet, wifi, cableado, VPN o acceso remoto: van a Redes.",
    },
}
ANA, BETO, CARLA, IMPOSTOR = "ana.cliente", "beto.cliente", "carla.cliente", "suplantador"
MAIL_TARGET = "No puedo entrar al correo de la oficina"
MAIL_SOURCE = "Outlook me pide la contraseña todo el tiempo"
NETWORK = ("Sin internet en el piso 2", "No carga ninguna página", "Se cayó la red")
# Incidente separado: el relacionado sale del incidente y sigue a cargo del mismo técnico
SEPARATED = ("Falla el lector de huellas de la entrada", "El lector de huellas no me reconoce")
DUE_SOON = "La impresora del contador no imprime"
OVERDUE = "No abre el sistema de planillas"
OVERDUE_WITH_CAUSE = "Necesito acceso a la carpeta de Finanzas"
DRAFT_ASSIGNED = "El monitor parpadea"
DRAFT_UNASSIGNED = "El teclado no responde"
# Avisos de la campana que ya corresponden a cada estado (como los dejaría due_notices.sync)
NOTICE_KINDS = {"due_soon": ("due_50", "due_80"), "overdue": ("due_50", "due_80", "overdue", "report")}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc(value: datetime) -> datetime:
    """Fecha de business_hours (zona de la oficina) como la guarda la base: UTC sin zona."""
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _email(local: str) -> str:
    return f"{local}@{DEMO_DOMAIN}"


@contextmanager
def quiet():
    """Sin correos, IA, agrupación ni asignación automática en este proceso; al salir, como estaba."""
    saved = {name: getattr(config, name) for name in (*QUIET_FLAGS, "MAIL_TRANSPORT")}
    for name in QUIET_FLAGS:
        setattr(config, name, False)
    config.MAIL_TRANSPORT = "log"
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(config, name, value)


# ----- Limpiar -----

def _demo_roots(db) -> dict[str, set[int]]:
    users = set(db.scalars(select(models.User.id).where(func.lower(models.User.email).like(f"%@{DEMO_DOMAIN}"))))
    tickets = set(db.scalars(select(models.Ticket.id).where(models.Ticket.created_by.in_(users)))) if users else set()
    drafts = set(db.scalars(select(models.EmailDraft.id).where(models.EmailDraft.requester_id.in_(users)))) if users else set()
    # Departamentos DEMO por la etiqueta del nombre; lo que cuelga de ellos (alias, ubicaciones) sigue por FK
    departments = set(db.scalars(select(models.Department.id).where(models.Department.name.like(f"{DEMO_TAG}%"))))
    return {"users": users, "tickets": tickets, "email_drafts": drafts, "departments": departments}


def _ticket_label_ids(label: str | None) -> set[int]:
    if not label or not label.startswith("Ticket #"):
        return set()
    return {int(n) for n in re.findall(r"#(\d+)", label)}


def collect(db) -> tuple[dict[str, set[int]], list[tuple]]:
    """Filas DEMO por tabla y referencias opcionales de filas reales a quitar (tabla, columna, ids)."""
    ids = _demo_roots(db)
    tables = models.Base.metadata.sorted_tables
    # Filas con el correo de una cuenta DEMO (invitaciones, bloqueos por intentos, registros pendientes)
    for table in tables:
        if table.name not in CORE_TABLES and "email" in table.c and "id" in table.c:
            found = set(db.scalars(select(table.c.id).where(func.lower(table.c.email).like(f"%@{DEMO_DOMAIN}"))))
            if found:
                ids.setdefault(table.name, set()).update(found)
    # Registro de cambios que solo nombra a lo DEMO por la etiqueta (agrupación, correo enmascarado)
    labels = db.execute(select(models.AccountChange.id, models.AccountChange.target_label)
                        .where(models.AccountChange.target_label.is_not(None))).all()
    for row_id, label in labels:
        if label.endswith(f"@{DEMO_DOMAIN}") or _ticket_label_ids(label) & ids["tickets"]:
            ids.setdefault("account_changes", set()).add(row_id)

    nullify: dict[tuple, set[int]] = {}
    changed = True
    while changed:
        changed = False
        for table in tables:
            for fk in table.foreign_keys:
                parent = fk.column.table.name
                demo_parents = ids.get(parent)
                if not demo_parents or "id" not in table.c:
                    continue
                column = fk.parent
                owning = not column.nullable or (table.name, column.name) in OWNING_COLUMNS
                if table.name in CORE_TABLES or not owning:
                    # La fila no es DEMO por esto: solo se quita la referencia
                    rows = set(db.scalars(select(table.c.id).where(column.in_(demo_parents))))
                    rows -= ids.get(table.name, set())
                    if rows - nullify.get((table.name, column.name), set()):
                        nullify.setdefault((table.name, column.name), set()).update(rows)
                        changed = True
                    continue
                rows = set(db.scalars(select(table.c.id).where(column.in_(demo_parents))))
                new = rows - ids.get(table.name, set())
                if new:
                    ids.setdefault(table.name, set()).update(new)
                    changed = True
    # Lo que se va a borrar no necesita que le quiten la referencia
    references = [
        (table, column, rows - ids.get(table, set()))
        for (table, column), rows in nullify.items()
        if rows - ids.get(table, set())
    ]
    return ids, references


def _stored_names(db, ids: dict[str, set[int]]) -> set[str]:
    names = set()
    for model, table in ((models.Attachment, "attachments"), (models.DraftAttachment, "draft_attachments")):
        if ids.get(table):
            names |= set(db.scalars(select(model.stored_name).where(model.id.in_(ids[table]))))
    return names


def clean(db) -> dict[str, int]:
    """Borra lo DEMO y sus adjuntos en disco. Devuelve cuántas filas se borraron por tabla. Hace commit."""
    ids, references = collect(db)
    if not any(ids.values()):
        return {}
    files = _stored_names(db, ids)
    # Siguiente nivel (sin columna id): las filas que salen de un departamento DEMO o llegan a uno
    demo_departments = ids.get("departments", set())
    if demo_departments:
        escalation = models.DepartmentEscalation
        db.execute(delete(escalation).where(escalation.department_id.in_(demo_departments)
                                            | escalation.next_department_id.in_(demo_departments)))
        # Ficha de la clasificación (tampoco tiene columna id)
        profile = models.DepartmentProfile
        db.execute(delete(profile).where(profile.department_id.in_(demo_departments)))
    # Clasificación de los borradores (sin columna id): la de los borradores DEMO y la que apunta a un
    # departamento DEMO (en ese caso el borrador real vuelve a «sin clasificación»)
    demo_drafts = ids.get("email_drafts", set())
    classification = models.DraftClassification
    condition = classification.draft_id.in_(demo_drafts) if demo_drafts else None
    if demo_departments:
        to_demo = (classification.department_id.in_(demo_departments)
                   | classification.ai_department_id.in_(demo_departments)
                   | classification.option1_id.in_(demo_departments)
                   | classification.option2_id.in_(demo_departments))
        condition = to_demo if condition is None else (condition | to_demo)
    if condition is not None:
        db.execute(delete(classification).where(condition))
    tables = {table.name: table for table in models.Base.metadata.sorted_tables}
    for table_name, column_name, rows in references:
        table = tables[table_name]
        db.execute(update(table).where(table.c.id.in_(rows)).values({column_name: None}))
    deleted = {}
    for table in reversed(models.Base.metadata.sorted_tables):
        rows = ids.get(table.name)
        if rows:
            db.execute(delete(table).where(table.c.id.in_(rows)))
            deleted[table.name] = len(rows)
    db.commit()
    # Solo los archivos que ya no usa ninguna fila (un adjunto de borrador y el del ticket comparten archivo)
    still_used = set(db.scalars(select(models.Attachment.stored_name))) | set(
        db.scalars(select(models.DraftAttachment.stored_name))
    )
    for name in files - still_used:
        path = os.path.join(config.ATTACHMENTS_DIR, name)
        if os.path.exists(path):
            os.remove(path)
    return deleted


# ----- Cargar -----

def _free_id_numbers(db, count: int) -> list[str]:
    taken = set(db.scalars(select(models.User.id_number).where(models.User.id_number.like(f"{DEMO_ID_PREFIX}%"))))
    result, n = [], 1
    while len(result) < count:
        candidate = f"{DEMO_ID_PREFIX}{n:05d}"
        if candidate not in taken:
            result.append(candidate)
        n += 1
    return result


def _user(db, local, name, role, phone=None, id_number=None) -> models.User:
    user = models.User(name=f"{DEMO_TAG} {name}", email=_email(local), role=role,
                       password_hash=hash_password(PASSWORD), phone=phone, id_number=id_number)
    db.add(user)
    db.flush()
    return user


def _schedule(db, user, days, start, end):
    for weekday in days:
        db.add(models.TechnicianSchedule(user_id=user.id, weekday=weekday, start_time=start, end_time=end))


class Builder:
    def __init__(self, db):
        self.db = db
        self.now = _now()
        self.agent = db.query(models.User).filter(models.User.role == "agent").first()
        self.categories = {c.name: c.id for c in db.query(models.Category)}
        self.priorities = {p.level: p.id for p in db.query(models.Priority)}

    def ticket(self, owner, title, description, *, category="Software", level=2, created_at=None,
               assigned=None, due_at=None, summary=None) -> models.Ticket:
        db = self.db
        created_at = created_at or self.now - timedelta(hours=1)
        ticket = models.Ticket(
            title=f"{DEMO_TAG} {title}", description=description,
            category_id=self.categories.get(category) or next(iter(self.categories.values())),
            priority_id=self.priorities.get(level) or next(iter(self.priorities.values())),
            status="in_progress" if assigned else "new", assigned_to=assigned.id if assigned else None,
            created_by=owner.id, contact_phone=owner.phone, contact_id_number=owner.id_number,
            source="web", created_at=created_at, updated_at=created_at,
        )
        db.add(ticket)
        db.flush()
        db.add(models.TicketFollower(ticket_id=ticket.id, user_id=owner.id))
        # Sin fecha dada: la del nivel (antes quedaban "Sin fecha límite")
        if due_at is None:
            due_at = email_analysis.compute_due_at(created_at, email_analysis.due_level(level))
        db.add(models.TicketAIAnalysis(
            ticket_id=ticket.id, status="done", ai_level=level, summary=summary or title[:300],
            due_at=due_at, model="demo", analyzed_at=created_at, created_at=created_at,
        ))
        if assigned:
            db.add(models.History(ticket_id=ticket.id, field="assigned_to", old_value=None,
                                  new_value=str(assigned.id), changed_by=self.agent.id, changed_at=created_at))
        # La instancia real que esté corriendo no manda alertas por correo de los tickets DEMO
        for kind in ALERT_KINDS:
            db.add(models.AlertSent(kind=kind, ticket_id=ticket.id, sent_at=self.now))
        return ticket

    def notices(self, ticket, state):
        """Avisos de vencimiento ya generados (no de la instalación: así se ven aunque sea la primera vez)."""
        for kind in NOTICE_KINDS[state]:
            self.db.add(models.TicketDueNotice(ticket_id=ticket.id, kind=kind, created_at=self.now, baseline=False))

    def draft(self, requester, title, body, assigned_to=None) -> models.EmailDraft:
        db = self.db
        draft = models.EmailDraft(
            message_id=f"<demo-{uuid.uuid4().hex}@{DEMO_DOMAIN}>", requester_id=requester.id,
            subject=f"{DEMO_TAG} {title}", body=body, status="pending_review", title=f"{DEMO_TAG} {title}",
            category_id=self.categories.get("Hardware"), priority_id=self.priorities.get(2), description=body,
            contact_phone=requester.phone, contact_id_number=requester.id_number, ai_level=2,
            summary=title, created_at=self.now - timedelta(minutes=30),
        )
        db.add(draft)
        db.flush()
        db.add(models.DraftAssignment(
            draft_id=draft.id, status="assigned" if assigned_to else "waiting",
            user_id=assigned_to.id if assigned_to else None,
            assigned_at=self.now if assigned_to else None, notify=True, created_at=self.now,
        ))
        for kind in ALERT_KINDS:
            db.add(models.AlertSent(kind=kind, draft_id=draft.id, sent_at=self.now))
        return draft


def load(db) -> list[dict]:
    """Crea los escenarios (antes borra los DEMO anteriores). Devuelve la tabla de cuentas. Hace commit."""
    clean(db)
    b = Builder(db)
    now, today = b.now, date.today()
    rows = []

    # Personal: tres turnos distintos (uno de vacaciones) y un coordinador
    morning = _user(db, MORNING, "Técnica Mañana", "technician")
    _schedule(db, morning, range(0, 5), "07:00", "15:00")
    evening = _user(db, EVENING, "Técnico Tarde", "technician")
    _schedule(db, evening, range(0, 5), "13:00", "21:00")
    _schedule(db, evening, [5], "08:00", "12:00")
    away = _user(db, AWAY, "Técnico Vacaciones", "technician")
    _schedule(db, away, range(0, 5), "08:00", "17:00")
    db.add(models.AvailabilityPeriod(user_id=away.id, kind="vacation", start_date=today,
                                     end_date=today + timedelta(days=6), created_by=None, created_at=now))
    coordinator = _user(db, COORDINATOR, "Coordinadora Demo", "coordinator")
    net_coordinator = _user(db, NET_COORDINATOR, "Coordinador Redes", "coordinator")
    unplaced = _user(db, UNPLACED, "Técnico Sin Departamento", "technician")
    _schedule(db, unplaced, range(0, 7), "00:00", "23:59")

    # Organigrama: dos departamentos que atienden tickets dentro de Gerencia General, y RR. HH. (no atiende)
    depts = {}
    for parent, name, handles in DEMO_DEPARTMENTS:
        row = models.Department(name=f"{DEMO_TAG} {name}", parent_id=depts[parent].id if parent else None,
                                is_active=True, handles_tickets=handles)
        db.add(row)
        db.flush()
        depts[name] = row
    db.add(models.DepartmentAlias(department_id=depts["Soporte N1"].id,
                                  alias=f"{DEMO_TAG} Gerencia General / {DEMO_TAG} Soporte Nivel 1",
                                  normalized=departments.normalize_path([f"{DEMO_TAG} Gerencia General",
                                                                         f"{DEMO_TAG} Soporte Nivel 1"])))
    db.add_all([
        models.DepartmentCoordinator(department_id=depts["Soporte N1"].id, user_id=coordinator.id),
        models.DepartmentCoordinator(department_id=depts["Redes"].id, user_id=net_coordinator.id),
        # Sin nivel del técnico: los niveles de soporte son departamentos
        models.StaffPlacement(user_id=morning.id, department_id=depts["Soporte N1"].id),
        models.StaffPlacement(user_id=evening.id, department_id=depts["Redes"].id),
        models.StaffPlacement(user_id=away.id, department_id=depts["Soporte N1"].id),
        # Escalamiento de ejemplo: lo que Soporte N1 no resuelve sube a Redes
        models.DepartmentEscalation(department_id=depts["Soporte N1"].id, next_department_id=depts["Redes"].id),
        # Fichas de la clasificación: así scripts.eval_classifier funciona después de cargar el demo
        *(models.DepartmentProfile(department_id=depts[name].id, **profile) for name, profile in DEMO_PROFILES.items()),
    ])
    db.flush()

    # Clientes con perfil completo y uno sin cédula que intenta usar la de otro
    ids = _free_id_numbers(db, 3)
    ana = _user(db, ANA, "Ana Cliente Uno", "requester", "80000001", ids[0])
    beto = _user(db, BETO, "Beto Cliente Dos", "requester", "80000002", ids[1])
    carla = _user(db, CARLA, "Carla Cliente Tres", "requester", "80000003", ids[2])
    impostor = _user(db, IMPOSTOR, "Cliente Suplantador", "requester", "80000004")

    # Agrupación A: el mismo cliente con el mismo problema en otras palabras (se fusiona)
    mail_1 = b.ticket(ana, MAIL_TARGET,
                      "Desde esta mañana Outlook no me deja iniciar sesión.", created_at=now - timedelta(hours=5))
    mail_2 = b.ticket(ana, MAIL_SOURCE,
                      "Pongo la clave y me la vuelve a pedir, no me llegan los correos.",
                      created_at=now - timedelta(hours=4))
    grouping.merge_ticket(db, mail_2, mail_1, grouping.Match(
        "merge", mail_1.id, 0.91, f"{DEMO_TAG} Mismo cliente y el mismo problema de acceso al correo"), b.agent.id)

    # Agrupación B: tres clientes con la misma falla (se ligan al más antiguo)
    net = [
        b.ticket(beto, NETWORK[0], "No hay red en ninguna computadora del piso 2.",
                 category="Red", level=3, created_at=now - timedelta(minutes=90)),
        b.ticket(carla, NETWORK[1], "El navegador dice que no hay conexión.",
                 category="Red", level=3, created_at=now - timedelta(minutes=70)),
        b.ticket(ana, NETWORK[2], "Perdimos la conexión hace un rato.",
                 category="Red", level=3, created_at=now - timedelta(minutes=50)),
    ]
    for related in net[1:]:
        grouping.link_ticket(db, related, grouping.Match(
            "link", net[0].id, 0.88, f"{DEMO_TAG} Misma falla de red en el mismo piso"), b.agent.id)

    # Incidente separado: el relacionado recibió al técnico por el incidente y, al separarlo, lo conserva
    finger = b.ticket(carla, SEPARATED[0], "Desde hoy el lector de la entrada no marca a nadie.",
                      category="Hardware", created_at=now - timedelta(minutes=40), assigned=evening)
    finger_related = b.ticket(beto, SEPARATED[1], "Pongo el dedo y no pasa nada.",
                              category="Hardware", created_at=now - timedelta(minutes=30))
    grouping.link_ticket(db, finger_related, grouping.Match(
        "link", finger.id, 0.86, f"{DEMO_TAG} Misma falla del lector de huellas"), b.agent.id)
    db.flush()
    finger_link = db.query(models.TicketLink).filter(
        models.TicketLink.principal_ticket_id == finger.id, models.TicketLink.related_ticket_id == finger_related.id,
        models.TicketLink.undone_at.is_(None),
    ).order_by(models.TicketLink.id.desc()).first()
    grouping.undo_link(db, finger_link, coordinator)

    # Vencimientos: por vencer (más del 80 % de las horas hábiles), vencido sin causa y vencido con causa
    start = now - timedelta(days=10)
    used = business_hours_between(start, now) or 8.0
    soon = b.ticket(beto, DUE_SOON, "Sale la hoja en blanco.", category="Hardware",
                    created_at=start, assigned=morning, due_at=_utc(add_business_hours(now, used * 0.1)))
    b.notices(soon, "due_soon")
    overdue = b.ticket(carla, OVERDUE, "Da un error al iniciar.", level=3,
                       created_at=start, assigned=evening, due_at=now - timedelta(days=1))
    b.notices(overdue, "overdue")
    late = b.ticket(ana, OVERDUE_WITH_CAUSE, "Me cambiaron de departamento.",
                    category="Accesos", created_at=start, assigned=away, due_at=now - timedelta(days=2))
    b.notices(late, "overdue")
    db.add(models.OverdueCause(ticket_id=late.id, cause="assignee_unavailable",
                               note=f"{DEMO_TAG} El responsable está de vacaciones.",
                               recorded_by=coordinator.id, recorded_at=now))

    # Borradores del correo: uno con revisor y uno "Sin revisor"
    demo_drafts = [b.draft(carla, DRAFT_ASSIGNED, "Buenas, el monitor parpadea cada rato y se apaga.", assigned_to=morning),
                   b.draft(beto, DRAFT_UNASSIGNED, "Algunas teclas no escriben desde ayer.")]
    # Tickets por departamento: los dos son de Soporte N1 (los revisa la coordinadora; apagado no se usa)
    for demo_draft in demo_drafts:
        db.add(models.DraftClassification(draft_id=demo_draft.id, status="routed", department_id=depts["Soporte N1"].id,
                                          created_at=now))

    # Suplantación: intenta usar la cédula de Ana (queda el reporte automático del Agente virtual)
    suspicion.id_number_conflict(db, impostor, ana.id_number, "profile", actor_id=impostor.id)

    # Tickets por departamento: los de red los atiende Redes y los demás Soporte N1 (la coordinadora sigue
    # viéndolos con el interruptor encendido); uno queda "Sin departamento" para clasificarlo
    network_ids = {t.id for t in net}
    for ticket in db.query(models.Ticket).filter(models.Ticket.created_by.in_([ana.id, beto.id, carla.id])).all():
        dept = depts["Redes"] if ticket.id in network_ids else depts["Soporte N1"]
        db.add(models.TicketDepartment(ticket_id=ticket.id, department_id=dept.id))
    b.ticket(beto, UNCLASSIFIED, "Quiero saber a qué hora abren el sábado.", created_at=now - timedelta(minutes=20))
    # Creado por la coordinadora en nombre de alguien de RR. HH. y transferido a Redes con motivo
    moved = models.Ticket(title=f"{DEMO_TAG} {TRANSFERRED}", description="La sala 3 no tiene conexión.",
                          category_id=b.categories.get("Red") or next(iter(b.categories.values())),
                          priority_id=b.priorities.get(2) or next(iter(b.priorities.values())),
                          status="new", created_by=coordinator.id, source="web", created_at=now - timedelta(minutes=15))
    db.add(moved)
    db.flush()
    # La instancia que esté corriendo no manda alertas por correo de los tickets DEMO
    for kind in ALERT_KINDS:
        db.add(models.AlertSent(kind=kind, ticket_id=moved.id, sent_at=now))
    db.add(models.TicketAIAnalysis(ticket_id=moved.id, status="done", ai_level=2, summary=TRANSFERRED, model="demo",
                                   due_at=email_analysis.compute_due_at(moved.created_at, 2),
                                   analyzed_at=moved.created_at, created_at=moved.created_at))
    db.add(models.TicketReporter(ticket_id=moved.id, name="Laura Jiménez (RR. HH.)", channel="phone",
                                 area_department_id=depts["Recursos Humanos"].id, phone="Ext. 210"))
    db.add(models.TicketDepartment(ticket_id=moved.id, department_id=depts["Redes"].id))
    db.add(models.History(ticket_id=moved.id, field="created_on_behalf", changed_by=coordinator.id,
                          new_value=f"Creado por {coordinator.name} en nombre de Laura Jiménez (RR. HH.) vía teléfono"))
    from_label = f"{DEMO_TAG} Gerencia General / {DEMO_TAG} Soporte N1"
    to_label = f"{DEMO_TAG} Gerencia General / {DEMO_TAG} Redes"
    db.add(models.DepartmentTransfer(ticket_id=moved.id, from_department_id=depts["Soporte N1"].id,
                                     to_department_id=depts["Redes"].id, reason=f"{DEMO_TAG} {TRANSFER_REASON}",
                                     transferred_by=coordinator.id))
    db.add(models.History(ticket_id=moved.id, field="department", old_value=from_label, changed_by=coordinator.id,
                          new_value=f"Transferido de {from_label} a {to_label} por {coordinator.name}: "
                                    f"{DEMO_TAG} {TRANSFER_REASON}"))
    db.add(models.InboxNotification(user_id=net_coordinator.id, kind="ticket_transferred", ticket_id=moved.id,
                                    text=f"Ticket #{moved.id} transferido de {from_label} a {to_label} "
                                         f"por {coordinator.name}: {DEMO_TAG} {TRANSFER_REASON}", created_at=now))
    db.commit()

    rows += [
        {"rol": "Técnico", "correo": morning.email,
         "probar": "Soporte N1. Turno L-V 07-15. Tiene el ticket por vencer. No ve borradores: los revisa la "
                   "coordinadora de su departamento."},
        {"rol": "Técnico", "correo": evening.email,
         "probar": "Redes. Turno L-V 13-21 y sábado 08-12. Tiene un ticket vencido sin causa y el #%d, "
                   "separado de un incidente, sigue en Mis tickets." % finger_related.id},
        {"rol": "Técnico", "correo": away.email,
         "probar": "Soporte N1. De vacaciones 7 días: no está Disponible. Tiene el vencido con causa."},
        {"rol": "Coordinador", "correo": coordinator.email,
         "probar": "Coordina Soporte N1. Agrupación (#%d en #%d), vencimientos, los 2 borradores de Soporte N1 "
                   "(al crear el ticket elige el técnico). "
                   "Escalar en el #%d: Redes ya elegido, motivo obligatorio." % (mail_2.id, mail_1.id, soon.id)},
        {"rol": "Coordinador", "correo": net_coordinator.email,
         "probar": "Coordina Redes. Con el interruptor encendido ve los tickets de red (#%d y sus ligados), el "
                   "transferido #%d (aviso en la campana) y no ve los de Soporte N1." % (net[0].id, moved.id)},
        {"rol": "Técnico", "correo": unplaced.email,
         "probar": "Sin departamento: con el interruptor encendido ve el aviso y no recibe tickets."},
        {"rol": "Cliente", "correo": ana.email,
         "probar": "Dueña de la cédula que intentan usar. Ve su ticket fusionado (#%d)." % mail_1.id},
        {"rol": "Cliente", "correo": beto.email, "probar": "Ticket de red principal y el borrador \"El teclado no responde\" "
                                                          "(Soporte N1)."},
        {"rol": "Cliente", "correo": carla.email, "probar": "Ticket de red ligado y el borrador \"El monitor parpadea\" "
                                                           "(Soporte N1)."},
        {"rol": "Cliente", "correo": impostor.email,
         "probar": "Intentó usar la cédula de Ana: el admin ve el reporte del Agente virtual."},
    ]
    for row in rows:
        row["clave"] = PASSWORD
    return rows


# ----- Verificar (por la API real, con cada cuenta DEMO; no cambia nada) -----

API_URL = "http://localhost:8000"
SKIPPED = "OMITIDO"
NO_ADMIN = "Requiere un administrador: no hay uno DEMO (se crean solo con app.create_admin)"
# Categoría que no existe: si el permiso fallara, la confirmación se detiene ahí sin cambiar nada
MISSING_CATEGORY_ID = 999_999_999


@dataclass
class Check:
    scenario: str
    role: str
    expected: str
    obtained: str = ""
    ok: bool | None = None  # None: omitido


class DemoApi:
    """Sesiones de las cuentas DEMO contra la API. Nunca muestra el token ni la contraseña."""

    def __init__(self, http):
        self.http = http
        self.tokens: dict[str, str] = {}
        self.login_errors: dict[str, str] = {}

    def login(self, local: str) -> None:
        # Un solo formulario: el rol sale de la cuenta
        response = self.http.post("/api/auth/login", json={"email": _email(local), "password": PASSWORD})
        token = response.json().get("token") if response.status_code == 200 else None
        if token:
            self.tokens[local] = token
        else:
            self.login_errors[local] = f"no pudo iniciar sesión (HTTP {response.status_code})"

    def _headers(self, local: str) -> dict:
        if local not in self.tokens:
            raise LookupError(self.login_errors.get(local, "sin sesión"))
        return {"Authorization": f"Bearer {self.tokens[local]}"}

    def get(self, local: str, path: str, **params):
        return self.http.get(path, params=params or None, headers=self._headers(local))

    def post(self, local: str, path: str, body: dict):
        return self.http.post(path, json=body, headers=self._headers(local))

    def logout_all(self) -> None:
        for local in list(self.tokens):
            self.http.post("/api/auth/logout", headers=self._headers(local))
            del self.tokens[local]


def _scenario_ids(db) -> dict | None:
    """Números reales de los escenarios DEMO (cambian en cada base). None si falta alguno."""
    users = {u.email.split("@")[0]: u.id for u in db.query(models.User).filter(
        func.lower(models.User.email).like(f"%@{DEMO_DOMAIN}"))}
    if not users:
        return None
    tickets = {t.title: t.id for t in db.query(models.Ticket).filter(models.Ticket.created_by.in_(users.values()))}
    drafts = {d.title: d.id for d in db.query(models.EmailDraft).filter(
        models.EmailDraft.requester_id.in_(users.values()))}

    def ticket(title):
        return tickets.get(f"{DEMO_TAG} {title}")

    found = {
        "users": users,
        "mail_target": ticket(MAIL_TARGET), "mail_source": ticket(MAIL_SOURCE),
        "network": [ticket(t) for t in NETWORK],
        "due_soon": ticket(DUE_SOON), "overdue": ticket(OVERDUE), "overdue_cause": ticket(OVERDUE_WITH_CAUSE),
        "separated": ticket(SEPARATED[1]),
        "unclassified": ticket(UNCLASSIFIED), "transferred": ticket(TRANSFERRED),
        "draft_assigned": drafts.get(f"{DEMO_TAG} {DRAFT_ASSIGNED}"),
        "draft_unassigned": drafts.get(f"{DEMO_TAG} {DRAFT_UNASSIGNED}"),
        "enforced": routing.enforced(db),
    }
    needed = [found[k] for k in found if k not in ("users", "network", "enforced")] + found["network"]
    needed += [users.get(k) for k in (MORNING, EVENING, COORDINATOR, NET_COORDINATOR, UNPLACED, ANA, IMPOSTOR)]
    return found if all(needed) else None


def _open_without_due(db, ids: dict) -> list[int]:
    """Tickets DEMO abiertos sin fecha límite (deberían ser ninguno)."""
    tickets = db.query(models.Ticket).filter(models.Ticket.created_by.in_(ids["users"].values()),
                                             models.Ticket.status.in_(("new", "in_progress"))).all()
    return sorted(t.id for t in tickets if t.ai_analysis is None or t.ai_analysis.due_at is None)


def _fingerprint(db) -> tuple:
    """Estado de lo DEMO que verificar no debe cambiar (no incluye sesiones ni avisos de la campana)."""
    roots = _demo_roots(db)
    users, tickets, drafts = roots["users"], roots["tickets"], roots["email_drafts"]
    return (
        sorted((u.id, u.role, u.is_active, u.name, u.id_number, u.password_hash)
               for u in db.query(models.User).filter(models.User.id.in_(users))),
        sorted((t.id, t.status, t.assigned_to, t.title)
               for t in db.query(models.Ticket).filter(models.Ticket.id.in_(tickets))),
        sorted((d.id, d.status, d.reviewed_by, d.ticket_id)
               for d in db.query(models.EmailDraft).filter(models.EmailDraft.id.in_(drafts))),
        sorted((a.draft_id, a.status, a.user_id)
               for a in db.query(models.DraftAssignment).filter(models.DraftAssignment.draft_id.in_(drafts))),
        sorted((r.id, r.status, r.reason)
               for r in db.query(models.AccountReport).filter(models.AccountReport.target_user_id.in_(users))),
        sorted((m.id, m.undone_at) for m in db.query(models.TicketMerge).filter(
            models.TicketMerge.target_ticket_id.in_(tickets))),
        sorted((link.id, link.undone_at) for link in db.query(models.TicketLink).filter(
            models.TicketLink.principal_ticket_id.in_(tickets))),
        sorted((c.ticket_id, c.cause) for c in db.query(models.OverdueCause).filter(
            models.OverdueCause.ticket_id.in_(tickets))),
        sorted((p.user_id, p.department_id) for p in db.query(models.StaffPlacement).filter(
            models.StaffPlacement.user_id.in_(users))),
        sorted((e.department_id, e.next_department_id) for e in db.query(models.DepartmentEscalation)),
        sorted((c.user_id, c.department_id) for c in db.query(models.DepartmentCoordinator).filter(
            models.DepartmentCoordinator.user_id.in_(users))),
        sorted((r.ticket_id, r.department_id) for r in db.query(models.TicketDepartment).filter(
            models.TicketDepartment.ticket_id.in_(tickets))),
        db.query(models.Ticket).filter(models.Ticket.created_by.in_(users)).count(),
    )


def _run_check(check: Check, test) -> Check:
    """test() devuelve (correcto, obtenido). Un error o una cuenta sin sesión cuenta como FALLA."""
    try:
        check.ok, check.obtained = test()
    except LookupError as exc:
        check.ok, check.obtained = False, str(exc)
    except Exception as exc:
        check.ok, check.obtained = False, f"error: {type(exc).__name__}"
    return check


def _checks(api: DemoApi, ids: dict) -> list[Check]:
    target, source = ids["mail_target"], ids["mail_source"]
    principal, related = ids["network"][0], ids["network"][1:]
    soon, overdue, late = ids["due_soon"], ids["overdue"], ids["overdue_cause"]
    assigned, unassigned = ids["draft_assigned"], ids["draft_unassigned"]
    impostor = ids["users"][IMPOSTOR]
    checks = []

    def add(scenario, role, expected, test):
        checks.append(_run_check(Check(scenario, role, expected), test))

    def merged_into(local):
        response = api.get(local, f"/api/tickets/{source}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        value = response.json().get("merged_into")
        return value == target, f"fusionado en #{value}" if value else "sin fusión"

    add("Agrupación", "Coordinador", f"#{source} fusionado en #{target}", lambda: merged_into(COORDINATOR))

    def linked():
        found = []
        for ticket_id in related:
            # Los tickets de red son de Redes
            response = api.get(NET_COORDINATOR, f"/api/tickets/{ticket_id}")
            group = response.json().get("grouping") or {} if response.status_code == 200 else {}
            found.append(((group.get("principal") or {}).get("ticket_id"), ticket_id))
        ok = all(p == principal for p, _ in found)
        return ok, ", ".join(f"#{t} → #{p}" if p else f"#{t} sin ligar" for p, t in found)

    add("Agrupación", "Coordinador Redes", ", ".join(f"#{t} → #{principal}" for t in related), linked)
    add("Agrupación", "Cliente (Ana)", f"ve que #{source} se agregó a #{target}", lambda: merged_into(ANA))

    def bell():
        response = api.get(MORNING, "/api/notifications/overdue")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        kinds = [i["kind"] for i in response.json()["items"] if i.get("ticket_id") == soon]
        return "due_80" in kinds, f"aviso {', '.join(kinds)}" if kinds else "sin aviso"

    add("Vencimientos", "Técnico (mañana)", f"aviso due_80 de #{soon} (por vencer)", bell)

    def report(ticket_id, cause):
        response = api.get(COORDINATOR, f"/api/tickets/{ticket_id}/overdue-report")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        got = (response.json().get("cause") or {}).get("cause")
        return got == cause, f"vencido, causa: {got or 'ninguna'}"

    add("Vencimientos", "Coordinador", f"#{overdue} vencido, causa: ninguna", lambda: report(overdue, None))
    add("Vencimientos", "Coordinador", f"#{late} vencido, causa: assignee_unavailable",
        lambda: report(late, "assignee_unavailable"))

    def overdue_view():
        seen, page, pages = set(), 1, 1
        while page <= pages and page <= 20:
            response = api.get(COORDINATOR, "/api/queue", view="overdue", search=DEMO_TAG, page=page)
            if response.status_code != 200:
                return False, f"HTTP {response.status_code}"
            data = response.json()
            seen |= {i["id"] for i in data["items"]}
            pages = data["pages"]
            page += 1
        found = sorted({overdue, late} & seen)
        return found == sorted({overdue, late}), "en Vencidos: " + (", ".join(f"#{t}" for t in found) or "ninguno")

    add("Vencimientos", "Coordinador", f"#{overdue} y #{late} en Vencidos", overdue_view)

    def drafts(local, draft_id):
        response = api.get(local, "/api/drafts")
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        return next((d for d in response.json() if d["id"] == draft_id), None), ""

    def reviewer_sees():
        draft, error = drafts(MORNING, assigned)
        if error:
            return False, error
        if draft is None:
            return False, "no lo ve"
        return bool(draft.get("can_review")), "lo ve, " + ("confirmable" if draft.get("can_review") else "no confirmable")

    def other_cannot_confirm(local=EVENING):
        body = {"title": "Verificación", "description": "Verificación", "category_id": MISSING_CATEGORY_ID,
                "priority_id": MISSING_CATEGORY_ID}
        response = api.post(local, f"/api/drafts/{assigned}/confirm", body)
        return response.status_code == 403, f"HTTP {response.status_code}"

    def technician_without_drafts():
        code = api.get(MORNING, "/api/drafts").status_code
        return code == 403, f"HTTP {code}"

    def coordinator_reviews_department():
        draft, error = drafts(COORDINATOR, assigned)
        if error:
            return False, error
        if draft is None:
            return False, "no lo ve"
        ok = draft.get("can_review") and draft.get("assigned_to") is None and not draft.get("can_reassign")
        return bool(ok), "lo ve, sin revisor y confirmable" if ok else "lo ve, con revisor o no confirmable"

    if ids["enforced"]:
        # Tickets por departamento: los técnicos ya no ven borradores; los revisa el coordinador del departamento
        add("Borradores", "Técnico (mañana)", "sin borradores: HTTP 403", technician_without_drafts)
        add("Borradores", "Coordinador Redes", f"confirmar #{assigned} (de Soporte N1): HTTP 403",
            lambda: other_cannot_confirm(NET_COORDINATOR))
        add("Borradores", "Coordinador", f"borrador #{assigned} de Soporte N1 visible, sin revisor",
            coordinator_reviews_department)

    def coordinator_sees_unassigned():
        draft, error = drafts(COORDINATOR, unassigned)
        if error:
            return False, error
        if draft is None:
            return False, "no lo ve"
        return draft.get("assigned_to") is None, "lo ve, " + (
            "Sin revisor" if draft.get("assigned_to") is None else "con revisor")

    if not ids["enforced"]:
        add("Borradores", "Técnico (mañana)", f"borrador #{assigned} visible y confirmable", reviewer_sees)
        add("Borradores", "Técnico (tarde)", f"confirmar #{assigned}: HTTP 403", other_cannot_confirm)
        add("Borradores", "Coordinador", f"borrador #{unassigned} visible, Sin revisor", coordinator_sees_unassigned)

    separated = ids["separated"]

    def separated_still_mine():
        response = api.get(EVENING, "/api/queue", view="mine_open", search=DEMO_TAG)
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        mine = {i["id"] for i in response.json()["items"]}
        return separated in mine, "en Mis tickets" if separated in mine else "no aparece en Mis tickets"

    add("Agrupación", "Técnico (tarde)", f"#{separated} separado del incidente, en Mis tickets", separated_still_mine)

    def foreign_ticket():
        response = api.get(EVENING, f"/api/tickets/{soon}")
        return response.status_code == 403, f"HTTP {response.status_code}"

    add("Permisos", "Técnico (tarde)", f"abrir #{soon} (de otro técnico): HTTP 403", foreign_ticket)
    checks.append(Check("Permisos", "Administrador", "la cola (/api/queue): HTTP 403", NO_ADMIN, None))

    def impostor_report():
        response = api.get(COORDINATOR, f"/api/accounts/{impostor}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        data = response.json()
        open_reports, reported = data["alerts"]["open_reports"], data["reported"]
        return open_reports >= 1 and reported is False, (
            f"{open_reports} reporte(s) abierto(s), " + ("Reportada" if reported else "sin etiqueta"))

    add("Suplantación", "Coordinador", "reporte abierto y sin la etiqueta \"Reportada\"", impostor_report)
    checks.append(Check("Suplantación", "Administrador", "el reporte lo abrió el Agente virtual", NO_ADMIN, None))

    # ----- Departamentos -----
    def me(local):
        response = api.get(local, "/api/auth/me")
        if response.status_code != 200:
            raise LookupError(f"HTTP {response.status_code}")
        return response.json()

    def coordinator_department():
        name = me(COORDINATOR).get("department_name") or ""
        return name.endswith(f"{DEMO_TAG} Soporte N1"), name or "sin departamento"

    add("Departamentos", "Coordinador", f"dirige {DEMO_TAG} Soporte N1", coordinator_department)

    def technician_department():
        # Sin nivel del técnico: los niveles de soporte son departamentos
        data = me(EVENING)
        ok = (data.get("department_name") or "").endswith(f"{DEMO_TAG} Redes") and "tier" not in data
        return ok, data.get("department_name") or "sin departamento"

    add("Departamentos", "Técnico (tarde)", f"{DEMO_TAG} Redes, sin nivel", technician_department)

    expected = "ve el aviso «Su cuenta no tiene departamento asignado»"
    if ids["enforced"]:
        def unplaced_notice():
            missing = me(UNPLACED).get("placement_missing")
            return missing is True, "ve el aviso" if missing else "no ve el aviso"

        add("Departamentos", "Técnico sin departamento", expected, unplaced_notice)
    else:
        checks.append(Check("Departamentos", "Técnico sin departamento", expected,
                            "Interruptor de tickets por departamento apagado (Administración › Activación)", None))
    checks.append(Check("Departamentos", "Administrador", "el técnico sin departamento aparece en la alerta",
                        NO_ADMIN, None))

    # ----- Tickets por departamento (solo con el interruptor encendido) -----
    transferred, unclassified = ids["transferred"], ids["unclassified"]
    switch_off = "Interruptor de tickets por departamento apagado (Administración › Activación)"

    def status_of(local, ticket_id):
        return api.get(local, f"/api/tickets/{ticket_id}").status_code

    def scoped(local, ticket_id, expected):
        code = status_of(local, ticket_id)
        return code == expected, f"HTTP {code}"

    def transfer_seen():
        response = api.get(NET_COORDINATOR, f"/api/tickets/{transferred}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        lines = [h.get("new_value") or "" for h in response.json()["history"] if h["field"] == "department"]
        ok = any(line.startswith("Transferido de ") for line in lines)
        return ok, "lo ve, con la transferencia en el historial" if ok else "lo ve, sin la transferencia"

    def escalate_offered():
        response = api.get(COORDINATOR, f"/api/tickets/{soon}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        target = response.json().get("escalate_to") or {}
        ok = (target.get("path") or "").endswith(f"{DEMO_TAG} Redes")
        return ok, f"Escalar a {target['path']}" if target else "sin Escalar"

    scope_checks = [
        ("Coordinador Redes", f"#{principal} (de red): lo ve", lambda: scoped(NET_COORDINATOR, principal, 200)),
        ("Coordinador", f"#{principal} (de red, en Redes): HTTP 403", lambda: scoped(COORDINATOR, principal, 403)),
        ("Coordinador", f"#{soon}: Escalar con {DEMO_TAG} Redes ya elegido", escalate_offered),
        ("Coordinador Redes", f"#{soon} (de Soporte N1): HTTP 403", lambda: scoped(NET_COORDINATOR, soon, 403)),
        ("Coordinador Redes", f"#{transferred} transferido desde Soporte N1, con motivo", transfer_seen),
        ("Coordinador", f"#{unclassified} (sin departamento): HTTP 403", lambda: scoped(COORDINATOR, unclassified, 403)),
    ]
    for role, expected_text, test in scope_checks:
        if ids["enforced"]:
            add("Por departamento", role, expected_text, test)
        else:
            checks.append(Check("Por departamento", role, expected_text, switch_off, None))
    return checks


def _table(headers: tuple, rows: list[tuple]) -> None:
    widths = [max(len(h), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(v).ljust(w) for v, w in zip(row, widths)))


def verify(db, http) -> int:
    """0 si todo lo comprobado está bien; 1 si algo falla o no se pudo verificar."""
    try:
        enabled = http.get("/api/auth/microsoft/config").json().get("enabled")
    except httpx.HTTPError:
        print(f"No se pudo conectar con la API en {API_URL}. ¿Está corriendo la app?")
        return 1
    if enabled:
        print("MS_LOGIN_ENABLED=true: las cuentas DEMO del personal no pueden entrar con contraseña "
              "(ni con Microsoft). Apáguelo en el .env de desarrollo para verificar. No se verificó nada.")
        return 1
    ids = _scenario_ids(db)
    if ids is None:
        print(f"Faltan escenarios {DEMO_TAG}. Ejecute primero: python -m scripts.demo_data cargar")
        return 1

    before = _fingerprint(db)
    without_due = _open_without_due(db, ids)
    api = DemoApi(http)
    try:
        for local in (MORNING, EVENING, COORDINATOR, NET_COORDINATOR, UNPLACED, ANA):
            api.login(local)
        checks = _checks(api, ids)
    finally:
        api.logout_all()
    db.expire_all()
    after = _fingerprint(db)
    checks.append(Check("Vencimientos", "—", "ningún ticket abierto sin fecha límite",
                        "todos con fecha" if not without_due else "sin fecha: " + ", ".join(f"#{t}" for t in without_due),
                        not without_due))
    checks.append(Check("Solo lectura", "—", "los datos DEMO no cambian",
                        "sin cambios" if after == before else "CAMBIARON", after == before))

    labels = {True: "OK", False: "FALLA", None: SKIPPED}
    _table(("Escenario", "Rol", "Esperado", "Obtenido", "OK/FALLA"),
           [(c.scenario, c.role, c.expected, c.obtained, labels[c.ok]) for c in checks])
    counted = [c for c in checks if c.ok is not None]
    correct = sum(1 for c in counted if c.ok)
    skipped = len(checks) - len(counted)
    print()
    print(f"{correct} de {len(counted)} correctos" + (f" ({skipped} omitidos)" if skipped else ""))
    return 0 if correct == len(counted) else 1


def _print_table(rows: list[dict]) -> None:
    headers = ("Rol", "Correo", "Contraseña", "Qué probar")
    keys = ("rol", "correo", "clave", "probar")
    widths = [max(len(h), *(len(r[k]) for r in rows)) for h, k in zip(headers, keys)]
    line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(row[k].ljust(w) for k, w in zip(keys, widths)))
    print()
    print("Los administradores no se crean aquí: use el suyo (los reportes y la agrupación se ven desde Administración).")
    if config.MS_LOGIN_ENABLED:
        print("Ojo: MS_LOGIN_ENABLED=true. El personal DEMO no puede entrar con contraseña ni con Microsoft "
              "(sus cuentas no existen en Microsoft). Para probarlas, apáguelo en el .env de desarrollo.")
    if config.AUTO_ASSIGN_ENABLED:
        print("Ojo: AUTO_ASSIGN_ENABLED=true. La página puede asignar tickets reales nuevos a los técnicos DEMO; "
              "\"limpiar\" les quita esa asignación.")


def run(command: str, http=None) -> int:
    """http: cliente de la API para verificar (por defecto, la app en API_URL)."""
    if command == "verificar":
        db = SessionLocal()
        own_http = http is None
        http = http or httpx.Client(base_url=API_URL, timeout=30)
        try:
            return verify(db, http)
        finally:
            db.close()
            if own_http:
                http.close()
    with quiet():
        # Tablas, categorías, prioridades y el Agente virtual, como al arrancar la app
        init_db()
        db = SessionLocal()
        try:
            if command == "cargar":
                rows = load(db)
                print(f"Escenarios {DEMO_TAG} cargados.\n")
                _print_table(rows)
            else:
                deleted = clean(db)
                total = sum(deleted.values())
                print(f"Se borraron {total} filas {DEMO_TAG}." if total else f"No había nada {DEMO_TAG}.")
        finally:
            db.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.demo_data", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("cargar", "verificar", "limpiar"))
    args = parser.parse_args(argv)
    return run(args.command)


if __name__ == "__main__":
    sys.exit(main())
