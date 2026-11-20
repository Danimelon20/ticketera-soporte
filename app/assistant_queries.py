"""Consultas del asistente: un catálogo fijo, de solo lectura, que calcula el servidor.

La IA nunca escribe consultas: elige un id de este catálogo (chatbot.select_query, con la salida restringida a
los ids que el rol puede usar) y redacta la respuesta con los números de aquí (chatbot.write_answer). verify()
comprueba que la redacción no cambió ni agregó números; si no pasa, se responde con el texto del servidor.

Alcance (scope): el mismo que la página.
- Administrador: con «Tickets por departamento» encendido ve todo; apagado, solo totales (sin #id ni títulos).
- Coordinador: con el interruptor, su departamento; sin él, todo (como ticket_access).
- Técnico: solo lo asignado a él.

Privacidad: a la IA (facts) solo van números, departamentos, nombres del personal y #id. Los títulos y los datos
del contacto nunca van: las listas con títulos las agrega el servidor debajo de la respuesta (listing).
"""
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import (alerts, auto_assign, availability, chatbot, classification_report, draft_classification, models, nav,
               people_activity, routing)
from .daily_summary import _due_at, _local
from .overdue_report import duration_text
from .notifications import STATUS_LABELS

LIST_MAX = 15
NO_DEPARTMENT = "Sin departamento"
NO_MATCH_TEXT = "No sé responder eso todavía. Puedo responder, por ejemplo:"
TOTALS_ONLY_NOTE = ("Con «Tickets por departamento» apagado, el administrador solo ve totales: "
                    "sin números ni títulos de tickets.")


@dataclass
class Scope:
    kind: str  # "all", "department" u "own"
    totals_only: bool = False


@dataclass
class Result:
    query: str
    label: str
    facts: list[str]
    text: str
    required: list[str]
    listing: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def scope(db: Session, user: models.User) -> Scope:
    if user.role == "technician":
        return Scope("own")
    enforced = routing.enforced(db)
    if user.role == "admin":
        return Scope("all", totals_only=not enforced)
    return Scope("department" if enforced else "all")


def _open_tickets(db: Session, user: models.User) -> list[models.Ticket]:
    """Los mismos tickets abiertos que cuenta el menú (nav.counts) para esa persona."""
    if user.role == "technician":
        return nav._open_tickets(db, assigned_to=user.id)
    return nav._open_tickets(db, viewer=user if routing.enforced(db) else None)


# ---------- Consultas ----------

def _open_tickets_query(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    tickets = _open_tickets(db, user)
    total = len(tickets)
    due_soon, overdue = nav._due_counts(tickets, now)
    new = sum(1 for t in tickets if t.status == "new")
    in_progress = sum(1 for t in tickets if t.status == "in_progress")
    unassigned = sum(1 for t in tickets if t.assigned_to is None)
    own = sc.kind == "own"
    whose = "Tus tickets abiertos" if own else "Tickets abiertos"
    facts = [
        f"{whose} (Nuevo y En proceso, incluidos los vencidos): {total}",
        f"Nuevo: {new}",
        f"En proceso: {in_progress}",
        f"Vencidos: {overdue}",
        f"Por vencer: {due_soon}",
    ]
    if not own:
        facts.append(f"Sin asignar: {unassigned}")
    if sc.kind == "department":
        facts.append("Solo de tu departamento.")
    text = (f"{'Tienes' if own else 'Hay'} {total} ticket(s) abierto(s): {new} nuevo(s) y {in_progress} en proceso. "
            f"{overdue} vencido(s) y {due_soon} por vencer.")
    if not own:
        text += f" Sin asignar: {unassigned}."
    listing = []
    if own and tickets:
        listing = ["Tus tickets abiertos:"] + [
            f"#{t.id} · {chatbot._redact(t.title)} · {STATUS_LABELS.get(t.status, t.status)}"
            f"{' · vencido' if alerts.due_state(t, now)[0] == alerts.OVERDUE else ''}"
            for t in sorted(tickets, key=lambda t: t.created_at)[:LIST_MAX]
        ]
    return Result("open_tickets", "Tickets abiertos", facts, text, [str(total)], listing)


def _open_by_department(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    tickets = _open_tickets(db, user)
    by_ticket = routing.ticket_department_ids(db, [t.id for t in tickets])
    by_id = routing.departments_by_id(db)
    counts: dict[str, int] = {}
    for t in tickets:
        dept_id = by_ticket.get(t.id)
        label = routing.path_label(by_id.get(dept_id), by_id) if dept_id is not None else None
        counts[label or NO_DEPARTMENT] = counts.get(label or NO_DEPARTMENT, 0) + 1
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    total = len(tickets)
    facts = [f"Tickets abiertos en total: {total}", "Por departamento:"]
    facts += [f"- {name}: {count}" for name, count in rows] or ["- Ninguno."]
    if sc.kind == "department":
        facts.append("Solo ves tu departamento.")
    text = f"Hay {total} ticket(s) abierto(s)" + (": " + "; ".join(f"{n}: {c}" for n, c in rows) if rows else "") + "."
    return Result("open_by_department", "Tickets abiertos por departamento", facts, text, [str(total)])


DUE_LIST_MAX = 10


def _department_labels(db: Session, tickets: list[models.Ticket]) -> dict[int, str]:
    by_ticket = routing.ticket_department_ids(db, [t.id for t in tickets])
    by_id = routing.departments_by_id(db)
    return {t.id: (routing.path_label(by_id.get(by_ticket.get(t.id)), by_id) if by_ticket.get(t.id) is not None
                   else None) or NO_DEPARTMENT for t in tickets}


def _due_query(state: str, db: Session, user: models.User, sc: Scope, now: datetime) -> Result:
    """Vencidos (state=OVERDUE) o por vencer (DUE_SOON), con el mismo cálculo que el menú y las alertas.
    Facts: total, por departamento y hasta 10 con #id, departamento, técnico y tiempo; nunca títulos.
    El administrador sin el interruptor recibe solo los totales por departamento."""
    overdue = state == alerts.OVERDUE
    found = []
    for t in _open_tickets(db, user):
        ticket_state, percent = alerts.due_state(t, now)
        if ticket_state == state:
            found.append((t, percent))
    if overdue:
        # El más vencido primero
        found.sort(key=lambda item: (_due_at(item[0]), item[0].id))
    else:
        found.sort(key=lambda item: (-(item[1] or 0), _due_at(item[0]), item[0].id))
    total = len(found)
    labels = _department_labels(db, [t for t, _ in found])
    by_dept: dict[str, int] = {}
    for t, _ in found:
        by_dept[labels[t.id]] = by_dept.get(labels[t.id], 0) + 1
    own = sc.kind == "own"
    name = "vencidos" if overdue else "por vencer"
    facts = [f"{'Tus tickets' if own else 'Tickets'} {name}: {total}"]
    if not own and by_dept:
        facts.append("Por departamento:")
        facts += [f"- {d}: {c}" for d, c in sorted(by_dept.items(), key=lambda kv: (-kv[1], kv[0]))]
    shown = [] if sc.totals_only else found[:DUE_LIST_MAX]
    if shown:
        facts.append(f"Los {len(shown)} más urgentes:")
    for t, percent in shown:
        if overdue:
            when = f"vencido hace {duration_text(int((now - _due_at(t)).total_seconds()))}"
        else:
            when = f"{percent} % del tiempo consumido"
        who = "" if own else f" · técnico: {t.assigned_user.name if t.assigned_user else 'sin asignar'}"
        facts.append(f"- #{t.id} · {labels[t.id]}{who} · {when}")
    if sc.kind == "department":
        facts.append("Solo de tu departamento.")
    text = (f"{'Tienes' if own else 'Hay'} {total} ticket(s) {name}."
            + (f" Por departamento: {'; '.join(f'{d}: {c}' for d, c in by_dept.items())}." if not own and by_dept else ""))
    listing = []
    if shown:
        listing = [f"Tickets {name}:"] + [
            f"#{t.id} · {chatbot._redact(t.title)} · {labels[t.id]}"
            f"{'' if own else ' · ' + (t.assigned_user.name if t.assigned_user else 'sin asignar')}"
            for t, _ in shown]
        if total > len(shown):
            listing.append(f"…y {total - len(shown)} más en la vista {'Vencidos' if overdue else 'Por vencer'}.")
    query_id = "overdue" if overdue else "due_soon"
    label = "Tickets vencidos" if overdue else "Tickets por vencer"
    return Result(query_id, label, facts, text, [str(total)], listing)


def _overdue(db, user, sc, now, period):
    return _due_query(alerts.OVERDUE, db, user, sc, now)


def _due_soon(db, user, sc, now, period):
    return _due_query(alerts.DUE_SOON, db, user, sc, now)


# ---------- Carga y ausencias: los mismos datos que Equipo (/api/team) ----------

ROLE_NAMES = {"technician": "técnico", "coordinator": "coordinador"}
ROUTING_OFF_NOTE = "«Tickets por departamento» está apagado: los correos no pasan por «Por clasificar»."


def _staff(db: Session, user: models.User, sc: Scope, roles: tuple) -> list[models.User]:
    """Personal activo que la persona puede ver: el técnico, solo él; el coordinador con el interruptor, su
    departamento; los demás, todos."""
    if sc.kind == "own":
        return [user]
    staff = (db.query(models.User)
             .filter(models.User.role.in_(roles), models.User.is_active.is_(True))
             .order_by(models.User.name).all())
    if sc.kind == "department":
        dept = routing.department_of(db, user)
        placed = routing.placements(db)
        staff = [u for u in staff if dept is not None and placed.get(u.id) == dept.id]
    return staff


def _name(user: models.User) -> str:
    return user.name or "Sin nombre"


def _technician_load(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    """Por técnico: tickets abiertos asignados y estado. El coordinador recibe además la carga de la asignación
    automática (auto_assign.loads), igual que en Equipo › Carga; el administrador no la ve en la página, así que
    aquí tampoco."""
    techs = _staff(db, user, sc, ("technician",))
    ids = [u.id for u in techs]
    open_counts = dict(
        db.query(models.Ticket.assigned_to, func.count(models.Ticket.id))
        .filter(models.Ticket.assigned_to.in_(ids), models.Ticket.status.in_(auto_assign.OPEN_STATUSES))
        .group_by(models.Ticket.assigned_to).all()
    ) if ids else {}
    statuses = availability.statuses_for(db, ids, now)
    state = {uid: availability.describe(*statuses[uid]) for uid in ids}
    label = "Carga por técnico"
    if sc.kind == "own":
        count = open_counts.get(user.id, 0)
        facts = [f"Tus tickets abiertos asignados: {count}", f"Tu estado: {state[user.id]}"]
        text = f"Tienes {count} ticket(s) abierto(s) asignado(s). Tu estado: {state[user.id]}."
        return Result("technician_load", "Tu carga", facts, text, [str(count)])
    with_load = user.role == "coordinator"
    loads = auto_assign.loads(db, ids) if with_load else {}
    metric = loads if with_load else {uid: open_counts.get(uid, 0) for uid in ids}
    rows = sorted(techs, key=lambda u: (-metric.get(u.id, 0), _name(u)))
    if not rows:
        where = " en tu departamento" if sc.kind == "department" else ""
        text = f"No hay técnicos activos{where}."
        return Result("technician_load", label, [text], text, ["0"])
    top = rows[0]
    facts = [f"Técnicos activos: {len(rows)}",
             f"Más carga: {_name(top)} ({metric[top.id]})",
             "Por técnico, de más a menos carga:"]
    if with_load:
        facts.insert(1, "Carga: la misma de la asignación automática (tickets abiertos y borradores por revisar).")
    for u in rows[:LIST_MAX]:
        load = f", carga {loads[u.id]}" if with_load else ""
        facts.append(f"- {_name(u)}: {open_counts.get(u.id, 0)} abierto(s){load} · {state[u.id]}")
    if len(rows) > LIST_MAX:
        facts.append(f"…y {len(rows) - LIST_MAX} técnico(s) más.")
    if sc.kind == "department":
        facts.append("Solo técnicos de tu departamento.")
    unit = "de carga" if with_load else "ticket(s) abierto(s)"
    text = (f"Más carga: {_name(top)}, con {metric[top.id]} {unit}. "
            + "; ".join(f"{_name(u)}: {open_counts.get(u.id, 0)} abierto(s) ({state[u.id]})" for u in rows[:LIST_MAX])
            + ".")
    return Result("technician_load", label, facts, text, [str(metric[top.id])])


def _staff_away(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    """Técnicos y coordinadores de vacaciones o con ausencia hoy (y cuándo regresan), fuera de su horario y
    disponibles ahora. Mismo cálculo que Equipo (availability.statuses_for)."""
    staff = _staff(db, user, sc, availability.ROLES)
    statuses = availability.statuses_for(db, [u.id for u in staff], now)
    off = sorted((u for u in staff if statuses[u.id][0] in ("vacation", "incapacity")),
                 key=lambda u: (statuses[u.id][1], _name(u)))
    away = [u for u in staff if statuses[u.id][0] == "away"]
    present = [u for u in staff if statuses[u.id][0] == "available"]

    def names(users):
        shown = ", ".join(_name(u) for u in users[:LIST_MAX])
        return shown + (f" y {len(users) - LIST_MAX} más" if len(users) > LIST_MAX else "")

    facts = [f"De vacaciones o con ausencia hoy: {len(off)}"]
    facts += [f"- {_name(u)} ({ROLE_NAMES[u.role]}) · {availability.describe(*statuses[u.id])}" for u in off[:LIST_MAX]]
    facts.append(f"Fuera de su horario ahora: {len(away)}" + (f" ({names(away)})" if away else ""))
    facts.append(f"Disponibles ahora: {len(present)}" + (f" ({names(present)})" if present else ""))
    if sc.kind == "department":
        facts.append("Solo personal de tu departamento.")
    text = f"De vacaciones o con ausencia hoy: {len(off)}"
    text += (": " + "; ".join(f"{_name(u)}, {availability.describe(*statuses[u.id])}" for u in off[:LIST_MAX])
             if off else "") + "."
    text += f" Fuera de su horario ahora: {len(away)}. Disponibles ahora: {len(present)}."
    return Result("staff_away", "Disponibilidad del personal", facts, text, [str(len(off))])


# ---------- Correos y precisión (solo el administrador): los mismos datos que su página ----------

def _drafts_unclassified(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    """Borradores en «Por clasificar» (el mismo conteo del menú, nav.drafts_unclassified) y el más antiguo; y los
    tickets abiertos sin departamento. Nunca el asunto, el remitente ni el cuerpo."""
    unclassified_tickets = routing.unclassified_open_tickets(db).count()
    label = "Correos por clasificar"
    if not routing.enforced(db):
        facts = [ROUTING_OFF_NOTE, f"Tickets abiertos sin departamento: {unclassified_tickets}"]
        text = f"{ROUTING_OFF_NOTE} Tickets abiertos sin departamento: {unclassified_tickets}."
        return Result("drafts_unclassified", label, facts, text, [])
    pending = draft_classification.pending_query(db)
    total = pending.count()
    facts = [f"Correos en «Por clasificar»: {total}"]
    text = f"Hay {total} correo(s) en «Por clasificar»."
    if total:
        oldest, _row = pending.first()
        age = duration_text(int((now - oldest.created_at).total_seconds()))
        facts.append(f"El más antiguo espera desde hace {age}")
        text += f" El más antiguo espera desde hace {age}."
    facts.append(f"Tickets abiertos sin departamento: {unclassified_tickets}")
    text += f" Tickets abiertos sin departamento: {unclassified_tickets}."
    return Result("drafts_unclassified", label, facts, text, [str(total)])


PRECISION_DAYS = 7


def _pct_text(value: float | None) -> str:
    return f"{value:g}".replace(".", ",") + " %" if value is not None else "sin confirmados todavía"


def _ai_precision_week(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    """Precisión de la IA de los últimos 7 días de la oficina (hoy incluido): el mismo reporte que
    /api/classification/precision con ese rango. Sin asuntos ni correcciones: solo números."""
    today = availability._local_day(now)
    date_from = today - timedelta(days=PRECISION_DAYS - 1)
    start, end = people_activity.date_range(date_from, today)
    report = classification_report.build(db, start, end)
    totals, pending = report["totals"], report["pending"]
    accuracy = totals["accuracy"]
    facts = [
        f"Periodo: del {date_from:%d/%m/%Y} al {today:%d/%m/%Y}",
        f"Correos clasificados por la IA: {report['received']}",
        f"Enviados directo al coordinador: {totals['direct']}",
        f"Confirmados como ticket: {totals['confirmed']}",
        f"Aciertos de la IA (confirmados en el departamento que eligió): {totals['hits']}",
        f"Precisión: {_pct_text(accuracy)}",
        f"Pendientes de confirmar: {totals['pending']}",
        f"Transferidos por un coordinador (rebotes): {totals['bounced']}",
        f"Fueron a «Por clasificar»: {pending['arrived']} (ya decididos: {pending['decided']})",
    ]
    if report["few_cases"]:
        facts.append(f"Pocos casos (menos de {report['min_cases']}): los números todavía no alcanzan para ajustar.")
    if not report["routing_enforced"]:
        facts.append("«Tickets por departamento» está apagado: la IA no clasifica correos por departamento.")
    text = (f"Precisión de la IA del {date_from:%d/%m/%Y} al {today:%d/%m/%Y}: {_pct_text(accuracy)} "
            f"({totals['hits']} acierto(s) de {totals['confirmed']} confirmado(s)). "
            f"Correos clasificados: {report['received']}.")
    if report["few_cases"]:
        text += f" Son pocos casos (menos de {report['min_cases']})."
    required = [_pct_text(accuracy).split(" ")[0]] if accuracy is not None else [str(totals["confirmed"])]
    return Result("ai_precision_week", "Precisión de la IA (7 días)", facts, text, required)


# ---------- Resueltos por periodo: el mismo cálculo que «Resueltos hoy» del menú ----------

PERIOD_LABELS = {
    "today": "hoy", "yesterday": "ayer", "this_week": "esta semana", "last_week": "la semana pasada",
    "this_month": "este mes", "last_month": "el mes pasado", "last_7_days": "los últimos 7 días",
    "last_30_days": "los últimos 30 días",
}
DEFAULT_PERIOD = "last_7_days"
# El periodo lo detecta el servidor por palabras (sin tildes, en minúscula), en este orden: «la semana pasada»
# antes que «semana». El modelo pequeño no elige periodo; sin palabras de periodo, los últimos 7 días
_PERIOD_WORDS = (
    ("yesterday", r"\bayer\b"),
    ("today", r"\bhoy\b"),
    ("last_week", r"semana (?:pasada|anterior)"),
    ("last_month", r"mes (?:pasado|anterior)"),
    ("last_30_days", r"(?:30|treinta) dias|ultimo mes"),
    ("last_7_days", r"(?:7|siete) dias|ultima semana"),
    ("this_week", r"\bsemana\b"),
    ("this_month", r"\bmes\b"),
)


def period_of(message: str) -> str:
    plain = _plain(message)
    return next((period for period, words in _PERIOD_WORDS if re.search(words, plain)), DEFAULT_PERIOD)


def period_range(period: str, now: datetime) -> tuple[datetime, datetime | None, date, date]:
    """(inicio, fin o None = hasta ahora, primer día, último día) en días de la oficina."""
    today = availability._local_day(now)
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    if period == "yesterday":
        days = (today - timedelta(days=1), today)
    elif period == "last_week":
        days = (monday - timedelta(days=7), monday)
    elif period == "this_week":
        days = (monday, None)
    elif period == "last_month":
        days = ((first - timedelta(days=1)).replace(day=1), first)
    elif period == "this_month":
        days = (first, None)
    elif period == "last_30_days":
        days = (today - timedelta(days=29), None)
    elif period == "last_7_days":
        days = (today - timedelta(days=6), None)
    else:
        days = (today, None)
    start = people_activity.local_day_start(days[0])
    end = people_activity.local_day_start(days[1]) if days[1] else None
    return start, end, days[0], (days[1] - timedelta(days=1)) if days[1] else today


def _resolved_period(db: Session, user: models.User, sc: Scope, now: datetime, period) -> Result:
    """Tickets que pasaron a Resuelto en el periodo y siguen resueltos o cerrados, con el alcance de la página.
    Por departamento y por técnico (nombres del personal, nunca títulos)."""
    period = period if period in PERIOD_LABELS else DEFAULT_PERIOD
    start, end, first, last = period_range(period, now)
    own = sc.kind == "own"
    if own:
        ids = nav.resolved_ids_between(db, start, end, assigned_to=user.id)
    else:
        ids = nav.resolved_ids_between(db, start, end, viewer=user if routing.enforced(db) else None)
    tickets = db.query(models.Ticket).filter(models.Ticket.id.in_(ids)).all() if ids else []
    total = len(tickets)
    when = f"{PERIOD_LABELS[period]} (del {first:%d/%m/%Y} al {last:%d/%m/%Y})"
    facts = [f"Periodo: {when}", f"{'Tus tickets' if own else 'Tickets'} resueltos: {total}"]
    text = f"{'Resolviste' if own else 'Se resolvieron'} {total} ticket(s) {when}."
    if not own and tickets:
        labels = _department_labels(db, tickets)
        by_dept: dict[str, int] = {}
        by_tech: dict[str, int] = {}
        for t in tickets:
            by_dept[labels[t.id]] = by_dept.get(labels[t.id], 0) + 1
            who = _name(t.assigned_user) if t.assigned_user else "sin asignar"
            by_tech[who] = by_tech.get(who, 0) + 1
        for title, counts in (("Por departamento:", by_dept), ("Por técnico:", by_tech)):
            facts.append(title)
            facts += [f"- {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:LIST_MAX]]
        top_dept = max(by_dept.items(), key=lambda kv: (kv[1], kv[0]))
        text += f" El departamento con más: {top_dept[0]} ({top_dept[1]})."
    if sc.kind == "department":
        facts.append("Solo de tu departamento.")
    return Result("resolved_period", f"Resueltos {PERIOD_LABELS[period]}", facts, text, [str(total)])


# Sinónimos y señales de «fuera del catálogo». Cada elemento es una expresión regular que se busca en la pregunta
# ya normalizada (_plain: minúsculas, sin tildes ni ¿¡): se escriben sin tildes y con n en vez de ñ.
# Sin asignar, en palabras informales: se suman al cue de open_tickets
UNASSIGNED_SYNONYMS: tuple[str, ...] = (
    r"sin asignar",
    r"sin (?:dueno|tomar|responsable)",
    r"nadie (?:\w+ ){0,2}(?:tomad|agarrad|atend)",   # nadie ha tomado, nadie quiere atenderlos
)
# Vencidos, en palabras informales: se suman al cue de overdue
OVERDUE_SYNONYMS: tuple[str, ...] = (
    r"fuera de (?:plazo|fecha)",
    r"\bpas(?:o|aron|ado|ados|ada|adas)\b (?:\w+ ){0,2}(?:fecha|plazo)",   # se (me) paso de fecha, pasaron su fecha
    r"caducad",
    r"\bno (?:\w+ ){1,2}a tiempo",   # no se atendieron a tiempo
)
# Pide algo que no son los abiertos de ahora (resueltos, quién crea o reporta, prioridad): las consultas de abiertos
# (about_open) no se ofrecen; la IA respondía con ellas, datos de otra cosa. Los resueltos van a resolved_period. Participios y pretéritos, nunca el infinitivo: «por resolver» es abierto.
# Con \b para no atrapar «general» ni «creo que». La negación («no resueltos») la descarta _NEGATION_RE.
OUTSIDE_SIGNALS: tuple[str, ...] = (
    # Resueltos, cerrados, atendidos
    r"resuelt", r"resolvi", r"solucionad", r"finalizad",
    r"cerrad", r"\bcerr(?:o|aron|amos)\b",
    r"(?<!des)atendid", r"\batend(?:io|ieron)\b",   # «desatendidos» son los que faltan
    # Quién genera, crea o reporta
    r"\bgenera(?:n|ron)?\b", r"\bcrea(?:n|ron|dos?|das?)?\b", r"\breporta(?:n|ron|dos?|das?)?\b",
    # Urgencia o prioridad
    r"urgen", r"priorid|priorit", r"critic",
)

def _cue(*parts: str) -> str:
    return "|".join(parts)


@dataclass
class Query:
    id: str
    roles: tuple
    description: str
    examples: tuple
    run: object
    # Palabras (sin tildes, en minúscula) que tiene que traer la pregunta para que se ofrezca esta consulta
    cue: str = ""
    # La consulta general (abiertos) no se ofrece si la pregunta trae palabras de una específica
    generic: bool = False
    # El cue no cuenta si va negado («no resueltos» pide abiertos, no resueltos)
    negatable: bool = False
    # Datos de tickets abiertos ahora: no se ofrece si la pregunta pide algo fuera del catálogo (OUTSIDE_SIGNALS)
    about_open: bool = False
    uses_period: bool = False
    # Para el técnico (solo lo suyo), si cambia
    own_description: str | None = None
    own_examples: tuple = ()


CATALOG = (
    Query("open_tickets", ("admin", "coordinator", "technician"),
          "Cifras de tickets abiertos o pendientes: total, nuevos, en proceso, sin asignar (que nadie ha tomado). "
          "No cubre: resueltos, quién los crea, prioridad.",
          ("¿Cuántos tickets hay abiertos?", "Resume el estado del equipo", "¿Cuántos están sin asignar?"),
          _open_tickets_query,
          cue=_cue(r"ticket|caso|solicitud|equipo|pendiente|abiert|asignad|tengo|estado", *UNASSIGNED_SYNONYMS),
          generic=True,
          about_open=True,
          own_description="Cifras y lista de TUS tickets abiertos (solo los asignados a ti). "
                          "No cubre: resueltos, prioridad.",
          own_examples=("¿Cuántos tickets tengo?", "Resume mis tickets abiertos", "¿Qué tickets tengo asignados?")),
    Query("open_by_department", ("admin", "coordinator"),
          "Tickets abiertos de cada departamento o área: cuántos tiene cada uno; también el resumen o el estado "
          "de los tickets por departamento. No cubre: resueltos, qué departamento crea más, prioridad.",
          ("Tickets abiertos por departamento", "¿Qué departamento tiene más tickets?", "Reparto por área",
           "Resumen de los tickets por departamento", "Estado de cada área"),
          _open_by_department,
          cue=r"departament|depto|area|reparto",
          about_open=True),
    Query("overdue", ("admin", "coordinator", "technician"),
          "Tickets vencidos, atrasados o fuera de plazo (la fecha límite ya pasó): cuántos y cuáles. "
          "No cubre: por vencer, prioridad.",
          ("¿Qué tickets están vencidos?", "¿Hay tickets atrasados?", "¿Cuántos vencidos hay?"),
          _overdue,
          cue=_cue(r"vencid|atrasad|retrasad|fuera de tiempo", *OVERDUE_SYNONYMS),
          own_description="TUS tickets vencidos o atrasados (la fecha límite ya pasó). No cubre: por vencer.",
          own_examples=("¿Tengo tickets vencidos?", "¿Cuáles de mis tickets están atrasados?")),
    Query("due_soon", ("admin", "coordinator", "technician"),
          "Tickets por vencer (cerca de su fecha límite, todavía no vencidos): cuántos y cuáles. "
          "No cubre: ya vencidos, prioridad.",
          ("¿Cuáles están por vencer?", "¿Qué tickets vencen pronto?", "¿Cuántos por vencer hay?"),
          _due_soon,
          cue=r"por vencer|vence|a punto de",
          own_description="TUS tickets por vencer (cerca de su fecha límite). No cubre: ya vencidos.",
          own_examples=("¿Qué tickets míos vencen pronto?", "¿Tengo tickets por vencer?")),
    Query("technician_load", ("admin", "coordinator", "technician"),
          "Carga de cada técnico ahora: tickets abiertos asignados a cada uno y su estado (disponible, "
          "vacaciones…); quién tiene más o menos. No cubre: resueltos, prioridad.",
          ("¿Quién tiene más carga?", "Carga de trabajo por técnico", "¿Cuántos tickets tiene cada técnico?"),
          _technician_load,
          cue=r"carga|ocupad|ataread|saturad|tecnicos|(?:por|cada|que|cual) tecnico|quien tiene (?:mas|menos)",
          about_open=True,
          own_description="TU carga: tus tickets abiertos asignados y tu estado. No cubre: la carga de otros.",
          own_examples=("¿Cuánta carga tengo?", "¿Estoy muy cargado?")),
    Query("staff_away", ("admin", "coordinator"),
          "Quién del personal está de vacaciones o ausente hoy y cuándo regresa; también quién está fuera de "
          "horario o disponible ahora. No cubre: tickets ni carga.",
          ("¿Quién está de vacaciones?", "¿Quién no está hoy?", "¿Quién está disponible?"),
          _staff_away,
          cue=r"vacacion|ausen|incapacid|permiso|disponib|no (?:esta|estan) hoy|no vin(?:o|ieron)"
              r"|fuera de (?:su )?horario|de turno|regres|vuelve"),
    Query("drafts_unclassified", ("admin",),
          "Correos en «Por clasificar» (los que la IA no envió a un coordinador): cuántos y hace cuánto espera "
          "el más antiguo; también los tickets abiertos sin departamento. No cubre: precisión de la IA.",
          ("¿Cuántos correos hay por clasificar?", "¿Hay correos sin clasificar?", "¿Hay borradores esperando?"),
          _drafts_unclassified,
          cue=r"correo|borrador|por clasificar|sin clasificar|sin departamento|bandeja"),
    Query("ai_precision_week", ("admin",),
          "Precisión de la IA al clasificar correos por departamento en los últimos 7 días: aciertos, "
          "confirmados y porcentaje. No cubre: correos que esperan en «Por clasificar» ahora.",
          ("¿Qué tan precisa fue la IA esta semana?", "Precisión de la clasificación", "¿Cuánto acertó la IA?"),
          _ai_precision_week,
          cue=r"precis|acert|aciert|exactitud|clasificacion|que tan bien|se equivoc|la ia\b"),
    Query("resolved_period", ("admin", "coordinator", "technician"),
          "Tickets resueltos en un periodo (hoy, ayer, esta semana, la semana pasada, este mes, el mes pasado, "
          "últimos 7 o 30 días): cuántos, por departamento y por técnico. No cubre: abiertos ahora, prioridad.",
          ("¿Cuántos tickets se resolvieron esta semana?", "Resueltos el mes pasado",
           "¿Qué departamento resolvió más tickets?"),
          _resolved_period,
          cue=r"resuelt|resolvi|solucionad|finalizad|cerrad|\bcerr(?:o|aron|amos)\b|(?<!des)atendid"
              r"|\batend(?:io|ieron)\b",
          negatable=True,
          uses_period=True,
          own_description="TUS tickets resueltos en un periodo (hoy, ayer, esta semana, el mes pasado…). "
                          "No cubre: abiertos ahora.",
          own_examples=("¿Cuántos tickets resolví esta semana?", "Mis resueltos de ayer")),
)
BY_ID = {q.id: q for q in CATALOG}
# Vacío: la IA no elige periodo; lo detecta el servidor (period_of)
PERIODS: tuple[str, ...] = ()


def allowed_for(user: models.User) -> list[Query]:
    return [q for q in CATALOG if user.role in q.roles]


# Preguntas de cómo hacer algo o qué significa algo: ayuda, aunque nombren tickets
_HOW_TO_RE = re.compile(
    r"^(que significa|que es|que hago|que reviso|pasos|por que|como (?!estamos|vamos|va\b|van\b|esta\b|estan\b))")

# Sin señales, (?!) nunca coincide
_OUTSIDE_RE = re.compile(_cue(*OUTSIDE_SIGNALS) or r"(?!)")
# «No resueltos», «sin cerrar», «no han sido atendidos», «nadie ha atendido»: siguen abiertos
_NEGATION_RE = re.compile(r"\b(?:no|sin|nadie|nunca)\b(?: \w+){0,3} $")


def _asks_outside(plain: str) -> bool:
    return _affirmed(_OUTSIDE_RE, plain)


def _affirmed(pattern, plain: str) -> bool:
    """Alguna coincidencia que no va negada («no resueltos», «sin cerrar»)."""
    return any(not _NEGATION_RE.search(plain[:m.start()]) for m in re.finditer(pattern, plain))


def _matches(query: "Query", plain: str) -> bool:
    return _affirmed(query.cue, plain) if query.negatable else bool(re.search(query.cue, plain))


def _plain(text: str) -> str:
    """Minúsculas, sin tildes ni signos de apertura: para buscar palabras."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.replace("¿", " ").replace("¡", " ").split())


def candidates(user: models.User, message: str) -> list[Query]:
    """Filtro por palabras, en el servidor y antes de la IA: solo se ofrecen las consultas permitidas al rol
    cuyas palabras trae la pregunta. Sin ninguna, la pregunta va directo a la ayuda general (sin datos)."""
    plain = _plain(message)
    if _HOW_TO_RE.search(plain):
        return []
    # Con palabras de una consulta específica (aunque el rol no la tenga), la general no se ofrece:
    # el modelo pequeño se quedaba con «abiertos» al preguntar por vencidos
    specific = any(_matches(q, plain) for q in CATALOG if not q.generic)
    # Lo mismo cuando pide algo fuera de los abiertos: los abiertos no son la respuesta
    outside = _asks_outside(plain)
    return [q for q in allowed_for(user)
            if _matches(q, plain) and not (q.generic and specific) and not (q.about_open and outside)]


def options_for(user: models.User, message: str | None = None) -> list[dict]:
    """Lo que ve la IA para elegir: id, descripción y ejemplos. Nada de datos. Con message, solo las que pasan
    el filtro por palabras."""
    own = user.role == "technician"
    queries = allowed_for(user) if message is None else candidates(user, message)
    return [{"id": q.id,
             "description": (q.own_description if own and q.own_description else q.description),
             "examples": list(q.own_examples if own and q.own_examples else q.examples)}
            for q in queries]


def choose(user: models.User, message: str) -> tuple[str, str | None]:
    """(id, periodo) para la pregunta. Si el filtro no deja ninguna consulta, ayuda sin llamar a la IA.
    Lanza chatbot.ChatbotUnavailable."""
    options = options_for(user, message)
    if not options:
        return chatbot.HELP, None
    periods = PERIODS if any(BY_ID[o["id"]].uses_period for o in options) else ()
    chosen, period = chatbot.select_query(message, options, periods)
    if chosen in BY_ID and BY_ID[chosen].uses_period:
        period = period_of(message)
    return chosen, period


def resolve(user: models.User, query_id: str) -> Query | None:
    """Segunda defensa: el id elegido tiene que estar permitido para el rol."""
    query = BY_ID.get(query_id)
    return query if query is not None and user.role in query.roles else None


def suggestions(user: models.User) -> list[str]:
    own = user.role == "technician"
    return [(q.own_examples if own and q.own_examples else q.examples)[0] for q in allowed_for(user)][:4]


def no_match_text(user: models.User) -> str:
    return "\n".join([NO_MATCH_TEXT, *[f"• {s}" for s in suggestions(user)]])


def run(db: Session, user: models.User, query: Query, period: str | None = None,
        now: datetime | None = None) -> Result:
    now = now or _now()
    sc = scope(db, user)
    result = query.run(db, user, sc, now, period)
    if sc.totals_only:
        # Segunda defensa: sin #id ni títulos para el administrador con el interruptor apagado
        result.listing = []
        result.facts.append(TOTALS_ONLY_NOTE)
    return result


# ---------- Verificación: los números de la respuesta salen del servidor ----------

_TOKEN_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"            # 2026-11-16
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"  # 16/11 y 16/11/2026
    r"|\d{1,2}:\d{2}"               # 10:42
    r"|\d+(?:[.,]\d+)?"             # 12, 45,5, 45.5
)
_WORDS = {"cero": "0", "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
          "siete": "7", "ocho": "8", "nueve": "9", "diez": "10", "once": "11", "doce": "12", "trece": "13",
          "catorce": "14", "quince": "15", "dieciséis": "16", "diecisiete": "17", "dieciocho": "18",
          "diecinueve": "19", "veinte": "20"}
_WORD_RE = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
# «No hay tickets», «ningún ticket»: cero dicho con palabras
_ZERO_RE = re.compile(r"\b(no hay|no tienes|no tiene|ningún|ninguno|ninguna)\b", re.IGNORECASE)


def _norm(token: str) -> str:
    if re.fullmatch(r"\d+(?:[.,]\d+)?", token):
        value = token.replace(",", ".")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        return value.lstrip("0") or "0"
    return token


def tokens(text: str) -> list[str]:
    """Números de un texto: fechas y horas como un solo valor, decimales con coma o punto, números escritos
    con palabras (del cero al veinte) y el cero dicho como «no hay» o «ningún»."""
    found = [_norm(t) for t in _TOKEN_RE.findall(text)]
    found += [_WORDS[w.lower()] for w in _WORD_RE.findall(text)]
    found += ["0"] * len(_ZERO_RE.findall(text))
    return found


def verify(reply: str, result: Result) -> tuple[bool, str | None]:
    """(pasa, motivo). Todo número de la respuesta tiene que estar en los datos del servidor y el número
    principal tiene que aparecer."""
    allowed = set(tokens("\n".join(result.facts))) | set(tokens(result.text))
    found = tokens(reply)
    for token in found:
        if token not in allowed:
            return False, f"número {token} no está en los datos"
    for token in result.required:
        if _norm(token) not in found:
            return False, f"falta el número principal {token}"
    return True, None


def compose(body: str, result: Result, now: datetime) -> str:
    parts = [body.strip()]
    if result.listing:
        parts.append("\n".join(result.listing))
    parts.append(f"(Datos del servidor: {result.label}, {_local(now):%H:%M})")
    return "\n\n".join(parts)


def answer(result: Result, message: str, now: datetime | None = None) -> str:
    """Redacción de la IA si pasa la verificación; si no (o si la IA no responde), el texto del servidor."""
    now = now or _now()
    try:
        reply = chatbot.write_answer(result.facts, message)
    except chatbot.ChatbotUnavailable:
        logging.info("Asistente: la IA no redactó; texto del servidor (consulta %s)", result.query)
        return compose(result.text, result, now)
    ok, _reason = verify(reply, result)
    if not ok:
        # Solo el id: nunca el texto ni los números
        logging.info("Asistente: la redacción no pasó la verificación (consulta %s)", result.query)
        return compose(result.text, result, now)
    return compose(reply, result, now)
