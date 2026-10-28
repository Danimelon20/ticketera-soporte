"""Registro de cambios por persona: solo el administrador, solo lectura y con datos enmascarados."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import audit, models
from app.database import SessionLocal

PASSWORD = "clave-segura-123"
SECRET_COMMENT = "texto-secreto-del-comentario"
SECRET_NOTE = "texto-secreto-de-la-nota"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _db():
    return SessionLocal()


def _activity(client, admin, person_id, **params):
    response = client.get(f"/api/admin/people/{person_id}/activity", params=params, headers=admin["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _ticket(requester_id: int, title="Ticket privado de prueba") -> int:
    db = _db()
    try:
        ticket = models.Ticket(title=title, description="Descripción privada", category_id=1, priority_id=1,
                               status="new", created_by=requester_id)
        db.add(ticket)
        db.commit()
        return ticket.id
    finally:
        db.close()


def _add(*rows):
    db = _db()
    try:
        for row in rows:
            db.add(row)
        db.commit()
    finally:
        db.close()


def _comment(ticket_id, user_id, content, internal=False, at=None):
    db = _db()
    try:
        comment = models.Comment(ticket_id=ticket_id, user_id=user_id, content=content, created_at=at or _now())
        db.add(comment)
        db.flush()
        if internal:
            db.add(models.InternalComment(comment_id=comment.id))
        db.commit()
        return comment.id
    finally:
        db.close()


def _record(actor_id, action, target_id=None, label=None, old=None, new=None, at=None):
    _add(models.AccountChange(actor_id=actor_id, target_user_id=target_id, target_label=label, action=action,
                              old_value=old, new_value=new, created_at=at or _now()))


# ----- Permisos -----
@pytest.mark.parametrize("role", ["technician", "coordinator", "requester"])
def test_only_admin_can_use_people_routes(client, make_user, role):
    user = make_user(role)
    person = make_user("technician")
    assert client.get("/api/admin/people", params={"query": "a"}, headers=user["headers"]).status_code == 403
    assert client.get(f"/api/admin/people/{person['id']}/activity", headers=user["headers"]).status_code == 403


def test_admin_can_use_people_routes(client, make_user):
    admin = make_user("admin")
    person = make_user("technician")
    assert client.get("/api/admin/people", params={"query": "a"}, headers=admin["headers"]).status_code == 200
    assert client.get(f"/api/admin/people/{person['id']}/activity", headers=admin["headers"]).status_code == 200


def test_admin_and_unknown_people_have_no_ficha(client, make_user):
    admin = make_user("admin")
    other_admin = make_user("admin")
    assert client.get(f"/api/admin/people/{other_admin['id']}/activity", headers=admin["headers"]).status_code == 404
    assert client.get("/api/admin/people/999999/activity", headers=admin["headers"]).status_code == 404


# ----- Búsqueda -----
def test_search_masks_email_skips_admins_and_limits_results(client, make_user):
    admin = make_user("admin")
    tag = uuid.uuid4().hex[:8]
    people = [make_user("technician", name=f"Persona {tag} {i}") for i in range(12)]
    make_user("admin", name=f"Persona {tag} admin")
    response = client.get("/api/admin/people", params={"query": tag}, headers=admin["headers"])
    hits = response.json()
    assert len(hits) == 10
    assert all(hit["role"] != "admin" for hit in hits)
    assert all("***@" in hit["email"] for hit in hits)
    for person in people:
        assert person["email"] not in response.text


def test_search_finds_clients_by_email(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    hits = client.get("/api/admin/people", params={"query": requester["email"]}, headers=admin["headers"]).json()
    assert [h["id"] for h in hits] == [requester["id"]]
    assert hits[0]["role"] == "requester"


# ----- Ficha del cliente: solo su cuenta -----
def test_client_ficha_never_returns_ticket_data(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    tech = make_user("technician")
    ticket_id = _ticket(requester["id"], title="Titulo-que-no-debe-salir")
    _comment(ticket_id, requester["id"], SECRET_COMMENT)
    _record(requester["id"], "attachment_uploaded", requester["id"], label=f"Ticket #{ticket_id}")
    _record(admin["id"], "conversation_viewed", requester["id"], label=f"Ticket #{ticket_id}", new="Motivo de prueba")
    _record(admin["id"], "phone_changed", requester["id"], old=audit.masked_digits("88881234"),
            new=audit.masked_digits("70005678"))
    _record(tech["id"], "report_created", requester["id"], new="Datos falsos")

    for params in ({}, {"types": "tickets,drafts"}, {"ticket": ticket_id}, {"types": "tickets", "ticket": ticket_id}):
        data = _activity(client, admin, requester["id"], **params)
        text = str(data)
        assert all(item["ticket_id"] is None for item in data["items"])
        assert "Ticket" not in text and "#" not in text
        assert "Titulo-que-no-debe-salir" not in text and SECRET_COMMENT not in text
        assert data["summary"] is None
        assert all(item["group"] in ("access", "data", "reports", "status") for item in data["items"])

    kinds = [i["kind"] for i in _activity(client, admin, requester["id"])["items"]]
    assert "phone_changed" in kinds and "report_created" in kinds
    assert "attachment_uploaded" not in kinds and "conversation_viewed" not in kinds


def test_client_header_is_masked(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    response = client.get(f"/api/admin/people/{requester['id']}/activity", headers=admin["headers"])
    person = response.json()["person"]
    assert person["email"].endswith("***@test.com") or "***@" in person["email"]
    assert person["phone"] == "****1234"
    assert requester["email"] not in response.text
    assert requester["phone"] not in response.text
    assert requester["id_number"] not in response.text
    assert person["state"] == "active" and person["reported"] is False


def test_client_changes_are_masked(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    _record(admin["id"], "id_number_changed", requester["id"], old=audit.masked_digits("112223333"),
            new=audit.masked_digits("445556666"))
    response = client.get(f"/api/admin/people/{requester['id']}/activity", headers=admin["headers"])
    assert "112223333" not in response.text and "445556666" not in response.text
    assert "****6666" in response.text


# ----- Ficha del personal -----
def test_staff_comments_and_notes_come_without_text(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    requester = make_user("requester")
    ticket_id = _ticket(requester["id"])
    _comment(ticket_id, tech["id"], SECRET_COMMENT)
    _comment(ticket_id, tech["id"], SECRET_NOTE, internal=True)
    response = client.get(f"/api/admin/people/{tech['id']}/activity", headers=admin["headers"])
    texts = [i["text"] for i in response.json()["items"]]
    assert f"Comentó en #{ticket_id}" in texts
    assert f"Nota interna en #{ticket_id}" in texts
    assert SECRET_COMMENT not in response.text and SECRET_NOTE not in response.text
    assert "Ticket privado de prueba" not in response.text


def test_staff_filters_by_group_dates_and_ticket(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    requester = make_user("requester")
    first, second = _ticket(requester["id"]), _ticket(requester["id"])
    old = _now() - timedelta(days=60)
    _comment(first, tech["id"], "uno")
    _comment(second, tech["id"], "dos")
    _comment(first, tech["id"], "viejo", at=old)
    _record(admin["id"], "schedule_changed", tech["id"], old="L-V 08:00–17:00", new="L-V 09:00–18:00")
    _record(None, "login", tech["id"])

    shifts = _activity(client, admin, tech["id"], types="shifts")["items"]
    assert [i["kind"] for i in shifts] == ["schedule_changed"]

    access = _activity(client, admin, tech["id"], types="access")["items"]
    assert [i["kind"] for i in access] == ["login"]

    by_ticket = _activity(client, admin, tech["id"], ticket=first)["items"]
    assert by_ticket and all(i["ticket_id"] == first for i in by_ticket)
    assert len(by_ticket) == 1  # el comentario viejo queda fuera de los últimos 30 días

    old_day = old.date()
    older = _activity(client, admin, tech["id"], **{"from": (old_day - timedelta(days=1)).isoformat(),
                                                    "to": (old_day + timedelta(days=1)).isoformat()})["items"]
    assert [i["ticket_id"] for i in older] == [first]


def test_cursor_pages_of_50_without_repeats(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    requester = make_user("requester")
    ticket_id = _ticket(requester["id"])
    base = _now() - timedelta(hours=2)
    same_moment = base + timedelta(minutes=30)
    for i in range(40):
        _comment(ticket_id, tech["id"], f"c{i}", at=base + timedelta(minutes=i))
    # Varios eventos con la misma fecha y de fuentes distintas
    for _ in range(10):
        _record(admin["id"], "schedule_changed", tech["id"], at=same_moment)
    for _ in range(10):
        _comment(ticket_id, tech["id"], "igual", at=same_moment)

    first = _activity(client, admin, tech["id"])
    assert len(first["items"]) == 50 and first["next_cursor"]
    second = _activity(client, admin, tech["id"], cursor=first["next_cursor"])
    assert len(second["items"]) == 10 and second["next_cursor"] is None
    assert second["summary"] is None
    keys = [(i["at"], i["kind"], i["text"]) for i in first["items"] + second["items"]]
    assert len(first["items"]) + len(second["items"]) == 60
    ats = [i["at"] for i in first["items"] + second["items"]]
    assert ats == sorted(ats, reverse=True)
    assert len(keys) == 60


def test_invalid_cursor_is_400(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    response = client.get(f"/api/admin/people/{tech['id']}/activity", params={"cursor": "no-es-un-cursor"},
                          headers=admin["headers"])
    assert response.status_code == 400


def test_staff_summary_counts(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    other = make_user("technician")
    requester = make_user("requester")
    t1, t2, t3, t4 = (_ticket(requester["id"]) for _ in range(4))
    # Todo unos minutos en el pasado: el rango llega hasta el momento de la consulta
    now = _now() - timedelta(minutes=10)
    hist = models.History
    _add(
        # Asignados: t1, t2 y t3 llegan a la persona
        hist(ticket_id=t1, field="assigned_to", old_value=None, new_value=str(tech["id"]), changed_by=admin["id"], changed_at=now),
        hist(ticket_id=t2, field="assigned_to", old_value=None, new_value=str(tech["id"]), changed_by=admin["id"], changed_at=now),
        # Recibido de otra persona
        hist(ticket_id=t3, field="assigned_to", old_value=str(other["id"]), new_value=str(tech["id"]), changed_by=admin["id"], changed_at=now),
        # Dado a otra persona
        hist(ticket_id=t2, field="assigned_to", old_value=str(tech["id"]), new_value=str(other["id"]), changed_by=tech["id"],
             changed_at=now + timedelta(minutes=1)),
        # Resuelto por la persona
        hist(ticket_id=t1, field="status", old_value="in_progress", new_value="resolved", changed_by=tech["id"], changed_at=now),
        # Fuera del rango: no cuenta
        hist(ticket_id=t4, field="assigned_to", old_value=None, new_value=str(tech["id"]), changed_by=admin["id"],
             changed_at=now - timedelta(days=90)),
        # Vencido mientras t1 era suyo; el de t2 venció cuando ya era de otra persona
        models.TicketDueNotice(ticket_id=t1, kind="overdue", created_at=now + timedelta(minutes=2)),
        models.TicketDueNotice(ticket_id=t2, kind="overdue", created_at=now + timedelta(minutes=2)),
        models.EmailDraft(message_id=f"<{uuid.uuid4()}>", requester_id=requester["id"], status="confirmed",
                          reviewed_by=tech["id"], reviewed_at=now),
        models.EmailDraft(message_id=f"<{uuid.uuid4()}>", requester_id=requester["id"], status="discarded",
                          reviewed_by=tech["id"], reviewed_at=now),
        models.EmailDraft(message_id=f"<{uuid.uuid4()}>", requester_id=requester["id"], status="discarded",
                          reviewed_by=tech["id"], reviewed_at=now),
    )
    _record(tech["id"], "login", tech["id"])
    _record(tech["id"], "microsoft_login", tech["id"])
    _record(None, "login_failed", tech["id"])

    summary = _activity(client, admin, tech["id"])["summary"]
    assert summary["assigned"] == 3
    assert summary["resolved"] == 1
    assert summary["reassigned_received"] == 1
    assert summary["reassigned_given"] == 1
    assert summary["overdue"] == 1
    assert summary["drafts_confirmed"] == 1
    assert summary["drafts_discarded"] == 2
    assert summary["logins"] == 2


def test_staff_header(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    person = _activity(client, admin, tech["id"])["person"]
    assert person["role"] == "technician" and person["state"] == "active"
    assert person["microsoft"] is False
    assert person["last_access"] is not None  # la sesión que crea make_user
    assert "***@" in person["email"]


# ----- Ingresos e intentos fallidos (se registran desde este cambio) -----
def test_login_and_failed_login_are_recorded(client, make_user):
    requester = make_user("requester")
    bad = client.post("/api/auth/login", json={"email": requester["email"], "password": "otra-clave-999", "portal": "requester"})
    assert bad.status_code == 401
    good = client.post("/api/auth/login", json={"email": requester["email"], "password": PASSWORD, "portal": "requester"})
    assert good.status_code == 200
    db = _db()
    try:
        actions = [r.action for r in db.query(models.AccountChange)
                   .filter(models.AccountChange.target_user_id == requester["id"]).order_by(models.AccountChange.id)]
    finally:
        db.close()
    assert actions == ["login_failed", "login"]


def test_recent_activity_can_skip_access_events(client, make_user):
    admin = make_user("admin")
    tech = make_user("technician")
    _record(tech["id"], "login", tech["id"])
    data = client.get("/api/admin/account-changes", params={"exclude_access": "true"}, headers=admin["headers"]).json()
    assert all(item["action"] not in ("login", "login_failed", "microsoft_login") for item in data["items"])
