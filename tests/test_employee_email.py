"""Sin portal: los problemas llegan por correo de los empleados (INTERNAL_DOMAINS). La primera vez se crea su
contacto, sin contraseña ni acceso; no se pide cédula y el teléfono es opcional. Los de afuera reciben «Este buzón
es solo para el personal de la empresa» (sin enlace de registro); un cliente archivado puede responder a su ticket
abierto, pero no abrir temas nuevos. Quien reportó por correo recibe el «Resuelto» con la solución."""
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import config, email_analysis, mailer, models
from app.database import SessionLocal
from app.email_processing import NOT_INTERNAL_BODY, process_message
from app.security import verify_password
from org_helpers import set_department, team

MAILBOX = "soporte@ejemplo.com"


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body, **kw: calls.append((to, subject, body)) or True)
    return calls


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr", "test.com"])
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)


def _email(sender, body, subject="Ayuda", name="Ana Mora"):
    msg = EmailMessage()
    msg["From"] = f"{name} <{sender}>"
    msg["To"] = MAILBOX
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    return msg


def _process(msg):
    with SessionLocal() as db:
        return process_message(db, msg)


def _user(email):
    with SessionLocal() as db:
        return db.query(models.User).filter(models.User.email == email).first()


TEMPLATE = "Categoría: Software\nPrioridad: Alta\nDescripción: No abre el sistema de planillas."


def test_first_email_creates_a_contact_without_access(sent):
    email = "laura.vargas@data.cr"
    assert _process(_email(email, TEMPLATE, subject="[Soporte] Planillas", name="Laura Vargas")) == "created"
    contact = _user(email)
    assert contact.role == "requester" and contact.name == "Laura Vargas" and contact.is_active
    # Sin contraseña utilizable: nunca inicia sesión
    assert not verify_password("", contact.password_hash) and not verify_password("!", contact.password_hash)
    with SessionLocal() as db:
        ticket = db.query(models.Ticket).filter(models.Ticket.created_by == contact.id).one()
        assert ticket.contact_id_number is None and ticket.contact_phone is None
    # «Recibimos su solicitud», sin enlace al portal
    [(to, subject, body)] = [m for m in sent if m[0] == email]
    assert "Recibimos su solicitud" in subject and config.APP_URL not in body
    # La segunda vez usa el mismo contacto
    assert _process(_email(email, TEMPLATE, subject="[Soporte] Otra vez")) == "created"
    with SessionLocal() as db:
        assert db.query(models.User).filter(models.User.email == email).count() == 1


def test_no_id_number_and_optional_phone(sent):
    email = "jose.rojas@data.cr"
    body = TEMPLATE.replace("Prioridad: Alta", "Prioridad: Alta\nTeléfono: 8888-1234\nCédula: esto ya no se lee")
    assert _process(_email(email, body, subject="[Soporte] Con teléfono")) == "created"
    with SessionLocal() as db:
        ticket = db.query(models.Ticket).filter(models.Ticket.created_by == _user(email).id).one()
        assert ticket.contact_phone == "88881234" and ticket.contact_id_number is None
    assert _user(email).phone == "88881234"
    # Un teléfono mal escrito no detiene el ticket
    assert _process(_email(email, TEMPLATE.replace("Prioridad: Alta", "Teléfono: 12"), subject="[Soporte] Mal")) == "created"


def test_missing_template_lines_ask_without_id_number(sent):
    email = "marta.solis@data.cr"
    assert _process(_email(email, "Categoría: Software", subject="[Soporte] Incompleto")) == "incomplete"
    [(_to, _subject, body)] = [m for m in sent if m[0] == email]
    assert "Cédula" not in body and "Teléfono: <8 dígitos; opcional>" in body


def test_outsiders_get_the_staff_only_answer(sent):
    assert _process(_email("alguien@gmail.com", TEMPLATE, subject="[Soporte] Hola")) == "unregistered"
    assert _user("alguien@gmail.com") is None
    [(_to, subject, body)] = sent
    assert body == NOT_INTERNAL_BODY and "Regístrese" not in body and config.APP_URL not in body


def test_archived_client_can_reply_but_not_open_new_issues(client, make_user, make_ticket, sent):
    # Un cliente de antes (otro dominio): su ticket abierto sigue y lo puede responder por correo
    archived = make_user("requester")
    with SessionLocal() as db:
        db.get(models.User, archived["id"]).email = archived["email"].replace("@test.com", "@cliente.com")
        db.commit()
        email = db.get(models.User, archived["id"]).email
    ticket = make_ticket(archived)
    with SessionLocal() as db:
        db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=archived["id"]))
        db.commit()
    reply = _email(email, "Sigue sin funcionar", subject=f"RE: [Ticket #{ticket['id']}] Nuevo comentario")
    assert _process(reply) == "comment_added"
    assert _process(_email(email, TEMPLATE, subject="[Soporte] Tema nuevo")) == "unregistered"


def test_staff_sender_is_sent_to_the_page(client, make_user, sent):
    technician = make_user("technician")
    assert _process(_email(technician["email"], TEMPLATE, subject="[Soporte] Yo")) == "staff_sender"
    assert "Nuevo ticket" in sent[-1][2]


def test_free_text_draft_does_not_wait_for_contact_data(monkeypatch, sent):
    """Con la IA, el borrador pasa directo a revisión: ya no espera teléfono ni cédula."""
    import json
    from app import chatbot

    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(chatbot, "post_chat", lambda payload, task=None: json.dumps(
        {"kind": "ticket", "title": "Sin red", "category": "Hardware", "description": "No hay red.",
         "level": 2, "summary": "No hay red."}))
    email = "carlos.mena@data.cr"
    assert _process(_email(email, "Desde las 8 no tengo red en la oficina del segundo piso.")) == "draft_created"
    email_analysis.analyze_pending_drafts(limit=10)
    with SessionLocal() as db:
        draft = db.query(models.EmailDraft).filter(models.EmailDraft.requester_id == _user(email).id).one()
        assert draft.status == "pending_review"
    [(_to, subject, body)] = [m for m in sent if m[0] == email]
    assert subject == "[Soporte] Recibimos su correo" and "Cédula" not in body


def test_confirm_draft_without_phone_or_id_number(client, make_user, sent):
    coordinator = make_user("coordinator")
    contact = make_user("requester", profile=False)
    with SessionLocal() as db:
        draft = models.EmailDraft(message_id=make_msgid(), requester_id=contact["id"], status="pending_review",
                                  subject="Sin red", body="No hay red", title="Sin red", description="No hay red.",
                                  category_id=1, priority_id=1)
        db.add(draft)
        db.commit()
        draft_id = draft.id
    response = client.post(f"/api/drafts/{draft_id}/confirm", headers=coordinator["headers"],
                           json={"title": "Sin red", "description": "No hay red.", "category_id": 1, "priority_id": 1})
    assert response.status_code == 200, response.text
    # Un teléfono mal escrito sí se rechaza (si se escribe, tiene que servir)
    with SessionLocal() as db:
        draft = models.EmailDraft(message_id=make_msgid(), requester_id=contact["id"], status="pending_review",
                                  subject="Otra", body="Otra", title="Otra", description="Otra.", category_id=1,
                                  priority_id=1)
        db.add(draft)
        db.commit()
        other_id = draft.id
    bad = client.post(f"/api/drafts/{other_id}/confirm", headers=coordinator["headers"],
                      json={"title": "Otra", "description": "Otra.", "category_id": 1, "priority_id": 1,
                            "contact_phone": "12"})
    assert bad.status_code == 422


def test_email_reporter_gets_resolved_with_solution(client, make_user, make_ticket, sent, routing_on):
    dept, coordinator, technician = team(make_user)
    contact = make_user("requester", "Rosa Mena")
    ticket = make_ticket(contact)
    set_department(ticket["id"], dept)
    with SessionLocal() as db:
        db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=contact["id"]))
        db.commit()
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    client.patch(f"/api/tickets/{ticket['id']}", json={"status": "in_progress"}, headers=technician["headers"])
    sent.clear()
    response = client.patch(f"/api/tickets/{ticket['id']}", headers=technician["headers"],
                            json={"status": "resolved", "solution": "Se reinició el enrutador del piso."})
    assert response.status_code == 200, response.text
    mails = [m for m in sent if m[0] == contact["email"]]
    assert len(mails) == 1
    _to, subject, body = mails[0]
    assert subject == f"[Ticket #{ticket['id']}] Resuelto"
    assert "Se reinició el enrutador del piso." in body and config.APP_URL not in body
    assert "Recibe este correo porque reportó este problema" in body
