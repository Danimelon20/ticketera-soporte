import base64
import os

import httpx
import pytest

from app import config, graph, mailer, ms_login

MAILBOX = "soporte@ejemplo.com"


class FakeMsalApp:
    """Reemplaza msal.PublicClientApplication: nunca se conecta a Microsoft."""

    def __init__(self, cache, username=MAILBOX, login_username=MAILBOX):
        self.cache = cache
        self.username = username
        self.login_username = login_username
        self.silent_calls = 0

    def get_accounts(self):
        return [{"username": self.username}] if self.username else []

    def acquire_token_silent(self, scopes, account):
        self.silent_calls += 1
        return {"access_token": f"token-{self.silent_calls}"}

    def initiate_device_flow(self, scopes):
        return {"user_code": "ABC123", "message": "Abre https://microsoft.com/devicelogin y escribe ABC123"}

    def acquire_token_by_device_flow(self, flow):
        # msal real escribe en el caché al iniciar sesión
        self.cache.add({
            "client_id": "cliente-prueba",
            "scope": graph.SCOPES,
            "token_endpoint": "https://login.microsoftonline.com/tenant/oauth2/v2.0/token",
            "response": {"access_token": "token", "token_type": "Bearer", "expires_in": 3600},
        })
        return {"access_token": "token", "id_token_claims": {"preferred_username": self.login_username}}


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "ms_token_cache.json"
    monkeypatch.setattr(config, "MS_TOKEN_CACHE", str(path))
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    return path


@pytest.fixture
def fake_app(cache_path, monkeypatch):
    cache_path.write_text("{}")
    apps = []

    def _build(cache):
        app = FakeMsalApp(cache)
        apps.append(app)
        return app

    monkeypatch.setattr(graph, "build_app", _build)
    return apps


@pytest.fixture
def graph_calls(monkeypatch):
    """Simula httpx.request con una lista de códigos de respuesta."""
    calls = []
    statuses = []

    def _request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        status = statuses.pop(0) if statuses else 202
        body = {"error": {"message": "Algo falló"}} if status >= 400 else None
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(graph.httpx, "request", _request)
    return calls, statuses


def test_get_token_without_cache_requires_login(cache_path):
    with pytest.raises(graph.GraphAuthRequired) as exc:
        graph.get_token()
    assert "python -m app.ms_login" in str(exc.value)


def test_get_token_without_matching_account_requires_login(cache_path, monkeypatch):
    cache_path.write_text("{}")
    monkeypatch.setattr(graph, "build_app", lambda cache: FakeMsalApp(cache, username="otra@ejemplo.com"))
    with pytest.raises(graph.GraphAuthRequired):
        graph.get_token()


def test_send_email_uses_sendmail_with_base64_mime(fake_app, graph_calls, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    calls, _statuses = graph_calls
    assert mailer.send_email("ana@test.com", "Prueba", "Hola") is True

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://graph.microsoft.com/v1.0/me/sendMail"
    assert call["headers"]["Content-Type"] == "text/plain"
    assert call["headers"]["Authorization"] == "Bearer token-1"
    mime = base64.b64decode(call["content"]).decode("utf-8")
    assert "Auto-Submitted: auto-generated" in mime
    assert "To: ana@test.com" in mime


def test_send_email_disabled_does_not_call_graph(fake_app, graph_calls):
    calls, _statuses = graph_calls
    assert mailer.send_email("ana@test.com", "Prueba", "Hola") is False
    assert calls == []


def test_send_email_returns_false_when_graph_fails(fake_app, graph_calls, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    _calls, statuses = graph_calls
    statuses.append(500)
    assert mailer.send_email("ana@test.com", "Prueba", "Hola") is False


def test_send_email_returns_false_without_session(cache_path, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    assert mailer.send_email("ana@test.com", "Prueba", "Hola") is False


def test_401_is_retried_once(fake_app, graph_calls):
    calls, statuses = graph_calls
    statuses.extend([401, 202])
    response = graph.graph_request("POST", "/me/sendMail", content=b"x")
    assert response.status_code == 202
    assert len(calls) == 2
    assert calls[1]["headers"]["Authorization"] == "Bearer token-1"
    assert len(fake_app) == 2  # se pidió el token otra vez


def test_second_401_raises_graph_error(fake_app, graph_calls):
    calls, statuses = graph_calls
    statuses.extend([401, 401, 202])
    with pytest.raises(graph.GraphError) as exc:
        graph.graph_request("POST", "/me/sendMail", content=b"x")
    assert exc.value.status_code == 401
    assert len(calls) == 2


def test_ms_login_does_not_save_other_account(cache_path, monkeypatch, capsys):
    monkeypatch.setattr(graph, "build_app", lambda cache: FakeMsalApp(cache, login_username="otra@ejemplo.com"))
    assert ms_login.main() == 1
    assert not os.path.exists(cache_path)
    assert "No se guardó la sesión" in capsys.readouterr().out


def test_ms_login_saves_cache_for_support_mailbox(cache_path, monkeypatch, capsys):
    monkeypatch.setattr(graph, "build_app", lambda cache: FakeMsalApp(cache))
    assert ms_login.main() == 0
    assert os.path.exists(cache_path)
    assert oct(os.stat(cache_path).st_mode & 0o777) == "0o600"
    assert f"Sesión de Microsoft guardada para {MAILBOX}" in capsys.readouterr().out


def test_send_email_body_survives_encoding(fake_app, graph_calls, monkeypatch):
    import email
    from email import policy

    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    calls, _statuses = graph_calls
    long_line = "Esta dirección no está registrada; regístrese en http://localhost:8000/registro " + "ñ" * 40
    assert len(long_line) > 100
    body = f"Buen día, José.\n\n{long_line}\nCédula, teléfono y descripción."
    assert mailer.send_email("ana@test.com", "Recibimos su solicitud", body) is True

    raw = base64.b64decode(calls[0]["content"])
    # Saltos de línea \r\n en todo el MIME y cuerpo en base64, no quoted-printable
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert msg["Content-Transfer-Encoding"] == "base64"
    sent_text = msg.get_content().replace("\r\n", "\n")
    assert sent_text == f"{body}\n\n{mailer.SIGNATURE}\n"
