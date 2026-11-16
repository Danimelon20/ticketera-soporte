"""Autenticidad del remitente (app/email_auth.py): el «De:» se puede falsificar; las marcas de Exchange Online no.
Interno (X-MS-Exchange-Organization-AuthAs: Internal) o la primera Authentication-Results con dmarc/compauth en
pass para el dominio del «De:». Sin eso, el correo no se procesa ni se responde. simulate_email no pasa por aquí."""
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import config, email_auth, mailer, models
from app.database import SessionLocal
from app.email_processing import process_message

MAILBOX = "soporte@ejemplo.com"
SENDER = "ana.mora@data.cr"


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "EMAIL_SENDER_CHECK", "auth")
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body, **kw: sent.append((to, subject)) or True)
    return sent


def _msg(sender=SENDER, **headers):
    msg = EmailMessage()
    msg["From"] = f"Ana Mora <{sender}>"
    msg["To"] = MAILBOX
    msg["Subject"] = "Sin internet"
    msg["Message-ID"] = make_msgid()
    for name, value in headers.items():
        if isinstance(value, list):
            for item in value:
                msg[name.replace("_", "-")] = item
        else:
            msg[name.replace("_", "-")] = value
    msg.set_content("Desde las 8 no tengo internet en mi computadora.")
    return msg


def _ar(result):
    return f"spf=pass (sender IP is 192.0.2.1) smtp.mailfrom=data.cr; dkim=pass header.d=data.cr; {result}"


@pytest.mark.parametrize("headers, ok", [
    ({"X_MS_Exchange_Organization_AuthAs": "Internal"}, True),
    ({"X_MS_Exchange_Organization_AuthAs": "Anonymous"}, False),
    ({"Authentication_Results": _ar("dmarc=pass action=none header.from=data.cr;compauth=pass reason=100")}, True),
    ({"Authentication_Results": _ar("dmarc=none action=none header.from=data.cr;compauth=pass reason=109")}, True),
    ({"Authentication_Results": _ar("dmarc=fail action=none header.from=data.cr;compauth=fail reason=000")}, False),
    # Pasa, pero para otro dominio que el del «De:»
    ({"Authentication_Results": _ar("dmarc=pass action=none header.from=otro.com;compauth=pass reason=100")}, False),
    # Una Authentication-Results falsa más abajo (la escribió el remitente) no cuenta: vale la primera
    ({"Authentication_Results": [_ar("dmarc=fail action=none header.from=data.cr;compauth=fail reason=000"),
                                 _ar("dmarc=pass action=none header.from=data.cr;compauth=pass reason=100")]}, False),
    ({}, False),
])
def test_sender_verified(headers, ok):
    assert email_auth.sender_verified(_msg(**headers), SENDER) is ok


def test_unverified_mail_is_not_processed_nor_answered(setup):
    with SessionLocal() as db:
        assert process_message(db, _msg()) == "unverified_sender"
        assert db.query(models.User).filter(models.User.email == SENDER).first() is None
        assert db.query(models.EmailDraft).filter(models.EmailDraft.subject == "Sin internet").count() == 0
    # No se le responde: el «De:» podría ser de otra persona
    assert setup == []


def test_internal_mail_goes_through(setup):
    with SessionLocal() as db:
        assert process_message(db, _msg(X_MS_Exchange_Organization_AuthAs="Internal")) == "draft_created"


def test_domain_mode_skips_the_check(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_SENDER_CHECK", "domain")
    assert email_auth.sender_verified(_msg(), SENDER) is True


def test_simulate_email_does_not_check(monkeypatch):
    """simulate_email llama a process_message sin la revisión (no pasa por Exchange)."""
    with SessionLocal() as db:
        assert process_message(db, _msg(sender="pedro.solis@data.cr"), verify_sender=False) == "draft_created"
