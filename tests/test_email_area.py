"""Correo entrante: la IA asigna el área si está segura; si no, al departamento de entrada."""
import json

from app import models, routing
from app.database import SessionLocal
from org_helpers import area, department, routing_of, setting
from test_email_drafts import (  # noqa: F401
    CONTACT_LINES, FREE_BODY, TEMPLATE_BODY, _confirm_payload, _email, _process, _drafts, llm, sent, setup,
)
from app import email_analysis


def _answer(**extra):
    return json.dumps({"kind": "ticket", "title": "Sin internet", "category": "Red",
                       "description": "No hay internet en la oficina.", "level": 2,
                       "summary": "Sin internet.", **extra})


def _area_name(area_id):
    db = SessionLocal()
    try:
        return db.get(models.ServiceArea, area_id).name
    finally:
        db.close()


def _confirm(client, draft_id, user):
    response = client.post(f"/api/drafts/{draft_id}/confirm", json=_confirm_payload(), headers=user["headers"])
    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        return db.get(models.EmailDraft, draft_id).ticket_id
    finally:
        db.close()


def test_confident_ai_area_is_used(client, make_user, sent, llm):
    dept = department()
    area_id = area(department_ids=[dept], entry_department_id=dept, description="Internet caído")
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    assert _process(_email(requester["email"], body=FREE_BODY + "\n" + CONTACT_LINES)) == "draft_created"
    llm["replies"].append(_answer(area=_area_name(area_id), area_confidence="alta"))
    email_analysis.analyze_pending_drafts()
    draft = _drafts(requester["id"])[-1]
    # La lista de áreas va en la misma consulta a la IA
    assert _area_name(area_id) in llm["payloads"][-1]["messages"][0]["content"]
    assert routing_of(_confirm(client, draft.id, coordinator)) == (area_id, dept)


def test_unsure_ai_goes_to_entry_department(client, make_user, sent, llm):
    entry = department()
    entry_area = area(department_ids=[entry])
    other = area()
    setting(routing.ENTRY_DEPARTMENT, str(entry))
    try:
        requester = make_user("requester")
        coordinator = make_user("coordinator")
        assert _process(_email(requester["email"], body=FREE_BODY + "\n" + CONTACT_LINES)) == "draft_created"
        llm["replies"].append(_answer(area=_area_name(other), area_confidence="baja"))
        email_analysis.analyze_pending_drafts()
        draft = _drafts(requester["id"])[-1]
        assert routing_of(_confirm(client, draft.id, coordinator)) == (entry_area, entry)
    finally:
        setting(routing.ENTRY_DEPARTMENT, None)


def test_template_email_goes_to_entry_department(make_user, sent):
    entry = department()
    entry_area = area(department_ids=[entry])
    setting(routing.ENTRY_DEPARTMENT, str(entry))
    try:
        requester = make_user("requester", profile=False)
        assert _process(_email(requester["email"], body=TEMPLATE_BODY, subject="[Soporte] Planillas")) == "created"
        db = SessionLocal()
        try:
            ticket = db.query(models.Ticket).filter(models.Ticket.created_by == requester["id"]).one()
        finally:
            db.close()
        assert routing_of(ticket.id) == (entry_area, entry)
    finally:
        setting(routing.ENTRY_DEPARTMENT, None)
