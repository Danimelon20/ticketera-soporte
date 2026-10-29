"""Borradores de correo por área: cada coordinador ve los de su área; sin área clara los ven todos."""
import uuid

from app import models
from app.database import SessionLocal
from org_helpers import area, department, place


def _draft(requester_id, area_id=None, has_row=True):
    db = SessionLocal()
    try:
        draft = models.EmailDraft(message_id=f"<{uuid.uuid4().hex}@prueba>", requester_id=requester_id,
                                  subject="Sin internet", body="No hay conexión", status="pending_review",
                                  title="Sin internet", category_id=1, priority_id=1, description="No hay conexión")
        db.add(draft)
        db.flush()
        if has_row:
            # area_id None: la IA no tuvo confianza alta
            db.add(models.DraftRouting(draft_id=draft.id, area_id=area_id))
        db.commit()
        return draft.id
    finally:
        db.close()


def _setup(make_user):
    coordinator_a = make_user("coordinator", "Coordinador A")
    coordinator_b = make_user("coordinator", "Coordinador B")
    dept_a, dept_b = department(), department()
    area_a = area(coordinator_id=coordinator_a["id"], department_ids=[dept_a], entry_department_id=dept_a)
    area_b = area(coordinator_id=coordinator_b["id"], department_ids=[dept_b], entry_department_id=dept_b)
    requester = make_user("requester")
    drafts = {
        "a": _draft(requester["id"], area_a),
        "b": _draft(requester["id"], area_b),
        "low": _draft(requester["id"], None),
        "none": _draft(requester["id"], has_row=False),
    }
    return coordinator_a, coordinator_b, dept_a, dept_b, area_b, drafts


def _listed(client, user):
    return {d["id"] for d in client.get("/api/drafts", headers=user["headers"]).json()}


def test_coordinator_sees_own_area_and_unclear_drafts(client, make_user, routing_on):
    coordinator_a, coordinator_b, _, _, _, drafts = _setup(make_user)
    seen_a, seen_b = _listed(client, coordinator_a), _listed(client, coordinator_b)
    assert {drafts["a"], drafts["low"], drafts["none"]} <= seen_a and drafts["b"] not in seen_a
    assert {drafts["b"], drafts["low"], drafts["none"]} <= seen_b and drafts["a"] not in seen_b
    # Sin área asignada: solo los que no tienen área clara
    lonely = make_user("coordinator", "Sin área")
    seen = _listed(client, lonely)
    assert {drafts["low"], drafts["none"]} <= seen and not {drafts["a"], drafts["b"]} & seen


def test_other_area_draft_is_blocked_in_backend(client, make_user, routing_on):
    coordinator_a, _, dept_a, _, _, drafts = _setup(make_user)
    other = drafts["b"]
    for path in ("discard", "request-details"):
        response = client.post(f"/api/drafts/{other}/{path}", headers=coordinator_a["headers"])
        assert response.status_code == 403
        assert response.json()["detail"] == "Este borrador es de otra área"
    technician = make_user("technician")
    place(technician["id"], dept_a)
    assert client.post(f"/api/drafts/{other}/assign", headers=coordinator_a["headers"],
                       json={"user_id": technician["id"], "confirm_unavailable": True}).status_code == 403
    # Uno sin área clara sí lo puede descartar cualquiera
    assert client.post(f"/api/drafts/{drafts['low']}/discard", headers=coordinator_a["headers"]).status_code == 200


def test_assign_only_to_technician_of_draft_area(client, make_user, routing_on):
    coordinator_a, _, dept_a, dept_b, _, drafts = _setup(make_user)
    inside, outside = make_user("technician"), make_user("technician")
    place(inside["id"], dept_a)
    place(outside["id"], dept_b)
    response = client.post(f"/api/drafts/{drafts['a']}/assign", headers=coordinator_a["headers"],
                           json={"user_id": outside["id"], "confirm_unavailable": True})
    assert response.status_code == 400
    assert response.json()["detail"] == "Ese técnico no está en el área del borrador"
    assert client.post(f"/api/drafts/{drafts['a']}/assign", headers=coordinator_a["headers"],
                       json={"user_id": inside["id"], "confirm_unavailable": True}).status_code == 200


def test_menu_count_follows_draft_area(client, make_user, routing_on):
    coordinator_a, _, _, _, _, drafts = _setup(make_user)
    expected = len(_listed(client, coordinator_a))
    assert client.get("/api/nav/counts", headers=coordinator_a["headers"]).json()["drafts_pending"] == expected


def test_without_switch_everyone_sees_every_draft(client, make_user):
    coordinator_a, _, _, _, _, drafts = _setup(make_user)
    assert set(drafts.values()) <= _listed(client, coordinator_a)
