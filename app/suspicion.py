"""Señales automáticas de posible suplantación: cédula de otra cuenta y nombre que no coincide con Hacienda.

El Agente virtual abre un reporte de cuenta que revisa el administrador. Mientras está abierto no pone
la etiqueta "Reportada" (account_reports.reported_ids): solo cuando el administrador lo confirma.
Un evento suma un solo intento. Los intentos siguientes no abren otro reporte: se suman al abierto
(AutoReportActivity). Nunca se guarda la cédula completa y el solicitante nunca se entera.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import audit, models

SOURCES = {
    "profile": "perfil",
    "email": "correo",
    "draft": "borrador del correo",
    "staff": "cambio del personal",
}
# Cédulas ajenas distintas a partir de las cuales el reporte se marca como señal fuerte
STRONG_DISTINCT_IDS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _owner_id(db: Session, id_number: str, user_id: int) -> int | None:
    row = (
        db.query(models.User.id)
        .filter(models.User.id_number == id_number, models.User.id != user_id)
        .first()
    )
    return row[0] if row else None


def _add_owner(db: Session, report: models.AccountReport, owner_id: int | None) -> None:
    if owner_id is None:
        return
    exists = db.query(models.AutoReportIdOwner.id).filter(
        models.AutoReportIdOwner.report_id == report.id, models.AutoReportIdOwner.owner_user_id == owner_id,
    ).first()
    if exists is None:
        db.add(models.AutoReportIdOwner(report_id=report.id, owner_user_id=owner_id))


def auto_report(db: Session, user: models.User, reason: str, comment: str, owner_id: int | None = None) -> None:
    """Un intento más en el reporte automático abierto de la cuenta, o uno nuevo si no hay. No hace commit.

    owner_id: cuenta dueña de la cédula ajena que se intentó usar (para contar cédulas distintas).
    """
    agent_id = audit.agent_id(db)
    if agent_id is None or user.role != "requester":
        return
    now = _now()
    report = (
        db.query(models.AccountReport)
        .filter(
            models.AccountReport.target_user_id == user.id,
            models.AccountReport.reporter_id == agent_id,
            models.AccountReport.status == "open",
        )
        .first()
    )
    if report is None:
        report = models.AccountReport(
            target_user_id=user.id, reporter_id=agent_id, reason=reason, comment=comment[:500],
            status="open", created_at=now,
        )
        db.add(report)
        db.flush()
        db.add(models.AutoReportActivity(report_id=report.id, attempts=1, last_attempt_at=now))
        _add_owner(db, report, owner_id)
        audit.record(db, agent_id, "report_created", target=user, new=models.REPORT_REASONS[reason])
        # La sesión no hace autoflush: sin esto un segundo aviso en la misma petición no lo vería
        db.flush()
        return

    activity = db.query(models.AutoReportActivity).filter(models.AutoReportActivity.report_id == report.id).first()
    if activity is None:
        activity = models.AutoReportActivity(report_id=report.id, attempts=1, last_attempt_at=now)
        db.add(activity)
    if report.reason == reason:
        activity.attempts += 1
        activity.last_attempt_at = now
        _add_owner(db, report, owner_id)
    else:
        # La base admite un solo reporte abierto del Agente por cuenta: el otro motivo se anota en el mismo
        extra = f"También: {models.REPORT_REASONS[reason]}."
        if extra not in (report.comment or ""):
            report.comment = f"{report.comment or ''}\n{extra}".strip()[:500]
        activity.last_attempt_at = now
    db.flush()


def id_number_conflict(
    db: Session, user: models.User, id_number: str, source: str, actor_id: int | None = None, report: bool = True,
) -> None:
    """Intento de usar una cédula que ya está en otra cuenta: al registro y, si report, reporte. No hace commit.

    actor_id: quién hizo el intento (sin indicar, el Agente virtual, que procesa los correos).
    """
    masked = audit.masked_digits(id_number)
    origin = SOURCES[source]
    audit.record(
        db, actor_id if actor_id is not None else audit.agent_id(db), "id_number_conflict",
        target=user, new=f"{masked} ({origin})",
    )
    if report:
        owner_id = _owner_id(db, id_number, user.id)
        owner = f"la cuenta #{owner_id}" if owner_id is not None else "otra cuenta"
        auto_report(
            db, user, "foreign_id",
            f"Intentó usar la cédula {masked}, que ya pertenece a {owner} (origen: {origin}). "
            "Revise las dos cuentas: el suplantador puede ser cualquiera de ellas.",
            owner_id=owner_id,
        )


def name_mismatch(db: Session, user: models.User, source: str) -> None:
    """Hacienda dice que el nombre no corresponde a la cédula y lo causó el cliente. No hace commit."""
    auto_report(
        db, user, "name_mismatch",
        f"El nombre del perfil no coincide con el de Hacienda para su cédula (origen: {SOURCES[source]}).",
    )
