"""Agrupación de tickets similares. Ollama siempre simulado: similarity.score y chatbot.post_chat."""
import json
import logging
import os
import random
import re
import string
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import attachments, chatbot, config, grouping, models, similarity
from app.database import SessionLocal
from app.email_processing import process_message

MAILBOX = "soporte@ejemplo.com"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
TEMPLATE_BODY = (
    "Categoría: Software\n"
    "Prioridad: Alta\n"
    "Teléfono: 8888-1234\n"
    "Cédula: 1-0234-0567\n"
    "Descripción: {description}"
)
# Cada prueba usa su propia marca: los tickets de otras pruebas nunca se parecen
TAG_RE = re.compile(r"zz[a-z]{10}")


def new_tag() -> str:
    return "zz" + "".join(random.choices(string.ascii_lowercase, k=10))


@pytest.fixture
def grouping_on(monkeypatch, sent):
    """Agrupación activa. Similitud 0,95 si los dos textos tienen la misma marca; si no, 0,10."""
    monkeypatch.setattr(config, "GROUPING_ENABLED", True)
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    monkeypatch.setattr(config, "APP_URL", "http://soporte.test")
    state = {"same": True, "chat_calls": [], "score_error": None}

    def fake_score(db, ticket_id, text, candidates, deadline):
        if state["score_error"] is not None:
            raise state["score_error"]
        tag = TAG_RE.search(text)
        result = [(cid, 0.95 if tag and tag.group(0) in ctext else 0.10) for cid, ctext in candidates]
        return sorted(result, key=lambda pair: pair[1], reverse=True)

    def fake_post_chat(payload, task="chat", timeout=None):
        state["chat_calls"].append({"task": task, "timeout": timeout, "payload": payload})
        return json.dumps({"mismo_problema": state["same"], "motivo": "Describen la misma falla"})

    monkeypatch.setattr(similarity, "score", fake_score)
    monkeypatch.setattr(chatbot, "post_chat", fake_post_chat)
    return state


def get(model, obj_id):
    db = SessionLocal()
    try:
        obj = db.get(model, obj_id)
        if obj is not None:
            db.expunge(obj)
        return obj
    finally:
        db.close()


def set_fields(model, obj_id, **values):
    db = SessionLocal()
    try:
        obj = db.get(model, obj_id)
        for key, value in values.items():
            setattr(obj, key, value)
        db.commit()
    finally:
        db.close()


def rows(model, *filters):
    db = SessionLocal()
    try:
        result = db.query(model).filter(*filters).order_by(model.id).all()
        for row in result:
            db.expunge(row)
        return result
    finally:
        db.close()


def active_merge(source_id):
    found = rows(models.TicketMerge, models.TicketMerge.source_ticket_id == source_id, models.TicketMerge.undone_at.is_(None))
    return found[0] if found else None


def active_link(related_id):
    found = rows(models.TicketLink, models.TicketLink.related_ticket_id == related_id, models.TicketLink.undone_at.is_(None))
    return found[0] if found else None


def assign(client, coordinator, ticket_id, user_id):
    response = client.patch(f"/api/tickets/{ticket_id}", json={"assigned_to": user_id, "confirm_unavailable": True},
                            headers=coordinator["headers"])
    assert response.status_code == 200, response.text


def upload(client, ticket_id, actor, name="foto.png", data=PNG):
    response = client.post(
        f"/api/tickets/{ticket_id}/comments/with-attachments",
        data={"content": "Adjunto la captura", "internal": "false"},
        files=[("files", (name, data, "application/octet-stream"))],
        headers=actor["headers"],
    )
    assert response.status_code == 200, response.text


def email(sender, subject, body, files=()):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = MAILBOX
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
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


# ----- Caso A: mismo cliente -----

def test_same_requester_same_problem_merges(client, make_user, make_ticket, grouping_on, sent):
    tag = new_tag()
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    first = make_ticket(requester, title=f"Outlook no envía correos {tag}")
    second = make_ticket(requester, title=f"Sigo sin poder enviar en Outlook {tag}")

    assert get(models.Ticket, second["id"]).status == "closed"
    merge = active_merge(second["id"])
    assert merge.target_ticket_id == first["id"] and merge.automatic and merge.similarity == 0.95
    # El mensaje del segundo queda como comentario del cliente en el primero
    comments = rows(models.Comment, models.Comment.ticket_id == first["id"])
    assert comments[-1].user_id == requester["id"]
    assert comments[-1].content.startswith(f"Agregado automáticamente desde #{second['id']}")
    assert f"Descripción de Sigo sin poder enviar en Outlook {tag}" in comments[-1].content
    # Aviso al cliente
    notice = [m for m in sent if m[0] == requester["email"] and "se agregó" in m[1]]
    assert notice and f"solicitud #{first['id']}" in notice[0][2]
    assert grouping_on["chat_calls"][0]["task"] == "group_check"
    # Fuera de la cola
    queue = client.get(f"/api/queue?view=all&search={tag}", headers=coordinator["headers"]).json()
    assert [item["id"] for item in queue["items"]] == [first["id"]]


def test_requester_sees_only_merged_into(client, make_user, make_ticket, grouping_on):
    tag = new_tag()
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    first = make_ticket(requester, title=f"Impresora atascada {tag}")
    second = make_ticket(requester, title=f"La impresora sigue atascada {tag}")

    own = client.get(f"/api/tickets/{second['id']}", headers=requester["headers"]).json()
    assert own["merged_into"] == first["id"]
    assert "grouping" not in own
    staff = client.get(f"/api/tickets/{first['id']}", headers=coordinator["headers"]).json()
    assert staff["grouping"]["merged_from"][0]["ticket_id"] == second["id"]
    assert staff["grouping"]["merged_from"][0]["can_separate"] is True
    source = client.get(f"/api/tickets/{second['id']}", headers=coordinator["headers"]).json()
    assert source["grouping"]["merged_into"]["ticket_id"] == first["id"]


def test_same_requester_different_problem_is_not_merged(make_user, make_ticket, grouping_on):
    requester = make_user("requester")
    make_ticket(requester, title=f"Outlook no envía {new_tag()}")
    second = make_ticket(requester, title=f"Necesito permisos en la carpeta {new_tag()}")
    assert get(models.Ticket, second["id"]).status == "new"
    assert active_merge(second["id"]) is None
    # Sin candidato que pase el umbral no se le pregunta a la IA
    assert grouping_on["chat_calls"] == []


def test_ai_says_no_with_high_similarity(make_user, make_ticket, grouping_on):
    grouping_on["same"] = False
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"Outlook no envía {tag}")
    second = make_ticket(requester, title=f"Outlook no recibe {tag}")
    assert get(models.Ticket, second["id"]).status == "new"
    assert active_merge(second["id"]) is None
    assert len(grouping_on["chat_calls"]) == 1


def test_ai_prompt_keeps_rules_in_system_and_masks_data(make_user, make_ticket, grouping_on):
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"VPN no conecta {tag}")
    make_ticket(requester, title=f"VPN caída, escribir a jefe@empresa.com o al 88881234 {tag}")
    messages = grouping_on["chat_calls"][0]["payload"]["messages"]
    assert messages[0]["role"] == "system" and messages[0]["content"] == grouping.CHECK_RULES
    assert "jefe@empresa.com" not in messages[1]["content"]
    assert "88881234" not in messages[1]["content"]
    assert grouping_on["chat_calls"][0]["timeout"] > 0


def test_merge_copies_attachments_and_separate_restores(client, make_user, make_ticket, grouping_on, monkeypatch):
    tag = new_tag()
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    first = make_ticket(requester, title=f"Pantalla azul {tag}")
    second = make_ticket(requester, title=f"Otra vez pantalla azul {tag}")
    upload(client, second["id"], requester)
    monkeypatch.setattr(config, "GROUPING_ENABLED", True)

    assert grouping.after_ticket_created(second["id"]) == "merged"
    merge = active_merge(second["id"])
    original = rows(models.Attachment, models.Attachment.ticket_id == second["id"])[0]
    copy = rows(models.Attachment, models.Attachment.comment_id == merge.copied_comment_id)[0]
    assert copy.ticket_id == first["id"] and copy.original_name == "foto.png"
    assert copy.stored_name != original.stored_name
    assert os.path.exists(attachments.path_of(copy))

    response = client.post(f"/api/merges/{merge.id}/undo", headers=coordinator["headers"])
    assert response.status_code == 204, response.text
    restored = get(models.Ticket, second["id"])
    assert restored.status == "new" and restored.assigned_to is None
    assert get(models.Comment, merge.copied_comment_id) is None
    assert rows(models.Attachment, models.Attachment.ticket_id == first["id"]) == []
    assert not os.path.exists(attachments.path_of(copy))
    # El archivo propio del ticket separado sigue ahí
    assert os.path.exists(attachments.path_of(original))
    assert active_merge(second["id"]) is None
    assert get(models.TicketMerge, merge.id).undone_by == coordinator["id"]
    # Vuelve a la cola
    queue = client.get(f"/api/queue?view=all&search={tag}", headers=coordinator["headers"]).json()
    assert {item["id"] for item in queue["items"]} == {first["id"], second["id"]}
    fields = [h.field for h in rows(models.History, models.History.ticket_id == second["id"])]
    assert "merged" in fields and "unmerged" in fields


def test_email_ticket_merged_skips_received_notice(make_user, make_ticket, grouping_on, sent):
    tag = new_tag()
    requester = make_user("requester", profile=False)
    db = SessionLocal()
    try:
        user = db.get(models.User, requester["id"])
        user.name, user.phone, user.id_number = "Cliente Correo", "88881234", "102340567"
        db.commit()
    finally:
        db.close()
    first = make_ticket(requester, title=f"Planillas no abre {tag}")
    msg = email(requester["email"], f"[Soporte] Planillas sigue sin abrir {tag}",
                TEMPLATE_BODY.format(description=f"No abre el sistema de planillas {tag}"))
    assert process(msg) == "created"
    created = rows(models.Ticket, models.Ticket.created_by == requester["id"], models.Ticket.source == "email")[0]
    assert active_merge(created.id).target_ticket_id == first["id"]
    subjects = [m[1] for m in sent if m[0] == requester["email"]]
    assert any("se agregó" in s for s in subjects)
    assert not any("Recibimos su solicitud" in s for s in subjects)


def test_draft_merged_and_separate_returns_to_analyzing(client, make_user, make_ticket, grouping_on, monkeypatch, sent):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    tag = new_tag()
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    first = make_ticket(requester, title=f"Internet lento {tag}")
    msg = email(requester["email"], f"Internet sigue lento {tag}", f"Buenas, el internet sigue muy lento {tag}",
                files=[("captura.png", PNG, "image/png")])
    assert process(msg) == "draft_created"
    draft = rows(models.EmailDraft, models.EmailDraft.requester_id == requester["id"])[-1]
    assert draft.status == "confirmed" and draft.ticket_id == first["id"]
    merge = rows(models.TicketMerge, models.TicketMerge.source_draft_id == draft.id)[0]
    files = rows(models.Attachment, models.Attachment.comment_id == merge.copied_comment_id)
    assert [f.original_name for f in files] == ["captura.png"]
    assert rows(models.DraftAttachment, models.DraftAttachment.draft_id == draft.id) == []
    assert any(m[0] == requester["email"] and "se agregó" in m[1] for m in sent)
    listed = client.get("/api/drafts?status=confirmed", headers=coordinator["headers"])
    assert listed.status_code == 200, listed.text
    assert [d["merged_into"] for d in listed.json() if d["id"] == draft.id] == [first["id"]]

    response = client.post(f"/api/merges/{merge.id}/undo", headers=coordinator["headers"])
    assert response.status_code == 204, response.text
    back = get(models.EmailDraft, draft.id)
    assert back.status == "analyzing" and back.ticket_id is None
    returned = rows(models.DraftAttachment, models.DraftAttachment.draft_id == draft.id)
    assert [f.original_name for f in returned] == ["captura.png"]
    # Mismo archivo en disco: vuelve al borrador, no se borra
    assert os.path.exists(os.path.join(config.ATTACHMENTS_DIR, returned[0].stored_name))
    assert get(models.Comment, merge.copied_comment_id) is None


# ----- Caso B: varios clientes -----

def test_three_clients_link_mark_incident_and_inherit_assignment(client, make_user, make_ticket, grouping_on, monkeypatch):
    monkeypatch.setattr(config, "GROUPING_INCIDENT_MIN", 3)
    tag = new_tag()
    coordinator = make_user("coordinator")
    technician = make_user("technician", name="Técnico Uno")
    other_technician = make_user("technician", name="Técnico Dos")
    clients = [make_user("requester", name=f"Cliente {i}") for i in range(4)]

    principal = make_ticket(clients[0], title=f"No hay internet en el edificio {tag}")
    second = make_ticket(clients[1], title=f"Se cayó el internet {tag}")
    third = make_ticket(clients[2], title=f"Sin internet desde hace rato {tag}")
    assert active_link(second["id"]).principal_ticket_id == principal["id"]
    assert active_link(third["id"]).principal_ticket_id == principal["id"]
    assert rows(models.TicketIncident, models.TicketIncident.principal_ticket_id == principal["id"]) == []

    # Una asignación manual nunca se pisa
    assign(client, coordinator, third["id"], other_technician["id"])
    assign(client, coordinator, principal["id"], technician["id"])
    assert get(models.Ticket, second["id"]).assigned_to == technician["id"]
    assert get(models.Ticket, third["id"]).assigned_to == other_technician["id"]

    # El cuarto se liga al principal (aunque el más parecido sea otro relacionado) y hereda el responsable
    fourth = make_ticket(clients[3], title=f"Internet caído {tag}")
    assert active_link(fourth["id"]).principal_ticket_id == principal["id"]
    assert get(models.Ticket, fourth["id"]).assigned_to == technician["id"]
    assert len(rows(models.TicketIncident, models.TicketIncident.principal_ticket_id == principal["id"])) == 1

    incidents = client.get("/api/incidents", headers=coordinator["headers"]).json()
    mine = [i for i in incidents if i["principal_id"] == principal["id"]]
    assert mine and mine[0]["clients"] == 4
    assert "Cliente" not in json.dumps(mine)
    assert client.get("/api/incidents", headers=technician["headers"]).status_code == 403
    queue = client.get(f"/api/queue?view=all&search={tag}", headers=coordinator["headers"]).json()
    flags = {item["id"]: item["incident"] for item in queue["items"]}
    assert flags[principal["id"]] is True and flags[second["id"]] is False
    detail = client.get(f"/api/tickets/{principal['id']}", headers=coordinator["headers"]).json()
    assert detail["grouping"]["incident"] is True
    assert [r["ticket_id"] for r in detail["grouping"]["related"]] == [second["id"], third["id"], fourth["id"]]


def test_requester_never_sees_other_client_ticket(client, make_user, make_ticket, grouping_on):
    tag = new_tag()
    first_client = make_user("requester")
    second_client = make_user("requester")
    principal = make_ticket(first_client, title=f"Wifi caído en recepción {tag}")
    related = make_ticket(second_client, title=f"No hay wifi {tag}")
    assert active_link(related["id"]) is not None
    own = client.get(f"/api/tickets/{related['id']}", headers=second_client["headers"])
    assert own.status_code == 200
    assert "grouping" not in own.json()
    assert not [h for h in own.json()["history"] if h["field"] in ("linked", "unlinked")]
    assert f"#{principal['id']}" not in own.text and "recepción" not in own.text


def test_different_problems_from_other_clients_are_not_linked(make_user, make_ticket, grouping_on):
    first_client = make_user("requester")
    second_client = make_user("requester")
    make_ticket(first_client, title=f"No hay internet {new_tag()}")
    other = make_ticket(second_client, title=f"Teclado sin letras {new_tag()}")
    assert active_link(other["id"]) is None


def test_unlink_recalculates_incident_and_returns_to_queue(client, make_user, make_ticket, grouping_on, monkeypatch):
    monkeypatch.setattr(config, "GROUPING_INCIDENT_MIN", 2)
    tag = new_tag()
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    clients = [make_user("requester") for _ in range(3)]
    principal = make_ticket(clients[0], title=f"Sistema contable caído {tag}")
    assign(client, coordinator, principal["id"], technician["id"])
    second = make_ticket(clients[1], title=f"No entra el sistema contable {tag}")
    third = make_ticket(clients[2], title=f"Contable no carga {tag}")
    assert get(models.Ticket, second["id"]).assigned_to == technician["id"]
    assert rows(models.TicketIncident, models.TicketIncident.principal_ticket_id == principal["id"])

    link = active_link(second["id"])
    response = client.post(f"/api/links/{link.id}/undo", headers=coordinator["headers"])
    assert response.status_code == 204, response.text
    separated = get(models.Ticket, second["id"])
    assert separated.assigned_to is None and separated.status == "new"
    assert active_link(second["id"]) is None
    assert active_link(third["id"]) is not None
    assert rows(models.TicketIncident, models.TicketIncident.principal_ticket_id == principal["id"]) == []
    # Ya separado: no se puede separar dos veces
    assert client.post(f"/api/links/{link.id}/undo", headers=coordinator["headers"]).status_code == 404


def test_resolving_principal_resolves_related_with_masked_reply(client, make_user, make_ticket, grouping_on, sent):
    tag = new_tag()
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    owner = make_user("requester", name="Ana Quesada")
    other = make_user("requester", name="Bruno Solís")
    principal = make_ticket(owner, title=f"Correo caído en contabilidad {tag}")
    related = make_ticket(other, title=f"No me llegan correos {tag}")
    assign(client, coordinator, principal["id"], technician["id"])

    reply = ("Ana, ya reiniciamos el servidor de correo. Si vuelve a fallar escriba a "
             "ana.quesada@empresa.com o llame al 88881234.")
    response = client.post(f"/api/tickets/{principal['id']}/comments", json={"content": reply, "internal": False},
                           headers=technician["headers"])
    assert response.status_code == 200, response.text
    for status in ("in_progress", "resolved"):
        response = client.patch(f"/api/tickets/{principal['id']}", json={"status": status}, headers=technician["headers"])
        assert response.status_code == 200, response.text

    assert get(models.Ticket, related["id"]).status == "resolved"
    copied = rows(models.Comment, models.Comment.ticket_id == related["id"])[-1]
    assert "servidor de correo" in copied.content
    for private in ("Ana", "Quesada", "ana.quesada", "empresa.com", "88881234"):
        assert private.lower() not in copied.content.lower()
    assert not internal(copied.id)
    to_other = [m for m in sent if m[0] == other["email"] and m[1] == f"[Ticket #{related['id']}] Resuelto"]
    assert len(to_other) == 1
    body = to_other[0][2]
    assert "servidor de correo" in body and f"#{related['id']}" in body
    for private in ("Ana", "Quesada", owner["email"], f"#{principal['id']}", "contabilidad"):
        assert private not in body
    # Nadie más recibe el correo de resuelto de ese relacionado
    assert not [m for m in sent if m[1] == f"[Ticket #{related['id']}] Resuelto" and m[0] != other["email"]]


def internal(comment_id) -> bool:
    from app import comments as internal_comments

    db = SessionLocal()
    try:
        return comment_id in internal_comments.internal_ids(db, [comment_id])
    finally:
        db.close()


# ----- Candidatos que no se usan -----

def test_excluded_candidates(make_user, make_ticket, grouping_on, monkeypatch):
    requester = make_user("requester")
    other = make_user("requester")
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def candidate(owner, tag, **values):
        monkeypatch.setattr(config, "GROUPING_ENABLED", False)
        ticket = make_ticket(owner, title=f"Problema con la impresora {tag}")
        monkeypatch.setattr(config, "GROUPING_ENABLED", True)
        if values:
            set_fields(models.Ticket, ticket["id"], **values)
        return ticket

    def detect(tag):
        db = SessionLocal()
        try:
            return grouping.detect(db, requester["id"], f"Impresora {tag}", f"No imprime {tag}")
        finally:
            db.close()

    cases = {
        "closed": (requester, {"status": "closed"}),
        "resolved": (other, {"status": "resolved"}),
        "same requester 8 days": (requester, {"created_at": now - timedelta(days=8)}),
        "other requester 49 h": (other, {"created_at": now - timedelta(hours=49)}),
    }
    for name, (owner, values) in cases.items():
        tag = new_tag()
        candidate(owner, tag, **values)
        assert detect(tag) is None, name

    # Fusionado en otro (aunque siga abierto por un error): no es candidato
    tag = new_tag()
    merged = candidate(requester, tag)
    target = candidate(other, new_tag())
    db = SessionLocal()
    try:
        db.add(models.TicketMerge(source_ticket_id=merged["id"], target_ticket_id=target["id"], kind="ticket",
                                  similarity=0.9, automatic=True, created_at=now))
        db.commit()
    finally:
        db.close()
    assert detect(tag) is None

    # Control: el mismo cliente con uno abierto de hace 6 días sí es candidato
    tag = new_tag()
    fresh = candidate(requester, tag, created_at=now - timedelta(days=6))
    match = detect(tag)
    assert match is not None and match.kind == "merge" and match.ticket_id == fresh["id"]


# ----- Fallas: el ticket se crea normal -----

def test_similarity_failure_leaves_ticket_normal(make_user, make_ticket, grouping_on):
    grouping_on["score_error"] = RuntimeError("Ollama caído")
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"Outlook no envía {tag}")
    second = make_ticket(requester, title=f"Outlook no envía nada {tag}")
    assert get(models.Ticket, second["id"]).status == "new"
    assert active_merge(second["id"]) is None


def test_ai_failure_leaves_ticket_normal(make_user, make_ticket, grouping_on, monkeypatch):
    def broken(payload, task="chat", timeout=None):
        raise chatbot.ChatbotUnavailable()

    monkeypatch.setattr(chatbot, "post_chat", broken)
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"Outlook no envía {tag}")
    second = make_ticket(requester, title=f"Outlook no envía nada {tag}")
    assert get(models.Ticket, second["id"]).status == "new"


def test_ai_invalid_json_leaves_ticket_normal(make_user, make_ticket, grouping_on, monkeypatch):
    monkeypatch.setattr(chatbot, "post_chat", lambda payload, task="chat", timeout=None: "esto no es JSON")
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"Outlook no envía {tag}")
    second = make_ticket(requester, title=f"Outlook no envía nada {tag}")
    assert get(models.Ticket, second["id"]).status == "new"


def test_deadline_passed_leaves_ticket_normal(make_user, make_ticket, grouping_on, monkeypatch):
    tag = new_tag()
    requester = make_user("requester")
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    make_ticket(requester, title=f"Outlook no envía {tag}")
    second = make_ticket(requester, title=f"Outlook no envía nada {tag}")
    monkeypatch.setattr(config, "GROUPING_ENABLED", True)
    # El reloj ya pasó el plazo cuando toca preguntarle a la IA (grouping y similarity usan el mismo time)
    ticks = [1000.0]
    monkeypatch.setattr(grouping.time, "monotonic", lambda: ticks.pop(0) if ticks else 2000.0)
    assert grouping.after_ticket_created(second["id"]) is None
    assert get(models.Ticket, second["id"]).status == "new"
    assert grouping_on["chat_calls"] == []


def test_disabled_does_nothing(make_user, make_ticket, grouping_on, monkeypatch):
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"Outlook no envía {tag}")
    second = make_ticket(requester, title=f"Outlook no envía nada {tag}")
    assert get(models.Ticket, second["id"]).status == "new"
    assert grouping_on["chat_calls"] == []


# ----- Permisos para Separar -----

def make_merge(client, make_user, make_ticket, requester=None):
    tag = new_tag()
    requester = requester or make_user("requester")
    first = make_ticket(requester, title=f"Monitor sin imagen {tag}")
    second = make_ticket(requester, title=f"El monitor sigue sin imagen {tag}")
    return first, second, active_merge(second["id"])


def test_separate_forbidden_for_requester_and_unassigned_technician(client, make_user, make_ticket, grouping_on):
    requester = make_user("requester")
    technician = make_user("technician")
    # El administrador tampoco separa: no entra al flujo de tickets
    first, second, merge = make_merge(client, make_user, make_ticket, requester)
    for actor in (requester, technician, make_user("admin")):
        response = client.post(f"/api/merges/{merge.id}/undo", headers=actor["headers"])
        assert response.status_code == 403
        assert response.json()["detail"] == grouping.NO_PERMISSION
    assert active_merge(second["id"]) is not None
    # Un técnico que tuvo el ticket (ya fusionado y cerrado) y ya no: lo ve en solo lectura, sin "Separar"
    db = SessionLocal()
    try:
        db.add(models.History(ticket_id=second["id"], field="assigned_to", old_value=str(technician["id"]),
                              new_value=None, changed_by=requester["id"]))
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/api/tickets/{second['id']}", headers=technician["headers"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["grouping"]["merged_into"]["can_separate"] is False


@pytest.mark.parametrize("role", ["assigned_technician", "coordinator"])
def test_separate_allowed(client, make_user, make_ticket, grouping_on, role):
    coordinator = make_user("coordinator")
    first, second, merge = make_merge(client, make_user, make_ticket)
    if role == "assigned_technician":
        actor = make_user("technician")
        assign(client, coordinator, first["id"], actor["id"])
    else:
        actor = make_user(role)
    response = client.post(f"/api/merges/{merge.id}/undo", headers=actor["headers"])
    assert response.status_code == 204, response.text
    assert active_merge(second["id"]) is None


def test_separate_link_forbidden_for_requester(client, make_user, make_ticket, grouping_on):
    tag = new_tag()
    first_client = make_user("requester")
    second_client = make_user("requester")
    make_ticket(first_client, title=f"Wifi caído {tag}")
    related = make_ticket(second_client, title=f"No hay wifi {tag}")
    link = active_link(related["id"])
    response = client.post(f"/api/links/{link.id}/undo", headers=second_client["headers"])
    assert response.status_code == 403
    assert active_link(related["id"]) is not None


# ----- Registros -----

def test_detection_log_has_no_text(make_user, make_ticket, grouping_on, caplog):
    caplog.set_level(logging.INFO)
    tag = new_tag()
    requester = make_user("requester")
    make_ticket(requester, title=f"Contraseña del correo vencida {tag}")
    make_ticket(requester, title=f"Venció la contraseña del correo {tag}")
    assert "Agrupación: merge" in caplog.text
    assert tag not in caplog.text
    assert "Contraseña" not in caplog.text and "Describen la misma falla" not in caplog.text


def test_change_log_has_similarity_and_automatic(client, make_user, make_ticket, grouping_on):
    admin = make_user("coordinator")
    first, second, merge = make_merge(client, make_user, make_ticket)
    label = f"Ticket #{second['id']} → #{first['id']}"
    merged = rows(models.AccountChange, models.AccountChange.action == "ticket_merged", models.AccountChange.target_label == label)
    assert merged[0].new_value == "similitud 0,95 · automático"
    client.post(f"/api/merges/{merge.id}/undo", headers=admin["headers"])
    undone = rows(models.AccountChange, models.AccountChange.action == "ticket_merge_undone", models.AccountChange.target_label == label)
    assert undone[0].actor_id == admin["id"]


# ----- Similitud real (Ollama simulado con httpx) -----

def test_embeddings_are_cached(make_user, make_ticket, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, count):
            self.count = count

        def json(self):
            return {"embeddings": [[1.0, 0.0]] * self.count}

    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/embed")
        calls.append(json["input"])
        return FakeResponse(len(json["input"]))

    monkeypatch.setattr(similarity.httpx, "post", fake_post)
    requester = make_user("requester")
    ticket = make_ticket(requester, title=f"Caché {new_tag()}")
    db = SessionLocal()
    try:
        deadline = similarity.time.monotonic() + 10
        first = similarity.score(db, None, "nuevo", [(ticket["id"], "texto del candidato")], deadline)
        second = similarity.score(db, None, "nuevo", [(ticket["id"], "texto del candidato")], deadline)
    finally:
        db.close()
    assert first == second == [(ticket["id"], 1.0)]
    # La segunda vez el candidato sale de ticket_embeddings: solo se calcula el texto nuevo
    assert calls == [["nuevo", "texto del candidato"], ["nuevo"]]
