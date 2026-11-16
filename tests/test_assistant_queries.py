"""Asistente con consultas predefinidas (app/assistant_queries.py).

La IA solo elige un id del catálogo y redacta; los números los calcula el servidor. Se comprueba:
- que los números son los mismos que muestra la página (/api/nav/counts) y los de datos conocidos;
- que una redacción con un número que no está en los datos cae al texto del servidor;
- los permisos en el servidor (consulta no permitida, departamento del coordinador, técnico, administrador sin
  el interruptor) y que a la IA nunca le llegan títulos, correos, teléfonos ni cédulas.
Ollama siempre se simula (chatbot.post_chat)."""
import json
import re

import pytest

from app import assistant_queries as aq, chatbot, config, models, routing
from app.database import SessionLocal
from app.routers import chat as chat_router
from org_helpers import department, set_department, team, unique

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SECRET_TITLE = "Laptop de Marta Quesada marta.q@ejemplo.com 8877-6655"


@pytest.fixture
def llm(monkeypatch):
    """Ollama simulado: state["query"] es lo que «elige» la IA; state["reply"], lo que «redacta» (una función
    de los facts, o un texto). Guarda cada payload enviado."""
    state = {"query": "open_tickets", "reply": None, "payloads": []}

    def fake_post(payload, task="chat", timeout=None):
        state["payloads"].append(payload)
        if task == "query_select":
            return json.dumps({"query": state["query"]})
        if task == "answer":
            reply = state["reply"]
            if reply is None:
                raise chatbot.ChatbotUnavailable()
            return reply(payload) if callable(reply) else reply
        raise AssertionError(f"tarea inesperada {task}")

    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    monkeypatch.setattr(chatbot, "post_chat", fake_post)
    chat_router.reset_rate_limit()
    yield state
    chat_router.reset_rate_limit()


def _ask(client, user, message="¿Cuántos tickets hay abiertos?"):
    response = client.post("/api/chat", json={"message": message}, headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()["reply"]


def _result(user, query_id):
    with SessionLocal() as db:
        return aq.run(db, db.get(models.User, user["id"]), aq.BY_ID[query_id])


def _total(result):
    return int(result.required[0])


# ---------- Verificación de números ----------

def _fake_result(facts, text="Hay 12 tickets.", required=("12",)):
    return aq.Result("x", "Prueba", facts, text, list(required))


@pytest.mark.parametrize("reply, ok", [
    ("Hay 12 tickets abiertos y 3 vencidos.", True),
    ("Hay doce tickets abiertos y tres vencidos.", True),
    ("Hay 12 tickets abiertos; el porcentaje es 45,5 %.", True),
    ("Hay 12 tickets, datos del 16/11 a las 10:42.", True),
    ("Hay 12 tickets abiertos, el #4567 es el más viejo.", False),
    ("Hay 13 tickets abiertos.", False),
    ("Hay catorce tickets abiertos y 12 en total.", False),
    # Falta el número principal
    ("Hay 3 vencidos.", False),
])
def test_verify(reply, ok):
    result = _fake_result(["Abiertos: 12", "Vencidos: 3", "Precisión: 45.5 %", "Fecha: 16/11 10:42"])
    assert aq.verify(reply, result)[0] is ok


def test_verify_says_which_number_failed():
    assert aq.verify("Hay 13 tickets.", _fake_result(["Abiertos: 12"])) == (False, "número 13 no está en los datos")
    assert aq.verify("Sin datos.", _fake_result(["Abiertos: 12"])) == (False, "falta el número principal 12")
    # «No hay» cuenta como 0: si el dato no es 0, no pasa
    assert aq.verify("No hay tickets.", _fake_result(["Abiertos: 12"])) == (False, "número 0 no está en los datos")


# ---------- Los números salen del servidor ----------

def test_admin_open_tickets_match_the_menu(client, make_user, make_ticket, llm, routing_on):
    admin = make_user("admin")
    dept, _coordinator, _technician = team(make_user)
    set_department(make_ticket(make_user("requester"))["id"], dept)
    nav = client.get("/api/nav/counts", headers=admin["headers"]).json()
    result = _result(admin, "open_tickets")
    # El menú separa los vencidos de los abiertos; el asistente da el total
    assert _total(result) == nav["open"] + nav["overdue"]
    assert f"Por vencer: {nav['due_soon']}" in result.facts


def test_coordinator_counts_only_their_department(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, _technician = team(make_user)
    other = department(unique("Otro equipo"))
    contact = make_user("requester")
    for _ in range(3):
        set_department(make_ticket(contact)["id"], dept)
    set_department(make_ticket(contact)["id"], other)

    result = _result(coordinator, "open_tickets")
    assert _total(result) == 3
    nav = client.get("/api/nav/counts", headers=coordinator["headers"]).json()
    assert _total(result) == nav["open"] + nav["overdue"]

    by_dept = _result(coordinator, "open_by_department")
    assert _total(by_dept) == 3 and not any("Otro equipo" in f for f in by_dept.facts)
    # Respuesta final con la redacción simulada que repite los números
    llm["query"], llm["reply"] = "open_tickets", "Tu departamento tiene 3 tickets abiertos."
    reply = _ask(client, coordinator)
    assert reply.startswith("Tu departamento tiene 3 tickets abiertos.")
    assert "(Datos del servidor: Tickets abiertos," in reply


def test_admin_sees_every_department(client, make_user, make_ticket, llm, routing_on):
    admin = make_user("admin")
    name_a, name_b = unique("Redes"), unique("Sistemas")
    dept_a, dept_b = department(name_a), department(name_b)
    contact = make_user("requester")
    for _ in range(2):
        set_department(make_ticket(contact)["id"], dept_a)
    set_department(make_ticket(contact)["id"], dept_b)
    facts = _result(admin, "open_by_department").facts
    assert any(f.endswith(f"{name_a}: 2") for f in facts) and any(f.endswith(f"{name_b}: 1") for f in facts)


def test_technician_counts_only_their_tickets(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, technician = team(make_user)
    contact = make_user("requester")
    mine = make_ticket(contact, title="Impresora del segundo piso")
    set_department(mine["id"], dept)
    set_department(make_ticket(contact)["id"], dept)
    client.patch(f"/api/tickets/{mine['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])

    result = _result(technician, "open_tickets")
    assert _total(result) == 1
    nav = client.get("/api/nav/counts", headers=technician["headers"]).json()
    assert _total(result) == nav["mine_open"] + nav["overdue"]
    # Su lista la arma el servidor (con el título); a la IA no le llega el título
    assert any(line.startswith(f"#{mine['id']} · Impresora del segundo piso") for line in result.listing)
    assert not any("Impresora" in f for f in result.facts)


# ---------- La IA no cambia números ----------

def test_wrong_number_falls_back_to_server_text(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, _technician = team(make_user)
    set_department(make_ticket(make_user("requester"))["id"], dept)
    expected = _result(coordinator, "open_tickets").text
    llm["reply"] = "Tu departamento tiene 99 tickets abiertos."
    reply = _ask(client, coordinator)
    assert "99" not in reply and reply.startswith(expected)


def test_ai_down_while_writing_gives_server_text(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, _technician = team(make_user)
    set_department(make_ticket(make_user("requester"))["id"], dept)
    expected = _result(coordinator, "open_tickets").text
    llm["reply"] = None
    assert _ask(client, coordinator).startswith(expected)


def test_selection_down_is_503(client, make_user, llm, monkeypatch):
    def broken(payload, task="chat", timeout=None):
        raise chatbot.ChatbotUnavailable()

    monkeypatch.setattr(chatbot, "post_chat", broken)
    response = client.post("/api/chat", json={"message": "¿Cuántos?"}, headers=make_user("admin")["headers"])
    assert response.status_code == 503


# ---------- Permisos en el servidor ----------

def test_query_not_allowed_for_role_is_no_match(client, make_user, llm):
    technician = make_user("technician")
    llm["query"] = "open_by_department"
    reply = _ask(client, technician, "Tickets por departamento")
    assert reply.startswith(aq.NO_MATCH_TEXT)
    # No se pidió redacción: no hubo datos
    assert [p for p in llm["payloads"] if p["options"]["num_predict"] == chatbot.NUM_PREDICT["answer"]] == []


def test_selection_only_offers_allowed_ids(client, make_user, llm):
    _ask(client, make_user("technician"))
    schema = llm["payloads"][0]["format"]
    assert schema["properties"]["query"]["enum"] == [chatbot.HELP, chatbot.NO_MATCH, "open_tickets"]


def test_admin_without_switch_gets_totals_only(client, make_user, make_ticket, llm):
    admin = make_user("admin")
    with SessionLocal() as db:
        assert not routing.enforced(db)
    make_ticket(make_user("requester"), title="Router de la bodega")
    result = _result(admin, "open_tickets")
    assert result.listing == [] and aq.TOTALS_ONLY_NOTE in result.facts
    llm["reply"] = lambda payload: f"Hay {_total(result)} tickets abiertos."
    reply = _ask(client, admin)
    assert "Router de la bodega" not in reply and "#" not in reply


def test_no_match_suggests_questions(client, make_user, llm):
    llm["query"] = chatbot.NO_MATCH
    reply = _ask(client, make_user("coordinator"), "¿Cuál es la capital de Francia?")
    assert reply.startswith(aq.NO_MATCH_TEXT) and "• ¿Cuántos tickets hay abiertos?" in reply


# ---------- Privacidad: lo que llega a la IA ----------

def test_ai_never_gets_titles_or_personal_data(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, technician = team(make_user)
    contact = make_user("requester")  # con teléfono 8888-1234 y cédula en su perfil
    ticket = make_ticket(contact, title=SECRET_TITLE)
    set_department(ticket["id"], dept)
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    llm["reply"] = "Listo."
    for user, query in ((technician, "open_tickets"), (coordinator, "open_tickets"),
                        (coordinator, "open_by_department"), (make_user("admin"), "open_by_department")):
        llm["query"] = query
        _ask(client, user, "Dame los datos")
    with SessionLocal() as db:
        person = db.get(models.User, contact["id"])
        secrets = [person.email, person.name, "8888-1234", "88881234", "8877-6655", "Marta", "Quesada"]
        if person.id_number:
            secrets.append(person.id_number)
    for payload in llm["payloads"]:
        text = json.dumps(payload, ensure_ascii=False)
        assert not EMAIL_RE.search(text)
        for secret in secrets:
            assert secret not in text


# ---------- Streaming: la página sabe que es una consulta de datos ----------

def test_stream_marks_data_answers(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, _technician = team(make_user)
    set_department(make_ticket(make_user("requester"))["id"], dept)
    llm["reply"] = "Tienes 1 ticket abierto."
    response = client.post("/api/chat/stream", json={"message": "¿Cuántos?", "history": []},
                           headers=coordinator["headers"])
    assert response.status_code == 200
    assert response.headers["x-chat-kind"] == "data"
    assert "(Datos del servidor: Tickets abiertos," in response.text


# ---------- scripts.assistant_eval (sin Ollama: la IA simulada) ----------

def test_assistant_eval_reports_choice_and_verification(client, make_user, llm, capsys):
    from scripts import assistant_eval

    make_user("admin")
    make_user("technician")
    dept, _coordinator, _technician = team(make_user)
    llm["query"], llm["reply"] = "open_tickets", "Hay 100000 tickets."
    assert assistant_eval.main([]) == 0
    out = capsys.readouterr().out
    assert "Elección:" in out and "Meta: 90 %" in out and "Omitidas:" in out
    # La redacción simulada inventa un número: cae al texto del servidor y dice cuál
    assert "texto del servidor: número 100000 no está en los datos" in out
    assert "Caídas al texto del servidor:" in out
