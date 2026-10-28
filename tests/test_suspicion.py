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

def test_profile_with_foreign_id_is_logged_and_reported(client, make_user, hacienda_match):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    response = client.put("/api/profile", json=_profile(owner["id_number"]), headers=other["headers"])
    assert response.status_code == 400
    # El cliente ve el mismo mensaje de siempre: nada de reportes ni de la otra cuenta
    assert response.json()["detail"] == ID_IN_USE

    change = _changes(other["id"], "id_number_conflict")[-1]
    assert change.actor_id == other["id"]
    assert owner["id_number"] not in change.new_value
    assert change.new_value == f"****{owner['id_number'][-4:]} (perfil)"

    reports = _auto_reports(other["id"])
    assert len(reports) == 1
    assert reports[0].reason == "foreign_id" and reports[0].status == "open"
    assert f"cuenta #{owner['id']}" in reports[0].comment
    assert owner["id_number"] not in reports[0].comment


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


def test_repeated_attempts_add_to_the_open_report(client, make_user, hacienda_match):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    admin = make_user("admin")
    assert client.put("/api/profile", json=_profile(owner["id_number"]), headers=other["headers"]).status_code == 400
    report = _auto_reports(other["id"])[0]
    _attempts, first_at, _owners = _activity(report.id)
    for _ in range(2):
        assert client.put("/api/profile", json=_profile(owner["id_number"]), headers=other["headers"]).status_code == 400

    # Cada intento queda en el registro, pero el administrador ve un solo reporte con el contador
    assert len(_changes(other["id"], "id_number_conflict")) == 3
    assert [r.id for r in _auto_reports(other["id"])] == [report.id]
    attempts, last_at, owners = _activity(report.id)
    assert attempts == 3 and last_at >= first_at
    # La misma cédula tres veces es una sola cédula distinta: no es señal fuerte
    assert owners == 1
    view = _admin_view(client, admin, other["id"])
    assert view["attempts"] == 3 and view["last_attempt_at"]
    assert view["distinct_id_numbers"] == 1 and view["strong_signal"] is False


def test_three_distinct_foreign_ids_is_a_strong_signal(client, make_user, hacienda_match):
    owners = [make_user("requester") for _ in range(3)]
    other = make_user("requester", profile=False)
    admin = make_user("admin")
    for index, owner in enumerate(owners, start=1):
        response = client.put("/api/profile", json=_profile(owner["id_number"]), headers=other["headers"])
        assert response.status_code == 400
        view = _admin_view(client, admin, other["id"])
        assert view["distinct_id_numbers"] == index
        # Con dos todavía no; desde la tercera cédula distinta, sí
        assert view["strong_signal"] is (index >= 3)
    assert len(_auto_reports(other["id"])) == 1
    assert view["attempts"] == 3


def test_other_reason_goes_into_the_same_open_report(client, make_user, hacienda_match, monkeypatch):
    owner = make_user("requester")
    other = make_user("requester", profile=False)
    assert client.put("/api/profile", json=_profile(owner["id_number"]), headers=other["headers"]).status_code == 400
    # Ahora un perfil válido, pero con un nombre que Hacienda no reconoce
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: {"tipo": "01", "name_tokens": frozenset()})
    assert client.put("/api/profile", json=_profile("1-0987-0654"), headers=other["headers"]).status_code == 200
    reports = _auto_reports(other["id"])
    assert len(reports) == 1 and reports[0].reason == "foreign_id"
    assert "También: El nombre no coincide." in reports[0].comment
    # Otro motivo no suma intentos de cédula ajena
    assert _activity(reports[0].id)[0] == 1


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

def test_profile_name_mismatch_is_reported(client, make_user):
    # El Hacienda simulado de conftest no trae nombre: el resultado es mismatch
    user = make_user("requester", profile=False)
    response = client.put("/api/profile", json=_profile("1-0234-0567"), headers=user["headers"])
    assert response.status_code == 200, response.text
    reports = _auto_reports(user["id"])
    assert [r.reason for r in reports] == ["name_mismatch"]
    assert "perfil" in reports[0].comment


def test_profile_name_match_is_not_reported(client, make_user, hacienda_match):
    user = make_user("requester", profile=False)
    assert client.put("/api/profile", json=_profile("1-0234-0567"), headers=user["headers"]).status_code == 200
    assert _auto_reports(user["id"]) == []


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

def test_auto_report_flags_only_after_admin_confirms(client, make_user, make_ticket, hacienda_match):
    owner = make_user("requester")
    other = make_user("requester")
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    other_id_number = other["id_number"]
    _set_id_number(other["id"], None)
    assert client.put("/api/profile", json=_profile(owner["id_number"]), headers=other["headers"]).status_code == 400
    _set_id_number(other["id"], other_id_number)
    ticket = make_ticket(other)

    assert _is_reported(other["id"]) is False
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["requester_flags"]["reported"] is False
    # Pero el administrador lo ve en la cola de Reportes
    open_reports = client.get("/api/account-reports?status=open", headers=admin["headers"]).json()
    assert any(r["target_user_id"] == other["id"] and r["reporter_role"] == "agent" for r in open_reports)

    report_id = _auto_reports(other["id"])[0].id
    response = client.post(
        f"/api/account-reports/{report_id}/confirm", json={"note": "Revisado"}, headers=admin["headers"]
    )
    assert response.status_code == 200, response.text
    assert _is_reported(other["id"]) is True
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["requester_flags"]["reported"] is True


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
