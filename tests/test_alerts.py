import logging
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app import alerts, config, mailer, models
from app.business_hours import business_hours_between
from app.database import SessionLocal

# Lunes 3 de marzo de 2031 en Costa Rica (UTC-6). Oficina de 08:00 a 17:00.
# Otra fecha que la del resumen diario, para no cruzarse en la base compartida.
MONDAY_8AM = datetime(2031, 3, 3, 14, 0)
MONDAY_9AM = datetime(2031, 3, 3, 15, 0)
FAR_AWAY = MONDAY_9AM + timedelta(days=30)


def _clean_db():
    db = SessionLocal()
    try:
        db.query(models.AlertSent).delete()
        db.query(models.TechnicianAvailability).delete()
        db.query(models.EmailDraft).delete()
        # Los tickets abiertos de otras pruebas también dispararían alertas
        db.query(models.Ticket).update({"status": "closed"})
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "ALERT_WARN_PERCENT", 75)
    monkeypatch.setattr(config, "OFFICE_DAYS", "1-5")
    monkeypatch.setattr(config, "OFFICE_START", "08:00")
    monkeypatch.setattr(config, "OFFICE_END", "17:00")
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    _clean_db()
    yield
    db = SessionLocal()
    try:
        db.query(models.AlertSent).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake_send(to, subject, body):
        calls.append((to, subject, body))
        return True

    monkeypatch.setattr(mailer, "send_email", _fake_send)
    return calls


def _alerts_sent(sent, prefix="[Ticketera]"):
    return [call for call in sent if call[1].startswith(prefix)]


def _ticket(make_ticket, requester, *, title="Ticket de alerta", level=3, due_at=FAR_AWAY,
            created_at=MONDAY_8AM, status="new", assigned_to=None):
    ticket_id = make_ticket(requester, title=title)["id"]
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, ticket_id)
        ticket.created_at = created_at
        ticket.status = status
        ticket.assigned_to = assigned_to
        db.add(models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=level, due_at=due_at))
        db.commit()
    finally:
        db.close()
    return ticket_id


def _draft(requester_id, status="pending_review", level=4, title="Caída de la red"):
    db = SessionLocal()
    try:
        draft = models.EmailDraft(
            message_id=f"<{status}-{title}-{datetime.now().timestamp()}@test>", requester_id=requester_id,
            status=status, title=title, description="Nadie tiene internet.", ai_level=level,
            category_id=1, priority_id=1, contact_phone="88881234", contact_id_number="102340567",
        )
        db.add(draft)
        db.commit()
        return draft.id
    finally:
        db.close()


def _run(now=MONDAY_9AM):
    db = SessionLocal()
    try:
        return alerts.run_alerts(db, now)
    finally:
        db.close()


def _rows(kind=None):
    db = SessionLocal()
    try:
        query = db.query(models.AlertSent)
        if kind:
            query = query.filter(models.AlertSent.kind == kind)
        return query.all()
    finally:
        db.close()


# ----- Crítico -----

def test_critical_open_ticket_only(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    technician = make_user("technician")
    requester = make_user("requester")
    critical = _ticket(make_ticket, requester, title="Servidor caído", level=4)
    _ticket(make_ticket, requester, title="Resuelto crítico", level=4, status="resolved")
    _ticket(make_ticket, requester, title="Cerrado crítico", level=4, status="closed")
    _ticket(make_ticket, requester, title="Normal", level=3)

    assert _run()["critical"] == 1
    calls = _alerts_sent(sent, "[Ticketera] CRÍTICO")
    assert {call[1] for call in calls} == {f"[Ticketera] CRÍTICO: Ticket #{critical} Servidor caído"}
    to = {call[0] for call in calls}
    assert coordinator["email"] in to and admin["email"] in to
    assert technician["email"] not in to and requester["email"] not in to
    body = calls[0][2]
    assert "Nivel 4 (Crítico)" in body and "Sin asignar" in body
    assert body.rstrip().endswith(config.APP_URL)


def test_alert_is_not_repeated(make_user, make_ticket, sent):
    make_user("coordinator")
    _ticket(make_ticket, make_user("requester"), level=4, due_at=MONDAY_9AM - timedelta(hours=1))
    first = _run()
    assert first["critical"] == 1 and first["overdue"] == 1
    count = len(sent)
    again = _run(MONDAY_9AM + timedelta(minutes=2))
    assert again == {"critical": 0, "overdue": 0, "due_soon": 0, "return_date": 0}
    assert len(sent) == count


# ----- Por vencer -----

def test_due_soon_after_warn_percent_goes_to_assignee(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    # 8 horas hábiles: de lunes 08:00 a lunes 16:00 en Costa Rica
    ticket = _ticket(make_ticket, make_user("requester"), title="Correo lento",
                     due_at=MONDAY_8AM + timedelta(hours=8), assigned_to=technician["id"], status="in_progress")

    # 12:00: pasó el 50 %
    assert _run(MONDAY_8AM + timedelta(hours=4))["due_soon"] == 0
    # 14:00: pasó el 75 %
    assert _run(MONDAY_8AM + timedelta(hours=6))["due_soon"] == 1
    calls = _alerts_sent(sent, "[Ticketera] Por vencer")
    assert [call[0] for call in calls] == [technician["email"]]
    assert calls[0][1] == f"[Ticketera] Por vencer: Ticket #{ticket} Correo lento"
    assert "Fecha límite: 03/03/2031 16:00" in calls[0][2]
    assert coordinator["email"] not in {call[0] for call in sent}


def test_due_soon_without_assignee_goes_to_staff(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    _ticket(make_ticket, make_user("requester"), due_at=MONDAY_8AM + timedelta(hours=8))
    assert _run(MONDAY_8AM + timedelta(hours=7))["due_soon"] == 1
    to = {call[0] for call in _alerts_sent(sent, "[Ticketera] Por vencer")}
    assert coordinator["email"] in to and technician["email"] not in to


def test_due_soon_counts_business_hours_only(make_user, make_ticket, sent):
    make_user("coordinator")
    # Creado el viernes a las 16:00, vence el lunes a las 16:00: 9 horas hábiles
    friday_4pm = datetime(2031, 2, 28, 22, 0)
    _ticket(make_ticket, make_user("requester"), created_at=friday_4pm, due_at=MONDAY_8AM + timedelta(hours=8))
    # Sábado: sigue en 1 hora hábil de 9
    assert _run(datetime(2031, 3, 1, 18, 0))["due_soon"] == 0
    # Lunes 14:00: 7 de 9 horas (77 %)
    assert _run(MONDAY_8AM + timedelta(hours=6))["due_soon"] == 1


# ----- Vencido -----

def test_overdue_includes_assignee_and_skips_resolved(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    technician = make_user("technician")
    requester = make_user("requester")
    overdue = _ticket(make_ticket, requester, title="Impresora", due_at=MONDAY_9AM - timedelta(hours=1),
                      assigned_to=technician["id"], status="in_progress")
    _ticket(make_ticket, requester, title="Resuelto", due_at=MONDAY_9AM - timedelta(hours=1),
            assigned_to=technician["id"], status="resolved")

    result = _run()
    assert result["overdue"] == 1
    # Ya vencido: no se manda además el "por vencer"
    assert result["due_soon"] == 0
    calls = _alerts_sent(sent, "[Ticketera] Vencido")
    assert {call[1] for call in calls} == {f"[Ticketera] Vencido: Ticket #{overdue} Impresora"}
    to = [call[0] for call in calls]
    assert coordinator["email"] in to and admin["email"] in to and technician["email"] in to
    assert len(to) == len(set(to))


# ----- Borradores -----

def test_pending_critical_draft_alerts(make_user, sent):
    coordinator = make_user("coordinator")
    requester = make_user("requester", "Ana Cliente")
    draft = _draft(requester["id"])
    _draft(requester["id"], status="confirmed", title="Confirmado")
    _draft(requester["id"], status="discarded", title="Descartado")
    _draft(requester["id"], level=3, title="Nivel tres")

    assert _run()["critical"] == 1
    calls = _alerts_sent(sent, "[Ticketera] CRÍTICO")
    assert {call[1] for call in calls} == {f"[Ticketera] CRÍTICO: Borrador #{draft} Caída de la red"}
    assert coordinator["email"] in {call[0] for call in calls}
    assert "Solicitante: Ana Cliente" in calls[0][2]
    assert [row.draft_id for row in _rows("critical")] == [draft]


def _confirm(client, reviewer, draft_id):
    payload = {"title": "Caída de la red", "description": "Nadie tiene internet.", "category_id": 1, "priority_id": 1}
    response = client.post(f"/api/drafts/{draft_id}/confirm", json=payload, headers=reviewer["headers"])
    assert response.status_code == 200, response.text
    return response.json()["ticket_id"]


def test_ticket_from_alerted_draft_is_not_alerted_again(client, make_user, sent):
    coordinator = make_user("coordinator")
    draft = _draft(make_user("requester")["id"])
    assert _run()["critical"] == 1
    before = len(_alerts_sent(sent, "[Ticketera] CRÍTICO"))

    ticket_id = _confirm(client, coordinator, draft)
    assert _run()["critical"] == 1
    # Se registró sin otro correo
    assert len(_alerts_sent(sent, "[Ticketera] CRÍTICO")) == before
    assert ticket_id in {row.ticket_id for row in _rows("critical")}
    assert _run()["critical"] == 0


def test_ticket_from_draft_without_alert_is_alerted(client, make_user, sent):
    coordinator = make_user("coordinator")
    draft = _draft(make_user("requester")["id"])
    ticket_id = _confirm(client, coordinator, draft)

    assert _run()["critical"] == 1
    subjects = {call[1] for call in _alerts_sent(sent, "[Ticketera] CRÍTICO")}
    assert subjects == {f"[Ticketera] CRÍTICO: Ticket #{ticket_id} Caída de la red"}


# ----- Reintentos, agrupado y privacidad -----

def test_failed_send_is_retried(make_user, make_ticket, monkeypatch, sent):
    make_user("coordinator")
    _ticket(make_ticket, make_user("requester"), level=4)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: False)
    assert _run()["critical"] == 0
    assert _rows("critical") == []

    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    assert _run()["critical"] == 1
    assert len(_rows("critical")) == 1


def test_more_than_ten_pending_are_grouped(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    ids = [
        _ticket(make_ticket, requester, title=f"Vencido {i}", due_at=MONDAY_9AM - timedelta(hours=1),
                assigned_to=technician["id"] if i < 2 else None, status="in_progress" if i < 2 else "new")
        for i in range(12)
    ]

    assert _run()["overdue"] == 12
    to_coordinator = [call for call in sent if call[0] == coordinator["email"]]
    assert len(to_coordinator) == 1
    subject, body = to_coordinator[0][1], to_coordinator[0][2]
    assert subject == "[Ticketera] Vencidos: 12 tickets"
    assert body.count("  - #") == 10 and "… y 2 más" in body

    to_technician = [call for call in sent if call[0] == technician["email"]]
    assert len(to_technician) == 1
    assert to_technician[0][1] == "[Ticketera] Vencidos: 2 tickets"
    assert f"#{ids[0]} |" in to_technician[0][2] and f"#{ids[1]} |" in to_technician[0][2]
    assert f"#{ids[2]} |" not in to_technician[0][2]
    assert len(_rows("overdue")) == 12


def test_body_has_no_personal_data(make_user, make_ticket, sent):
    make_user("coordinator")
    requester = make_user("requester")
    # make_ticket guarda teléfono 8888-1234 y cédula 1-0234-0567 y repite el título en la descripción
    _ticket(make_ticket, requester, title="Llamar al 8888-1234 cédula 1-0234-0567", level=4,
            due_at=MONDAY_9AM - timedelta(hours=1))
    _draft(requester["id"], title="Llámeme al 8777-6655")

    _run()
    calls = _alerts_sent(sent)
    assert calls
    for _to, subject, body in calls:
        text = subject + body
        for secret in ("8888-1234", "88881234", "1-0234-0567", "102340567", "8777-6655", "87776655"):
            assert secret not in text
        assert "Descripción de" not in text and "Nadie tiene internet" not in text
    assert any("[dato oculto]" in call[1] for call in calls)


def test_disabled_sends_nothing(make_user, make_ticket, sent, monkeypatch):
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    make_user("coordinator")
    _ticket(make_ticket, make_user("requester"), level=4, due_at=MONDAY_9AM - timedelta(hours=1))
    assert _run() == {}
    assert sent == []
    assert _rows() == []


# ----- Base y panel -----

def test_business_hours_between_skips_weekend():
    friday_4pm = datetime(2031, 2, 28, 22, 0)
    monday_10am = datetime(2031, 3, 3, 16, 0)
    assert business_hours_between(friday_4pm, monday_10am) == 3
    assert business_hours_between(monday_10am, friday_4pm) == 0


def test_duplicate_alert_is_rejected(make_user, make_ticket):
    ticket_id = _ticket(make_ticket, make_user("requester"))
    db = SessionLocal()
    try:
        db.add(models.AlertSent(kind="critical", ticket_id=ticket_id, sent_at=MONDAY_9AM))
        db.commit()
        db.add(models.AlertSent(kind="critical", ticket_id=ticket_id, sent_at=MONDAY_9AM))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_health_shows_alerts(client, make_user, make_ticket, sent):
    admin = make_user("admin")
    health = client.get("/api/admin/health", headers=admin["headers"]).json()
    assert health["alerts_enabled"] is True
    assert health["alerts_last"] is None

    _ticket(make_ticket, make_user("requester"), level=4)
    _run()
    health = client.get("/api/admin/health", headers=admin["headers"]).json()
    assert health["alerts_last"].startswith("2031-03-03T15:00:00")


# ----- Técnicos no disponibles -----

def _set_availability(user_id, status, return_date=None):
    db = SessionLocal()
    try:
        db.add(models.TechnicianAvailability(
            user_id=user_id, status=status, return_date=return_date, updated_at=datetime(2031, 3, 1),
        ))
        db.commit()
    finally:
        db.close()


def _availability(user_id):
    db = SessionLocal()
    try:
        return db.query(models.TechnicianAvailability).filter(models.TechnicianAvailability.user_id == user_id).one()
    finally:
        db.close()


def test_due_soon_of_unavailable_technician_goes_to_coordinators(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    _set_availability(technician["id"], "vacation")
    _ticket(make_ticket, make_user("requester"), title="Impresora", due_at=MONDAY_8AM + timedelta(hours=8),
            assigned_to=technician["id"], status="in_progress")
    assert _run(MONDAY_8AM + timedelta(hours=7))["due_soon"] == 1
    to = {call[0] for call in _alerts_sent(sent, "[Ticketera] Por vencer")}
    assert coordinator["email"] in to
    assert technician["email"] not in to


def test_overdue_of_unavailable_technician_skips_technician(make_user, make_ticket, sent):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    _set_availability(technician["id"], "away")
    _ticket(make_ticket, make_user("requester"), due_at=MONDAY_9AM - timedelta(hours=1),
            assigned_to=technician["id"], status="in_progress")
    assert _run()["overdue"] == 1
    to = {call[0] for call in _alerts_sent(sent, "[Ticketera] Vencido")}
    assert coordinator["email"] in to
    assert technician["email"] not in to


@pytest.fixture
def summary_hour(monkeypatch):
    monkeypatch.setattr(config, "DAILY_SUMMARY_HOUR", 8)


def _return_notices(sent):
    return [call for call in sent if call[1].startswith("[Ticketera] Fecha de regreso")]


def test_return_date_notice_once_and_state_unchanged(make_user, sent, summary_hour, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Luis Rojas")
    _set_availability(technician["id"], "vacation", date(2031, 3, 3))

    assert _run()["return_date"] == 1
    notices = _return_notices(sent)
    assert {call[0] for call in notices} >= {technician["email"], coordinator["email"]}
    assert "Luis Rojas" in notices[0][2] and "03/03/2031" in notices[0][2]
    # El estado no vuelve solo a Disponible
    assert _availability(technician["id"]).status == "vacation"

    count = len(sent)
    assert _run(MONDAY_9AM + timedelta(hours=3))["return_date"] == 0
    assert len(sent) == count


def test_return_date_notice_again_for_a_new_date(make_user, sent, summary_hour, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    make_user("coordinator")
    technician = make_user("technician")
    _set_availability(technician["id"], "away", date(2031, 3, 3))
    assert _run()["return_date"] == 1
    db = SessionLocal()
    try:
        row = db.query(models.TechnicianAvailability).filter(models.TechnicianAvailability.user_id == technician["id"]).one()
        row.return_date = date(2031, 3, 4)
        db.commit()
    finally:
        db.close()
    assert _run()["return_date"] == 0
    assert _run(MONDAY_9AM + timedelta(days=1))["return_date"] == 1


def test_return_date_notice_waits_for_summary_hour(make_user, sent, summary_hour, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    make_user("coordinator")
    technician = make_user("technician")
    _set_availability(technician["id"], "vacation", date(2031, 3, 3))
    # 07:00 en Costa Rica: todavía no
    assert _run(MONDAY_8AM - timedelta(hours=1))["return_date"] == 0
    assert _return_notices(sent) == []
    # 08:00 en adelante: sí
    assert _run(MONDAY_8AM)["return_date"] == 1
    assert _return_notices(sent)


def test_return_date_notice_without_email_goes_to_log_once(make_user, summary_hour, monkeypatch, caplog):
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    make_user("coordinator")
    technician = make_user("technician")
    _set_availability(technician["id"], "vacation", date(2031, 3, 3))
    with caplog.at_level(logging.WARNING):
        assert _run()["return_date"] == 1
        assert _run(MONDAY_9AM + timedelta(minutes=2))["return_date"] == 0
    marks = [r for r in caplog.records if "[SOLO DESARROLLO] Aviso de fecha de regreso" in r.getMessage()]
    assert len(marks) == 1
    # El log no lleva correos
    assert technician["email"] not in caplog.text
    assert _availability(technician["id"]).return_notice_for == date(2031, 3, 3)
