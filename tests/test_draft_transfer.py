"""Fase 4 de la clasificación de correos: el coordinador transfiere un borrador de su departamento a otro que
atiende tickets y tiene coordinador activo, con motivo (mínimo 15 caracteres). El borrador llega completo al otro
coordinador con aviso en su campana, el anterior deja de verlo (403) y queda como corrección de la IA. Solo con
el interruptor encendido."""
from datetime import datetime
from uuid import uuid4

import pytest

from app import config, mailer, models, nav
from app.database import SessionLocal
from app.due_notices import items_for, mark_read
from app.routers import drafts as drafts_router
from org_helpers import coordinate, department, team, unique

REASON = "Es un problema de red, no de equipos"


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    monkeypatch.setattr(mailer, "send_email", lambda *args, **kwargs: True)
    with SessionLocal() as db:
        db.query(models.EmailDraft).delete()
        db.commit()


@pytest.fixture
def org(make_user):
    soporte, soporte_coord, soporte_tech = team(make_user)
    redes, redes_coord, redes_tech = team(make_user)
    return {"soporte": soporte, "redes": redes, "soporte_coord": soporte_coord, "redes_coord": redes_coord,
            "soporte_tech": soporte_tech, "redes_tech": redes_tech}


def _draft(requester, department_id, status="routed", ai_department_id=None):
    """Borrador pendiente clasificado en un departamento, con un adjunto y una nota."""
    with SessionLocal() as db:
        draft = models.EmailDraft(
            message_id=f"<fase4-{uuid4().hex}@test>", requester_id=requester["id"], status="pending_review",
            subject="Sin conexión", body="Desde la mañana no tengo conexión en el piso 3", title="Sin conexión",
            description="No hay conexión.", category_id=1, priority_id=1, ai_level=2,
        )
        db.add(draft)
        db.flush()
        db.add(models.DraftClassification(
            draft_id=draft.id, status=status, department_id=department_id,
            ai_department_id=ai_department_id if ai_department_id is not None else department_id,
            created_at=datetime.now()))
        db.add(models.DraftAttachment(draft_id=draft.id, original_name="captura.png", content_type="image/png",
                                      size=10, sha256="a" * 64, stored_name=uuid4().hex))
        db.add(models.DraftAttachmentNote(draft_id=draft.id, text="Se omitió un enlace a la nube"))
        db.commit()
        return draft.id


def _transfer(client, user, draft_id, department_id, reason=REASON):
    return client.post(f"/api/drafts/{draft_id}/transfer", headers=user["headers"],
                       json={"department_id": department_id, "reason": reason})


def _ids(client, user):
    response = client.get("/api/drafts", headers=user["headers"])
    assert response.status_code == 200, response.text
    return [d["id"] for d in response.json()]


def test_transfer_moves_the_whole_draft_to_the_other_coordinator(client, make_user, org, routing_on):
    requester = make_user("requester")
    draft_id = _draft(requester, org["soporte"])
    response = _transfer(client, org["soporte_coord"], draft_id, org["redes"])
    assert response.status_code == 200, response.text
    assert response.json()["draft_id"] == draft_id and response.json()["department_name"]

    # El anterior deja de verlo: no está en su lista y abrirlo da 403
    assert draft_id not in _ids(client, org["soporte_coord"])
    for method, path in (("get", f"/api/drafts/{draft_id}"), ("post", f"/api/drafts/{draft_id}/discard")):
        assert getattr(client, method)(path, headers=org["soporte_coord"]["headers"]).status_code == 403

    # El nuevo lo ve completo: texto, adjuntos, notas, quién lo envió y de dónde vino
    detail = client.get(f"/api/drafts/{draft_id}", headers=org["redes_coord"]["headers"]).json()
    assert detail["body"] == "Desde la mañana no tengo conexión en el piso 3"
    assert detail["requester_id"] == requester["id"] and detail["requester_name"]
    assert [a["original_name"] for a in detail["attachments"]] == ["captura.png"]
    assert detail["attachment_notes"] == ["Se omitió un enlace a la nube"]
    assert detail["contact_phone"] and detail["contact_id_number"]
    assert detail["can_review"] is True and detail["can_transfer"] is True
    assert detail["transfer_note"].startswith("Transferido desde ") and REASON in detail["transfer_note"]
    assert draft_id in _ids(client, org["redes_coord"])
    with SessionLocal() as db:
        assert nav.counts(db, db.get(models.User, org["redes_coord"]["id"]))["drafts_pending"] == 1
        assert nav.counts(db, db.get(models.User, org["soporte_coord"]["id"]))["drafts_pending"] == 0
    # Y lo puede confirmar con un técnico de su departamento
    confirmed = client.post(f"/api/drafts/{draft_id}/confirm", headers=org["redes_coord"]["headers"],
                            json={"title": "Sin conexión", "description": "No hay conexión.", "category_id": 1,
                                  "priority_id": 1, "assigned_to": org["redes_tech"]["id"]})
    assert confirmed.status_code == 200, confirmed.text


def test_transfer_is_recorded_as_an_ai_correction(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["soporte"])
    _transfer(client, org["soporte_coord"], draft_id, org["redes"])
    with SessionLocal() as db:
        [row] = db.query(models.DraftTransfer).filter(models.DraftTransfer.draft_id == draft_id).all()
        assert row.ai_department_id == org["soporte"] and row.origin == "ai"
        assert row.from_department_id == org["soporte"] and row.to_department_id == org["redes"]
        assert row.transferred_by == org["soporte_coord"]["id"] and row.transferred_at is not None
        assert row.reason == REASON
    # Otra vez, desde el destino: el origen ahora es otra transferencia
    _transfer(client, org["redes_coord"], draft_id, org["soporte"], reason="Vuelve: era el equipo del usuario")
    with SessionLocal() as db:
        origins = [r.origin for r in db.query(models.DraftTransfer).filter(
            models.DraftTransfer.draft_id == draft_id).order_by(models.DraftTransfer.id)]
        assert origins == ["ai", "transfer"]


def test_origin_is_the_admin_when_the_admin_classified_it(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["soporte"], status="classified", ai_department_id=org["redes"])
    assert _transfer(client, org["soporte_coord"], draft_id, org["redes"]).status_code == 200
    with SessionLocal() as db:
        row = db.query(models.DraftTransfer).filter(models.DraftTransfer.draft_id == draft_id).one()
        assert row.origin == "admin" and row.ai_department_id == org["redes"]


def test_the_other_coordinator_gets_a_notice_in_the_bell(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["soporte"])
    _transfer(client, org["soporte_coord"], draft_id, org["redes"])
    with SessionLocal() as db:
        coordinator = db.get(models.User, org["redes_coord"]["id"])
        [item] = [i for i in items_for(db, coordinator) if i["kind"] == "draft_transferred"]
        assert item["draft_id"] == draft_id and item["read"] is False and REASON in item["text"]
        # Sin teléfono ni correo en el aviso
        assert "@" not in item["text"]
        mark_read(db, coordinator, draft_id=draft_id)
        [item] = [i for i in items_for(db, coordinator) if i["kind"] == "draft_transferred"]
        assert item["read"] is True
        # El anterior no recibe nada
        previous = db.get(models.User, org["soporte_coord"]["id"])
        assert not [i for i in items_for(db, previous) if i["kind"] == "draft_transferred"]
    response = client.get("/api/notifications/overdue", headers=org["redes_coord"]["headers"])
    assert response.status_code == 200 and any(i["kind"] == "draft_transferred" for i in response.json()["items"])
    # Si se vuelve a transferir, el aviso deja de ser suyo
    _transfer(client, org["redes_coord"], draft_id, org["soporte"], reason="Vuelve: era el equipo del usuario")
    with SessionLocal() as db:
        coordinator = db.get(models.User, org["redes_coord"]["id"])
        assert not [i for i in items_for(db, coordinator) if i["kind"] == "draft_transferred"]


def test_options_disable_own_department_and_those_without_coordinator(client, make_user, org, routing_on):
    without = department(unique("Sin coordinador"))
    response = client.get("/api/drafts/transfer-options", headers=org["soporte_coord"]["headers"])
    assert response.status_code == 200, response.text
    by_id = {o["id"]: o for o in response.json()}
    assert by_id[org["redes"]]["enabled"] is True and by_id[org["redes"]]["disabled_reason"] is None
    assert by_id[org["soporte"]] == {**by_id[org["soporte"]], "enabled": False, "disabled_reason": "Es su departamento"}
    assert by_id[without]["enabled"] is False and by_id[without]["disabled_reason"] == "Sin coordinador activo"
    # Los que no atienden tickets no aparecen
    assert department(unique("Archivo"), handles=False) not in {o["id"] for o in client.get(
        "/api/drafts/transfer-options", headers=org["soporte_coord"]["headers"]).json()}


def test_transfer_validations(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["soporte"])
    coordinator = org["soporte_coord"]
    # Motivo corto (también con espacios de relleno)
    assert _transfer(client, coordinator, draft_id, org["redes"], reason="Es de red").status_code == 422
    assert _transfer(client, coordinator, draft_id, org["redes"], reason="  corto      ").status_code == 422
    # El mismo departamento
    response = _transfer(client, coordinator, draft_id, org["soporte"])
    assert response.status_code == 400 and response.json()["detail"] == drafts_router.TRANSFER_SAME
    # Sin coordinador activo
    without = department(unique("Sin coordinador"))
    response = _transfer(client, coordinator, draft_id, without)
    assert response.status_code == 409 and response.json()["detail"] == drafts_router.TRANSFER_NO_COORDINATOR
    inactive_coord = make_user("coordinator")
    inactive_dept = department(unique("Coordinador inactivo"))
    coordinate(inactive_coord["id"], inactive_dept)
    with SessionLocal() as db:
        db.get(models.User, inactive_coord["id"]).is_active = False
        db.commit()
    assert _transfer(client, coordinator, draft_id, inactive_dept).status_code == 409
    # No atiende tickets o no existe
    assert _transfer(client, coordinator, draft_id, department(handles=False)).status_code == 422
    assert _transfer(client, coordinator, draft_id, 999999).status_code in (404, 422)
    with SessionLocal() as db:
        assert db.query(models.DraftTransfer).filter(models.DraftTransfer.draft_id == draft_id).count() == 0
        assert db.get(models.DraftClassification, draft_id).department_id == org["soporte"]


def test_only_the_coordinator_of_the_department_can_transfer(client, make_user, org, routing_on):
    draft_id = _draft(make_user("requester"), org["soporte"])
    # Otro coordinador, el técnico, el administrador y el solicitante
    assert _transfer(client, org["redes_coord"], draft_id, org["redes"]).status_code == 403
    for role in ("technician", "admin"):
        assert _transfer(client, make_user(role), draft_id, org["redes"]).status_code == 403
    # «Por clasificar» lo decide el administrador
    waiting = _draft(make_user("requester"), None, status="pending")
    assert _transfer(client, org["soporte_coord"], waiting, org["redes"]).status_code == 403
    # Ya confirmado o descartado
    with SessionLocal() as db:
        db.get(models.EmailDraft, draft_id).status = "discarded"
        db.commit()
    assert _transfer(client, org["soporte_coord"], draft_id, org["redes"]).status_code == 409


def test_switch_off_has_no_transfer(client, make_user, org, routing_switch):
    draft_id = _draft(make_user("requester"), org["soporte"])
    response = _transfer(client, org["soporte_coord"], draft_id, org["redes"])
    assert response.status_code == 409 and response.json()["detail"] == drafts_router.TRANSFER_OFF
    assert client.get("/api/drafts/transfer-options", headers=org["soporte_coord"]["headers"]).status_code == 409
    drafts = client.get("/api/drafts", headers=org["soporte_coord"]["headers"]).json()
    assert all(d["can_transfer"] is False and d["transfer_note"] is None for d in drafts)
