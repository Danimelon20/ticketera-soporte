import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import config, database, email_poller, graph, mailer, models
from app.graph import GraphAuthRequired

MAILBOX = "soporte@ejemplo.com"
FOLDER_ID = "carpeta-ticketera"

COMPLETE_BODY = (
    "Categoría: Software\n"
    "Prioridad: Alta\n"
    "Teléfono: 8888-1234\n"
    "Cédula: 1-0234-0567\n"
    "Descripción: No abre el sistema de planillas."
)


class FakeResponse:
    def __init__(self, data=None, content=b""):
        self._data = data or {}
        self.content = content
        self.status_code = 200

    def json(self):
        return self._data


class FakeGraph:
    """Simula Graph: una carpeta "Ticketera" con correos no leídos en MIME."""

    def __init__(self, messages, folders=None):
        self.messages = messages  # id -> bytes MIME
        self.folders = folders if folders is not None else [{"id": FOLDER_ID, "displayName": "Ticketera"}]
        self.marked_read = []

    def __call__(self, method, path, json=None, content=None, headers=None):
        if method == "PATCH":
            assert json == {"isRead": True}
            self.marked_read.append(path.rsplit("/", 1)[1])
            return FakeResponse()
        if path.startswith("/me/mailFolders/inbox/childFolders"):
            return FakeResponse({"value": []})
        if path.startswith(f"/me/mailFolders/{FOLDER_ID}/messages"):
            assert "isRead%20eq%20false" in path
            return FakeResponse({"value": [{"id": i} for i in self.messages]})
        if path.startswith("/me/mailFolders"):
            return FakeResponse({"value": self.folders})
        if path.endswith("/$value"):
            message_id = path.split("/")[3]
            return FakeResponse(content=self.messages[message_id])
        raise AssertionError(f"Ruta inesperada: {method} {path}")


@pytest.fixture(autouse=True)
def poller_settings(monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    monkeypatch.setattr(config, "MAIL_FOLDER", "Ticketera")
    monkeypatch.setattr(email_poller, "_folder_ids", {})
    monkeypatch.setattr(email_poller, "SessionLocal", database.SessionLocal)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: True)


def _mime(sender, body=COMPLETE_BODY):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = MAILBOX
    msg["Subject"] = "[Soporte] Planillas"
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    return msg.as_bytes()


def test_poll_once_processes_unread(monkeypatch, make_user):
    requester = make_user("requester", "Ana", profile=False)
    fake = FakeGraph({
        "msg-1": _mime(requester["email"]),
        "msg-2": _mime(f"nadie-{uuid.uuid4().hex}@test.com"),
    })
    monkeypatch.setattr(graph, "graph_request", fake)

    assert email_poller.poll_once() == 2

    db = database.SessionLocal()
    try:
        tickets = db.query(models.Ticket).filter(models.Ticket.created_by == requester["id"]).all()
        assert len(tickets) == 1
        assert tickets[0].source == "email"
    finally:
        db.close()
    assert sorted(fake.marked_read) == ["msg-1", "msg-2"]
    assert email_poller.last_processed == 2
    assert email_poller.last_error is None
    assert email_poller.last_run is not None


def test_failed_message_is_marked_read(monkeypatch):
    fake = FakeGraph({"msg-roto": b"no es un correo"})
    monkeypatch.setattr(graph, "graph_request", fake)
    monkeypatch.setattr(email_poller, "process_message", lambda db, msg: 1 / 0)

    assert email_poller.poll_once() == 0
    assert fake.marked_read == ["msg-roto"]


def test_missing_folder(monkeypatch):
    monkeypatch.setattr(graph, "graph_request", FakeGraph({}, folders=[]))

    with pytest.raises(ValueError, match="No existe la carpeta Ticketera en el buzón"):
        email_poller.poll_once()
    assert email_poller.last_error == "No existe la carpeta Ticketera en el buzón"


def test_inbox_needs_no_lookup(monkeypatch):
    monkeypatch.setattr(config, "MAIL_FOLDER", "Inbox")
    monkeypatch.setattr(graph, "graph_request", lambda *a, **k: pytest.fail("No debe llamar a Graph"))
    assert email_poller.get_folder_id() == "inbox"


def test_auth_required(monkeypatch):
    def _no_session(*args, **kwargs):
        raise GraphAuthRequired(graph.LOGIN_REQUIRED_MESSAGE)

    monkeypatch.setattr(graph, "graph_request", _no_session)

    with pytest.raises(GraphAuthRequired):
        email_poller.poll_once()
    assert email_poller.last_error == graph.LOGIN_REQUIRED_MESSAGE
    assert "app.ms_login" in email_poller.last_error
