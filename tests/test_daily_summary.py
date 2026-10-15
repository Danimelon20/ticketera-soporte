from datetime import date, datetime, timedelta

import pytest

from app import config, daily_summary, mailer, models
from app.database import SessionLocal

# Lunes 4 de marzo de 2030, 09:00 en Costa Rica (15:00 UTC). Lejos de la fecha real:
# ningún ticket de otras pruebas cuenta como "nuevo en las últimas 24 horas".
MONDAY_9AM = datetime(2030, 3, 4, 15, 0)
MONDAY_7AM = datetime(2030, 3, 4, 13, 0)
SATURDAY_9AM = datetime(2030, 3, 9, 15, 0)

SECTIONS = (
    "Tickets críticos (nivel 4) abiertos",
    "Tickets vencidos",
    "Tickets que vencen hoy",
    "Tickets sin asignar",
    "Borradores pendientes de revisión",
    "Tickets nuevos en las últimas 24 horas",
)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(config, "DAILY_SUMMARY_ENABLED", True)
    monkeypatch.setattr(config, "DAILY_SUMMARY_HOUR", 8)
    monkeypatch.setattr(config, "OFFICE_DAYS", "1-5")
    monkeypatch.setattr(config, "OFFICE_START", "08:00")
    monkeypatch.setattr(config, "OFFICE_END", "17:00")
    monkeypatch.setattr(config, "OFFICE_TZ", "America/Costa_Rica")
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    # La base de pruebas es compartida: cada prueba empieza sin resúmenes ni borradores
    db = SessionLocal()
    try:
        db.query(models.DailySummary).delete()
        db.query(models.EmailDraft).delete()
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


def _sections(body: str) -> dict[str, str]:
    """Texto de cada sección, del título hasta la siguiente."""
    result = {}
    for i, title in enumerate(SECTIONS):
        start = body.index(f"{title}:")
        end = body.index(f"{SECTIONS[i + 1]}:") if i + 1 < len(SECTIONS) else len(body)
        result[title] = body[start:end]
    return result


def _set_ticket(ticket_id, *, level=None, due_at=None, created_at=None, status=None, assigned_to=None):
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, ticket_id)
        ticket.created_at = created_at or MONDAY_9AM - timedelta(days=3)
        if status:
            ticket.status = status
        if assigned_to:
            ticket.assigned_to = assigned_to
        if level is not None or due_at is not None:
            db.add(models.TicketAIAnalysis(ticket_id=ticket_id, status="done", ai_level=level, due_at=due_at))
        db.commit()
    finally:
        db.close()


def _add_draft(requester_id, status, title="Impresora no imprime", level=2):
    db = SessionLocal()
    try:
        draft = models.EmailDraft(message_id=f"<{status}-{title}@test>", requester_id=requester_id,
                                  status=status, title=title, ai_level=level,
                                  contact_phone="88881234", contact_id_number="102340567")
        db.add(draft)
        db.commit()
        return draft.id
    finally:
        db.close()


def _summary_body(now=MONDAY_9AM):
    db = SessionLocal()
    try:
        return daily_summary.build_summary(db, now)
    finally:
        db.close()


def _maybe_send(now):
    db = SessionLocal()
    try:
        return daily_summary.maybe_send_daily_summary(db, now)
    finally:
        db.close()


def _summary_rows():
    db = SessionLocal()
    try:
        return db.query(models.DailySummary).all()
    finally:
        db.close()


def test_summary_groups_each_section(make_user, make_ticket):
    # Los tickets abiertos de otras pruebas llenarían el tope de 10 por sección
    db = SessionLocal()
    try:
        db.query(models.Ticket).update({"status": "closed"})
        db.commit()
    finally:
        db.close()
    requester = make_user("requester", "Ana Cliente")
    technician = make_user("technician", "Tomás Técnico")

    critical = make_ticket(requester, title="Caída de red")["id"]
    _set_ticket(critical, level=4, status="in_progress", assigned_to=technician["id"],
                due_at=MONDAY_9AM + timedelta(days=5))
    overdue = make_ticket(requester, title="Correo lento")["id"]
    _set_ticket(overdue, level=2, status="in_progress", assigned_to=technician["id"],
                due_at=MONDAY_9AM - timedelta(hours=2))
    due_today = make_ticket(requester, title="Pantalla negra")["id"]
    _set_ticket(due_today, level=3, status="in_progress", assigned_to=technician["id"],
                due_at=MONDAY_9AM + timedelta(hours=3))
    unassigned = make_ticket(requester, title="Sin asignar todavía")["id"]
    _set_ticket(unassigned)
    resolved = make_ticket(requester, title="Ya resuelto")["id"]
    _set_ticket(resolved, level=4, status="resolved", assigned_to=technician["id"],
                due_at=MONDAY_9AM - timedelta(hours=2))
    new_critical = make_ticket(requester, title="Servidor caído")["id"]
    _set_ticket(new_critical, level=4, created_at=MONDAY_9AM - timedelta(hours=1),
                due_at=MONDAY_9AM + timedelta(days=5))
    new_query = make_ticket(requester, title="Pregunta de estado")["id"]
    _set_ticket(new_query, level=1, created_at=MONDAY_9AM - timedelta(hours=5),
                due_at=MONDAY_9AM + timedelta(days=5))
    pending = _add_draft(requester["id"], "pending_review", title="Impresora atascada", level=3)
    _add_draft(requester["id"], "waiting_contact")
    _add_draft(requester["id"], "waiting_contact", title="Otro")
    _add_draft(requester["id"], "unclear", title="Confuso")

    subject, body = _summary_body()
    assert subject == "[Ticketera] Resumen del día 04/03/2030"
    sections = _sections(body)

    def where(ticket_id):
        return [title for title, text in sections.items() if f"- #{ticket_id} |" in text]

    assert where(critical) == ["Tickets críticos (nivel 4) abiertos"]
    assert f"#{critical} | Caída de red | Nivel 4 (Crítico) | En proceso | Tomás Técnico" in body
    assert where(overdue) == ["Tickets vencidos"]
    assert where(due_today) == ["Tickets que vencen hoy"]
    assert where(unassigned) == ["Tickets sin asignar"]
    # Resuelto: no está abierto y no es nuevo
    assert where(resolved) == []
    # Nuevo, crítico y sin asignar
    assert where(new_critical) == [
        "Tickets críticos (nivel 4) abiertos", "Tickets sin asignar", "Tickets nuevos en las últimas 24 horas",
    ]
    assert f"Borrador #{pending} | Impresora atascada | Nivel 3 (Incidente) | Solicitante: Ana Cliente" in body

    drafts = sections["Borradores pendientes de revisión"]
    assert drafts.startswith("Borradores pendientes de revisión: 1")
    assert "Esperan datos de contacto: 2" in drafts
    assert "Confuso" not in body

    new = sections["Tickets nuevos en las últimas 24 horas"]
    assert new.startswith("Tickets nuevos en las últimas 24 horas: 2")
    assert "Nivel 4 (Crítico): 1" in new and "Nivel 1 (Consulta): 1" in new
    assert new.index(f"#{new_critical} |") < new.index(f"#{new_query} |")
    assert body.rstrip().endswith(f"Puede ver el detalle en {config.APP_URL}")


def test_empty_section_says_none():
    later = MONDAY_9AM + timedelta(days=365)
    sections = _sections(_summary_body(later)[1])
    assert "Ninguno" in sections["Borradores pendientes de revisión"]
    assert sections["Tickets nuevos en las últimas 24 horas"].startswith("Tickets nuevos en las últimas 24 horas: 0")
    assert "Ninguno" in sections["Tickets nuevos en las últimas 24 horas"]


def test_section_lists_at_most_ten():
    lines = daily_summary._section("Prueba", [f"#{i}" for i in range(13)])
    assert lines[0] == "Prueba: 13"
    assert len([line for line in lines if line.startswith("  - #")]) == 10
    assert lines[-1] == "  - … y 3 más"


def test_summary_has_no_personal_data(make_user, make_ticket):
    requester = make_user("requester")
    # make_ticket guarda teléfono 8888-1234 y cédula 1-0234-0567, y repite el título en la descripción
    ticket = make_ticket(requester, title="Llamar al 8888-1234 o cédula 1-0234-0567")["id"]
    _set_ticket(ticket, level=3, created_at=MONDAY_9AM - timedelta(hours=1))
    _add_draft(requester["id"], "pending_review", title="Llámeme al 8777-6655")

    _subject, body = _summary_body()
    for secret in ("8888-1234", "88881234", "1-0234-0567", "102340567", "8777-6655", "87776655"):
        assert secret not in body
    assert "Descripción de" not in body
    assert f"#{ticket} | Llamar al [dato oculto] o cédula [dato oculto]" in body
    assert "Llámeme al [dato oculto]" in body


def test_recipients_are_active_coordinators_and_admins(make_user, sent):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    technician = make_user("technician")
    requester = make_user("requester")
    inactive = make_user("coordinator")
    db = SessionLocal()
    try:
        db.get(models.User, inactive["id"]).is_active = False
        db.commit()
    finally:
        db.close()

    assert _maybe_send(MONDAY_9AM) is True
    to = {call[0] for call in sent}
    assert coordinator["email"] in to and admin["email"] in to
    assert technician["email"] not in to and requester["email"] not in to and inactive["email"] not in to
    assert {call[1] for call in sent} == {"[Ticketera] Resumen del día 04/03/2030"}


def test_sent_once_per_day(make_user, sent):
    make_user("coordinator")
    assert _maybe_send(MONDAY_9AM) is True
    first = len(sent)
    assert first > 0
    # Más tarde el mismo día (o tras reiniciar la app): no se repite
    assert _maybe_send(MONDAY_9AM + timedelta(hours=3)) is False
    assert len(sent) == first
    rows = _summary_rows()
    assert [row.day for row in rows] == [date(2030, 3, 4)]
    # Al día hábil siguiente sí
    assert _maybe_send(MONDAY_9AM + timedelta(days=1)) is True
    assert len(sent) == 2 * first


def test_not_sent_before_hour(make_user, sent):
    make_user("coordinator")
    assert _maybe_send(MONDAY_7AM) is False
    assert sent == []
    assert _summary_rows() == []


def test_not_sent_on_weekend(make_user, sent):
    make_user("coordinator")
    assert _maybe_send(SATURDAY_9AM) is False
    assert sent == []


def test_not_sent_on_holiday(make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "2030-03-04")
    make_user("coordinator")
    assert _maybe_send(MONDAY_9AM) is False
    assert sent == []


def test_disabled_sends_nothing(make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "DAILY_SUMMARY_ENABLED", False)
    make_user("coordinator")
    assert _maybe_send(MONDAY_9AM) is False
    assert sent == []
    assert _summary_rows() == []


def test_failed_send_is_retried(make_user, monkeypatch):
    make_user("coordinator")
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: False)
    assert _maybe_send(MONDAY_9AM) is False
    assert _summary_rows() == []


@pytest.mark.parametrize("role", ["coordinator", "technician"])
def test_send_now_requires_admin(client, make_user, sent, monkeypatch, role):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    user = make_user(role)
    assert client.post("/api/admin/daily-summary/send-now", headers=user["headers"]).status_code == 403
    assert sent == []


def test_send_now_ignores_daily_control(client, make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    admin = make_user("admin")
    assert _maybe_send(MONDAY_9AM) is True
    before = len(sent)

    response = client.post("/api/admin/daily-summary/send-now", headers=admin["headers"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sent"] == data["recipients"] > 0
    assert len(sent) == before + data["sent"]
    assert len(_summary_rows()) == 1


def test_send_now_with_email_off(client, make_user, sent, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    admin = make_user("admin")
    response = client.post("/api/admin/daily-summary/send-now", headers=admin["headers"])
    assert response.status_code == 400
    assert sent == []


def test_health_shows_last_automatic_summary(client, make_user, sent):
    admin = make_user("admin")
    health = client.get("/api/admin/health", headers=admin["headers"]).json()
    assert health["daily_summary_last"] is None
    assert health["daily_summary_enabled"] is True

    _maybe_send(MONDAY_9AM)
    health = client.get("/api/admin/health", headers=admin["headers"]).json()
    assert health["daily_summary_last"].startswith("2030-03-04T15:00:00")
