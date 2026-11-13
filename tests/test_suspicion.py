"""Señales automáticas de suplantación: cédula de otra cuenta y nombre que no coincide con Hacienda."""
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import account_reports, config, hacienda, identity, models
from app.database import SessionLocal
from app.email_processing import process_message

ID_IN_USE = "No es posible usar esta cédula. Si le pertenece, contacte a soporte."
MAILBOX = "soporte@ejemplo.com"


def _profile(id_number, name="Daniel Alejandro Sáenz Maroto"):
    return {"name": name, "phone": "8888-1234", "id_number": id_number}


def _changes(user_id, action):
    db = SessionLocal()
    try:
        return (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == user_id, models.AccountChange.action == action)
            .order_by(models.AccountChange.id)
            .all()
        )
    finally:
        db.close()


def _auto_reports(user_id):
    db = SessionLocal()
    try:
        agent = db.query(models.User).filter(models.User.role == "agent").first()
        return (
            db.query(models.AccountReport)
            .filter(models.AccountReport.target_user_id == user_id, models.AccountReport.reporter_id == agent.id)
            .order_by(models.AccountReport.id)
            .all()
        )
    finally:
        db.close()


def _is_reported(user_id):
    db = SessionLocal()
    try:
        return bool(account_reports.reported_ids(db, [user_id]))
    finally:
        db.close()


@pytest.fixture
def hacienda_match(monkeypatch):
    """Hacienda devuelve el mismo nombre que escribe la prueba: sin mismatch."""
    def _lookup(id_number):
        return {"tipo": "01" if len(id_number) == 9 else "03",
                "name_tokens": hacienda.name_tokens("Daniel Alejandro Sáenz Maroto")}

    monkeypatch.setattr(hacienda, "lookup_id", _lookup)


# ----- Cédula de otra cuenta -----


def _activity(report_id):
    db = SessionLocal()
    try:
        activity = db.query(models.AutoReportActivity).filter(models.AutoReportActivity.report_id == report_id).first()
        owners = db.query(models.AutoReportIdOwner).filter(models.AutoReportIdOwner.report_id == report_id).count()
        return activity.attempts, activity.last_attempt_at, owners
    finally:
        db.close()


def _admin_view(client, admin, user_id):
    reports = client.get("/api/account-reports?status=open", headers=admin["headers"]).json()
    return next(r for r in reports if r["target_user_id"] == user_id and r["reporter_role"] == "agent")


def test_email_with_foreign_id_is_reported(client, make_user, sent, monkeypatch, hacienda_match):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    msg = EmailMessage()
    msg["From"] = other["email"]
    msg["To"] = MAILBOX
    msg["Subject"] = "[Soporte] Planillas"
    msg["Message-ID"] = make_msgid()
    msg.set_content(
        "Categoría: Software\nPrioridad: Alta\nTeléfono: 8888-1234\n"
        f"Cédula: {owner['id_number']}\nDescripción: No abre el sistema de planillas."
    )
    db = SessionLocal()
    try:
        assert process_message(db, msg) == "incomplete"
    finally:
        db.close()

    # Un correo es un evento: un registro, un reporte y un intento
    changes = _changes(other["id"], "id_number_conflict")
    assert len(changes) == 1 and changes[0].new_value.endswith("(correo)")
    reports = _auto_reports(other["id"])
    assert len(reports) == 1 and reports[0].reason == "foreign_id"
    assert changes[0].actor_id == reports[0].reporter_id  # el Agente virtual
    assert _activity(reports[0].id)[0] == 1
    # La respuesta al cliente no menciona la otra cuenta
    assert owner["email"] not in sent[-1][2]


def test_email_contact_with_foreign_id_is_reported(make_user, hacienda_match):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    db = SessionLocal()
    try:
        user = db.get(models.User, other["id"])
        assert identity.apply_contact_from_email(db, user, None, owner["id_number"]) == identity.ID_IN_USE_REASON
        db.commit()
    finally:
        db.close()
    assert len(_changes(other["id"], "id_number_conflict")) == 1
    reports = _auto_reports(other["id"])
    assert len(reports) == 1 and reports[0].reason == "foreign_id"
    assert _activity(reports[0].id)[0] == 1


def _draft(requester_id, contact_id_number):
    db = SessionLocal()
    try:
        draft = models.EmailDraft(
            message_id=make_msgid(), requester_id=requester_id, subject="Impresora", body="No imprime",
            status="pending_review", contact_phone="88881234", contact_id_number=contact_id_number,
        )
        db.add(draft)
        db.commit()
        return draft.id
    finally:
        db.close()


def _confirm(client, draft_id, actor, contact_id_number):
    payload = {
        "title": "Impresora", "category_id": 1, "priority_id": 3, "description": "No imprime.",
        "contact_phone": "8888-1234", "contact_id_number": contact_id_number,
    }
    return client.post(f"/api/drafts/{draft_id}/confirm", json=payload, headers=actor["headers"])


def test_confirming_draft_does_not_count_the_email_attempt_twice(client, make_user, hacienda_match):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    coordinator = make_user("coordinator")
    # El intento del correo ya se contó al llegar
    db = SessionLocal()
    try:
        identity.apply_contact_from_email(db, db.get(models.User, other["id"]), None, owner["id_number"])
        db.commit()
    finally:
        db.close()
    draft_id = _draft(other["id"], owner["id_number"])
    response = _confirm(client, draft_id, coordinator, owner["id_number"])
    assert response.status_code == 400
    change = _changes(other["id"], "id_number_conflict")[-1]
    assert change.actor_id == coordinator["id"] and change.new_value.endswith("(borrador del correo)")
    reports = _auto_reports(other["id"])
    assert len(reports) == 1 and _activity(reports[0].id)[0] == 1


def test_draft_with_staff_typed_foreign_id_is_only_logged(client, make_user, hacienda_match):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    coordinator = make_user("coordinator")
    draft_id = _draft(other["id"], None)
    response = _confirm(client, draft_id, coordinator, owner["id_number"])
    assert response.status_code == 400
    assert _changes(other["id"], "id_number_conflict")[-1].new_value.endswith("(cambio del personal)")
    assert _auto_reports(other["id"]) == []


def test_admin_applying_foreign_id_is_only_logged(client, make_user, make_ticket, hacienda_match):
    owner = make_user("requester")
    target = make_user("requester")
    make_ticket(target)  # cédula fija: el cambio va por solicitud
    coordinator = make_user("coordinator")
    response = client.post(
        "/api/account-requests",
        json={"target_user_id": target["id"], "kind": "id_number", "new_value": owner["id_number"], "reason": "Corrección"},
        headers=coordinator["headers"],
    )
    assert response.status_code == 400
    change = _changes(target["id"], "id_number_conflict")[-1]
    assert change.actor_id == coordinator["id"]
    assert _auto_reports(target["id"]) == []


# ----- Nombre que no coincide con Hacienda -----


def test_email_mismatch_reported_only_when_client_sends_it(make_user):
    from_email = make_user("requester", name="Ana Mora Vargas", profile=False)
    confirmed = make_user("requester", name="Luis Rojas Solís", profile=False)
    admin = make_user("admin")
    db = SessionLocal()
    try:
        identity.apply_contact_from_email(db, db.get(models.User, from_email["id"]), None, "1-0234-0567")
        identity.apply_contact_from_email(
            db, db.get(models.User, confirmed["id"]), None, "1-0987-0654", actor_id=admin["id"],
        )
        db.commit()
    finally:
        db.close()
    assert [r.reason for r in _auto_reports(from_email["id"])] == ["name_mismatch"]
    assert _auto_reports(confirmed["id"]) == []


# ----- Etiqueta "Reportada": solo después de que el administrador confirma -----


def test_manual_open_report_still_flags_immediately(client, make_user, make_ticket):
    requester = make_user("requester")
    coordinator = make_user("coordinator")
    make_ticket(requester)
    response = client.post(
        "/api/account-reports", json={"target_user_id": requester["id"], "reason": "fake_data"},
        headers=coordinator["headers"],
    )
    assert response.status_code == 201, response.text
    assert _is_reported(requester["id"]) is True


def _set_id_number(user_id, value):
    db = SessionLocal()
    try:
        db.get(models.User, user_id).id_number = value
        db.commit()
    finally:
        db.close()
