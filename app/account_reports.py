"""Reportes de cuentas sospechosas: solo los ve el personal, nunca el solicitante."""
from sqlalchemy.orm import Session

from . import identity, models

# Abierto o confirmado: la cuenta lleva la etiqueta "Reportada"
FLAGGED_STATUSES = ("open", "confirmed")


def reported_ids(db: Session, user_ids: list[int]) -> set[int]:
    if not user_ids:
        return set()
    rows = (
        db.query(models.AccountReport.target_user_id)
        .filter(
            models.AccountReport.target_user_id.in_(user_ids),
            models.AccountReport.status.in_(FLAGGED_STATUSES),
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def staff_flags(db: Session, user: models.User | None) -> dict | None:
    """Etiquetas para el personal sobre un solicitante: identidad de Hacienda y si está reportado."""
    if user is None or user.role != "requester":
        return None
    return {"identity": identity.hacienda_label_code(db, user), "reported": bool(reported_ids(db, [user.id]))}
