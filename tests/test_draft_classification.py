"""Fase 2 de la clasificación de correos: al llegar, la IA elige el departamento (al coordinador o a «Por
clasificar»), la bandeja del administrador con las dos opciones y un clic, el umbral editable y los pendientes
al encender. Ollama y los embeddings se simulan: nunca hay red."""
import json
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import auto_assign, chatbot, classifier, config, draft_classification, email_analysis, mailer, models
from app import routing, similarity
from app.database import SessionLocal
from app.email_processing import process_message
from org_helpers import changes, department, team, unique
from scripts import simulate_email

MAILBOX = "soporte@ejemplo.com"
NETWORK = "Desde las 8 no tengo conexión a internet en mi computadora. Mi teléfono 8888-1234"
PRINTER = "La impresora del segundo piso saca las hojas en blanco"
KEYWORDS = (("internet", "red", "wifi", "conexion", "conexión"), ("impresora", "imprime", "monitor", "pantalla"))


def _vector(text):
    clean = text.lower()
    return [sum(clean.count(k) for k in group) for group in KEYWORDS] + [0.1]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(mailer, "send_email", lambda *args, **kwargs: True)
    with SessionLocal() as db:
        db.query(models.EmailDraft).delete()
        db.query(models.DepartmentExampleEmbedding).delete()
        db.query(models.DepartmentProfile).delete()
        db.commit()


@pytest.fixture
def ai(monkeypatch):
    """IA simulada: el análisis del borrador y la elección del departamento (choice: ruta o None)."""
    state = {"choice": None, "draft_error": None}

    def _post_chat(payload, task=None):
        if task == "draft_analysis":
            if state["draft_error"] is not None:
                raise state["draft_error"]
            return json.dumps({"kind": "ticket", "title": "Problema", "category": "Hardware",
                               "description": "No funciona.", "level": 2, "summary": "No funciona."})
        enum = payload["format"]["properties"]["department"]["enum"]
        chosen = state["choice"] if state["choice"] in enum else classifier.NONE_CHOICE
        return json.dumps({"reason": "Según la ficha.", "department": chosen})

    monkeypatch.setattr(chatbot, "post_chat", _post_chat)
    monkeypatch.setattr(similarity, "embed", lambda texts, deadline: [_vector(t) for t in texts])
    return state


def _profile(dept_id, handles, examples):
    with SessionLocal() as db:
        db.add(models.DepartmentProfile(department_id=dept_id, handles=handles, examples=examples))
        db.commit()


def _label(dept_id):
    with SessionLocal() as db:
        people = classifier.participants(db)
        return dict(zip((p.id for p in people), classifier.labels(people)))[dept_id]


@pytest.fixture
def org(make_user):
    """Redes y Soporte con coordinador, técnico y ficha."""
    redes, redes_coord, redes_tech = team(make_user)
    soporte, soporte_coord, _ = team(make_user)
    _profile(redes, "Internet y wifi", "Sin internet en el piso\nEl wifi no conecta")
    _profile(soporte, "Impresoras y monitores", "La impresora no imprime\nEl monitor no da imagen")
    return {"redes": redes, "soporte": soporte, "redes_coord": redes_coord, "soporte_coord": soporte_coord,
            "redes_tech": redes_tech}


def _arrive(requester, body, subject="Ayuda"):
    msg = EmailMessage()
    msg["From"] = requester["email"]
    msg["To"] = MAILBOX
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    with SessionLocal() as db:
        assert process_message(db, msg) == "draft_created"
        draft_id = db.query(models.EmailDraft).filter(
            models.EmailDraft.message_id == "".join(msg["Message-ID"].split())).one().id
    email_analysis.analyze_pending_drafts(limit=10)
    return draft_id


def _row(draft_id):
    with SessionLocal() as db:
        return draft_classification.row_of(db, draft_id)


def _pending(client, admin):
    response = client.get("/api/admin/drafts/pending-classification", headers=admin["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _choose(client, user, draft_id, department_id):
    return client.post(f"/api/admin/drafts/{draft_id}/department", headers=user["headers"],
                       json={"department_id": department_id})


# ----- Al llegar el correo -----

def test_switch_off_keeps_drafts_as_before(client, make_user, ai, org, routing_switch):
    draft_id = _arrive(make_user("requester"), NETWORK)
    assert _row(draft_id) is None


def test_clear_email_goes_to_the_coordinator(client, make_user, ai, org, routing_on):
    ai["choice"] = _label(org["redes"])
    draft_id = _arrive(make_user("requester"), NETWORK)
    row = _row(draft_id)
    assert row.status == "routed" and row.department_id == org["redes"] and row.ai_agrees is True
    assert row.option1_id == org["redes"] and row.option2_id == org["soporte"] and row.ai_reason
    drafts = client.get("/api/drafts", headers=org["redes_coord"]["headers"]).json()
    mine = next(d for d in drafts if d["id"] == draft_id)
    assert mine["classification"] == "routed" and mine["department_name"].endswith(_label(org["redes"]))


def test_ambiguous_email_waits_with_two_masked_options(client, make_user, ai, org, routing_on):
    admin = make_user("admin")
    # La IA elige Soporte pero el correo se parece a Redes: no coinciden
    ai["choice"] = _label(org["soporte"])
    draft_id = _arrive(make_user("requester"), NETWORK, subject="Urgente 8888-1234")
    row = _row(draft_id)
    assert row.status == "pending" and row.pending_reason == "below_threshold" and row.department_id is None
    [item] = [i for i in _pending(client, admin)["items"] if i["draft_id"] == draft_id]
    assert [o["department_id"] for o in item["options"]] == [org["soporte"], org["redes"]]
    assert all(o["has_coordinator"] for o in item["options"])
    assert item["pending_reason"] == models.DRAFT_PENDING_REASONS["below_threshold"]
    # Teléfono enmascarado en el asunto y el fragmento
    assert "8888-1234" not in item["subject"] and "8888-1234" not in item["excerpt"]
    assert "[dato oculto]" in item["excerpt"]


def test_department_without_active_coordinator_waits(client, make_user, ai, org, routing_on):
    lonely = department(unique("Sin coordinador"))
    _profile(lonely, "Internet", "Sin internet en el piso\nNo tengo internet")
    with SessionLocal() as db:
        db.query(models.DepartmentProfile).filter(models.DepartmentProfile.department_id == org["redes"]).delete()
        db.commit()
    ai["choice"] = _label(lonely)
    draft_id = _arrive(make_user("requester"), NETWORK)
    row = _row(draft_id)
    assert row.status == "pending" and row.pending_reason == "no_coordinator" and row.ai_department_id == lonely


def test_failed_draft_analysis_waits_without_suggestion(client, make_user, ai, org, routing_on):
    ai["draft_error"] = chatbot.ChatbotUnavailable()
    requester = make_user("requester")
    msg_draft = None
    for _ in range(email_analysis.MAX_ATTEMPTS):
        msg_draft = msg_draft or _arrive(requester, NETWORK)
        email_analysis.analyze_pending_drafts(limit=10)
    row = _row(msg_draft)
    assert row is not None and row.status == "pending" and row.pending_reason == "ai_error"
    assert row.option1_id is None


def test_technicians_do_not_get_drafts_with_the_switch_on(client, make_user, ai, org, routing_on, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    ai["choice"] = _label(org["redes"])
    draft_id = _arrive(make_user("requester"), NETWORK)
    assert auto_assign.assign_draft(draft_id) == "skipped"
    assert auto_assign.run_pending_drafts() == 0
    with SessionLocal() as db:
        row = auto_assign.draft_row(db, draft_id)
        assert row is None or row.user_id is None


# ----- Por clasificar: un clic -----

def test_admin_chooses_with_one_click_and_records_the_match(client, make_user, ai, org, routing_on):
    admin = make_user("admin")
    requester = make_user("requester")
    ai["choice"] = _label(org["soporte"])
    first = _arrive(requester, NETWORK)
    second = _arrive(requester, NETWORK)
    third = _arrive(requester, NETWORK)
    # 1.ª opción (la de la IA): coincidió
    response = _choose(client, admin, first, org["soporte"])
    assert response.status_code == 200, response.text
    assert response.json()["matched_first"] is True and response.json()["department_name"]
    # 2.ª opción: no coincidió
    assert _choose(client, admin, second, org["redes"]).json()["matched_first"] is False
    # Otro departamento con coordinador (fuera de las opciones): tampoco
    other, _, _ = team(make_user)
    assert _choose(client, admin, third, other).json()["matched_first"] is False
    row = _row(first)
    assert row.status == "classified" and row.department_id == org["soporte"] and row.decided_by == admin["id"]
    remaining = [i["draft_id"] for i in _pending(client, admin)["items"]]
    assert first not in remaining and second not in remaining and third not in remaining
    # Ya clasificado
    assert _choose(client, admin, first, org["redes"]).status_code == 409
    # Lo ve el coordinador con su departamento
    drafts = client.get("/api/drafts", headers=org["soporte_coord"]["headers"]).json()
    assert next(d for d in drafts if d["id"] == first)["classification"] == "classified"


def test_choose_validations_and_permissions(client, make_user, ai, org, routing_on):
    admin = make_user("admin")
    ai["choice"] = _label(org["soporte"])
    draft_id = _arrive(make_user("requester"), NETWORK)
    without_coordinator = department(unique("Sin coordinador"))
    response = _choose(client, admin, draft_id, without_coordinator)
    assert response.status_code == 409 and response.json()["detail"] == draft_classification.NO_COORDINATOR
    assert _choose(client, admin, draft_id, department(handles=False)).status_code == 422
    assert _choose(client, admin, 999999, org["redes"]).status_code == 404
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert _choose(client, user, draft_id, org["redes"]).status_code == 403
        assert client.get("/api/admin/drafts/pending-classification", headers=user["headers"]).status_code == 403
    assert _row(draft_id).status == "pending"


# ----- Umbral -----

def test_threshold_is_editable_audited_and_applies_to_the_next_email(client, make_user, ai, org, routing_on):
    admin = make_user("admin")
    url = "/api/admin/classification/threshold"
    try:
        assert client.put(url, headers=admin["headers"], json={"threshold": 1.5}).status_code == 422
        assert client.put(url, headers=make_user("coordinator")["headers"], json={"threshold": 0.2}).status_code == 403
        response = client.put(url, headers=admin["headers"], json={"threshold": 1.0})
        assert response.status_code == 200 and response.json()["threshold"] == 1.0
        assert _pending(client, admin)["threshold"] == 1.0
        audit_rows = changes("setting_changed", draft_classification.THRESHOLD_LABEL)
        assert audit_rows and audit_rows[-1].new_value == "1.000" and audit_rows[-1].actor_id == admin["id"]
        # Con un umbral tan alto, un correo claro también espera
        ai["choice"] = _label(org["redes"])
        draft_id = _arrive(make_user("requester"), NETWORK)
        row = _row(draft_id)
        assert row.status == "pending" and row.threshold == 1.0
    finally:
        with SessionLocal() as db:
            routing.set_setting(db, classifier.THRESHOLD_KEY, None)
            db.commit()


# ----- Pendientes al encender -----

def test_pending_drafts_move_to_por_clasificar_when_switched_on(client, make_user, ai, org, routing_switch):
    admin = make_user("admin")
    draft_id = _arrive(make_user("requester"), PRINTER)
    assert _row(draft_id) is None
    with SessionLocal() as db:
        routing.set_setting(db, routing.ROUTING_ENFORCED, "true")
        db.commit()
    try:
        with SessionLocal() as db:
            assert draft_classification.install_pending(db) >= 1
            # Una segunda vez no duplica
            assert draft_classification.install_pending(db) == 0
        row = _row(draft_id)
        assert row.status == "pending" and row.pending_reason == "installed"
        [item] = [i for i in _pending(client, admin)["items"] if i["draft_id"] == draft_id]
        assert item["options"] == [] and item["pending_reason"] == models.DRAFT_PENDING_REASONS["installed"]
        # Sin sugerencia de la IA: no cuenta para la calibración
        assert _choose(client, admin, draft_id, org["soporte"]).json()["matched_first"] is None
        nav = client.get("/api/nav/counts", headers=admin["headers"])
        assert nav.status_code == 200 and nav.json()["drafts_unclassified"] >= 0
    finally:
        with SessionLocal() as db:
            routing.set_setting(db, routing.ROUTING_ENFORCED, "false")
            db.commit()


def test_install_does_nothing_with_the_switch_off(client, make_user, ai, org, routing_switch):
    draft_id = _arrive(make_user("requester"), PRINTER)
    with SessionLocal() as db:
        assert draft_classification.install_pending(db) == 0
    assert _row(draft_id) is None


# ----- simulate_email -----

@pytest.mark.parametrize("env", [
    {"APP_ENV": ""}, {"APP_ENV": "produccion"},
    {"APP_ENV": "pruebas", "MAILBOX_POLL_ENABLED": True},
    {"APP_ENV": "pruebas", "MAIL_TRANSPORT": "graph"},
])
def test_simulate_email_refuses_outside_the_test_environment(client, make_user, ai, monkeypatch, capsys, env):
    monkeypatch.setattr(config, "MAILBOX_POLL_ENABLED", False)
    monkeypatch.setattr(config, "MAIL_TRANSPORT", "log")
    for key, value in env.items():
        monkeypatch.setattr(config, key, value)
    requester = make_user("requester")
    assert simulate_email.main(["--from", requester["email"], "--body", NETWORK]) == 1
    assert "No se simuló nada" in capsys.readouterr().out
    with SessionLocal() as db:
        assert db.query(models.EmailDraft).filter(models.EmailDraft.requester_id == requester["id"]).count() == 0


def test_simulate_email_creates_analyzes_and_classifies(client, make_user, ai, org, routing_on, monkeypatch, capsys):
    monkeypatch.setattr(config, "APP_ENV", "pruebas")
    monkeypatch.setattr(config, "MAILBOX_POLL_ENABLED", False)
    monkeypatch.setattr(config, "MAIL_TRANSPORT", "smtp")
    ai["choice"] = _label(org["redes"])
    requester = make_user("requester")
    assert simulate_email.main(["--from", requester["email"], "--subject", "Sin red", "--body", NETWORK]) == 0
    out = capsys.readouterr().out
    assert "Resultado: draft_created" in out and "Clasificado por la IA" in out
    with SessionLocal() as db:
        draft = db.query(models.EmailDraft).filter(models.EmailDraft.requester_id == requester["id"]).one()
        assert draft.status == "pending_review"
        assert draft_classification.row_of(db, draft.id).department_id == org["redes"]
