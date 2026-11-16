"""Borradores por departamento (con «Tickets por departamento» encendido): al llegar, la IA elige el departamento
con las fichas (classifier). Si pasa el umbral y el departamento tiene coordinador activo, va a ese coordinador;
si no, queda en «Por clasificar» y el administrador elige con un clic entre las dos opciones más probables.

Apagado, los borradores siguen como antes. Nunca registrar en logs el texto de los correos.
"""
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import audit, classifier, models, routing

# Borradores que esperan a una persona (los mismos que la lista de borradores)
PENDING_DRAFT_STATUSES = ("pending_review", "waiting_contact", "error")
NO_COORDINATOR = "Ese departamento no tiene coordinador activo: asígnele uno primero (Administración › Departamentos)"
ALREADY_CLASSIFIED = "Este borrador ya tiene departamento"
NOT_FOUND = "El borrador no existe o ya no espera revisión"
THRESHOLD_LABEL = "Umbral de clasificación de correos"
MIN_SIMILARITY_LABEL = "Similitud mínima de clasificación"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def row_of(db: Session, draft_id: int) -> models.DraftClassification | None:
    return db.get(models.DraftClassification, draft_id)


def has_coordinator(db: Session, department_id: int | None) -> bool:
    """El departamento tiene un coordinador activo."""
    if department_id is None:
        return False
    row = routing.coordinator_of(db, department_id)
    if row is None:
        return False
    user = db.get(models.User, row.user_id)
    return user is not None and user.is_active


def _row(db: Session, draft: models.EmailDraft) -> models.DraftClassification:
    row = row_of(db, draft.id)
    if row is None:
        row = models.DraftClassification(draft_id=draft.id, status="pending", created_at=_now())
        db.add(row)
    return row


def classify_draft(db: Session, draft: models.EmailDraft, lock: bool = True) -> models.DraftClassification:
    """Clasifica el borrador y lo envía al coordinador o a «Por clasificar». Nunca lanza por la IA. No hace
    commit."""
    row = _row(db, draft)
    try:
        result = classifier.classify(db, draft.subject, draft.body, lock=lock)
    except Exception:
        logging.exception("Falló la clasificación del borrador %s", draft.id)
        row.status, row.pending_reason, row.department_id = "pending", "ai_error", None
        return row
    sims = dict(result.similarities)
    row.ai_department_id = result.llm_department_id
    row.ai_reason = result.reason
    row.ai_margin = result.margin
    row.ai_agrees = result.agrees
    row.threshold = result.threshold
    row.option1_id = result.options[0] if result.options else None
    row.option2_id = result.options[1] if len(result.options) > 1 else None
    row.option1_similarity = sims.get(row.option1_id)
    row.option2_similarity = sims.get(row.option2_id)
    if result.passes and has_coordinator(db, result.llm_department_id):
        row.status, row.pending_reason, row.department_id = "routed", None, result.llm_department_id
    else:
        row.status, row.department_id = "pending", None
        if result.passes:
            row.pending_reason = "no_coordinator"
        elif classifier.NO_PROFILES in result.notes:
            row.pending_reason = "no_profiles"
        elif result.low_similarity:
            row.pending_reason = "low_similarity"
        elif result.llm_department_id is None and not result.similarities:
            row.pending_reason = "ai_error"
        else:
            row.pending_reason = "below_threshold"
    logging.info("Borrador %s clasificado: %s", draft.id, row.status)
    return row


def mark_ai_error(db: Session, draft: models.EmailDraft) -> None:
    """El análisis del borrador falló: «Por clasificar» sin sugerencia. No hace commit."""
    row = _row(db, draft)
    if row.status == "pending":
        row.pending_reason = "ai_error"


def install_pending(db: Session) -> int:
    """Al encender (o arrancar encendido): los borradores pendientes sin clasificación pasan a «Por clasificar».
    Hace commit. Devuelve cuántos."""
    if not routing.enforced(db):
        return 0
    drafts = db.query(models.EmailDraft).filter(
        models.EmailDraft.status.in_(PENDING_DRAFT_STATUSES),
        models.EmailDraft.id.notin_(db.query(models.DraftClassification.draft_id)),
    ).all()
    for draft in drafts:
        db.add(models.DraftClassification(draft_id=draft.id, status="pending", pending_reason="installed",
                                          created_at=_now()))
    db.commit()
    if drafts:
        logging.info("%s borradores pendientes pasaron a Por clasificar", len(drafts))
    return len(drafts)


def pending_query(db: Session):
    """Borradores en «Por clasificar» que todavía esperan revisión, del más antiguo al más nuevo."""
    return (
        db.query(models.EmailDraft, models.DraftClassification)
        .join(models.DraftClassification, models.DraftClassification.draft_id == models.EmailDraft.id)
        .filter(models.DraftClassification.status == "pending",
                models.EmailDraft.status.in_(PENDING_DRAFT_STATUSES))
        .order_by(models.EmailDraft.created_at.asc(), models.EmailDraft.id.asc())
    )


def choose(db: Session, admin: models.User, draft_id: int, department_id: int) -> models.DraftClassification:
    """El administrador elige el departamento desde «Por clasificar». 404/409/422 si no se puede. No hace commit."""
    draft = db.get(models.EmailDraft, draft_id)
    row = row_of(db, draft_id)
    if draft is None or row is None or draft.status not in PENDING_DRAFT_STATUSES:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=ALREADY_CLASSIFIED)
    dept = routing.serving_department(db, department_id)
    if not has_coordinator(db, dept.id):
        raise HTTPException(status_code=409, detail=NO_COORDINATOR)
    row.status = "classified"
    row.department_id = dept.id
    # El motivo de «Por clasificar» se conserva: el reporte «Precisión de la IA» lo desglosa
    row.decided_by = admin.id
    row.decided_at = _now()
    # Para calibrar el umbral: ¿la IA tenía razón con su 1.ª opción? (sin sugerencia: no cuenta)
    row.matched_first = (dept.id == row.option1_id) if row.option1_id is not None else None
    return row


def set_min_similarity(db: Session, admin: models.User, value: float) -> None:
    """Similitud mínima del 1.º lugar, editable por el administrador; el cambio queda en el Registro de cambios.
    No hace commit."""
    old = classifier.min_similarity(db)
    routing.set_setting(db, classifier.MIN_SIMILARITY_KEY, f"{value:.3f}", actor=admin)
    if round(old, 3) != round(value, 3):
        audit.record(db, admin.id, "setting_changed", target_label=MIN_SIMILARITY_LABEL,
                     old=f"{old:.3f}", new=f"{value:.3f}")


def set_threshold(db: Session, admin: models.User, value: float) -> None:
    """Umbral editable por el administrador; el cambio queda en el Registro de cambios. No hace commit."""
    old = classifier.threshold(db)
    routing.set_setting(db, classifier.THRESHOLD_KEY, f"{value:.3f}", actor=admin)
    if round(old, 3) != round(value, 3):
        audit.record(db, admin.id, "setting_changed", target_label=THRESHOLD_LABEL,
                     old=f"{old:.3f}", new=f"{value:.3f}")
