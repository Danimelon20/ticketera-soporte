"""Ningún ticket abierto sin vencimiento: sin nivel de la IA vence como Estándar y queda en el historial."""
import json
from datetime import timedelta

import pytest

from app import chatbot, config, email_analysis, main, models
from app.database import SessionLocal


@pytest.fixture
def llm(monkeypatch):
    """Simula Ollama: nunca hay red."""
    state = {"reply": None, "error": None, "payloads": []}

    def _fake_post_chat(payload, task=None):
        state["payloads"].append(payload)
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


def test_periodic_check_is_configurable(monkeypatch):
    """0 (las pruebas): solo al arrancar; con un valor, el ciclo usa ese intervalo."""
    import asyncio

    assert config.DUE_DATES_CHECK_SECONDS == 0
    calls, waits = [], []

    async def _fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(config, "DUE_DATES_CHECK_SECONDS", 90)
    monkeypatch.setattr(main.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(main, "fill_due_dates", lambda: calls.append(1) or 0)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.due_dates_forever())
    assert waits == [90, 90] and calls == [1]


def _sent_titles(llm):
    return " ".join(p["messages"][1]["content"] for p in llm["payloads"])


def test_analyze_missing_retries_only_the_default_standard(client, make_user, make_ticket, llm, monkeypatch):
    """Solo los que quedaron en Estándar por defecto (con la línea automática); nunca un nivel puesto a mano."""
    admin, coordinator, requester = make_user("admin"), make_user("coordinator"), make_user("requester")
    tag = make_user("requester")["id"]
    defaulted = make_ticket(requester, title=f"Quedó por defecto {tag}")["id"]
    by_hand = make_ticket(requester, title=f"Estándar a mano {tag}")["id"]
    old_row = make_ticket(requester, title=f"Fila sin línea {tag}")["id"]
    # IA apagada: los tres quedan en Estándar por defecto, con su línea
    main.fill_due_dates()
    assert all(_default_lines(t) == 1 for t in (defaulted, by_hand, old_row))
    # Una persona confirma Estándar a mano
    response = client.patch(f"/api/tickets/{by_hand}/ai-level", json={"level": 2}, headers=coordinator["headers"])
    assert response.status_code == 200
    # Fila sin modelo ni la línea automática (por ejemplo, de una versión anterior)
    _detach(old_row)
    with SessionLocal() as db:
        db.add(models.TicketAIAnalysis(ticket_id=old_row, status="done", ai_level=2,
                                       due_at=email_analysis._now() + timedelta(days=1)))
        db.commit()

    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    llm["reply"] = json.dumps({"level": 4, "category": "Software", "summary": "No abre."})
    assert client.post("/api/admin/ai/analyze-missing", headers=admin["headers"]).status_code == 200
    sent = _sent_titles(llm)
    assert f"Quedó por defecto {tag}" in sent
    assert f"Estándar a mano {tag}" not in sent and f"Fila sin línea {tag}" not in sent
    assert _row(defaulted).ai_level == 4 and _row(defaulted).model is not None
    hand = _row(by_hand)
    assert hand.human_level == 2 and hand.ai_level == 2 and hand.model is None
    assert _row(old_row).ai_level == 2 and _row(old_row).model is None

    # Ya analizado: no se repite
    llm["payloads"].clear()
    client.post("/api/admin/ai/analyze-missing", headers=admin["headers"])
    assert f"Quedó por defecto {tag}" not in _sent_titles(llm)


def test_retry_without_level_keeps_a_single_line(client, make_user, make_ticket, llm, monkeypatch):
    ticket = make_ticket(make_user("requester"))["id"]
    main.fill_due_dates()
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    llm["reply"] = json.dumps({"level": None, "category": "Software", "summary": "No abre."})
    assert email_analysis.analyze_ticket_by_id(ticket) == "done"
    row = _row(ticket)
    assert row.ai_level == email_analysis.DEFAULT_LEVEL and row.model is not None
    assert _default_lines(ticket) == 1

