"""Registro de cambios por persona (solo administrador, solo lectura).

Se arma con lo que ya existe: el registro de cuentas (account_changes), el historial de tickets, los
comentarios, los borradores y las causas de vencimiento. Nada de aquí escribe en la base.

- Personal: encabezado, resumen de un rango y línea de tiempo de sus acciones. Solo el número del ticket,
  nunca el título ni el texto de comentarios o notas.
- Cliente: solo los cambios de su cuenta. Nunca tickets, títulos, cantidades ni mensajes.

Los datos personales salen enmascarados (correo con mask_email, teléfono y cédula con los últimos dígitos).
La línea de tiempo se pagina con un cursor: (fecha, fuente, id) del último evento entregado.
"""
import base64
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, text, true
from sqlalchemy.orm import Session

from . import account_reports, availability, config, identity, models, schedules, throttle
from .security import mask_email
from .validators import mask_last4

PAGE = 50
SEARCH_LIMIT = 10
SUMMARY_DAYS = 30
STAFF_ROLES = ("technician", "coordinator")
PERSON_ROLES = ("technician", "coordinator", "requester")

# Grupos de la línea de tiempo
STAFF_GROUPS = ("access", "tickets", "drafts", "shifts", "account")
CLIENT_GROUPS = ("access", "data", "reports", "status")

ACCESS_ACTIONS = (
    "login", "login_failed", "login_locked", "login_unlocked", "microsoft_login", "microsoft_login_rejected",
    "emergency_access", "microsoft_linked", "sessions_closed", "password_changed", "password_reset",
    "access_reset_sent", "access_reset_forced",
)
LOGIN_ACTIONS = ("login", "microsoft_login", "emergency_access")
SHIFT_ACTIONS = ("schedule_changed", "availability_changed", "availability_period_added", "availability_period_removed")
# Acciones del registro que hablan de tickets (target_label "Ticket #N")
TICKET_ACTIONS = (
    "attachment_uploaded", "attachment_downloaded", "email_attachments_received", "email_attachments_sent",
    "ticket_merged", "ticket_merge_undone", "ticket_linked", "ticket_link_undone", "incident_marked",
    "overdue_report_downloaded", "conversation_viewed", "tickets_released",
)

# Cliente: lista blanca, solo su cuenta
CLIENT_ACTIONS = {
    "access": ("login", "login_failed", "login_locked", "login_unlocked", "password_changed", "password_reset",
               "access_reset_sent", "access_reset_forced", "sessions_closed"),
    "data": ("email_changed", "phone_changed", "id_number_changed", "name_changed", "id_number_conflict"),
    "reports": ("report_created", "report_confirmed", "report_dismissed"),
    "status": ("account_activated", "account_deactivated", "server_account_activated", "server_account_deactivated"),
}

ACTION_LABELS = {
    "password_changed": "Contraseña cambiada",
    "email_changed": "Correo cambiado",
    "phone_changed": "Teléfono cambiado",
    "name_changed": "Nombre cambiado",
    "id_number_changed": "Cédula cambiada",
    "role_changed": "Rol cambiado",
    "account_activated": "Cuenta activada",
    "account_deactivated": "Cuenta desactivada",
    "invitation_sent": "Invitación enviada",
    "invitation_used": "Invitación usada",
    "invitation_cancelled": "Invitación cancelada",
    "microsoft_linked": "Cuenta Microsoft enlazada",
    "microsoft_login": "Ingreso con Microsoft",
    "microsoft_login_rejected": "Ingreso con Microsoft rechazado",
    "emergency_access": "Acceso de emergencia",
    "id_number_conflict": "Cédula de otra cuenta",
    "login": "Ingreso",
    "login_failed": "Intento de ingreso fallido",
    "login_locked": "Bloqueo por intentos",
    "login_unlocked": "Desbloqueo de intentos",
    "password_reset": "Contraseña restablecida",
    "access_reset_sent": "Código de acceso enviado",
    "access_reset_forced": "Restablecimiento forzado",
    "tickets_released": "Tickets liberados",
    "request_created": "Solicitud creada",
    "request_approved": "Solicitud aprobada",
    "request_rejected": "Solicitud rechazada",
    "sessions_closed": "Sesiones cerradas",
    "availability_changed": "Disponibilidad cambiada",
    "availability_period_added": "Vacaciones o ausencia registrada",
    "availability_period_removed": "Vacaciones o ausencia quitada",
    "schedule_changed": "Horario cambiado",
    "report_created": "Cuenta reportada",
    "report_confirmed": "Reporte confirmado",
    "report_dismissed": "Reporte descartado",
    "server_account_deactivated": "Cuenta desactivada (servidor)",
    "server_account_activated": "Cuenta activada (servidor)",
    "server_role_changed": "Rol cambiado (servidor)",
    "overdue_report_downloaded": "PDF de reporte de vencimiento descargado",
    "attachment_uploaded": "Adjunto subido",
    "attachment_downloaded": "Adjunto descargado",
    "email_attachments_received": "Adjuntos recibidos por correo",
    "email_attachments_sent": "Adjuntos enviados por correo",
    "ticket_merged": "Tickets fusionados",
    "ticket_merge_undone": "Fusión separada",
    "ticket_linked": "Tickets relacionados",
    "ticket_link_undone": "Relación separada",
    "incident_marked": "Posible incidente",
    "conversation_viewed": "Ver conversación",
}
STATUS_LABELS = {"new": "Nuevo", "in_progress": "En proceso", "resolved": "Resuelto", "closed": "Cerrado"}
DRAFT_LABELS = {"confirmed": "Confirmó el borrador", "discarded": "Descartó el borrador",
                "waiting_contact": "Pidió más detalles en el borrador"}

# Fuentes de la línea de tiempo y su orden cuando dos eventos tienen la misma fecha
SRC_CHANGE, SRC_HISTORY, SRC_COMMENT, SRC_DRAFT, SRC_CAUSE = 1, 2, 3, 4, 5

# Índices para que las consultas por persona sean rápidas (create_all no los agrega a tablas existentes)
INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_history_changed_by_at ON history (changed_by, changed_at)",
    "CREATE INDEX IF NOT EXISTS ix_history_field_new ON history (field, new_value)",
    "CREATE INDEX IF NOT EXISTS ix_history_field_old ON history (field, old_value)",
    "CREATE INDEX IF NOT EXISTS ix_comments_user_at ON comments (user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_account_changes_actor_at ON account_changes (actor_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_email_drafts_reviewer_at ON email_drafts (reviewed_by, reviewed_at)",
)


def ensure_indexes(engine) -> None:
    with engine.begin() as conn:
        for statement in INDEXES:
            conn.execute(text(statement))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None and value.tzinfo else value


# ----- Búsqueda -----
def search(db: Session, query: str) -> list[dict]:
    term = (query or "").strip().lower()
    if not term:
        return []
    like = f"%{term}%"
    users = (
        db.query(models.User)
        .filter(models.User.role.in_(PERSON_ROLES))
        .filter(func.lower(models.User.name).like(like) | func.lower(models.User.email).like(like))
        .order_by(models.User.name)
        .limit(SEARCH_LIMIT)
        .all()
    )
    return [{"id": u.id, "name": u.name or "Sin nombre", "role": u.role, "email": mask_email(u.email)} for u in users]


def person(db: Session, user_id: int) -> models.User | None:
    return db.query(models.User).filter(models.User.id == user_id, models.User.role.in_(PERSON_ROLES)).first()


# ----- Encabezado -----
def _state(db: Session, user: models.User) -> str:
    if not user.is_active:
        return "inactive"
    if throttle.is_locked(db, user.email.strip().lower()):
        return "locked"
    return "active"


def last_access(db: Session, user: models.User) -> datetime | None:
    logged = (
        db.query(func.max(models.AccountChange.created_at))
        .filter(models.AccountChange.target_user_id == user.id, models.AccountChange.action.in_(LOGIN_ACTIONS))
        .scalar()
    )
    used = (
        db.query(func.max(func.coalesce(models.SessionActivity.last_used_at, models.Session.created_at)))
        .select_from(models.Session)
        .outerjoin(models.SessionActivity, models.SessionActivity.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .scalar()
    )
    found = [_naive(v) for v in (logged, used) if v is not None]
    return max(found) if found else None


def header(db: Session, user: models.User) -> dict:
    base = {
        "id": user.id,
        "name": user.name or "Sin nombre",
        "role": user.role,
        "email": mask_email(user.email),
        "state": _state(db, user),
    }
    if user.role == "requester":
        base.update(
            phone=mask_last4(user.phone),
            reported=user.id in account_reports.reported_ids(db, [user.id]),
            identity=identity.hacienda_codes(db, [user.id]).get(user.id),
        )
        return base
    now = _now()
    rows = schedules.for_users(db, [user.id]).get(user.id, [])
    today = schedules.today_range(rows, now)
    status = availability.statuses_for(db, [user.id], now).get(user.id, ("available", None))
    base.update(
        schedule_today=f"{today[0]}–{today[1]}" if today else None,
        on_shift=schedules.on_shift(rows, now),
        availability=status[0],
        last_access=last_access(db, user),
        microsoft=db.query(models.UserMicrosoftLink.id).filter(models.UserMicrosoftLink.user_id == user.id).first() is not None,
    )
    return base


# ----- Resumen del personal -----
def _in_range(column, start: datetime, end: datetime):
    return and_(column >= start, column < end)


def summary(db: Session, user: models.User, start: datetime, end: datetime) -> dict:
    uid = str(user.id)
    hist = models.History
    assigned = (
        db.query(func.count(func.distinct(hist.ticket_id)))
        .filter(hist.field == "assigned_to", hist.new_value == uid, _in_range(hist.changed_at, start, end))
        .scalar()
    )
    resolved = (
        db.query(func.count(func.distinct(hist.ticket_id)))
        .filter(hist.field == "status", hist.new_value == "resolved", hist.changed_by == user.id,
                _in_range(hist.changed_at, start, end))
        .scalar()
    )
    given = (
        db.query(func.count(hist.id))
        .filter(hist.field == "assigned_to", hist.old_value == uid, hist.new_value.isnot(None), hist.new_value != uid,
                _in_range(hist.changed_at, start, end))
        .scalar()
    )
    received = (
        db.query(func.count(hist.id))
        .filter(hist.field == "assigned_to", hist.new_value == uid, hist.old_value.isnot(None), hist.old_value != uid,
                _in_range(hist.changed_at, start, end))
        .scalar()
    )
    drafts = dict(
        db.query(models.EmailDraft.status, func.count(models.EmailDraft.id))
        .filter(models.EmailDraft.reviewed_by == user.id, models.EmailDraft.status.in_(("confirmed", "discarded")),
                _in_range(models.EmailDraft.reviewed_at, start, end))
        .group_by(models.EmailDraft.status)
        .all()
    )
    logins = (
        db.query(func.count(models.AccountChange.id))
        .filter(models.AccountChange.target_user_id == user.id, models.AccountChange.action.in_(LOGIN_ACTIONS),
                _in_range(models.AccountChange.created_at, start, end))
        .scalar()
    )
    return {
        "start": start, "end": end,
        "assigned": assigned or 0, "resolved": resolved or 0,
        "reassigned_given": given or 0, "reassigned_received": received or 0,
        "overdue": _overdue_count(db, user, start, end),
        "drafts_confirmed": drafts.get("confirmed", 0), "drafts_discarded": drafts.get("discarded", 0),
        "logins": logins or 0,
    }


def _overdue_count(db: Session, user: models.User, start: datetime, end: datetime) -> int:
    """Avisos "vencido" del rango cuyo responsable en ese momento (según el historial) era la persona."""
    notices = (
        db.query(models.TicketDueNotice.ticket_id, models.TicketDueNotice.created_at)
        .filter(models.TicketDueNotice.kind == "overdue", models.TicketDueNotice.baseline.is_(False),
                _in_range(models.TicketDueNotice.created_at, start, end))
        .all()
    )
    if not notices:
        return 0
    ticket_ids = {n.ticket_id for n in notices}
    changes: dict[int, list[tuple[datetime, str | None]]] = {}
    for ticket_id, changed_at, new_value in (
        db.query(models.History.ticket_id, models.History.changed_at, models.History.new_value)
        .filter(models.History.field == "assigned_to", models.History.ticket_id.in_(ticket_ids))
        .order_by(models.History.changed_at, models.History.id)
        .all()
    ):
        changes.setdefault(ticket_id, []).append((_naive(changed_at), new_value))
    current = dict(db.query(models.Ticket.id, models.Ticket.assigned_to).filter(models.Ticket.id.in_(ticket_ids)).all())
    count = 0
    for notice in notices:
        at = _naive(notice.created_at)
        owner = None
        history = changes.get(notice.ticket_id)
        if history:
            for changed_at, new_value in history:
                if changed_at <= at:
                    owner = new_value
        else:
            owner = str(current.get(notice.ticket_id)) if current.get(notice.ticket_id) else None
        if owner == str(user.id):
            count += 1
    return count


# ----- Línea de tiempo -----
def encode_cursor(at: datetime, src: int, row_id: int) -> str:
    raw = f"{at.isoformat()}|{src}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str | None) -> tuple[datetime, int, int] | None:
    if not cursor:
        return None
    try:
        at, src, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(at), int(src), int(row_id)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Cursor inválido")


def _before(column, id_column, src: int, cursor):
    """Filas que van después del cursor en el orden (fecha desc, fuente desc, id desc)."""
    if cursor is None:
        return true()
    at, c_src, c_id = cursor
    if src < c_src:
        return column <= at
    if src > c_src:
        return column < at
    return or_(column < at, and_(column == at, id_column < c_id))


def _ticket_label(ticket_id: int) -> str:
    return f"#{ticket_id}"


def _change_group(action: str) -> str:
    if action in ACCESS_ACTIONS:
        return "access"
    if action in SHIFT_ACTIONS:
        return "shifts"
    if action in TICKET_ACTIONS:
        return "tickets"
    return "account"


def _client_group(action: str) -> str | None:
    for group, actions in CLIENT_ACTIONS.items():
        if action in actions:
            return group
    return None


def _change_text(row: models.AccountChange, user: models.User, client: bool) -> str:
    label = ACTION_LABELS.get(row.action, row.action)
    parts = [label]
    if not client and row.target_user_id is not None and row.target_user_id != user.id and row.target is not None:
        parts.append(f"a {row.target.name or mask_email(row.target.email)}")
    elif not client and row.target_label and row.action not in ("conversation_viewed",):
        parts.append(row.target_label)
    if row.action == "conversation_viewed" and row.target_label:
        parts.append(row.target_label)
    if row.old_value or row.new_value:
        if row.action in ("login_locked", "report_created", "report_confirmed", "report_dismissed", "conversation_viewed",
                          "id_number_conflict", "request_created"):
            parts.append(f"· {row.new_value or row.old_value}")
        else:
            parts.append(f"· {row.old_value or '—'} → {row.new_value or '—'}")
    return " ".join(parts)


def _ticket_from_label(label: str | None) -> int | None:
    if label and label.startswith("Ticket #"):
        digits = "".join(c for c in label[len("Ticket #"):].split(" ")[0] if c.isdigit())
        return int(digits) if digits else None
    return None


def _changes(db, user, client, groups, start, end, ticket, cursor, limit):
    ac = models.AccountChange
    query = db.query(ac).filter(_before(ac.created_at, ac.id, SRC_CHANGE, cursor))
    if client:
        allowed = [a for g in groups for a in CLIENT_ACTIONS.get(g, ())]
        query = query.filter(ac.target_user_id == user.id, ac.action.in_(allowed))
    else:
        query = query.filter(or_(ac.target_user_id == user.id, ac.actor_id == user.id))
        actions = []
        if "access" in groups:
            actions += ACCESS_ACTIONS
        if "shifts" in groups:
            actions += SHIFT_ACTIONS
        if "tickets" in groups:
            actions += TICKET_ACTIONS
        conditions = [ac.action.in_(actions)] if actions else []
        if "account" in groups:
            conditions.append(~ac.action.in_(ACCESS_ACTIONS + SHIFT_ACTIONS + TICKET_ACTIONS))
        if not conditions:
            return []
        query = query.filter(or_(*conditions))
        if ticket is not None:
            query = query.filter(or_(ac.target_label == f"Ticket #{ticket}", ac.target_label.like(f"Ticket #{ticket} %")))
    if start:
        query = query.filter(ac.created_at >= start)
    if end:
        query = query.filter(ac.created_at < end)
    rows = query.order_by(ac.created_at.desc(), ac.id.desc()).limit(limit).all()
    items = []
    for row in rows:
        group = _client_group(row.action) if client else _change_group(row.action)
        items.append({
            "at": _naive(row.created_at), "src": SRC_CHANGE, "id": row.id, "group": group, "kind": row.action,
            "text": _change_text(row, user, client),
            "ticket_id": None if client else _ticket_from_label(row.target_label),
        })
    return items


def _names(db: Session, ids: set[str]) -> dict[str, str]:
    numeric = [int(v) for v in ids if v and v.isdigit()]
    if not numeric:
        return {}
    return {str(uid): name or "Sin nombre" for uid, name in
            db.query(models.User.id, models.User.name).filter(models.User.id.in_(numeric)).all()}


def _history_text(row, names, priorities, categories) -> str:
    ticket = _ticket_label(row.ticket_id)
    field = row.field
    if field == "status":
        return f"Cambió el estado de {ticket}: {STATUS_LABELS.get(row.old_value, row.old_value or '—')} → " \
               f"{STATUS_LABELS.get(row.new_value, row.new_value or '—')}"
    if field == "assigned_to":
        old = names.get(row.old_value or "", "sin asignar") if row.old_value else "sin asignar"
        new = names.get(row.new_value or "", "sin asignar") if row.new_value else "sin asignar"
        return f"Asignación de {ticket}: {old} → {new}"
    if field == "priority_id":
        return f"Cambió la prioridad de {ticket}: {priorities.get(row.old_value, '—')} → {priorities.get(row.new_value, '—')}"
    if field == "category_id":
        return f"Cambió la categoría de {ticket}: {categories.get(row.old_value, '—')} → {categories.get(row.new_value, '—')}"
    if field == "cancelled":
        return f"Canceló {ticket}"
    if field == "reopened":
        return f"Reabrió {ticket}"
    if field == "merged":
        return f"Fusionó {ticket} en #{row.new_value}"
    if field in ("merged_from", "unmerged", "unmerged_from"):
        return f"Cambió la fusión de {ticket}"
    if field in ("linked", "unlinked"):
        return f"{'Relacionó' if field == 'linked' else 'Separó'} {ticket}"
    return f"Cambió {ticket}"


def _history(db, user, start, end, ticket, cursor, limit):
    hist = models.History
    query = db.query(hist).filter(hist.changed_by == user.id, _before(hist.changed_at, hist.id, SRC_HISTORY, cursor))
    if ticket is not None:
        query = query.filter(hist.ticket_id == ticket)
    if start:
        query = query.filter(hist.changed_at >= start)
    if end:
        query = query.filter(hist.changed_at < end)
    rows = query.order_by(hist.changed_at.desc(), hist.id.desc()).limit(limit).all()
    names = _names(db, {r.old_value for r in rows if r.field == "assigned_to"} | {r.new_value for r in rows if r.field == "assigned_to"})
    priorities = {str(i): n for i, n in db.query(models.Priority.id, models.Priority.name).all()}
    categories = {str(i): n for i, n in db.query(models.Category.id, models.Category.name).all()}
    return [{
        "at": _naive(r.changed_at), "src": SRC_HISTORY, "id": r.id, "group": "tickets", "kind": f"ticket_{r.field}",
        "text": _history_text(r, names, priorities, categories), "ticket_id": r.ticket_id,
    } for r in rows]


def _comments(db, user, start, end, ticket, cursor, limit):
    # Solo id, ticket, fecha y si es interno: el texto nunca se lee
    com = models.Comment
    query = (
        db.query(com.id, com.ticket_id, com.created_at, models.InternalComment.id.label("internal"))
        .outerjoin(models.InternalComment, models.InternalComment.comment_id == com.id)
        .filter(com.user_id == user.id, _before(com.created_at, com.id, SRC_COMMENT, cursor))
    )
    if ticket is not None:
        query = query.filter(com.ticket_id == ticket)
    if start:
        query = query.filter(com.created_at >= start)
    if end:
        query = query.filter(com.created_at < end)
    rows = query.order_by(com.created_at.desc(), com.id.desc()).limit(limit).all()
    return [{
        "at": _naive(r.created_at), "src": SRC_COMMENT, "id": r.id, "group": "tickets",
        "kind": "internal_note" if r.internal else "comment",
        "text": f"{'Nota interna en' if r.internal else 'Comentó en'} {_ticket_label(r.ticket_id)}", "ticket_id": r.ticket_id,
    } for r in rows]


def _drafts(db, user, start, end, ticket, cursor, limit):
    draft = models.EmailDraft
    query = (
        db.query(draft.id, draft.status, draft.reviewed_at, draft.ticket_id)
        .filter(draft.reviewed_by == user.id, draft.reviewed_at.isnot(None), draft.status.in_(tuple(DRAFT_LABELS)),
                _before(draft.reviewed_at, draft.id, SRC_DRAFT, cursor))
    )
    if ticket is not None:
        query = query.filter(draft.ticket_id == ticket)
    if start:
        query = query.filter(draft.reviewed_at >= start)
    if end:
        query = query.filter(draft.reviewed_at < end)
    rows = query.order_by(draft.reviewed_at.desc(), draft.id.desc()).limit(limit).all()
    return [{
        "at": _naive(r.reviewed_at), "src": SRC_DRAFT, "id": r.id, "group": "drafts", "kind": f"draft_{r.status}",
        "text": f"{DRAFT_LABELS[r.status]} {r.id}" + (f" (ticket {_ticket_label(r.ticket_id)})" if r.ticket_id else ""),
        "ticket_id": r.ticket_id,
    } for r in rows]


def _causes(db, user, start, end, ticket, cursor, limit):
    cause = models.OverdueCause
    query = (
        db.query(cause.id, cause.ticket_id, cause.cause, cause.recorded_at)
        .filter(cause.recorded_by == user.id, _before(cause.recorded_at, cause.id, SRC_CAUSE, cursor))
    )
    if ticket is not None:
        query = query.filter(cause.ticket_id == ticket)
    if start:
        query = query.filter(cause.recorded_at >= start)
    if end:
        query = query.filter(cause.recorded_at < end)
    rows = query.order_by(cause.recorded_at.desc(), cause.id.desc()).limit(limit).all()
    return [{
        "at": _naive(r.recorded_at), "src": SRC_CAUSE, "id": r.id, "group": "tickets", "kind": "overdue_cause",
        "text": f"Registró la causa del vencimiento de {_ticket_label(r.ticket_id)}: "
                f"{models.OVERDUE_CAUSES.get(r.cause, r.cause)}",
        "ticket_id": r.ticket_id,
    } for r in rows]


def timeline(db: Session, user: models.User, groups: list[str] | None, start: datetime | None, end: datetime | None,
             ticket: int | None, cursor: str | None) -> tuple[list[dict], str | None]:
    client = user.role == "requester"
    valid = CLIENT_GROUPS if client else STAFF_GROUPS
    wanted = [g for g in (groups or valid) if g in valid]
    if client:
        # La ficha de un cliente nunca toca tickets, aunque se pida
        ticket = None
    position = decode_cursor(cursor)
    limit = PAGE + 1
    items = _changes(db, user, client, wanted, start, end, ticket, position, limit)
    if not client:
        if "tickets" in wanted:
            items += _history(db, user, start, end, ticket, position, limit)
            items += _comments(db, user, start, end, ticket, position, limit)
            items += _causes(db, user, start, end, ticket, position, limit)
        if "drafts" in wanted:
            items += _drafts(db, user, start, end, ticket, position, limit)
    items.sort(key=lambda i: (i["at"], i["src"], i["id"]), reverse=True)
    page = items[:PAGE]
    next_cursor = encode_cursor(page[-1]["at"], page[-1]["src"], page[-1]["id"]) if len(items) > PAGE else None
    return page, next_cursor


def local_day_start(day: date) -> datetime:
    """Inicio de un día de la oficina, en UTC sin zona (como guarda la base)."""
    local = datetime.combine(day, time.min, tzinfo=ZoneInfo(config.OFFICE_TZ))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def date_range(start: date | None, end: date | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Rango [desde, hasta] en días de la oficina; sin fechas, los últimos 30 días hasta ahora."""
    now = now or _now()
    begin = local_day_start(start) if start else now - timedelta(days=SUMMARY_DAYS)
    finish = local_day_start(end + timedelta(days=1)) if end else now + timedelta(seconds=1)
    return begin, finish
