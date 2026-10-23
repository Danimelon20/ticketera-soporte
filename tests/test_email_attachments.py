import io
import logging
import os
import zipfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import attachments, chatbot, config, email_attachments, graph, mailer, models
from app.database import SessionLocal
from app.email_processing import process_message

MAILBOX = "soporte@ejemplo.com"
FREE_BODY = "Buenas, desde ayer la impresora del tercer piso no imprime nada. ¿Me ayudan?"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def png(size: int) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    return header + b"\x00" * (size - len(header))


def make_zip(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"contenido")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def setup(monkeypatch, sent):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(config, "APP_URL", "http://soporte.test")

    # Con la IA encendida, los tickets de la web también se analizan: Ollama simulado sin respuesta
    def _no_ollama(payload, task="chat", timeout=None):
        raise chatbot.ChatbotUnavailable()

    monkeypatch.setattr(chatbot, "post_chat", _no_ollama)
    db = SessionLocal()
    try:
        db.query(models.EmailDraft).delete()
        db.commit()
    finally:
        db.close()


def email(sender, body=FREE_BODY, subject="Problema con la impresora", files=(), inline=(), html=None):
    """files: (nombre, contenido, tipo/subtipo). inline: (contenido, subtipo) incrustadas con Content-ID."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = MAILBOX
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    if html is not None or inline:
        msg.add_alternative(html or "<p>Hola</p>", subtype="html")
        html_part = msg.get_payload()[-1]
        for data, subtype in inline:
            html_part.add_related(data, maintype="image", subtype=subtype, cid=make_msgid())
    for name, data, kind in files:
        maintype, subtype = kind.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def process(msg):
    db = SessionLocal()
    try:
        return process_message(db, msg)
    finally:
        db.close()


def last_draft(requester_id):
    db = SessionLocal()
    try:
        draft = (
            db.query(models.EmailDraft)
            .filter(models.EmailDraft.requester_id == requester_id)
            .order_by(models.EmailDraft.id.desc())
            .first()
        )
        files = db.query(models.DraftAttachment).filter(models.DraftAttachment.draft_id == draft.id).all()
        notes = db.query(models.DraftAttachmentNote).filter(models.DraftAttachmentNote.draft_id == draft.id).all()
        return draft.id, [(f.original_name, f.stored_name) for f in files], [n.text for n in notes]
    finally:
        db.close()


def on_disk(stored_name: str) -> bool:
    return os.path.isfile(os.path.join(config.ATTACHMENTS_DIR, stored_name))


@pytest.fixture
def requester(make_user):
    return make_user("requester", name="Cliente Correo")


# ---------- Correos que llegan ----------

def test_pdf_and_screenshot_kept_logo_dropped(requester):
    msg = email(
        requester["email"],
        files=[("factura.pdf", PDF, "application/pdf")],
        inline=[(png(40 * 1024), "png"), (png(5 * 1024), "png")],
    )
    assert process(msg) == "draft_created"
    _, files, notes = last_draft(requester["id"])
    assert [name for name, _ in files] == ["imagen-1.png", "factura.pdf"]
    assert notes == []
    assert all(on_disk(stored) for _, stored in files)


def test_attached_email_is_ignored(requester):
    inner = email("otra@ejemplo.com", files=[("adentro.pdf", PDF, "application/pdf")])
    msg = email(requester["email"])
    msg.add_attachment(inner)
    process(msg)
    _, files, notes = last_draft(requester["id"])
    assert files == [] and notes == []


@pytest.mark.parametrize("name,data,kind,reason", [
    ("programa.exe", b"MZ\x90\x00", "application/octet-stream", "no es un tipo de archivo permitido"),
    ("paquete.zip", make_zip(["setup.exe"]), "application/zip", "el .zip contiene archivos que no se permiten"),
])
def test_rejected_files_are_noted(requester, name, data, kind, reason):
    process(email(requester["email"], files=[(name, data, kind)]))
    _, files, notes = last_draft(requester["id"])
    assert files == []
    assert notes == [f"No se adjuntó «{name}»: {reason}."]


def test_cloud_link_is_noted(requester):
    html = '<p>Ver <a href="https://empresa-my.sharepoint.com/:b:/g/personal/x/abc">el archivo</a></p>'
    process(email(requester["email"], html=html))
    _, files, notes = last_draft(requester["id"])
    assert notes == [email_attachments.CLOUD_NOTE]


def test_more_than_five_files(requester):
    files = [(f"doc{i}.pdf", PDF, "application/pdf") for i in range(7)]
    process(email(requester["email"], files=files))
    _, saved, notes = last_draft(requester["id"])
    assert [name for name, _ in saved] == [f"doc{i}.pdf" for i in range(5)]
    assert notes == ["Quedaron fuera 2 archivo(s): se toman como máximo 5 por correo."]


def test_received_is_recorded_without_names(requester):
    process(email(requester["email"], files=[("factura.pdf", PDF, "application/pdf"), ("x.exe", b"MZ", "application/octet-stream")]))
    draft_id, _, _ = last_draft(requester["id"])
    db = SessionLocal()
    try:
        row = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.action == "email_attachments_received")
            .order_by(models.AccountChange.id.desc())
            .first()
        )
        assert row.target_label == f"Borrador #{draft_id}"
        assert row.new_value == "1 adjunto(s), 1 rechazado(s)"
    finally:
        db.close()


def test_logs_never_have_names(requester, caplog):
    caplog.set_level(logging.DEBUG)
    process(email(requester["email"], files=[("Cédula secreta.pdf", PDF, "application/pdf"), ("malo secreto.exe", b"MZ", "application/octet-stream")]))
    assert "secret" not in caplog.text


# ---------- Confirmar y descartar ----------

def confirm(client, draft_id, reviewer):
    return client.post(
        f"/api/drafts/{draft_id}/confirm",
        json={"title": "Impresora", "description": "No imprime", "category_id": 1, "priority_id": 2},
        headers=reviewer["headers"],
    )


def test_confirm_moves_files_to_first_message(client, make_user, requester, monkeypatch):
    from app import auto_assign
    monkeypatch.setattr(auto_assign, "assign_new", lambda ticket_id: None)
    process(email(requester["email"], files=[("factura.pdf", PDF, "application/pdf"), ("x.exe", b"MZ", "application/octet-stream")]))
    draft_id, files, _ = last_draft(requester["id"])
    coordinator = make_user("coordinator")
    # Quien revisa borradores ve sus adjuntos; el solicitante no
    draft = next(d for d in client.get("/api/drafts?status=analyzing", headers=coordinator["headers"]).json() if d["id"] == draft_id)
    assert [a["original_name"] for a in draft["attachments"]] == ["factura.pdf"]
    assert draft["attachment_notes"] == ["No se adjuntó «x.exe»: no es un tipo de archivo permitido."]
    preview = client.get(f"/api/drafts/attachments/{draft['attachments'][0]['id']}?preview=1", headers=coordinator["headers"])
    assert preview.status_code == 200 and preview.content == PDF
    assert client.get(f"/api/drafts/attachments/{draft['attachments'][0]['id']}", headers=requester["headers"]).status_code == 403

    response = confirm(client, draft_id, coordinator)
    assert response.status_code == 200, response.text
    ticket_id = response.json()["ticket_id"]
    assert on_disk(files[0][1])

    detail = client.get(f"/api/tickets/{ticket_id}", headers=requester["headers"]).json()
    first = detail["comments"][0]
    assert first["user_id"] == requester["id"] and first["content"] == ""
    assert [a["original_name"] for a in first["attachments"]] == ["factura.pdf"]
    download = client.get(f"/api/attachments/{first['attachments'][0]['id']}", headers=requester["headers"])
    assert download.status_code == 200 and download.content == PDF
    # La nota de lo rechazado es interna: el solicitante no la ve, el personal sí
    assert not any("x.exe" in c["content"] for c in detail["comments"])
    staff = client.get(f"/api/tickets/{ticket_id}", headers=coordinator["headers"]).json()
    assert any(c["internal"] and "x.exe" in c["content"] for c in staff["comments"])


def test_discard_removes_files(client, make_user, requester):
    process(email(requester["email"], files=[("factura.pdf", PDF, "application/pdf")]))
    draft_id, files, _ = last_draft(requester["id"])
    assert on_disk(files[0][1])
    coordinator = make_user("coordinator")
    assert client.post(f"/api/drafts/{draft_id}/discard", headers=coordinator["headers"]).status_code == 200
    assert not on_disk(files[0][1])
    _, left, _ = last_draft(requester["id"])
    assert left == []


def test_stale_drafts_are_purged(requester):
    process(email(requester["email"], files=[("viejo.pdf", PDF, "application/pdf")]))
    old_id, old_files, _ = last_draft(requester["id"])
    process(email(requester["email"], files=[("nuevo.pdf", PDF, "application/pdf")]))
    new_id, new_files, _ = last_draft(requester["id"])
    db = SessionLocal()
    try:
        now = datetime(2026, 10, 22, 12, 0)
        for draft_id, age in ((old_id, 31), (new_id, 5)):
            draft = db.get(models.EmailDraft, draft_id)
            draft.status = "not_support"
            draft.created_at = now - timedelta(days=age)
        db.commit()
        assert email_attachments.purge_stale_drafts(db, now) == 1
    finally:
        db.close()
    assert not on_disk(old_files[0][1])
    assert on_disk(new_files[0][1])


# ---------- Respuestas a un ticket y tickets directos ----------

def test_reply_to_ticket_adds_files_to_comment(client, requester, make_ticket, sent):
    ticket = make_ticket(requester)
    db = SessionLocal()
    try:
        db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=requester["id"]))
        db.commit()
    finally:
        db.close()
    subject = f"RE: [Ticket #{ticket['id']}] Nuevo comentario"
    assert process(email(requester["email"], body="Le mando la factura", subject=subject,
                         files=[("factura.pdf", PDF, "application/pdf")])) == "comment_added"
    # Solo con el adjunto, sin texto: también se guarda
    assert process(email(requester["email"], body="", subject=subject,
                         files=[("foto.png", png(100), "image/png")])) == "comment_added"
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"]).json()
    comments = [c for c in detail["comments"] if c["attachments"]]
    assert [(c["content"], c["attachments"][0]["original_name"]) for c in comments] == [
        ("Le mando la factura", "factura.pdf"), ("", "foto.png"),
    ]


def test_template_ticket_gets_files_in_first_message(client, requester, monkeypatch):
    from app import auto_assign
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(auto_assign, "assign_new", lambda ticket_id: None)
    body = (
        "Categoría: Software\nPrioridad: Alta\nTeléfono: 8888-1234\n"
        f"Cédula: {requester['id_number']}\nDescripción: No abre el sistema de planillas."
    )
    assert process(email(requester["email"], body=body, subject="[Soporte] Planillas",
                         files=[("captura.png", png(100), "image/png")])) == "created"
    tickets = client.get("/api/tickets", headers=requester["headers"]).json()
    detail = client.get(f"/api/tickets/{tickets[0]['id']}", headers=requester["headers"]).json()
    first = detail["comments"][0]
    assert first["user_id"] == requester["id"]
    assert [a["original_name"] for a in first["attachments"]] == ["captura.png"]


# ---------- Correos que salen ----------

@pytest.fixture
def followed_ticket(make_user, make_ticket, requester):
    ticket = make_ticket(requester)
    coordinator = make_user("coordinator", name="Coordinadora")
    # Otro seguidor que ve el ticket (un técnico no asignado ya no lo ve ni recibe sus avisos)
    follower = make_user("coordinator", name="Coordinador Seguidor")
    db = SessionLocal()
    try:
        for user in (requester, follower):
            db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=user["id"]))
        db.commit()
    finally:
        db.close()
    return ticket, coordinator, follower


def reply(client, ticket_id, actor, files, internal=False, content="Le envío lo pedido"):
    return client.post(
        f"/api/tickets/{ticket_id}/comments/with-attachments",
        data={"content": content, "internal": "true" if internal else "false"},
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=actor["headers"],
    )


def sent_to(sent, address):
    return [(mail, files) for mail, files in zip(sent, sent.attachments) if mail[0] == address]


def test_reply_with_files_goes_to_requester(client, followed_ticket, requester, sent):
    ticket, coordinator, follower = followed_ticket
    response = reply(client, ticket["id"], coordinator, [("factura.pdf", PDF), ("foto.png", png(100))])
    assert response.status_code == 200
    [(mail, files)] = sent_to(sent, requester["email"])
    assert [(name, data) for name, data, _ in files] == [("factura.pdf", PDF), ("foto.png", png(100))]
    assert "Adjuntó 2 archivo(s)." in mail[2]
    [(other, other_files)] = sent_to(sent, follower["email"])
    assert other_files is None
    assert "Adjuntó 2 archivo(s)." in other[2]

    db = SessionLocal()
    try:
        row = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.action == "email_attachments_sent")
            .order_by(models.AccountChange.id.desc())
            .first()
        )
        assert row.target_label == f"Ticket #{ticket['id']}" and row.new_value == "2 adjunto(s)"
    finally:
        db.close()


def test_more_than_20_mb_sends_portal_link(client, followed_ticket, requester, sent):
    ticket, coordinator, _ = followed_ticket
    big = [(f"grande{i}.png", png(attachments.MAX_BYTES)) for i in range(3)]
    assert reply(client, ticket["id"], coordinator, big).status_code == 200
    [(mail, files)] = sent_to(sent, requester["email"])
    assert files is None
    assert "Los archivos adjuntos están disponibles en el portal de soporte." in mail[2]
    assert f"http://soporte.test/#ticket/{ticket['id']}" in mail[2]


def test_internal_note_with_files_sends_nothing(client, followed_ticket, sent):
    ticket, coordinator, _ = followed_ticket
    assert reply(client, ticket["id"], coordinator, [("interno.pdf", PDF)], internal=True).status_code == 200
    assert list(sent) == []


# ---------- Envío por Graph (simulado) ----------

class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


@pytest.fixture
def graph_calls(monkeypatch):
    # Este módulo reemplaza mailer.send_email con el fake de "sent": aquí se prueba el de verdad
    monkeypatch.undo()
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    calls = []

    def fake_request(method, path, json=None, content=None, headers=None):
        calls.append((method, path, json))
        if path == "/me/messages":
            return FakeResponse(201, {"id": "borrador1"})
        if path.endswith("/createUploadSession"):
            return FakeResponse(201, {"uploadUrl": "https://subida.test/sesion"})
        if path.endswith("/send"):
            return FakeResponse(202)
        return FakeResponse(201)

    def fake_upload(url, data):
        calls.append(("UPLOAD", url, len(data)))

    monkeypatch.setattr(graph, "graph_request", fake_request)
    monkeypatch.setattr(graph, "upload_to_session", fake_upload)
    return calls


def test_graph_small_files_use_post(graph_calls):
    assert mailer.send_email("cliente@ejemplo.com", "Asunto", "Hola", attachments=[("a.pdf", PDF, "application/pdf")])
    paths = [(c[0], c[1]) for c in graph_calls]
    assert paths == [
        ("POST", "/me/messages"),
        ("POST", "/me/messages/borrador1/attachments"),
        ("POST", "/me/messages/borrador1/send"),
    ]
    assert graph_calls[1][2]["name"] == "a.pdf"


def test_graph_big_files_use_upload_session(graph_calls):
    big = png(4 * 1024 * 1024)
    assert mailer.send_email("cliente@ejemplo.com", "Asunto", "Hola", attachments=[("grande.png", big, "image/png")])
    assert ("POST", "/me/messages/borrador1/attachments/createUploadSession") in [(c[0], c[1]) for c in graph_calls]
    assert ("UPLOAD", "https://subida.test/sesion", len(big)) in graph_calls
    assert graph_calls[-1][1] == "/me/messages/borrador1/send"


def test_graph_failure_deletes_draft_and_returns_false(graph_calls, monkeypatch):
    def broken(url, data):
        raise graph.GraphError(500, "falló")
    monkeypatch.setattr(graph, "upload_to_session", broken)
    big = png(4 * 1024 * 1024)
    assert mailer.send_email("cliente@ejemplo.com", "Asunto", "Hola", attachments=[("grande.png", big, "image/png")]) is False
    assert ("DELETE", "/me/messages/borrador1") in [(c[0], c[1]) for c in graph_calls]


def test_upload_to_session_sends_chunks(monkeypatch):
    puts = []

    class Response:
        status_code = 200

    def fake_put(url, content=None, headers=None, timeout=None):
        puts.append((headers["Content-Range"], len(content), "Authorization" in headers))
        return Response()

    monkeypatch.setattr(graph.httpx, "put", fake_put)
    data = b"x" * (graph.UPLOAD_CHUNK_BYTES + 10)
    graph.upload_to_session("https://subida.test/sesion", data)
    assert puts == [
        (f"bytes 0-{graph.UPLOAD_CHUNK_BYTES - 1}/{len(data)}", graph.UPLOAD_CHUNK_BYTES, False),
        (f"bytes {graph.UPLOAD_CHUNK_BYTES}-{len(data) - 1}/{len(data)}", 10, False),
    ]


def test_email_disabled_logs_only_the_count(monkeypatch, caplog):
    monkeypatch.undo()
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    caplog.set_level(logging.INFO)
    assert mailer.send_email("cliente@ejemplo.com", "Asunto", "Hola",
                             attachments=[("Cédula secreta.pdf", PDF, "application/pdf")]) is False
    assert "con 1 adjuntos" in caplog.text
    assert "secreta" not in caplog.text
