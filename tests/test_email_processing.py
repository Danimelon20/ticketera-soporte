import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import config, hacienda, mailer, models
from app.database import SessionLocal
from app.email_processing import process_message

MAILBOX = "soporte@ejemplo.com"

COMPLETE_BODY = (
    "Categoría: Software\n"
    "Prioridad: Alta\n"
    "Teléfono: 8888-1234\n"
    "Cédula: 1-0234-0567\n"
    "Descripción: No abre el sistema de planillas."
)


@pytest.fixture(autouse=True)
def email_settings(monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake_send(to, subject, body):
        calls.append((to, subject, body))
        return True

    monkeypatch.setattr(mailer, "send_email", _fake_send)
    return calls


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _set_user(user_id, **values):
    session = SessionLocal()
    try:
        user = session.get(models.User, user_id)
        for key, value in values.items():
            setattr(user, key, value)
        session.commit()
    finally:
        session.close()


def _email(sender, body=COMPLETE_BODY, subject="[Soporte] Planillas", message_id=None, **headers):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = MAILBOX
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or make_msgid()
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    msg.set_content(body)
    return msg


def _tickets_of(db, user_id):
    return db.query(models.Ticket).filter(models.Ticket.created_by == user_id).all()


def test_complete_email_creates_ticket(db, sent, make_user):
    requester = make_user("requester", "Ana", profile=False)
    assert process_message(db, _email(requester["email"])) == "created"

    tickets = _tickets_of(db, requester["id"])
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.source == "email"
    assert ticket.created_by == requester["id"]
    assert ticket.contact_phone == "88881234"
    assert ticket.contact_id_number == "102340567"

    followers = db.query(models.TicketFollower).filter(models.TicketFollower.ticket_id == ticket.id).all()
    assert [f.user_id for f in followers] == [requester["id"]]
    comments = db.query(models.Comment).filter(models.Comment.ticket_id == ticket.id).all()
    assert len(comments) == 1
    assert comments[0].user.role == "agent"

    assert len(sent) == 1
    to, subject, _body = sent[0]
    assert to == requester["email"]
    assert subject.startswith("[Ticket #")


def test_uses_profile_phone_and_id_number(db, sent, make_user):
    requester = make_user("requester", profile=False)
    _set_user(requester["id"], phone="88887777", id_number="109870654")
    body = "Categoría: Red\nDescripción: Sin internet"
    assert process_message(db, _email(requester["email"], body=body)) == "created"
    ticket = _tickets_of(db, requester["id"])[0]
    assert ticket.contact_phone == "88887777"
    assert ticket.contact_id_number == "109870654"


def test_missing_category_is_incomplete(db, sent, make_user):
    requester = make_user("requester", profile=False)
    body = COMPLETE_BODY.replace("Categoría: Software\n", "")
    assert process_message(db, _email(requester["email"], body=body)) == "incomplete"
    assert _tickets_of(db, requester["id"]) == []
    _to, subject, reply = sent[0]
    assert subject == "Faltan datos para crear su ticket"
    assert "Falta la línea Categoría" in reply
    assert "[Soporte]" in reply


def test_unregistered_sender(db, sent):
    sender = f"nadie-{uuid.uuid4().hex}@test.com"
    assert process_message(db, _email(sender)) == "unregistered"
    assert sent[0][0] == sender
    assert sent[0][1] == "No pudimos crear su ticket"


def test_technician_is_unregistered(db, sent, make_user):
    technician = make_user("technician")
    assert process_message(db, _email(technician["email"])) == "unregistered"
    assert _tickets_of(db, technician["id"]) == []


def test_same_message_id_twice_is_duplicate(db, sent, make_user):
    requester = make_user("requester", profile=False)
    message_id = make_msgid()
    assert process_message(db, _email(requester["email"], message_id=message_id)) == "created"
    assert process_message(db, _email(requester["email"], message_id=message_id)) == "duplicate"
    assert len(_tickets_of(db, requester["id"])) == 1
    assert len(sent) == 1


def test_mailbox_sender_is_ignored(db, sent):
    assert process_message(db, _email(f"Soporte <{MAILBOX.upper()}>")) == "ignored"
    assert sent == []


def test_auto_replied_is_ignored(db, sent, make_user):
    requester = make_user("requester", profile=False)
    msg = _email(requester["email"], Auto_Submitted="auto-replied")
    assert process_message(db, msg) == "ignored"
    assert sent == []
    assert _tickets_of(db, requester["id"]) == []


def test_sender_email_is_case_insensitive(db, sent, make_user):
    requester = make_user("requester", profile=False)
    email = f"daniel-{uuid.uuid4().hex}@data.cr"
    _set_user(requester["id"], email=email)
    sender = "Daniel <" + email.replace("daniel", "Daniel").replace("data.cr", "Data.cr") + ">"
    assert process_message(db, _email(sender)) == "created"
    assert len(_tickets_of(db, requester["id"])) == 1


def test_id_number_rejected_by_hacienda_is_incomplete(db, sent, make_user, monkeypatch):
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: {"tipo": "02"})
    requester = make_user("requester", profile=False)
    assert process_message(db, _email(requester["email"])) == "incomplete"
    assert _tickets_of(db, requester["id"]) == []
    assert "La cédula no corresponde a una persona física o DIMEX" in sent[0][2]
