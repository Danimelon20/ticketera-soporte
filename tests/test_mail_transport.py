"""Transporte del correo (graph | smtp | log), lectura del buzón y protección del entorno de pruebas.

El SMTP es siempre una clase falsa: nunca se conecta a Mailpit ni a otro servidor.
"""
import asyncio
import email
import logging
import smtplib
from email import policy

import pytest

from app import config, mailer, main
from app.routers import auth

PDF = b"%PDF-1.4 adjunto de prueba"


class FakeSMTP:
    """Reemplazo de smtplib.SMTP: guarda la conexión y los mensajes en sent_messages."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.sent_messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, msg):
        # Ida y vuelta por bytes, como lo vería el servidor
        self.sent_messages.append(email.message_from_bytes(msg.as_bytes(policy=policy.SMTP), policy=policy.default))


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(config, "MAIL_TRANSPORT", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "mailpit")
    monkeypatch.setattr(config, "SMTP_PORT", 1025)
    monkeypatch.setattr(config, "SMTP_TIMEOUT_SECONDS", 7.0)
    monkeypatch.setattr(config, "MAIL_FROM", "ticketera@pruebas.local")
    return FakeSMTP.instances


# ----- envío por SMTP -----

def test_smtp_sends_subject_text_html_and_attachment(smtp):
    sent = mailer.send_email("ana@pruebas.local", "Código de verificación", "Su código es 123456",
                             attachments=[("informe.pdf", PDF, "application/pdf")],
                             html="<p>Su código es <b>123456</b></p>")
    assert sent is True

    assert len(smtp) == 1
    server = smtp[0]
    assert (server.host, server.port, server.timeout) == ("mailpit", 1025, 7.0)
    [msg] = server.sent_messages
    assert msg["Subject"] == "Código de verificación"
    assert msg["From"] == "ticketera@pruebas.local"
    assert msg["To"] == "ana@pruebas.local"
    assert msg["Auto-Submitted"] == "auto-generated"

    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Su código es 123456" in text and mailer.SIGNATURE in text
    assert "<b>123456</b>" in msg.get_body(preferencelist=("html",)).get_content()

    [attachment] = list(msg.iter_attachments())
    assert attachment.get_filename() == "informe.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_content() == PDF


def test_smtp_without_html_is_plain_text(smtp):
    assert mailer.send_email("ana@pruebas.local", "Aviso", "Hola") is True
    [msg] = smtp[0].sent_messages
    assert msg.get_content_type() == "text/plain"


def test_smtp_error_returns_false_without_raising(smtp, monkeypatch, caplog):
    def _fail(self, msg):
        raise smtplib.SMTPServerDisconnected("se cayó")

    monkeypatch.setattr(FakeSMTP, "send_message", _fail)
    with caplog.at_level(logging.ERROR):
        assert mailer.send_email("ana@pruebas.local", "Asunto secreto", "Cuerpo secreto") is False
    assert "No se pudo enviar el correo" in caplog.text
    assert "Cuerpo secreto" not in caplog.text and "ana@pruebas.local" not in caplog.text


def test_smtp_rejects_attachments_over_limit(smtp, monkeypatch):
    monkeypatch.setattr(mailer, "MAX_ATTACHMENTS_BYTES", 10)
    assert mailer.send_email("ana@pruebas.local", "Asunto", "Hola", attachments=[("a.pdf", PDF, "application/pdf")]) is False
    assert smtp == []


def test_smtp_skips_invalid_domain(smtp):
    assert mailer.send_email("demo@demo.invalid", "Asunto", "Hola") is False
    assert smtp == []


def test_log_transport_does_not_send(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("No debe abrir una conexión SMTP")

    monkeypatch.setattr(smtplib, "SMTP", _fail)
    assert mailer.send_email("ana@pruebas.local", "Asunto", "Hola") is False


# ----- compatibilidad: sin las variables nuevas todo sale de EMAIL_ENABLED -----

@pytest.mark.parametrize("env, expected", [
    ({}, ("log", False)),
    ({"EMAIL_ENABLED": "false"}, ("log", False)),
    ({"EMAIL_ENABLED": "true"}, ("graph", True)),
    ({"EMAIL_ENABLED": "TRUE"}, ("graph", True)),
    # Las variables nuevas, si están, mandan
    ({"EMAIL_ENABLED": "true", "MAIL_TRANSPORT": "smtp", "MAILBOX_POLL_ENABLED": "false"}, ("smtp", False)),
    ({"EMAIL_ENABLED": "false", "MAIL_TRANSPORT": "Graph", "MAILBOX_POLL_ENABLED": "true"}, ("graph", True)),
    ({"EMAIL_ENABLED": "true", "MAIL_TRANSPORT": "log"}, ("log", True)),
    ({"EMAIL_ENABLED": "true", "MAIL_TRANSPORT": "", "MAILBOX_POLL_ENABLED": ""}, ("graph", True)),
])
def test_compatibility_table(env, expected):
    assert config.resolve_mail_settings(env) == expected


# ----- protección al arrancar -----

def _settings(monkeypatch, app_env, transport, host="mailpit", poll=False, allowed=("mailpit",)):
    monkeypatch.setattr(config, "APP_ENV", app_env)
    monkeypatch.setattr(config, "MAIL_TRANSPORT", transport)
    monkeypatch.setattr(config, "SMTP_HOST", host)
    monkeypatch.setattr(config, "MAILBOX_POLL_ENABLED", poll)
    monkeypatch.setattr(config, "SMTP_ALLOWED_HOSTS", list(allowed))


def test_pruebas_with_mailpit_starts(monkeypatch):
    _settings(monkeypatch, "pruebas", "smtp")
    mailer.check_mail_config()


def test_pruebas_rejects_smtp_host_not_allowed(monkeypatch):
    _settings(monkeypatch, "pruebas", "smtp", host="smtp.office365.com")
    with pytest.raises(RuntimeError) as exc:
        mailer.check_mail_config()
    message = str(exc.value)
    assert "smtp.office365.com" in message and "SMTP_ALLOWED_HOSTS" in message and "internet" in message


def test_pruebas_allowed_hosts_can_be_changed(monkeypatch):
    _settings(monkeypatch, "pruebas", "smtp", host="otro-mailpit", allowed=("mailpit", "otro-mailpit"))
    mailer.check_mail_config()


def test_pruebas_rejects_graph(monkeypatch):
    _settings(monkeypatch, "pruebas", "graph")
    with pytest.raises(RuntimeError, match="MAIL_TRANSPORT=graph"):
        mailer.check_mail_config()


def test_pruebas_rejects_mailbox_poll(monkeypatch):
    _settings(monkeypatch, "pruebas", "log", poll=True)
    with pytest.raises(RuntimeError, match="MAILBOX_POLL_ENABLED=true"):
        mailer.check_mail_config()


def test_invalid_transport_is_rejected(monkeypatch):
    _settings(monkeypatch, "", "sendgrid")
    with pytest.raises(RuntimeError, match="graph, smtp o log"):
        mailer.check_mail_config()


@pytest.mark.parametrize("transport, host, poll", [("graph", "mailpit", True), ("smtp", "smtp.ejemplo.com", False)])
def test_outside_pruebas_nothing_is_restricted(monkeypatch, transport, host, poll):
    _settings(monkeypatch, "", transport, host=host, poll=poll)
    mailer.check_mail_config()


def test_app_does_not_start_with_forbidden_config(client, monkeypatch):
    _settings(monkeypatch, "pruebas", "graph")

    async def _start():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="APP_ENV=pruebas"):
        asyncio.run(_start())


def test_startup_line_has_no_secrets(monkeypatch):
    _settings(monkeypatch, "pruebas", "smtp")
    monkeypatch.setattr(config, "SMTP_PORT", 1025)
    monkeypatch.setattr(config, "MAIL_FROM", "remitente-secreto@pruebas.local")
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", "soporte-real@ejemplo.com")
    line = mailer.describe_mail_config()
    assert line == "Correo: transporte=smtp (mailpit:1025), lectura del buzón=no, entorno=pruebas"
    assert "remitente-secreto" not in line and "soporte-real" not in line


# ----- lectura del buzón -----

def _run_lifespan(monkeypatch, poll_enabled):
    calls = []

    def _fake_poll_forever():
        calls.append(1)
        return asyncio.sleep(3600)

    monkeypatch.setattr(main, "poll_forever", _fake_poll_forever)
    monkeypatch.setattr(config, "MAILBOX_POLL_ENABLED", poll_enabled)
    monkeypatch.setattr(config, "MS_TENANT_ID", "inquilino")
    monkeypatch.setattr(config, "MS_CLIENT_ID", "cliente")
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", "soporte@ejemplo.com")

    async def _start():
        async with main.lifespan(main.app):
            pass

    asyncio.run(_start())
    return calls


def test_mailbox_poll_does_not_start_when_disabled(client, monkeypatch):
    assert _run_lifespan(monkeypatch, False) == []


def test_mailbox_poll_starts_when_enabled(client, monkeypatch):
    assert _run_lifespan(monkeypatch, True) == [1]


# ----- [SOLO DESARROLLO] solo con transporte log -----

@pytest.mark.parametrize("transport, logged", [("log", True), ("smtp", False), ("graph", False)])
def test_dev_code_only_logged_with_log_transport(monkeypatch, caplog, transport, logged):
    monkeypatch.setattr(config, "MAIL_TRANSPORT", transport)
    monkeypatch.setattr(auth.mailer, "send_email", lambda *args, **kwargs: True)
    with caplog.at_level(logging.WARNING):
        auth.send_code_email("ana@pruebas.local", "Código", "Su código es 654321", "654321")
    assert ("654321" in caplog.text) is logged
