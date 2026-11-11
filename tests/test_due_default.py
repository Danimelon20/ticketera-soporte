"""Ningún ticket abierto sin vencimiento: sin nivel de la IA vence como Estándar y queda en el historial."""
import json
from datetime import timedelta

import pytest

from app import chatbot, config, email_analysis, main, models
from app.database import SessionLocal


@pytest.fixture
def llm(monkeypatch):
    """Simula Ollama: nunca hay red."""
    state = {"reply": None, "error": None}

    def _fake_post_chat(payload, task=None):
        if state["error"] is not None:
            raise state["error"]
        return state["reply"]

    monkeypatch.setattr(chatbot, "post_chat", _fake_post_chat)
    return state


def _row(ticket_id):
    with SessionLocal() as db:
        return db.query(models.TicketAIAnalysis).filter(models.TicketAIAnalysis.ticket_id == ticket_id).first()


def _default_lines(ticket_id):
    with SessionLocal() as db:
        return db.query(models.History).filter(models.History.ticket_id == ticket_id,
                                               models.History.field == email_analysis.DEFAULT_LEVEL_FIELD).count()


def _analyze(ticket_id):
    with SessionLocal() as db:
        return email_analysis.analyze_ticket(db, db.get(models.Ticket, ticket_id))


def _detach(ticket_id):
    """Quita el análisis para simular un ticket que la IA todavía no vio."""
    with SessionLocal() as db:
        db.query(models.History).filter(models.History.ticket_id == ticket_id).delete()
        db.query(models.TicketAIAnalysis).filter(models.TicketAIAnalysis.ticket_id == ticket_id).delete()
        db.commit()


def test_ai_off_web_ticket_gets_standard_due_and_history(client, make_user, make_ticket):
    requester, coordinator = make_user("requester"), make_user("coordinator")
    ticket = make_ticket(requester)
    # Con la IA apagada, la revisión periódica (al arrancar y cada minuto) le pone Estándar
    assert main.fill_due_dates() >= 1
    row = _row(ticket["id"])
    assert row.ai_level == email_analysis.DEFAULT_LEVEL and row.due_at is not None and row.needs_review
    assert _default_lines(ticket["id"]) == 1
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["ai_analysis"]["due_at"] is not None and detail["due_state"]
    assert any(h["field"] == "level_default" and "estándar" in h["new_value"] for h in detail["history"])
    # El solicitante no ve esa línea interna
    mine = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"]).json()
    assert all(h["field"] != "level_default" for h in mine["history"])
    # Una segunda vuelta no repite la línea
    main.fill_due_dates()
    assert _default_lines(ticket["id"]) == 1


@pytest.mark.parametrize("level", [None, 0])
def test_ai_without_level_uses_standard(client, make_user, make_ticket, llm, level):
    ticket = make_ticket(make_user("requester"))
    _detach(ticket["id"])
    llm["reply"] = json.dumps({"level": level, "category": "Software", "summary": "No abre."})
    assert _analyze(ticket["id"]) == "done"
    row = _row(ticket["id"])
    assert row.ai_level == email_analysis.DEFAULT_LEVEL and row.due_at is not None
    assert _default_lines(ticket["id"]) == 1


def test_ai_with_level_keeps_it_without_history(client, make_user, make_ticket, llm):
    ticket = make_ticket(make_user("requester"))
    _detach(ticket["id"])
    llm["reply"] = json.dumps({"level": 4, "category": "Software", "summary": "No abre."})
    _analyze(ticket["id"])
    row = _row(ticket["id"])
    assert row.ai_level == 4 and row.due_at is not None
    assert _default_lines(ticket["id"]) == 0


def test_failed_analysis_uses_standard(client, make_user, make_ticket, llm):
    ticket = make_ticket(make_user("requester"))
    _detach(ticket["id"])
    with SessionLocal() as db:
        db.add(models.TicketAIAnalysis(ticket_id=ticket["id"], status="pending",
                                       attempts=email_analysis.MAX_ATTEMPTS - 1))
        db.commit()
    llm["error"] = chatbot.ChatbotUnavailable()
    assert _analyze(ticket["id"]) == "error"
    row = _row(ticket["id"])
    assert row.ai_level == email_analysis.DEFAULT_LEVEL and row.due_at is not None


def test_backfill_leaves_no_open_ticket_without_due(client, make_user, make_ticket, monkeypatch):
    requester = make_user("requester")
    open_ticket, closed_ticket, waiting = (make_ticket(requester) for _ in range(3))
    for ticket in (open_ticket, closed_ticket, waiting):
        _detach(ticket["id"])
    with SessionLocal() as db:
        db.get(models.Ticket, closed_ticket["id"]).status = "closed"
        # Análisis terminado sin vencimiento (versión anterior)
        db.add(models.TicketAIAnalysis(ticket_id=open_ticket["id"], status="done", ai_level=None, model="viejo"))
        db.commit()
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    with SessionLocal() as db:
        email_analysis.fill_missing_due_dates(db)
    assert _row(open_ticket["id"]).due_at is not None and _default_lines(open_ticket["id"]) == 1
    assert _row(closed_ticket["id"]) is None
    # Recién creado sin análisis y con la IA encendida: espera al análisis
    assert _row(waiting["id"]) is None
    with SessionLocal() as db:
        later = email_analysis._now() + timedelta(minutes=email_analysis.MISSING_ANALYSIS_MINUTES + 1)
        email_analysis.fill_missing_due_dates(db, now=later)
        open_count = db.query(models.Ticket).outerjoin(models.TicketAIAnalysis).filter(
            models.Ticket.status.in_(email_analysis.TICKET_OPEN_STATUSES),
            # Los "pending" esperan al análisis de la IA (encendida)
            models.TicketAIAnalysis.id.is_(None)
            | ((models.TicketAIAnalysis.status != "pending") & models.TicketAIAnalysis.due_at.is_(None)),
        ).count()
    assert _row(waiting["id"]).due_at is not None
    assert open_count == 0


def test_human_level_zero_keeps_level_and_standard_due(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    ticket = make_ticket(make_user("requester"))
    main.fill_due_dates()
    data = client.patch(f"/api/tickets/{ticket['id']}/ai-level", json={"level": 0},
                        headers=coordinator["headers"]).json()
    assert data["human_level"] == 0 and data["due_at"] is not None
