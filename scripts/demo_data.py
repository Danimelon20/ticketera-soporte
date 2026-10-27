"""Escenarios de prueba para desarrollo, sin usar la página.

Uso:
    docker compose exec app python -m scripts.demo_data cargar
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
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update

from app import config, grouping, models, suspicion
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
    "EMAIL_ENABLED", "AI_ANALYSIS_ENABLED", "CHATBOT_ENABLED", "GROUPING_ENABLED",
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
    saved = {name: getattr(config, name) for name in QUIET_FLAGS}
    for name in QUIET_FLAGS:
        setattr(config, name, False)
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
    return {"users": users, "tickets": tickets, "email_drafts": drafts}


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
    morning = _user(db, "tecnica.manana", "Técnica Mañana", "technician")
    _schedule(db, morning, range(0, 5), "07:00", "15:00")
    evening = _user(db, "tecnico.tarde", "Técnico Tarde", "technician")
    _schedule(db, evening, range(0, 5), "13:00", "21:00")
    _schedule(db, evening, [5], "08:00", "12:00")
    away = _user(db, "tecnico.vacaciones", "Técnico Vacaciones", "technician")
    _schedule(db, away, range(0, 5), "08:00", "17:00")
    db.add(models.AvailabilityPeriod(user_id=away.id, kind="vacation", start_date=today,
                                     end_date=today + timedelta(days=6), created_by=None, created_at=now))
    coordinator = _user(db, "coordinadora", "Coordinadora Demo", "coordinator")

    # Clientes con perfil completo y uno sin cédula que intenta usar la de otro
    ids = _free_id_numbers(db, 3)
    ana = _user(db, "ana.cliente", "Ana Cliente Uno", "requester", "80000001", ids[0])
    beto = _user(db, "beto.cliente", "Beto Cliente Dos", "requester", "80000002", ids[1])
    carla = _user(db, "carla.cliente", "Carla Cliente Tres", "requester", "80000003", ids[2])
    impostor = _user(db, "suplantador", "Cliente Suplantador", "requester", "80000004")

    # Agrupación A: el mismo cliente con el mismo problema en otras palabras (se fusiona)
    mail_1 = b.ticket(ana, "No puedo entrar al correo de la oficina",
                      "Desde esta mañana Outlook no me deja iniciar sesión.", created_at=now - timedelta(hours=5))
    mail_2 = b.ticket(ana, "Outlook me pide la contraseña todo el tiempo",
                      "Pongo la clave y me la vuelve a pedir, no me llegan los correos.",
                      created_at=now - timedelta(hours=4))
    grouping.merge_ticket(db, mail_2, mail_1, grouping.Match(
        "merge", mail_1.id, 0.91, f"{DEMO_TAG} Mismo cliente y el mismo problema de acceso al correo"), b.agent.id)

    # Agrupación B: tres clientes con la misma falla (se ligan al más antiguo)
    net = [
        b.ticket(beto, "Sin internet en el piso 2", "No hay red en ninguna computadora del piso 2.",
                 category="Red", level=3, created_at=now - timedelta(minutes=90)),
        b.ticket(carla, "No carga ninguna página", "El navegador dice que no hay conexión.",
                 category="Red", level=3, created_at=now - timedelta(minutes=70)),
        b.ticket(ana, "Se cayó la red", "Perdimos la conexión hace un rato.",
                 category="Red", level=3, created_at=now - timedelta(minutes=50)),
    ]
    for related in net[1:]:
        grouping.link_ticket(db, related, grouping.Match(
            "link", net[0].id, 0.88, f"{DEMO_TAG} Misma falla de red en el mismo piso"), b.agent.id)

    # Vencimientos: por vencer (más del 80 % de las horas hábiles), vencido sin causa y vencido con causa
    start = now - timedelta(days=10)
    used = business_hours_between(start, now) or 8.0
    b.ticket(beto, "La impresora del contador no imprime", "Sale la hoja en blanco.", category="Hardware",
             created_at=start, assigned=morning, due_at=_utc(add_business_hours(now, used * 0.1)))
    b.ticket(carla, "No abre el sistema de planillas", "Da un error al iniciar.", level=3,
             created_at=start, assigned=evening, due_at=now - timedelta(days=1))
    late = b.ticket(ana, "Necesito acceso a la carpeta de Finanzas", "Me cambiaron de departamento.",
                    category="Accesos", created_at=start, assigned=away, due_at=now - timedelta(days=2))
    db.add(models.OverdueCause(ticket_id=late.id, cause="assignee_unavailable",
                               note=f"{DEMO_TAG} El responsable está de vacaciones.",
                               recorded_by=coordinator.id, recorded_at=now))

    # Borradores del correo: uno con revisor y uno "Sin revisor"
    b.draft(carla, "El monitor parpadea", "Buenas, el monitor parpadea cada rato y se apaga.", assigned_to=morning)
    b.draft(beto, "El teclado no responde", "Algunas teclas no escriben desde ayer.")

    # Suplantación: intenta usar la cédula de Ana (queda el reporte automático del Agente virtual)
    suspicion.id_number_conflict(db, impostor, ana.id_number, "profile", actor_id=impostor.id)
    db.commit()

    rows += [
        {"rol": "Técnico", "correo": morning.email,
         "probar": "Turno L-V 07-15. Tiene el ticket por vencer y el borrador \"El monitor parpadea\"."},
        {"rol": "Técnico", "correo": evening.email,
         "probar": "Turno L-V 13-21 y sábado 08-12. Tiene un ticket vencido sin causa."},
        {"rol": "Técnico", "correo": away.email,
         "probar": "De vacaciones 7 días: no está Disponible. Tiene el vencido con causa."},
        {"rol": "Coordinador", "correo": coordinator.email,
         "probar": "Agrupación (#%d en #%d y la red en #%d), vencimientos, borrador \"Sin revisor\"."
                   % (mail_2.id, mail_1.id, net[0].id)},
        {"rol": "Cliente", "correo": ana.email,
         "probar": "Dueña de la cédula que intentan usar. Ve su ticket fusionado (#%d)." % mail_1.id},
        {"rol": "Cliente", "correo": beto.email, "probar": "Ticket de red principal y borrador sin revisor."},
        {"rol": "Cliente", "correo": carla.email, "probar": "Ticket de red ligado y borrador con revisor."},
        {"rol": "Cliente", "correo": impostor.email,
         "probar": "Intentó usar la cédula de Ana: el admin ve el reporte del Agente virtual."},
    ]
    for row in rows:
        row["clave"] = PASSWORD
    return rows


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


def run(command: str) -> int:
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
    parser.add_argument("command", choices=("cargar", "limpiar"))
    args = parser.parse_args(argv)
    return run(args.command)


if __name__ == "__main__":
    sys.exit(main())
