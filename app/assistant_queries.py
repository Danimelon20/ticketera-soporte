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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import alerts, chatbot, models, nav, routing
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
# Pide algo que el catálogo no tiene: open_tickets y open_by_department no se ofrecen (la IA respondía con los
# abiertos, datos de otra cosa). Participios y pretéritos, nunca el infinitivo: «por resolver» es abierto.
# Con \b para no atrapar «general» ni «creo que». La negación («no resueltos») la descarta _NEGATION_RE.
OUTSIDE_SIGNALS: tuple[str, ...] = (
    # Resueltos, cerrados, atendidos
    r"resuelt", r"resolvi", r"solucionad", r"finalizad",
    r"cerrad", r"\bcerr(?:o|aron)\b",
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
)
BY_ID = {q.id: q for q in CATALOG}
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
    return any(not _NEGATION_RE.search(plain[:m.start()]) for m in _OUTSIDE_RE.finditer(plain))


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
    specific = any(re.search(q.cue, plain) for q in CATALOG if not q.generic)
    # Lo mismo cuando pide algo fuera del catálogo: los abiertos no son la respuesta (mejor ayuda)
    outside = _asks_outside(plain)
    return [q for q in allowed_for(user)
            if re.search(q.cue, plain) and not (q.generic and specific) and not (q.about_open and outside)]


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
    return chatbot.select_query(message, options, periods)


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
