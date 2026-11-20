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
        if task == "chat":
            # Ayuda general: no lleva datos
            return "Respuesta de ayuda."
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
    response = client.post("/api/chat", json={"message": "¿Cuántos tickets hay?"}, headers=make_user("admin")["headers"])
    assert response.status_code == 503


# ---------- Permisos en el servidor ----------

def test_query_not_allowed_for_role_is_not_run(client, make_user, llm):
    """Si la IA devuelve un id que no se le ofreció (o no permitido al rol), no hay datos: ayuda general."""
    technician = make_user("technician")
    llm["query"] = "open_by_department"
    assert _ask(client, technician, "¿Cuántos tickets tengo?") == "Respuesta de ayuda."
    # No se pidió redacción: no hubo datos
    assert [p for p in llm["payloads"] if p["options"]["num_predict"] == chatbot.NUM_PREDICT["answer"]] == []
    # Segunda defensa en el servidor
    with SessionLocal() as db:
        assert aq.resolve(db.get(models.User, technician["id"]), "open_by_department") is None


def test_selection_only_offers_allowed_ids(client, make_user, llm):
    llm["query"] = "overdue"
    _ask(client, make_user("technician"), "¿Qué tickets están vencidos?")
    schema = llm["payloads"][0]["format"]
    assert schema["properties"]["query"]["enum"] == ["overdue", chatbot.HELP]


@pytest.mark.parametrize("role, message, offered", [
    ("admin", "¿Cuántos tickets hay abiertos?", ["open_tickets"]),
    ("admin", "¿Cuántos tickets están sin asignar?", ["open_tickets"]),
    ("admin", "¿Cómo estamos de tickets?", ["open_tickets"]),
    ("coordinator", "Resume el estado del equipo", ["open_tickets"]),
    ("admin", "Tickets abiertos por departamento", ["open_by_department"]),
    ("coordinator", "¿Qué ÁREA tiene más?", ["open_by_department"]),
    ("technician", "¿Tengo tickets pendientes?", ["open_tickets"]),
    # El técnico no tiene «por departamento»: tampoco se le ofrecen sus abiertos (va a la ayuda)
    ("technician", "Tickets por departamento", []),
    ("admin", "¿Qué tickets están vencidos?", ["overdue"]),
    ("coordinator", "¿Hay casos atrasados?", ["overdue"]),
    ("admin", "¿Cuáles vencen pronto?", ["due_soon"]),
    ("technician", "¿Tengo tickets por vencer?", ["due_soon"]),
    # Cómo hacer algo o qué significa algo: ayuda, aunque nombre tickets
    ("coordinator", "¿Cómo reasigno un ticket?", []),
    ("technician", "¿Qué significa En proceso?", []),
    ("admin", "Pasos para diagnosticar una red lenta", []),
    # Sin palabras de datos
    ("admin", "Cuéntame un chiste", []),
    ("admin", "¿Cuántas personas trabajan en la empresa?", []),
    # Sinónimos informales
    ("admin", "¿Qué hay que nadie ha tomado todavía?", ["open_tickets"]),
    ("coordinator", "¿Cuántos siguen sin dueño?", ["open_tickets"]),
    ("admin", "¿Algo se pasó de fecha?", ["overdue"]),
    ("coordinator", "¿Qué va fuera de plazo?", ["overdue"]),
    ("technician", "¿Se me pasaron de fecha algunos?", ["overdue"]),
    ("admin", "¿Cuántos tickets no se atendieron a tiempo?", ["overdue"]),
    ("coordinator", "¿Hay algo que nadie ha atendido?", ["open_tickets"]),
    # Pide algo fuera del catálogo: nunca los abiertos (la IA respondía con datos de otra cosa)
    ("admin", "¿Qué departamento resolvió más tickets?", []),
    ("coordinator", "¿Qué área cerró más casos este mes?", []),
    ("admin", "¿Cuál departamento genera más tickets?", []),
    ("admin", "¿Qué departamento crea más solicitudes?", []),
    ("coordinator", "¿Quién reporta más tickets?", []),
    ("admin", "¿Cuántos tickets urgentes hay?", []),
    ("technician", "¿Cuáles de mis tickets tienen prioridad alta?", []),
    # Negado sigue siendo abierto; «general» y «desatendidos» no son señales
    ("admin", "¿Cuántos tickets no se han cerrado?", ["open_tickets"]),
    ("coordinator", "Casos no resueltos por departamento", ["open_by_department"]),
    ("admin", "Resumen general de tickets", ["open_tickets"]),
    ("coordinator", "¿Hay casos desatendidos?", ["open_tickets"]),
    # Resumen o estado por departamento
    ("coordinator", "¿Cómo están los departamentos?", ["open_by_department"]),
    # Fase 3: carga y ausencias (específicas: quitan la general)
    ("admin", "¿Quién tiene más carga?", ["technician_load"]),
    ("coordinator", "¿Cuántos tickets tiene cada técnico?", ["technician_load"]),
    ("technician", "¿Cuánta carga tengo?", ["technician_load"]),
    ("admin", "¿Quién no está hoy?", ["staff_away"]),
    ("coordinator", "¿Quién está de vacaciones?", ["staff_away"]),
    # El técnico no tiene ausencias del personal: ayuda, nunca sus abiertos
    ("technician", "¿Quién está de vacaciones?", []),
    # La carga es de abiertos ahora: resueltos no
    ("coordinator", "¿Qué técnico resolvió más tickets?", []),
])
def test_keyword_filter(make_user, role, message, offered):
    user = make_user(role)
    with SessionLocal() as db:
        assert [q.id for q in aq.candidates(db.get(models.User, user["id"]), message)] == offered


def test_without_candidates_the_ai_does_not_choose(client, make_user, llm):
    """Sin consultas posibles, la pregunta va directo a la ayuda general: no hay llamada de elección."""
    _ask(client, make_user("admin"), "Cuéntame un chiste")
    assert [p for p in llm["payloads"] if "format" in p] == []


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


def test_help_suggests_data_questions(client, make_user, llm):
    """«ayuda» y «ninguna» son una sola opción: la ayuda general sabe qué preguntas de datos sugerir."""
    assert _ask(client, make_user("coordinator"), "¿Cuál es la capital de Francia?") == "Respuesta de ayuda."
    system = llm["payloads"][-1]["messages"][0]["content"]
    assert chatbot.HELP_NOTE in system and "«¿Cuántos tickets hay abiertos?»" in system


# ---------- Privacidad: lo que llega a la IA ----------

def test_ai_never_gets_titles_or_personal_data(client, make_user, make_ticket, llm, routing_on):
    dept, coordinator, technician = team(make_user)
    contact = make_user("requester")  # con teléfono 8888-1234 y cédula en su perfil
    ticket = make_ticket(contact, title=SECRET_TITLE)
    set_department(ticket["id"], dept)
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    llm["reply"] = "Listo."
    for user, query, message in (
            (technician, "open_tickets", "¿Cuántos tickets tengo?"),
            (coordinator, "open_tickets", "¿Cuántos tickets hay abiertos?"),
            (coordinator, "open_by_department", "Tickets por departamento"),
            (make_user("admin"), "open_by_department", "Tickets por departamento")):
        llm["query"] = query
        _ask(client, user, message)
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
    response = client.post("/api/chat/stream", json={"message": "¿Cuántos tickets hay?", "history": []},
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


# ---------- Fase 2: vencidos y por vencer ----------

def _set_due(ticket_id, due_at, created_at):
    with SessionLocal() as db:
        ticket = db.get(models.Ticket, ticket_id)
        ticket.created_at = created_at
        analysis = db.query(models.TicketAIAnalysis).filter(models.TicketAIAnalysis.ticket_id == ticket_id).first()
        if analysis is None:
            analysis = models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=3)
            db.add(analysis)
        analysis.due_at = due_at
        db.commit()


@pytest.fixture
def due_team(client, make_user, make_ticket, routing_on):
    """Departamento con 2 vencidos (uno asignado), 1 por vencer y 1 a tiempo; y 1 vencido en otro departamento."""
    from datetime import timedelta

    now = aq._now()
    dept, coordinator, technician = team(make_user)
    other = department(unique("Otro equipo"))
    contact = make_user("requester")
    ids = {}
    for key, title, due, dept_id in (
            ("late1", "Servidor caído", now - timedelta(days=2), dept),
            ("late2", "Correo sin enviar", now - timedelta(days=1), dept),
            ("soon", "VPN intermitente", now + timedelta(minutes=30), dept),
            ("fine", "Cambio de mouse", now + timedelta(days=60), dept),
            ("other", "Ajeno vencido", now - timedelta(days=3), other)):
        ticket = make_ticket(contact, title=title)
        set_department(ticket["id"], dept_id)
        _set_due(ticket["id"], due, now - timedelta(days=30))
        ids[key] = ticket["id"]
    client.patch(f"/api/tickets/{ids['late2']}", json={"assigned_to": technician["id"]},
                 headers=coordinator["headers"])
    return {"coordinator": coordinator, "technician": technician, "ids": ids}


def test_overdue_for_coordinator(client, due_team, llm):
    coordinator, ids = due_team["coordinator"], due_team["ids"]
    result = _result(coordinator, "overdue")
    assert _total(result) == 2
    nav = client.get("/api/nav/counts", headers=coordinator["headers"]).json()
    assert _total(result) == nav["overdue"]
    # El más vencido primero; nunca el de otro departamento; sin títulos para la IA
    ticket_lines = [f for f in result.facts if f.startswith("- #")]
    assert ticket_lines[0].startswith(f"- #{ids['late1']} ") and ticket_lines[1].startswith(f"- #{ids['late2']} ")
    assert "vencido hace 2 d" in ticket_lines[0] and "técnico: sin asignar" in ticket_lines[0]
    assert due_team["technician"]["name"] in ticket_lines[1]
    facts = "\n".join(result.facts)
    assert f"#{ids['other']}" not in facts and "Servidor caído" not in facts
    # La lista con títulos la agrega el servidor
    assert any(line.startswith(f"#{ids['late1']} · Servidor caído") for line in result.listing)


def test_due_soon_for_coordinator(client, due_team, llm):
    coordinator, ids = due_team["coordinator"], due_team["ids"]
    result = _result(coordinator, "due_soon")
    assert _total(result) == 1
    nav = client.get("/api/nav/counts", headers=coordinator["headers"]).json()
    assert _total(result) == nav["due_soon"]
    assert any(f.startswith(f"- #{ids['soon']} ") and "% del tiempo consumido" in f for f in result.facts)
    llm["query"], llm["reply"] = "due_soon", "Hay 1 ticket por vencer."
    reply = _ask(client, coordinator, "¿Cuáles están por vencer?")
    assert reply.startswith("Hay 1 ticket por vencer.") and f"#{ids['soon']} · VPN intermitente" in reply


def test_technician_sees_only_own_overdue(client, due_team, llm):
    technician, ids = due_team["technician"], due_team["ids"]
    result = _result(technician, "overdue")
    assert _total(result) == 1
    assert [f for f in result.facts if f.startswith("- #")][0].startswith(f"- #{ids['late2']} ")
    nav = client.get("/api/nav/counts", headers=technician["headers"]).json()
    assert _total(result) == nav["overdue"]


def test_admin_overdue_without_switch_is_totals_only(client, make_user, make_ticket, llm):
    from datetime import timedelta

    admin = make_user("admin")
    now = aq._now()
    ticket = make_ticket(make_user("requester"), title="Bodega sin red")
    _set_due(ticket["id"], now - timedelta(days=1), now - timedelta(days=10))
    result = _result(admin, "overdue")
    assert _total(result) >= 1 and result.listing == []
    facts = "\n".join(result.facts)
    assert "#" not in facts and "Bodega" not in facts and aq.TOTALS_ONLY_NOTE in facts


def test_overdue_wrong_ticket_number_falls_back(client, due_team, llm):
    coordinator = due_team["coordinator"]
    llm["query"], llm["reply"] = "overdue", "Hay 2 vencidos; el peor es el #999999."
    reply = _ask(client, coordinator, "¿Qué tickets están vencidos?")
    assert "999999" not in reply and reply.startswith(_result(coordinator, "overdue").text)


# ---------- Fase 3: carga y ausencias ----------

@pytest.fixture
def load_team(client, make_user, make_ticket, routing_on):
    """Departamento con 2 técnicos (uno con 2 abiertos y 1 resuelto, otro de vacaciones con 1 abierto) y un
    técnico de otro departamento con 1 abierto."""
    from datetime import timedelta

    from org_helpers import place

    dept, coordinator, busy = team(make_user)
    away = make_user("technician", unique("Técnico vacaciones"))
    place(away["id"], dept)
    other_dept = department(unique("Otro equipo"))
    outsider = make_user("technician", unique("Técnico ajeno"))
    place(outsider["id"], other_dept)
    contact = make_user("requester")

    def ticket(dept_id, assignee, status=None):
        created = make_ticket(contact)
        set_department(created["id"], dept_id)
        with SessionLocal() as db:
            row = db.get(models.Ticket, created["id"])
            row.assigned_to = assignee["id"]
            if status:
                row.status = status
            db.commit()

    ticket(dept, busy)
    ticket(dept, busy)
    ticket(dept, busy, status="resolved")
    ticket(dept, away)
    ticket(other_dept, outsider)
    today = aq._now().date()
    with SessionLocal() as db:
        db.add(models.AvailabilityPeriod(user_id=away["id"], kind="vacation", start_date=today - timedelta(days=1),
                                         end_date=today + timedelta(days=3), created_at=aq._now()))
        db.commit()
    return {"coordinator": coordinator, "busy": busy, "away": away, "outsider": outsider,
            "return": (today + timedelta(days=4)).strftime("%d/%m/%Y")}


def test_coordinator_load_matches_team_page(client, load_team, llm):
    coordinator, busy, away = load_team["coordinator"], load_team["busy"], load_team["away"]
    result = _result(coordinator, "technician_load")
    team_page = {m["id"]: m for m in client.get("/api/team", headers=coordinator["headers"]).json()}
    # La misma carga que Equipo › Carga (por niveles o por cantidad, según LOAD_MODE); el resuelto no cuenta
    load = team_page[busy["id"]]["load"]
    assert _total(result) == load
    facts = "\n".join(result.facts)
    assert f"Más carga: {busy['name']} ({load})" in facts
    assert f"- {busy['name']}: 2 abierto(s), carga {load} · Disponible" in facts
    assert f"- {away['name']}: 1 abierto(s), carga {team_page[away['id']]['load']} · Vacaciones (regresa " \
           f"{load_team['return']})" in facts
    # Nunca técnicos de otro departamento
    assert load_team["outsider"]["name"] not in facts


def test_admin_load_without_load_numbers(client, make_user, load_team, llm):
    """El administrador no ve la carga en Equipo: el asistente le da los abiertos de cada técnico, sin carga."""
    admin = make_user("admin")
    result = _result(admin, "technician_load")
    facts = "\n".join(result.facts)
    assert "carga 2" not in facts and "asignación automática" not in facts
    assert f"- {load_team['busy']['name']}: 2 abierto(s) · Disponible" in facts
    # Con el interruptor encendido el administrador ve todos los departamentos
    assert load_team["outsider"]["name"] in facts


def test_technician_sees_only_own_load(client, load_team, llm):
    busy = load_team["busy"]
    result = _result(busy, "technician_load")
    assert _total(result) == 2 and result.label == "Tu carga"
    facts = "\n".join(result.facts)
    assert load_team["away"]["name"] not in facts and "Tu estado: Disponible" in facts


def test_staff_away_for_coordinator(client, load_team, llm):
    coordinator, away = load_team["coordinator"], load_team["away"]
    result = _result(coordinator, "staff_away")
    assert _total(result) == 1
    team_page = {m["id"]: m for m in client.get("/api/team", headers=coordinator["headers"]).json()}
    assert team_page[away["id"]]["availability"] == "vacation"
    facts = "\n".join(result.facts)
    assert f"- {away['name']} (técnico) · Vacaciones (regresa {load_team['return']})" in facts
    assert load_team["outsider"]["name"] not in facts
    # La redacción con el nombre y la fecha de regreso pasa la verificación
    llm["query"] = "staff_away"
    llm["reply"] = f"Hay 1 persona de vacaciones: {away['name']}, regresa el {load_team['return']}."
    reply = _ask(client, coordinator, "¿Quién está de vacaciones?")
    assert reply.startswith(llm["reply"]) and "(Datos del servidor: Disponibilidad del personal," in reply


def test_staff_away_not_for_technician(client, load_team, llm):
    """Si la IA eligiera staff_away para un técnico, el servidor no la corre."""
    with SessionLocal() as db:
        assert aq.resolve(db.get(models.User, load_team["busy"]["id"]), "staff_away") is None
