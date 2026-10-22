import json
import logging
import threading
import time

import httpx
import pytest

from app import chatbot, config, main
from app.routers import chat as chat_router
from app.schemas import ChatTurn

SECRET = "Mi cédula es 102340567 y el problema es secreto"


def done_line(**extra):
    data = {"done": True, "total_duration": 2_500_000_000, "load_duration": 100_000_000,
            "prompt_eval_count": 120, "eval_count": 40, "eval_duration": 2_000_000_000}
    data.update(extra)
    return json.dumps(data)


class FakeStream:
    def __init__(self, status_code, lines):
        self.status_code = status_code
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        yield from self.lines


@pytest.fixture
def ollama_stream(monkeypatch):
    """Simula httpx.stream hacia Ollama: devuelve las líneas NDJSON indicadas y guarda el cuerpo enviado."""
    state = {"lines": [], "status": 200, "payloads": []}

    def fake_stream(method, url, json=None, timeout=None):
        state["payloads"].append(json)
        return FakeStream(state["status"], state["lines"])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    return state


def chunk(text):
    return json.dumps({"message": {"content": text}, "done": False})


# ---------- Cuerpo de cada llamada ----------

@pytest.mark.parametrize("task,limit", [("chat", 400), ("ticket_analysis", 300), ("draft_analysis", 300)])
def test_payload_has_speed_options(monkeypatch, task, limit):
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:1.7b")
    payload = chatbot.ollama_payload(task, [{"role": "user", "content": "x"}], temperature=0.1, format={"type": "object"})
    assert payload["think"] is False
    assert payload["keep_alive"] == "24h"
    assert payload["options"]["num_predict"] == limit
    assert payload["format"] == {"type": "object"}
    assert payload["model"] == "qwen3:1.7b"


def test_chat_payload_has_no_format():
    payload = chatbot.ollama_payload("chat", [], temperature=0.3, stream=True, num_ctx=4096)
    assert "format" not in payload
    assert payload["stream"] is True
    assert payload["options"] == {"temperature": 0.3, "num_predict": 400, "num_ctx": 4096}


def test_system_prompt_starts_with_fixed_rules(make_user):
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        ana = db.get(models.User, make_user("requester", name="Ana")["id"])
        beto = db.get(models.User, make_user("coordinator", name="Beto")["id"])
        first = chatbot.build_system_prompt(ana, "datos de Ana")
        second = chatbot.build_system_prompt(beto, "datos de Beto")
    finally:
        db.close()
    # El inicio es idéntico para todos: Ollama reutiliza lo ya leído
    assert first.startswith(chatbot.ASSISTANT_RULES) and second.startswith(chatbot.ASSISTANT_RULES)
    assert "Ana" not in chatbot.ASSISTANT_RULES


# ---------- Streaming del asistente ----------

def test_stream_llm_yields_text_and_sends_stream_true(ollama_stream, caplog):
    caplog.set_level(logging.INFO)
    ollama_stream["lines"] = [chunk("Hola, "), "", chunk("¿en qué "), chunk("te ayudo?"), done_line()]
    history = [ChatTurn(role="user", text="Hola")]
    parts = list(chatbot.stream_llm("SISTEMA", history, "¿Cómo va mi ticket?"))
    assert "".join(parts) == "Hola, ¿en qué te ayudo?"
    assert len(parts) == 3
    payload = ollama_stream["payloads"][0]
    assert payload["stream"] is True and payload["think"] is False and payload["keep_alive"] == "24h"
    assert payload["options"]["num_predict"] == 400
    assert "Ollama chat: total 2.50 s, carga 0.10 s, prompt_eval_count 120, eval_count 40, 20.0 tokens/s" in caplog.text


def test_stream_strips_think_split_across_chunks(ollama_stream):
    ollama_stream["lines"] = [chunk("<thi"), chunk("nk>pensando..."), chunk("</th"), chunk("ink>\n\nRespuesta "), chunk("final"), done_line()]
    assert "".join(chatbot.stream_llm("S", [], "hola")) == "Respuesta final"


def test_stream_only_think_is_unavailable(ollama_stream):
    ollama_stream["lines"] = [chunk("<think>solo pensó</think>"), done_line()]
    with pytest.raises(chatbot.ChatbotUnavailable):
        list(chatbot.stream_llm("S", [], "hola"))


def test_stream_endpoint(client, make_user, ollama_stream, monkeypatch):
    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    chat_router.reset_rate_limit()
    user = make_user("requester")
    ollama_stream["lines"] = [chunk("Su ticket "), chunk("está en proceso."), done_line()]
    response = client.post("/api/chat/stream", json={"message": "¿Cómo va?", "history": []}, headers=user["headers"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Su ticket está en proceso."


def test_stream_endpoint_unavailable(client, make_user, ollama_stream, monkeypatch):
    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    chat_router.reset_rate_limit()
    ollama_stream["status"] = 404
    response = client.post("/api/chat/stream", json={"message": "hola", "history": []},
                           headers=make_user("requester")["headers"])
    assert response.status_code == 503


# ---------- Calentamiento ----------

def test_warm_up_failure_does_not_raise(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise httpx.ConnectError("sin Ollama")
    monkeypatch.setattr(httpx, "post", broken)
    chatbot.warm_up()
    assert "No se pudo precargar el modelo: ConnectError" in caplog.text


def test_warm_up_runs_in_background(monkeypatch):
    release = threading.Event()
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"total_duration": 1, "load_duration": 1}

    def slow_post(url, json=None, timeout=None):
        calls.append((url, json))
        release.wait(5)
        return Response()

    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setattr(httpx, "post", slow_post)
    started = time.monotonic()
    thread = main.start_warm_up()
    # No espera a Ollama
    assert time.monotonic() - started < 1
    assert thread is not None and thread.daemon
    release.set()
    thread.join(5)
    assert calls[0][0].endswith("/api/generate")
    assert calls[0][1] == {"model": "qwen3:1.7b", "keep_alive": "24h"}


def test_no_warm_up_when_ai_is_off(monkeypatch):
    monkeypatch.setattr(config, "CHATBOT_ENABLED", False)
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    assert main.start_warm_up() is None


def test_app_starts_even_if_warm_up_fails(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("sin Ollama")))
    with TestClient(main.app) as fresh:
        assert fresh.get("/api/health").json() == {"status": "ok"}


# ---------- Log de tiempos ----------

def test_timing_log_has_no_content(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    class Response:
        status_code = 200

        def json(self):
            return {"message": {"content": SECRET}, "total_duration": 900_000_000, "eval_count": 10,
                    "eval_duration": 500_000_000, "prompt_eval_count": 30, "load_duration": 0}

    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: Response())
    payload = chatbot.ollama_payload("ticket_analysis", [{"role": "user", "content": SECRET}], temperature=0.1)
    assert chatbot.post_chat(payload, task="ticket_analysis") == SECRET
    assert "Ollama ticket_analysis: total 0.90 s, carga 0.00 s, prompt_eval_count 30, eval_count 10, 20.0 tokens/s" in caplog.text
    assert "102340567" not in caplog.text and "secreto" not in caplog.text
