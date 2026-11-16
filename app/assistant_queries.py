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
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import alerts, chatbot, models, nav, routing
from .daily_summary import _local
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


@dataclass
class Query:
    id: str
    roles: tuple
    description: str
    examples: tuple
    run: object
    uses_period: bool = False
    # Para el técnico (solo lo suyo), si cambia
    own_description: str | None = None
    own_examples: tuple = ()


CATALOG = (
    Query("open_tickets", ("admin", "coordinator", "technician"),
          "Cifras de tickets abiertos: total, nuevos, en proceso, sin asignar.",
          ("¿Cuántos tickets hay abiertos?", "Resume el estado del equipo", "¿Cuántos están sin asignar?"),
          _open_tickets_query,
          own_description="Cifras y lista de TUS tickets abiertos (solo los asignados a ti).",
          own_examples=("¿Cuántos tickets tengo?", "Resume mis tickets abiertos", "¿Qué tickets tengo asignados?")),
    Query("open_by_department", ("admin", "coordinator"),
          "Tickets abiertos repartidos por departamento o área.",
          ("Tickets abiertos por departamento", "¿Qué departamento tiene más tickets?", "Reparto por área"),
          _open_by_department),
)
BY_ID = {q.id: q for q in CATALOG}
PERIODS: tuple[str, ...] = ()


def allowed_for(user: models.User) -> list[Query]:
    return [q for q in CATALOG if user.role in q.roles]


def options_for(user: models.User) -> list[dict]:
    """Lo que ve la IA para elegir: id, descripción y ejemplos. Nada de datos."""
    own = user.role == "technician"
    return [{"id": q.id,
             "description": (q.own_description if own and q.own_description else q.description),
             "examples": list(q.own_examples if own and q.own_examples else q.examples)}
            for q in allowed_for(user)]


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
