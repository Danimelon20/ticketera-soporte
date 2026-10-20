import json
import threading
from datetime import date, datetime, timedelta

import pytest

from app import auto_assign, chatbot, config, mailer, models
from app.database import SessionLocal

# Lunes 10 de marzo de 2031 en Costa Rica (UTC-6)
MONDAY_7AM = datetime(2031, 3, 10, 13, 0)
MONDAY_8AM = datetime(2031, 3, 10, 14, 0)
MONDAY_10AM = datetime(2031, 3, 10, 16, 0)
SUNDAY_10AM = datetime(2031, 3, 9, 16, 0)
WEEKDAYS = [{"weekday": d, "start": "08:00", "end": "17:00"} for d in range(5)]
ALL_DAY = [{"weekday": d, "start": "00:00", "end": "23:59"} for d in range(7)]


def _clean():
    db = SessionLocal()
    try:
        # Horarios y asignaciones de otras pruebas cambiarían los candidatos
        db.query(models.TicketAutoAssignment).delete()
        db.query(models.TechnicianSchedule).delete()
        db.query(models.TechnicianAvailability).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch, client):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", "levels")
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: True)
    _clean()
    yield
    _clean()


def _tech(make_user, name="Técnico", days=WEEKDAYS, status=None, active=True):
    user = make_user("technician", name)
    db = SessionLocal()
    try:
        for day in days or []:
            db.add(models.TechnicianSchedule(
                user_id=user["id"], weekday=day["weekday"], start_time=day["start"], end_time=day["end"],
            ))
        if status:
            db.add(models.TechnicianAvailability(user_id=user["id"], status=status, updated_at=MONDAY_7AM))
        db.get(models.User, user["id"]).is_active = active
        db.commit()
    finally:
        db.close()
    return user


def _ticket(requester, level=None, assigned_to=None, status="new", created_at=MONDAY_7AM, analyzed=True):
    """Ticket directo en la base (sin pasar por la ruta, que intentaría asignarlo)."""
    db = SessionLocal()
    try:
        ticket = models.Ticket(
            title="Ticket de carga", description="Descripción", category_id=1, priority_id=1,
            status=status, created_by=requester["id"], assigned_to=assigned_to, source="web", created_at=created_at,
        )
        db.add(ticket)
        db.flush()
        if analyzed:
            db.add(models.TicketAIAnalysis(ticket_id=ticket.id, status="done", ai_level=level))
        db.commit()
        return ticket.id
    finally:
        db.close()


def _assigned_to(ticket_id):
    db = SessionLocal()
    try:
        return db.get(models.Ticket, ticket_id).assigned_to
    finally:
        db.close()


def _row(ticket_id):
    db = SessionLocal()
    try:
        return db.query(models.TicketAutoAssignment).filter(models.TicketAutoAssignment.ticket_id == ticket_id).first()
    finally:
        db.close()


def _assignment_history(ticket_id):
    db = SessionLocal()
    try:
        return (
            db.query(models.History)
            .filter(models.History.ticket_id == ticket_id, models.History.field == "assigned_to")
            .all()
        )
    finally:
        db.close()


def _agent_id():
    db = SessionLocal()
    try:
        return db.query(models.User.id).filter(models.User.role == "agent").one()[0]
    finally:
        db.close()


# ----- Encendido y apagado -----

def test_disabled_assigns_nothing(make_user, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    _tech(make_user)
    ticket = _ticket(make_user("requester"), level=2)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "disabled"
    assert auto_assign.run_pending(MONDAY_10AM) == 0
    assert _assigned_to(ticket) is None
    assert _row(ticket) is None


def test_assigns_and_records_as_system(client, make_user):
    technician = _tech(make_user, "Ana Rojas")
    requester = make_user("requester")
    ticket = _ticket(requester, level=2)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "assigned"
    assert _assigned_to(ticket) == technician["id"]
    history = _assignment_history(ticket)
    assert len(history) == 1 and history[0].changed_by == _agent_id()
    detail = client.get(f"/api/tickets/{ticket}", headers=make_user("coordinator")["headers"]).json()
    assert detail["auto_assigned"] is True


# ----- Carga -----

@pytest.mark.parametrize("mode,winner", [("levels", "b"), ("count", "a")])
def test_levels_and_count_choose_differently(make_user, monkeypatch, mode, winner):
    monkeypatch.setattr(config, "AUTO_ASSIGN_LOAD_MODE", mode)
    requester = make_user("requester")
    techs = {"a": _tech(make_user, "A"), "b": _tech(make_user, "B")}
    # A: 3 tickets nivel 3 (carga 9 o 3). B: 5 tickets nivel 1 (carga 5 o 5).
    for _ in range(3):
        _ticket(requester, level=3, assigned_to=techs["a"]["id"], status="in_progress")
    for _ in range(5):
        _ticket(requester, level=1, assigned_to=techs["b"]["id"])
    ticket = _ticket(requester, level=2)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "assigned"
    assert _assigned_to(ticket) == techs[winner]["id"]


def test_resolved_and_closed_do_not_count(make_user):
    requester = make_user("requester")
    busy = _tech(make_user, "Ocupado")
    free = _tech(make_user, "Libre")
    _ticket(requester, level=1, assigned_to=busy["id"])
    for status in ("resolved", "closed"):
        _ticket(requester, level=4, assigned_to=free["id"], status=status)
    ticket = _ticket(requester, level=2)
    auto_assign.assign_new(ticket, MONDAY_10AM)
    assert _assigned_to(ticket) == free["id"]


@pytest.mark.parametrize("analyzed", [True, False])
def test_missing_level_counts_as_two(make_user, analyzed):
    requester = make_user("requester")
    a = _tech(make_user, "Sin nivel")
    b = _tech(make_user, "Nivel 3")
    # A: un ticket sin nivel (cuenta 2). B: uno de nivel 3. Gana A.
    _ticket(requester, level=None, assigned_to=a["id"], analyzed=analyzed)
    _ticket(requester, level=3, assigned_to=b["id"])
    ticket = _ticket(requester, level=2)
    auto_assign.assign_new(ticket, MONDAY_10AM)
    assert _assigned_to(ticket) == a["id"]
    # Ahora A tiene 2 + 2 = 4 y B 3: gana B
    second = _ticket(requester, level=2)
    auto_assign.assign_new(second, MONDAY_10AM)
    assert _assigned_to(second) == b["id"]


def test_tie_goes_to_oldest_automatic_assignment(make_user):
    requester = make_user("requester")
    first = _tech(make_user, "Primero")
    second = _tech(make_user, "Segundo")
    db = SessionLocal()
    try:
        # Primero recibió una automática hace más tiempo que Segundo; ninguno tiene carga abierta
        for user, minutes in ((first, 120), (second, 30)):
            old = _ticket(requester, level=1, assigned_to=user["id"], status="closed")
            db.add(models.TicketAutoAssignment(
                ticket_id=old, status="assigned", user_id=user["id"],
                assigned_at=MONDAY_10AM - timedelta(minutes=minutes), created_at=MONDAY_7AM,
            ))
        db.commit()
    finally:
        db.close()
    never = _tech(make_user, "Nunca")
    ticket = _ticket(requester, level=2)
    auto_assign.assign_new(ticket, MONDAY_10AM)
    # Quien nunca recibió una va primero
    assert _assigned_to(ticket) == never["id"]
    # Empate de carga entre Primero y Segundo (Nunca ya tiene 2): gana la automática más antigua
    next_ticket = _ticket(requester, level=2)
    auto_assign.assign_new(next_ticket, MONDAY_10AM)
    assert _assigned_to(next_ticket) == first["id"]


# ----- Candidatos -----

def test_excluded_technicians(make_user):
    requester = make_user("requester")
    _tech(make_user, "Vacaciones", status="vacation")
    _tech(make_user, "Ausente", status="away")
    _tech(make_user, "Sin horario", days=[])
    _tech(make_user, "Inactivo", active=False)
    _tech(make_user, "Solo fines de semana", days=[{"weekday": 5, "start": "08:00", "end": "17:00"}])
    _tech(make_user, "Tarde", days=[{"weekday": 0, "start": "13:00", "end": "17:00"}])
    ticket = _ticket(requester, level=2)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "waiting"
    assert _assigned_to(ticket) is None
    # Uno que sí cumple todo
    ok = _tech(make_user, "Disponible", status="available")
    assert auto_assign.run_pending(MONDAY_10AM) == 1
    assert _assigned_to(ticket) == ok["id"]


def test_holiday_is_off_shift(make_user, monkeypatch):
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", date(2031, 3, 10).isoformat())
    _tech(make_user)
    ticket = _ticket(make_user("requester"), level=2)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "waiting"


# ----- Fuera de horario y ciclo de pendientes -----

def test_off_hours_waits_and_assigns_when_shift_starts(make_user):
    requester = make_user("requester")
    technician = _tech(make_user)
    older = _ticket(requester, level=2, created_at=MONDAY_7AM - timedelta(hours=10))
    newer = _ticket(requester, level=2, created_at=MONDAY_7AM)
    for ticket in (newer, older):
        assert auto_assign.assign_new(ticket, SUNDAY_10AM) == "waiting"
    assert auto_assign.run_pending(MONDAY_7AM) == 0
    assert _assigned_to(older) is None and _assigned_to(newer) is None
    assert auto_assign.run_pending(MONDAY_8AM) == 2
    assert _assigned_to(older) == technician["id"] and _assigned_to(newer) == technician["id"]
    # Del más antiguo al más nuevo
    assert _row(older).history_id < _row(newer).history_id


def test_oldest_waiting_goes_first_to_least_loaded(make_user):
    requester = make_user("requester")
    a = _tech(make_user, "A")
    b = _tech(make_user, "B")
    older = _ticket(requester, level=4, created_at=MONDAY_7AM - timedelta(hours=1))
    newer = _ticket(requester, level=1, created_at=MONDAY_7AM)
    for ticket in (older, newer):
        auto_assign.assign_new(ticket, SUNDAY_10AM)
    auto_assign.run_pending(MONDAY_8AM)
    # La carga se recalcula después de cada asignación: no van los dos al mismo
    assert {_assigned_to(older), _assigned_to(newer)} == {a["id"], b["id"]}


def test_runs_without_email(client, make_user, monkeypatch):
    """Con EMAIL_ENABLED=false el ciclo arranca igual y asigna el pendiente al empezar el turno."""
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    started = threading.Event()

    async def _fake_forever():
        started.set()

    monkeypatch.setattr(auto_assign, "auto_assign_forever", _fake_forever)
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app):
        assert started.wait(5)

    technician = _tech(make_user)
    ticket = _ticket(make_user("requester"), level=2)
    assert auto_assign.assign_new(ticket, SUNDAY_10AM) == "waiting"
    assert auto_assign.run_pending(MONDAY_8AM) == 1
    assert _assigned_to(ticket) == technician["id"]


def test_loop_does_not_start_when_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", False)
    started = threading.Event()

    async def _fake_forever():
        started.set()

    monkeypatch.setattr(auto_assign, "auto_assign_forever", _fake_forever)
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app):
        assert not started.wait(0.5)


# ----- Nunca se reasigna -----

def test_assigned_ticket_is_never_reassigned(make_user):
    requester = make_user("requester")
    _tech(make_user, "Libre")
    other = make_user("technician", "Asignado a mano")
    ticket = _ticket(requester, level=2, assigned_to=other["id"])
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "skipped"
    assert _assigned_to(ticket) == other["id"]
    assert _assignment_history(ticket) == []


def test_manual_assignment_cancels_waiting(client, make_user):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    other = make_user("technician", "A mano")
    ticket = _ticket(requester, level=2)
    assert auto_assign.assign_new(ticket, SUNDAY_10AM) == "waiting"
    response = client.patch(f"/api/tickets/{ticket}", json={"assigned_to": other["id"]}, headers=coordinator["headers"])
    assert response.status_code == 200
    assert _row(ticket).status == "cancelled"
    _tech(make_user)
    assert auto_assign.run_pending(MONDAY_10AM) == 0
    assert _assigned_to(ticket) == other["id"]


def test_manual_reassignment_removes_auto_label(client, make_user):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    _tech(make_user)
    other = make_user("technician", "Otro")
    ticket = _ticket(requester, level=2)
    auto_assign.assign_new(ticket, MONDAY_10AM)
    client.patch(f"/api/tickets/{ticket}", json={"assigned_to": other["id"]}, headers=coordinator["headers"])
    detail = client.get(f"/api/tickets/{ticket}", headers=coordinator["headers"]).json()
    assert detail["assigned_to"] == other["id"]
    assert detail["auto_assigned"] is False
    # Ya procesado: otro intento no lo toca
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "skipped"
    assert _assigned_to(ticket) == other["id"]


def test_resolved_waiting_ticket_is_cancelled(make_user):
    requester = make_user("requester")
    ticket = _ticket(requester, level=2)
    auto_assign.assign_new(ticket, SUNDAY_10AM)
    db = SessionLocal()
    try:
        db.get(models.Ticket, ticket).status = "resolved"
        db.commit()
    finally:
        db.close()
    _tech(make_user)
    assert auto_assign.run_pending(MONDAY_10AM) == 0
    assert _row(ticket).status == "cancelled"
    assert _assigned_to(ticket) is None


# ----- Concurrencia -----

def test_simultaneous_assignments_assign_once(make_user):
    requester = make_user("requester")
    _tech(make_user, "A")
    _tech(make_user, "B")
    ticket = _ticket(requester, level=2)
    barrier = threading.Barrier(2)
    results = []

    def _run():
        barrier.wait()
        results.append(auto_assign.assign_new(ticket, MONDAY_10AM))

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == ["assigned", "skipped"]
    assert len(_assignment_history(ticket)) == 1


def test_conditional_update_does_not_overwrite(make_user, monkeypatch):
    """Aunque el lock no alcanzara (otro proceso), el UPDATE solo asigna si sigue sin responsable."""
    requester = make_user("requester")
    _tech(make_user, "Sistema elige")
    manual = make_user("technician", "Ganó otro proceso")
    ticket = _ticket(requester, level=2)
    real_choose = auto_assign.choose

    def _choose_then_other_process_assigns(db, now):
        user = real_choose(db, now)
        other = SessionLocal()
        try:
            other.get(models.Ticket, ticket).assigned_to = manual["id"]
            other.commit()
        finally:
            other.close()
        return user

    monkeypatch.setattr(auto_assign, "choose", _choose_then_other_process_assigns)
    assert auto_assign.assign_new(ticket, MONDAY_10AM) == "skipped"
    assert _assigned_to(ticket) == manual["id"]
    assert _assignment_history(ticket) == []


# ----- Enganches: después del análisis -----

def test_web_ticket_is_assigned_after_ai_analysis(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)
    seen = {}

    def _fake_post_chat(payload):
        # Cuando la IA responde todavía no hay asignación
        seen["assigned_during_analysis"] = db_assigned_any()
        return json.dumps({"level": 4, "category": "Software", "summary": "Caída."})

    def db_assigned_any():
        db = SessionLocal()
        try:
            return db.query(models.TicketAutoAssignment).count() > 0
        finally:
            db.close()

    monkeypatch.setattr(chatbot, "post_chat", _fake_post_chat)
    a = _tech(make_user, "A")
    b = _tech(make_user, "B")
    requester = make_user("requester")
    # B ya tiene un nivel 3; A uno sin nivel (2): gana A
    _ticket(requester, level=3, assigned_to=b["id"])
    _ticket(requester, level=None, assigned_to=a["id"])
    response = client.post("/api/tickets", json={
        "title": "Servidor caído", "description": "No responde.", "category_id": 1, "priority_id": 1,
    }, headers=requester["headers"])
    assert response.status_code == 200
    ticket = response.json()["id"]
    assert seen["assigned_during_analysis"] is False
    assert _assigned_to(ticket) == a["id"]
    # Con el nivel de la IA (4), la carga de A pasa a 6
    loads = auto_assign.loads(SessionLocal(), [a["id"], b["id"]])
    assert loads == {a["id"]: 6, b["id"]: 3}


def test_web_ticket_with_failed_analysis_counts_as_two(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)

    def _fail(payload):
        raise RuntimeError("Ollama no responde")

    monkeypatch.setattr(chatbot, "post_chat", _fail)
    technician = _tech(make_user)
    requester = make_user("requester")
    ticket = client.post("/api/tickets", json={
        "title": "No imprime", "description": "Nada.", "category_id": 1, "priority_id": 1,
    }, headers=requester["headers"]).json()["id"]
    assert _assigned_to(ticket) == technician["id"]
    assert auto_assign.loads(SessionLocal(), [technician["id"]]) == {technician["id"]: 2}


def test_web_ticket_without_ai_is_assigned(client, make_user, monkeypatch):
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)
    technician = _tech(make_user)
    requester = make_user("requester")
    ticket = client.post("/api/tickets", json={
        "title": "Correo lento", "description": "Tarda.", "category_id": 1, "priority_id": 1,
    }, headers=requester["headers"]).json()["id"]
    assert _assigned_to(ticket) == technician["id"]


def test_confirmed_draft_is_assigned(client, make_user, monkeypatch):
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)
    technician = _tech(make_user)
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    db = SessionLocal()
    try:
        draft = models.EmailDraft(
            message_id=f"<auto-{requester['id']}@test>", requester_id=requester["id"], status="pending_review",
            subject="Impresora", body="No imprime.", title="Impresora", description="No imprime.",
            category_id=1, priority_id=1, ai_level=3,
            contact_phone="88881234", contact_id_number=requester["id_number"],
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id
    finally:
        db.close()
    response = client.post(f"/api/drafts/{draft_id}/confirm", json={
        "title": "Impresora", "description": "No imprime.", "category_id": 1, "priority_id": 1,
        "contact_phone": "", "contact_id_number": "",
    }, headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    ticket = response.json()["ticket_id"]
    assert _assigned_to(ticket) == technician["id"]
    assert auto_assign.loads(SessionLocal(), [technician["id"]]) == {technician["id"]: 3}


def test_email_ticket_is_assigned_after_pending_analysis(make_user, monkeypatch):
    from app import email_analysis

    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(auto_assign, "_now", lambda: MONDAY_10AM)
    monkeypatch.setattr(chatbot, "post_chat", lambda payload: json.dumps(
        {"level": 3, "category": "Software", "summary": "No abre."}
    ))
    technician = _tech(make_user)
    requester = make_user("requester")
    db = SessionLocal()
    try:
        # Otras pruebas pudieron dejar análisis pendientes: que no se crucen
        db.query(models.TicketAIAnalysis).filter(models.TicketAIAnalysis.status == "pending").update({"status": "error"})
        db.commit()
    finally:
        db.close()
    # Como lo deja email_processing: análisis pendiente, sin asignar
    ticket = _ticket(requester, analyzed=False)
    db = SessionLocal()
    try:
        db.add(models.TicketAIAnalysis(ticket_id=ticket, status="pending"))
        db.commit()
    finally:
        db.close()
    assert _assigned_to(ticket) is None
    email_analysis.analyze_pending()
    assert _assigned_to(ticket) == technician["id"]
    assert auto_assign.loads(SessionLocal(), [technician["id"]]) == {technician["id"]: 3}
