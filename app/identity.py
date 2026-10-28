"""Perfil del solicitante: cédula única, verificación con Hacienda y bloqueo tras el primer ticket."""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import audit, hacienda, models, suspicion

ID_IN_USE = "No es posible usar esta cédula. Si le pertenece, contacte a soporte."
ID_IN_USE_REASON = "cédula en uso por otra cuenta"
LOCKED = "El nombre y la cédula ya no se pueden cambiar porque ya creó un ticket. Contacte a soporte."
INCOMPLETE_OWN = "Complete su perfil (nombre completo, cédula y teléfono) antes de crear tickets"
INCOMPLETE_OTHER = "El perfil del solicitante está incompleto (nombre completo, cédula y teléfono)"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def profile_complete(user: models.User) -> bool:
    if user.role != "requester":
        return True
    return bool((user.name or "").strip() and user.phone and user.id_number)


def _row(db: Session, user: models.User) -> models.RequesterIdentity | None:
    return db.query(models.RequesterIdentity).filter(models.RequesterIdentity.user_id == user.id).first()


def _get_or_create(db: Session, user: models.User) -> models.RequesterIdentity:
    row = _row(db, user)
    if row is None:
        row = models.RequesterIdentity(user_id=user.id)
        db.add(row)
        # La sesión no hace autoflush: sin esto otra consulta no la vería y se duplicaría
        db.flush()
    return row


def _first_ticket_at(db: Session, user: models.User) -> datetime | None:
    first = (
        db.query(models.Ticket.created_at)
        .filter(models.Ticket.created_by == user.id)
        .order_by(models.Ticket.created_at.asc())
        .first()
    )
    return first[0] if first else None


def is_locked(db: Session, user: models.User) -> bool:
    """Nombre y cédula fijos desde el primer ticket (también para cuentas anteriores a esta tabla).

    Si falta la fecha de bloqueo la agrega con la del primer ticket. No hace commit.
    """
    if user.role != "requester":
        return False
    row = _row(db, user)
    if row is not None and row.locked_at is not None:
        return True
    first_at = _first_ticket_at(db, user)
    if first_at is None:
        return False
    row = row or _get_or_create(db, user)
    row.locked_at = first_at
    return True


def lock(db: Session, user: models.User) -> None:
    """Marca la fecha de bloqueo si todavía no la tiene. No hace commit."""
    if user.role != "requester":
        return
    row = _get_or_create(db, user)
    if row.locked_at is None:
        row.locked_at = _now()


def id_number_taken(db: Session, id_number: str, user_id: int | None) -> bool:
    query = db.query(models.User.id).filter(models.User.id_number == id_number)
    if user_id is not None:
        query = query.filter(models.User.id != user_id)
    return query.first() is not None


def hacienda_label_code(db: Session, user: models.User) -> str:
    """Solo el resultado (match, mismatch, not_found o unverified), nunca el nombre de Hacienda."""
    row = _row(db, user)
    return row.hacienda_result if row is not None and row.hacienda_result else "unverified"


def hacienda_codes(db: Session, user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    rows = db.query(models.RequesterIdentity).filter(models.RequesterIdentity.user_id.in_(user_ids)).all()
    found = {r.user_id: r.hacienda_result for r in rows if r.hacienda_result}
    return {uid: found.get(uid, "unverified") for uid in user_ids}


def save_hacienda_result(db: Session, user: models.User, name: str, id_number: str) -> str:
    """Guarda si el nombre coincide con Hacienda (nunca el nombre de Hacienda) y lo devuelve. No hace commit."""
    row = _get_or_create(db, user)
    row.hacienda_result = hacienda.identity_result(id_number, name)
    row.checked_at = _now()
    return row.hacienda_result


def check_id_number(db: Session, user: models.User, id_number: str) -> str | None:
    """Cédula ya normalizada: mensaje de error o None si se puede usar en este perfil."""
    if id_number_taken(db, id_number, user.id):
        return ID_IN_USE
    return hacienda.check_id_number(id_number)


def apply_contact_from_email(
    db: Session, user: models.User, phone: str | None, id_number: str | None, actor_id: int | None = None
) -> str | None:
    """Datos que el cliente mandó por correo, con las reglas del perfil. No hace commit.

    El teléfono siempre se guarda. Devuelve el motivo si la cédula pertenece a otra cuenta.
    actor_id: quién confirma el cambio en el registro (sin indicar, el Agente virtual).
    Sin actor_id los datos vienen directo del correo del cliente: sus señales sospechosas se reportan.
    """
    from_client = actor_id is None
    if actor_id is None:
        actor_id = audit.agent_id(db)
    if phone and phone != user.phone:
        audit.record(
            db, actor_id, "phone_changed", target=user,
            old=audit.masked_digits(user.phone), new=audit.masked_digits(phone),
        )
    if phone:
        user.phone = phone
    if not id_number or id_number == user.id_number:
        return None
    if id_number_taken(db, id_number, user.id):
        suspicion.id_number_conflict(db, user, id_number, "email", actor_id=actor_id)
        return ID_IN_USE_REASON
    if user.id_number and is_locked(db, user):
        # Ya quedó fija: se conserva la del perfil
        return None
    audit.record(
        db, actor_id, "id_number_changed", target=user,
        old=audit.masked_digits(user.id_number), new=audit.masked_digits(id_number),
    )
    user.id_number = id_number
    if (user.name or "").strip():
        if save_hacienda_result(db, user, user.name, id_number) == "mismatch" and from_client:
            suspicion.name_mismatch(db, user, "email")
    return None
