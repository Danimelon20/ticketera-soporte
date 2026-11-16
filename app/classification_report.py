"""Reporte «Precisión de la IA» (solo el administrador): qué tan bien clasifica la IA los correos de soporte.

Acierto: el borrador se confirmó como ticket en el departamento que eligió la IA. Los que todavía no se confirmaron
(o se descartaron) son «pendientes» y no entran en el %. Las transferencias se cuentan aparte como «rebotes»: un
coordinador puede transferir por error y otro devolverlo, y eso no es un error de la IA.

Todo sale de lo que ya se guarda: draft_classifications (lo que eligió la IA, por qué fue a «Por clasificar» y lo
que decidió el administrador) y draft_transfers (las correcciones de los coordinadores). El periodo se cuenta por
la llegada del correo (cuando se clasificó), así los números de cada correo cuadran entre sí.

Privacidad: el asunto y el motivo de la transferencia van enmascarados (correos y números largos); nunca el
cuerpo del correo.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from . import classifier, models, routing

# Por qué un correo fue a «Por clasificar» y qué ajustar. El motivo guardado «below_threshold» se separa con lo
# que quedó guardado de la IA: no eligió, no coincidió con la similitud, margen bajo o (si todo eso estaba bien)
# su departamento no tenía ejemplos
REASONS = {
    "low_margin": ("Margen bajo", "Umbral (Por clasificar)"),
    "disagree": ("La IA y la similitud no coinciden", "Fichas: ejemplos y «Qué no atiende»"),
    "low_similarity": ("Similitud baja", "Similitud mínima (Por clasificar) o fichas"),
    "no_coordinator": ("Sin coordinador activo", "Departamentos: asignar coordinador"),
    "no_profile": ("Sin ficha", "Fichas: completar «Qué atiende»"),
    "no_examples": ("Sin ejemplos en la ficha", "Fichas: agregar ejemplos"),
    "ai_none": ("La IA no eligió departamento", "Fichas, o no era de soporte"),
    "ai_error": ("La IA no respondió", "Estado del sistema (Ollama)"),
    "installed": ("Llegó antes de la clasificación", "Nada: correos anteriores al encendido"),
}
# Con menos correos en el periodo, los números no alcanzan para ajustar el umbral, la similitud mínima o las fichas
MIN_CASES = 20
STORED_REASONS = {"no_coordinator": "no_coordinator", "low_similarity": "low_similarity",
                  "no_profiles": "no_profile", "ai_error": "ai_error", "installed": "installed"}


def reason_key(row: models.DraftClassification) -> str:
    """Motivo de «Por clasificar» de una fila (con el desglose de «la IA no estuvo segura»)."""
    if row.pending_reason in STORED_REASONS:
        return STORED_REASONS[row.pending_reason]
    if row.ai_department_id is None:
        return "ai_none"
    if not row.ai_agrees:
        return "disagree"
    if row.ai_margin is not None and row.threshold is not None and row.ai_margin < row.threshold:
        return "low_margin"
    return "no_examples"


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def build(db: Session, start: datetime, end: datetime) -> dict:
    """Números del periodo [start, end) por la llegada del correo."""
    by_id = routing.departments_by_id(db)

    def label(dept_id):
        return routing.path_label(by_id.get(dept_id), by_id) if dept_id is not None else None

    found = db.query(models.DraftClassification).filter(
        models.DraftClassification.created_at >= start, models.DraftClassification.created_at < end).all()
    # «Directo» sin lo que eligió la IA no es una decisión de la IA (los borradores DEMO llegan ya clasificados):
    # no cuentan, ni sus transferencias
    without_ai = sorted(r.draft_id for r in found if r.status == "routed" and r.ai_department_id is None)
    rows = [r for r in found if r.draft_id not in set(without_ai)]
    draft_ids = [r.draft_id for r in rows]
    transfers = (db.query(models.DraftTransfer).filter(models.DraftTransfer.draft_id.in_(draft_ids))
                 .order_by(models.DraftTransfer.transferred_at.desc(), models.DraftTransfer.id.desc()).all()
                 if draft_ids else [])
    transfers_of: dict[int, list[models.DraftTransfer]] = {}
    for t in transfers:
        transfers_of.setdefault(t.draft_id, []).append(t)

    drafts = {d.id: d for d in db.query(models.EmailDraft).filter(models.EmailDraft.id.in_(draft_ids))} \
        if draft_ids else {}

    def outcome(row) -> str:
        """ai_right / ai_wrong si se confirmó (¿en el departamento de la IA?); pending si todavía no."""
        draft = drafts.get(row.draft_id)
        if draft is None or draft.status != "confirmed":
            return "pending"
        # El departamento del borrador al confirmarlo es el del ticket que nació de él
        return "ai_right" if row.department_id == row.ai_department_id else "ai_wrong"

    # Directo: por el departamento que eligió la IA
    keys = ("direct", "confirmed", "hits", "pending", "bounced", "transfers")
    per_dept: dict[int, dict] = {}
    for row in rows:
        if row.status != "routed":
            continue
        dept_id = row.ai_department_id
        item = per_dept.setdefault(dept_id, {"department_id": dept_id, "path": label(dept_id) or "—",
                                             **{k: 0 for k in keys}})
        item["direct"] += 1
        result = outcome(row)
        item["pending"] += result == "pending"
        item["confirmed"] += result != "pending"
        item["hits"] += result == "ai_right"
        count = len(transfers_of.get(row.draft_id, []))
        item["transfers"] += count
        item["bounced"] += 1 if count else 0
    departments = sorted(per_dept.values(), key=lambda d: d["path"].casefold())
    for item in departments:
        item["accuracy"] = _pct(item["hits"], item["confirmed"])
    totals = {k: sum(d[k] for d in departments) for k in keys}
    totals["accuracy"] = _pct(totals["hits"], totals["confirmed"])

    # «Por clasificar»: los que no fueron directo
    waiting = [r for r in rows if r.status != "routed"]
    decided = [r for r in waiting if r.status == "classified"]
    with_suggestion = [r for r in decided if r.matched_first is not None]
    matched = sum(1 for r in with_suggestion if r.matched_first)
    reason_counts: dict[str, int] = {}
    for row in waiting:
        key = reason_key(row)
        reason_counts[key] = reason_counts.get(key, 0) + 1
    reasons = [{"key": key, "label": text, "adjust": adjust, "count": reason_counts[key],
                "pct": _pct(reason_counts[key], len(waiting))}
               for key, (text, adjust) in REASONS.items() if reason_counts.get(key)]
    pending = {"arrived": len(waiting), "decided": len(decided), "still_waiting": len(waiting) - len(decided),
               "with_suggestion": len(with_suggestion), "matched": matched,
               "matched_pct": _pct(matched, len(with_suggestion))}

    # Correcciones, de la más reciente a la más antigua
    rows_by_draft = {r.draft_id: r for r in rows}
    users = {u.id: u for u in db.query(models.User).filter(
        models.User.id.in_({t.transferred_by for t in transfers}))} if transfers else {}
    corrections = []
    for t in transfers:
        draft = drafts.get(t.draft_id)
        actor = users.get(t.transferred_by)
        corrections.append({
            "draft_id": t.draft_id,
            "ticket_id": draft.ticket_id if draft else None,
            "draft_status": draft.status if draft else None,
            "subject": classifier.mask(draft.subject if draft else "") or "(Sin asunto)",
            "ai_department": label(t.ai_department_id),
            "origin": t.origin,
            "origin_label": models.DRAFT_TRANSFER_ORIGINS.get(t.origin, t.origin),
            "from_department": label(t.from_department_id),
            "to_department": label(t.to_department_id),
            "reason": classifier.mask(t.reason),
            "by_name": (actor.name if actor else None) or "Sin nombre",
            "at": t.transferred_at,
            # ¿Terminó donde dijo la IA? (pending: todavía no se confirmó; current: dónde está hoy)
            "outcome": outcome(rows_by_draft[t.draft_id]),
            "current_department": label(rows_by_draft[t.draft_id].department_id),
        })
    return {"routing_enforced": routing.enforced(db), "received": len(rows), "without_ai": len(without_ai),
            "without_ai_ids": without_ai,
            "few_cases": len(rows) < MIN_CASES, "min_cases": MIN_CASES, "totals": totals,
            "departments": departments, "pending": pending, "reasons": reasons, "corrections": corrections}
