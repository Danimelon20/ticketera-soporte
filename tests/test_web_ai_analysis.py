import json

import pytest

from app import chatbot, config, email_analysis, mailer, models
from app.database import SessionLocal

OUTAGE = "Desde esta mañana nadie del segundo piso tiene internet. El router muestra una luz roja."


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setattr(config, "OFFICE_DAYS", "1-5")
    monkeypatch.setattr(config, "OFFICE_START", "08:00")
    monkeypatch.setattr(config, "OFFICE_END", "17:00")
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake_send(to, subject, body):
        calls.append((to, subject, body))
        return True

    monkeypatch.setattr(mailer, "send_email", _fake_send)
    return calls


@pytest.fixture
def llm(monkeypatch):
    """Simula Ollama: responde siempre lo mismo (o falla) y guarda lo enviado."""
    state = {"payloads": [], "error": None,
             "reply": json.dumps({"level": 3, "category": "Software", "summary": "No abre el sistema."})}

    def _fake_post_chat(payload):
        # Un solo análisis a la vez: el modelo solo se llama con el lock tomado
        assert email_analysis.ANALYSIS_LOCK.locked()
        state["payloads"].append(payload)
        if state["error"] is not None:
            raise state["error"]
        return state["reply"]

    monkeypatch.setattr(chatbot, "post_chat", _fake_post_chat)
    return state


def _category_id(name):
    db = SessionLocal()
    try:
        return db.query(models.Category).filter(models.Category.name == name).one().id
    finally:
        db.close()


def _create(client, actor, title="No abre el sistema", description="El sistema de planillas no abre.",
            category="Software", requester_id=None):
    payload = {
        "title": title,
        "description": description,
        "category_id": _category_id(category),
        "priority_id": 1,
        "contact_phone": "8888-1234",
        "contact_id_number": "1-0234-0567",
    }
    if requester_id is not None:
        payload["requester_id"] = requester_id
    response = client.post("/api/tickets", json=payload, headers=actor["headers"])
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _row(ticket_id):
    db = SessionLocal()
    try:
        return db.query(models.TicketAIAnalysis).filter(models.TicketAIAnalysis.ticket_id == ticket_id).first()
    finally:
        db.close()


def _set_status(ticket_id, status):
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket_id).status = status
        db.commit()
    finally:
        db.close()


def test_web_ticket_gets_analysis(client, make_user, sent, llm):
    requester = make_user("requester")
    ticket_id = _create(client, requester)

    row = _row(ticket_id)
    assert row is not None and row.status == "done"
    assert row.ai_level == 3
    assert row.ai_category_id == _category_id("Software")
    assert row.summary == "No abre el sistema."
    assert row.due_at is not None
    assert len(llm["payloads"]) == 1
    # El análisis no escribe al solicitante
    assert sent == []


def test_coordinator_ticket_for_requester_gets_analysis(client, make_user, sent, llm):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    ticket_id = _create(client, coordinator, requester_id=requester["id"])
    assert _row(ticket_id).status == "done"


def test_web_ticket_uses_level_floor(client, make_user, sent, llm):
    llm["reply"] = json.dumps({"level": 3, "category": "Red", "summary": "Sin internet en el segundo piso."})
    ticket_id = _create(client, make_user("requester"), title="Sin internet", description=OUTAGE, category="Red")
    row = _row(ticket_id)
    assert row.ai_level == 4
    assert row.due_at is not None


def test_model_failure_still_creates_ticket(client, make_user, sent, llm):
    llm["error"] = chatbot.ChatbotUnavailable()
    requester = make_user("requester")
    ticket_id = _create(client, requester)

    detail = client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"])
    assert detail.status_code == 200
    assert _row(ticket_id) is None
    assert sent == []


def test_ai_off_does_not_call_model(client, make_user, sent, llm, monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    ticket_id = _create(client, make_user("requester"))
    assert _row(ticket_id) is None
    assert llm["payloads"] == []


def test_personal_data_is_masked_before_model(client, make_user, sent, llm):
    _create(client, make_user("requester"), title="Llamar al 8888-1234",
            description="Llámeme al 8888-1234, cédula 1-0234-0567.")
    sent_to_model = json.dumps(llm["payloads"][-1], ensure_ascii=False)
    for secret in ("8888-1234", "88881234", "1-0234-0567", "102340567"):
        assert secret not in sent_to_model
    assert "[dato oculto]" in sent_to_model


def test_analyze_missing_only_open_tickets_without_analysis(client, make_user, sent, llm, monkeypatch):
    # Los tickets abiertos de otras pruebas también contarían
    db = SessionLocal()
    try:
        db.query(models.Ticket).update({"status": "closed"})
        db.commit()
    finally:
        db.close()

    admin = make_user("admin")
    requester = make_user("requester")
    analyzed = _create(client, requester, title="Ya analizado")
    before = _row(analyzed)

    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    new = _create(client, requester, title="Abierto nuevo")
    in_progress = _create(client, requester, title="Abierto en proceso")
    resolved = _create(client, requester, title="Resuelto")
    closed = _create(client, requester, title="Cerrado")
    _set_status(in_progress, "in_progress")
    _set_status(resolved, "resolved")
    _set_status(closed, "closed")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    llm["payloads"].clear()

    response = client.post("/api/admin/ai/analyze-missing", headers=admin["headers"])
    assert response.status_code == 200, response.text
    assert response.json() == {"found": 2}
    assert _row(new).status == "done"
    assert _row(in_progress).status == "done"
    assert _row(resolved) is None and _row(closed) is None
    # El que ya tenía análisis no se repite
    assert _row(analyzed).id == before.id and _row(analyzed).analyzed_at == before.analyzed_at
    sent_titles = " ".join(p["messages"][1]["content"] for p in llm["payloads"])
    assert len(llm["payloads"]) == 2 and "Ya analizado" not in sent_titles

    again = client.post("/api/admin/ai/analyze-missing", headers=admin["headers"])
    assert again.json() == {"found": 0}
    assert len(llm["payloads"]) == 2


def test_analyze_missing_keeps_failed_ticket_without_analysis(client, make_user, sent, llm, monkeypatch):
    db = SessionLocal()
    try:
        db.query(models.Ticket).update({"status": "closed"})
        db.commit()
    finally:
        db.close()
    admin = make_user("admin")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    ticket_id = _create(client, make_user("requester"))
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)

    llm["error"] = chatbot.ChatbotUnavailable()
    assert client.post("/api/admin/ai/analyze-missing", headers=admin["headers"]).json() == {"found": 1}
    assert _row(ticket_id) is None
    # Se puede reintentar cuando el modelo vuelve
    llm["error"] = None
    assert client.post("/api/admin/ai/analyze-missing", headers=admin["headers"]).json() == {"found": 1}
    assert _row(ticket_id).status == "done"


@pytest.mark.parametrize("role", ["coordinator", "technician"])
def test_analyze_missing_requires_admin(client, make_user, llm, role):
    user = make_user(role)
    assert client.post("/api/admin/ai/analyze-missing", headers=user["headers"]).status_code == 403
    assert llm["payloads"] == []


def test_analyze_missing_with_ai_off(client, make_user, llm, monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    admin = make_user("admin")
    response = client.post("/api/admin/ai/analyze-missing", headers=admin["headers"])
    assert response.status_code == 400
    assert llm["payloads"] == []
