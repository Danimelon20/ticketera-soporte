"""Fase 5 de la clasificación de correos: reporte «Precisión de la IA» (solo el administrador). Directo y
corregidos por el departamento que eligió la IA, «Por clasificar» con la coincidencia del administrador, el desglose
de motivos, la lista de correcciones y el filtro por periodo (por la llegada del correo)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app import availability, classification_report, config, mailer, models
from app.database import SessionLocal
from org_helpers import team

URL = "/api/admin/classification/precision"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def clean(client):
    with SessionLocal() as db:
        db.query(models.EmailDraft).delete()
        db.commit()


@pytest.fixture
def org(make_user):
    soporte, soporte_coord, soporte_tech = team(make_user)
    redes, redes_coord, _ = team(make_user)
    return {"soporte": soporte, "redes": redes, "soporte_coord": soporte_coord, "redes_coord": redes_coord,
            "soporte_tech": soporte_tech}


def _ticket(requester):
    with SessionLocal() as db:
        ticket = models.Ticket(title="Del correo", description="x", category_id=1, priority_id=1, status="new",
                               created_by=requester["id"])
        db.add(ticket)
        db.commit()
        return ticket.id


def _draft(requester, created_at=None, ticket_id=None, draft_status=None, **classification):
    """Borrador con su clasificación; created_at es la llegada (por defecto, ahora). Con ticket_id, confirmado."""
    with SessionLocal() as db:
        draft = models.EmailDraft(message_id=f"<fase5-{uuid4().hex}@test>", requester_id=requester["id"],
                                  status=draft_status or ("confirmed" if ticket_id else "pending_review"),
                                  subject="Correo de cliente@empresa.test", body="Texto", ticket_id=ticket_id)
        db.add(draft)
        db.flush()
        db.add(models.DraftClassification(draft_id=draft.id, created_at=created_at or _utcnow(),
                                          **classification))
        db.commit()
        return draft.id


def _transfer(draft_id, by, ai_id, from_id, to_id, origin="ai", reason="Era de red, no de equipos 8888-1234"):
    with SessionLocal() as db:
        db.add(models.DraftTransfer(draft_id=draft_id, ai_department_id=ai_id, origin=origin,
                                    from_department_id=from_id, to_department_id=to_id, reason=reason,
                                    transferred_by=by["id"], transferred_at=_utcnow()))
        db.commit()


def _routed(requester, dept, final=None, confirmed=False, **extra):
    """Directo al departamento que eligió la IA (dept); final: donde está (o se confirmó) después de transferir."""
    if confirmed:
        extra["ticket_id"] = _ticket(requester)
    return _draft(requester, status="routed", department_id=final or dept, ai_department_id=dept, ai_agrees=True,
                  ai_margin=0.2, threshold=0.05, **extra)


def _report(client, admin, **params):
    response = client.get(URL, headers=admin["headers"], params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_accuracy_counts_where_the_draft_was_confirmed(client, make_user, org, routing_on):
    """Acierto: se confirmó en el departamento que eligió la IA. Las transferencias son rebotes aparte: el teclado
    que una coordinadora mandó a Redes por error y Redes devolvió sigue siendo un acierto de la IA."""
    admin, requester = make_user("admin"), make_user("requester")
    soporte, redes = org["soporte"], org["redes"]
    _routed(requester, soporte, confirmed=True)
    keyboard = _routed(requester, soporte, confirmed=True)
    _transfer(keyboard, org["soporte_coord"], soporte, soporte, redes)
    _transfer(keyboard, org["redes_coord"], soporte, redes, soporte, origin="transfer")
    wrong = _routed(requester, soporte, final=redes, confirmed=True)
    _transfer(wrong, org["soporte_coord"], soporte, soporte, redes)
    # Sin confirmar o descartados: pendientes, fuera del %
    _routed(requester, soporte)
    _routed(requester, soporte, draft_status="discarded")
    _routed(requester, redes, confirmed=True)
    data = _report(client, admin)
    rows = {d["department_id"]: d for d in data["departments"]}
    assert rows[soporte] == {**rows[soporte], "direct": 5, "confirmed": 3, "hits": 2, "accuracy": 66.7,
                             "pending": 2, "bounced": 2, "transfers": 3}
    assert rows[redes] == {**rows[redes], "direct": 1, "confirmed": 1, "hits": 1, "accuracy": 100.0,
                           "pending": 0, "bounced": 0, "transfers": 0}
    assert data["totals"] == {"direct": 6, "confirmed": 4, "hits": 3, "pending": 2, "bounced": 2, "transfers": 3,
                              "accuracy": 75.0}
    # En la lista: el teclado terminó donde dijo la IA; el otro, no
    outcomes = {(c["draft_id"], c["outcome"]) for c in data["corrections"]}
    assert outcomes == {(keyboard, "ai_right"), (wrong, "ai_wrong")}


def test_only_pending_drafts_have_no_accuracy(client, make_user, org, routing_on):
    admin, requester = make_user("admin"), make_user("requester")
    draft_id = _routed(requester, org["soporte"])
    _transfer(draft_id, org["soporte_coord"], org["soporte"], org["soporte"], org["redes"])
    data = _report(client, admin)
    assert data["totals"]["accuracy"] is None and data["totals"]["pending"] == 1
    [item] = data["corrections"]
    assert item["outcome"] == "pending" and item["current_department"] == item["from_department"]


def test_few_cases_warn_instead_of_recommending(client, make_user, org, routing_on):
    admin, requester = make_user("admin"), make_user("requester")
    for _ in range(3):
        _routed(requester, org["soporte"])
    data = _report(client, admin)
    assert data["few_cases"] is True and data["min_cases"] == 20
    for _ in range(17):
        _routed(requester, org["soporte"])
    assert _report(client, admin)["few_cases"] is False


def test_por_clasificar_matches_and_reasons(client, make_user, org, routing_on):
    admin, requester = make_user("admin"), make_user("requester")
    common = {"status": "classified", "department_id": org["soporte"], "decided_by": admin["id"]}
    # Decididos: dos coincidieron con la 1.ª opción, uno no, uno sin sugerencia
    _draft(requester, **common, matched_first=True, pending_reason="below_threshold", ai_department_id=org["soporte"],
           ai_agrees=True, ai_margin=0.01, threshold=0.05)
    _draft(requester, **common, matched_first=True, pending_reason="below_threshold", ai_department_id=org["soporte"],
           ai_agrees=False, ai_margin=0.2, threshold=0.05)
    _draft(requester, **common, matched_first=False, pending_reason="low_similarity", ai_department_id=org["redes"],
           ai_agrees=True, ai_margin=0.2, threshold=0.05)
    _draft(requester, **common, matched_first=None, pending_reason="ai_error")
    # Todavía esperando
    _draft(requester, status="pending", pending_reason="no_coordinator", ai_department_id=org["redes"],
           ai_agrees=True, ai_margin=0.2, threshold=0.05)
    _draft(requester, status="pending", pending_reason="no_profiles")
    _draft(requester, status="pending", pending_reason="below_threshold", ai_department_id=org["redes"],
           ai_agrees=True, ai_margin=0.2, threshold=0.05)
    _draft(requester, status="pending", pending_reason="below_threshold", ai_department_id=None)
    _draft(requester, status="pending", pending_reason="installed")
    data = _report(client, admin)
    assert data["pending"] == {"arrived": 9, "decided": 4, "still_waiting": 5, "with_suggestion": 3, "matched": 2,
                               "matched_pct": 66.7}
    reasons = {r["key"]: r["count"] for r in data["reasons"]}
    assert reasons == {"low_margin": 1, "disagree": 1, "low_similarity": 1, "ai_error": 1, "no_coordinator": 1,
                       "no_profile": 1, "no_examples": 1, "ai_none": 1, "installed": 1}
    # Cada motivo dice qué ajustar
    adjust = {r["key"]: r["adjust"] for r in data["reasons"]}
    assert "Umbral" in adjust["low_margin"] and "Similitud mínima" in adjust["low_similarity"]
    assert "Fichas" in adjust["disagree"] and "coordinador" in adjust["no_coordinator"]
    assert data["totals"]["direct"] == 0 and data["departments"] == []


def test_corrections_list_is_masked_and_links_the_ticket(client, make_user, org, routing_on):
    admin, requester = make_user("admin"), make_user("requester")
    with SessionLocal() as db:
        ticket = models.Ticket(title="Red", description="x", category_id=1, priority_id=1, status="new",
                               created_by=requester["id"])
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
    draft_id = _routed(requester, org["soporte"], ticket_id=ticket_id)
    _transfer(draft_id, org["soporte_coord"], org["soporte"], org["soporte"], org["redes"])
    [item] = _report(client, admin)["corrections"]
    assert item["draft_id"] == draft_id and item["ticket_id"] == ticket_id and item["draft_status"] == "confirmed"
    assert item["origin"] == "ai" and item["origin_label"] == "La IA"
    assert item["ai_department"] and item["from_department"] == item["ai_department"] and item["to_department"]
    assert item["by_name"] == org["soporte_coord"]["name"] and item["at"]
    # Ni el correo ni el teléfono en claro
    assert "cliente@empresa.test" not in item["subject"] and "8888-1234" not in item["reason"]


def test_period_counts_by_arrival(client, make_user, org, routing_on):
    admin, requester = make_user("admin"), make_user("requester")
    today = availability.office_today()
    old = _utcnow() - timedelta(days=40)
    _routed(requester, org["soporte"], created_at=old)
    recent = _routed(requester, org["soporte"])
    assert _report(client, admin)["totals"]["direct"] == 1
    assert _report(client, admin, **{"from": (today - timedelta(days=7)).isoformat()})["totals"]["direct"] == 1
    data = _report(client, admin, **{"from": (today - timedelta(days=90)).isoformat(), "to": today.isoformat()})
    assert data["totals"]["direct"] == 2 and data["date_to"] == today.isoformat()
    # Entre fechas: solo el antiguo
    day = (old + timedelta(hours=12)).date()
    data = _report(client, admin, **{"from": (day - timedelta(days=1)).isoformat(), "to": (day + timedelta(days=1)).isoformat()})
    assert data["totals"]["direct"] == 1 and recent
    bad = client.get(URL, headers=admin["headers"], params={"from": today.isoformat(),
                                                           "to": (today - timedelta(days=1)).isoformat()})
    assert bad.status_code == 400


def test_only_the_admin(client, make_user, routing_on):
    for role in ("coordinator", "technician", "requester"):
        assert client.get(URL, headers=make_user(role)["headers"]).status_code == 403


def test_reason_key_follows_the_stored_fields():
    row = models.DraftClassification(pending_reason="below_threshold", ai_department_id=1, ai_agrees=True,
                                     ai_margin=0.01, threshold=0.05)
    assert classification_report.reason_key(row) == "low_margin"
    row.ai_margin = 0.2
    assert classification_report.reason_key(row) == "no_examples"
    row.ai_agrees = False
    assert classification_report.reason_key(row) == "disagree"
    row.ai_department_id = None
    assert classification_report.reason_key(row) == "ai_none"


def test_direct_without_ai_data_is_not_counted(client, make_user, org, routing_on):
    """Los borradores DEMO llegan «directo» sin lo que eligió la IA: no son aciertos ni errores de la IA."""
    admin, requester = make_user("admin"), make_user("requester")
    demo = _draft(requester, status="routed", department_id=org["soporte"])
    _transfer(demo, org["soporte_coord"], None, org["soporte"], org["redes"])
    _routed(requester, org["redes"])
    data = _report(client, admin)
    assert data["received"] == 1 and data["without_ai"] == 1 and data["without_ai_ids"] == [demo]
    assert data["totals"]["direct"] == 1 and data["totals"]["transfers"] == 0
    assert data["corrections"] == []


def test_admin_decision_keeps_the_reason(client, make_user, org, routing_on):
    """Decidir en «Por clasificar» ya no borra el motivo: el desglose lo necesita."""
    admin, requester = make_user("admin"), make_user("requester")
    draft_id = _draft(requester, status="pending", pending_reason="no_coordinator", ai_department_id=org["redes"],
                      ai_agrees=True, ai_margin=0.2, threshold=0.05, option1_id=org["redes"])
    response = client.post(f"/api/admin/drafts/{draft_id}/department", headers=admin["headers"],
                           json={"department_id": org["redes"]})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        row = db.get(models.DraftClassification, draft_id)
        assert row.status == "classified" and row.pending_reason == "no_coordinator"
    assert {r["key"] for r in _report(client, admin)["reasons"]} == {"no_coordinator"}


def test_confirmed_through_the_coordinator_counts_as_a_hit(client, make_user, org, routing_on, monkeypatch):
    """De punta a punta, por las mismas rutas que la página: la IA lo manda a Soporte N1, la coordinadora lo
    transfiere a Redes por error, Redes lo devuelve y la coordinadora lo confirma con un técnico de Soporte N1
    (Fase 3). Es un acierto de la IA, con un rebote de dos transferencias."""
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    monkeypatch.setattr(mailer, "send_email", lambda *args, **kwargs: True)
    admin, requester = make_user("admin"), make_user("requester")
    draft_id = _routed(requester, org["soporte"])
    transfers = ((org["soporte_coord"], org["redes"], "El teclado es inalámbrico y pierde la conexión"),
                 (org["redes_coord"], org["soporte"], "Revisado: el teclado está dañado, no es de red"))
    for user, target, reason in transfers:
        response = client.post(f"/api/drafts/{draft_id}/transfer", headers=user["headers"],
                               json={"department_id": target, "reason": reason})
        assert response.status_code == 200, response.text
    # Antes de confirmar: pendiente, fuera del %
    before = {d["department_id"]: d for d in _report(client, admin)["departments"]}[org["soporte"]]
    assert before["pending"] == 1 and before["confirmed"] == 0 and before["accuracy"] is None
    response = client.post(f"/api/drafts/{draft_id}/confirm", headers=org["soporte_coord"]["headers"],
                           json={"title": "Teclado", "description": "No responde.", "category_id": 1,
                                 "priority_id": 1, "assigned_to": org["soporte_tech"]["id"]})
    assert response.status_code == 200, response.text
    data = _report(client, admin)
    row = {d["department_id"]: d for d in data["departments"]}[org["soporte"]]
    assert row == {**row, "direct": 1, "confirmed": 1, "hits": 1, "accuracy": 100.0, "pending": 0,
                   "bounced": 1, "transfers": 2}
    mine = [c for c in data["corrections"] if c["draft_id"] == draft_id]
    assert len(mine) == 2 and {c["outcome"] for c in mine} == {"ai_right"}
    assert all(c["ticket_id"] == response.json()["ticket_id"] for c in mine)
