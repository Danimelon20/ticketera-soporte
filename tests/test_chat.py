import re

import httpx
import pytest

from app import chatbot, config
from app.routers import chat as chat_router
from app.schemas import ChatTurn

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@pytest.fixture
def prompts(monkeypatch):
    calls = []

    def _fake_llm(system_prompt, history, message):
        calls.append(system_prompt)
        return "Respuesta de prueba"

    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    monkeypatch.setattr(chatbot, "call_llm", _fake_llm)
    # Pregunta de ayuda general (sin datos); las consultas del catálogo: tests/test_assistant_queries.py
    monkeypatch.setattr(chatbot, "select_query", lambda message, options, periods=(): (chatbot.HELP, None))
    chat_router.reset_rate_limit()
    yield calls
    chat_router.reset_rate_limit()


def _chat(client, user, message="¿Cómo va mi ticket?", **extra):
    return client.post("/api/chat", json={"message": message, **extra}, headers=user["headers"])


def test_help_prompt_has_no_ticket_data(client, make_user, make_ticket, prompts):
    """La ayuda general ya no lleva datos de tickets: los números salen solo del catálogo."""
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    ticket = make_ticket(requester, title="Servidor de archivos lento")
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    for user in (technician, coordinator, make_user("admin")):
        assert _chat(client, user).status_code == 200
    for prompt in prompts:
        assert "Servidor de archivos lento" not in prompt and f"#{ticket['id']}" not in prompt
        assert not EMAIL_RE.search(prompt)
        assert chatbot.HELP_NOTE in prompt


def test_disabled_returns_503(client, make_user, prompts, monkeypatch):
    monkeypatch.setattr(config, "CHATBOT_ENABLED", False)
    response = _chat(client, make_user("technician"))
    assert response.status_code == 503
    assert response.json()["detail"] == "El asistente no está disponible en este momento"
    assert prompts == []


def test_unavailable_returns_503(client, make_user, prompts, monkeypatch):
    def _broken(system_prompt, history, message):
        raise chatbot.ChatbotUnavailable()

    monkeypatch.setattr(chatbot, "call_llm", _broken)
    response = _chat(client, make_user("technician"))
    assert response.status_code == 503
    assert response.json()["detail"] == "El asistente no está disponible en este momento"


def test_rate_limit(client, make_user, prompts):
    user = make_user("technician")
    for _ in range(20):
        assert _chat(client, user).status_code == 200
    response = _chat(client, user)
    assert response.status_code == 429
    assert response.json()["detail"] == "Has enviado muchos mensajes. Espera unos minutos."
    # Otro usuario no se ve afectado
    assert _chat(client, make_user("technician")).status_code == 200


def test_message_too_long(client, make_user, prompts):
    response = _chat(client, make_user("technician"), message="a" * 1001)
    assert response.status_code == 422


def test_history_too_long(client, make_user, prompts):
    history = [{"role": "user", "text": "hola"}] * 11
    response = _chat(client, make_user("technician"), history=history)
    assert response.status_code == 422


def test_requires_login(client, prompts):
    response = client.post("/api/chat", json={"message": "hola"})
    assert response.status_code == 401


class _FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_call_llm_strips_think_and_disables_thinking(monkeypatch):
    sent = {}

    def _fake_post(url, json=None, timeout=None):
        sent.update(url=url, json=json, timeout=timeout)
        return _FakeResponse(200, {"message": {"content": "<think>\nrazonando...\n</think>\n\n  Hola, ¿en qué te ayudo?  "}})

    monkeypatch.setattr(config, "LLM_BASE_URL", "http://ollama:11434")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setattr(httpx, "post", _fake_post)

    history = [ChatTurn(role="user", text="Hola"), ChatTurn(role="model", text="Hola, soy el asistente")]
    reply = chatbot.call_llm("SISTEMA", history, "¿Cómo va mi ticket?")

    assert reply == "Hola, ¿en qué te ayudo?"
    assert sent["url"] == "http://ollama:11434/api/chat"
    assert sent["timeout"] == 120
    payload = sent["json"]
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["model"] == "qwen3:1.7b"
    assert payload["keep_alive"] == "24h"
    assert payload["options"] == {"temperature": 0.3, "num_predict": 400, "num_ctx": 4096}
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "user"]
    assert payload["messages"][0]["content"] == "SISTEMA"
    assert payload["messages"][-1]["content"] == "¿Cómo va mi ticket?"


def test_call_llm_missing_model(monkeypatch, caplog):
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: _FakeResponse(404, {"error": "model not found"}))
    with pytest.raises(chatbot.ChatbotUnavailable):
        chatbot.call_llm("SISTEMA", [], "hola")
    assert "docker compose exec ollama ollama pull qwen3:1.7b" in caplog.text


@pytest.mark.parametrize("outcome", ["network", "empty"])
def test_call_llm_errors(monkeypatch, outcome):
    def _fake_post(url, json=None, timeout=None):
        if outcome == "network":
            raise httpx.ConnectError("sin conexión")
        return _FakeResponse(200, {"message": {"content": "<think>solo pensó</think>"}})

    monkeypatch.setattr(httpx, "post", _fake_post)
    with pytest.raises(chatbot.ChatbotUnavailable):
        chatbot.call_llm("SISTEMA", [], "hola")
