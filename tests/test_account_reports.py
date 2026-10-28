import pytest

from app import chatbot, models
from app.database import SessionLocal


def _report(client, actor, target_id, reason="fake_data", comment=None):
    body = {"target_user_id": target_id, "reason": reason}
    if comment is not None:
        body["comment"] = comment
    return client.post("/api/account-reports", json=body, headers=actor["headers"])


def _resolve(client, actor, report_id, action, note="Revisado con el cliente"):
    return client.post(f"/api/account-reports/{report_id}/{action}", json={"note": note}, headers=actor["headers"])


def _actions(user_id):
    db = SessionLocal()
    try:
        rows = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == user_id, models.AccountChange.action.like("report_%"))
            .order_by(models.AccountChange.id)
            .all()
        )
        return [(r.action, r.actor_id, r.old_value, r.new_value) for r in rows]
    finally:
        db.close()


def _requester_with_ticket(make_user, make_ticket):
    requester = make_user("requester")
    ticket = make_ticket(requester)
    return requester, ticket


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_staff_can_report_requester(client, make_user, make_ticket, role):
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    actor = make_user(role)
    response = _report(client, actor, requester["id"], "foreign_id")
    assert response.status_code == 201
    assert response.json()["status"] == "open"
    assert response.json()["reason_label"] == "Cédula ajena"


def test_requester_cannot_report(client, make_user):
    target = make_user("requester")
    assert _report(client, make_user("requester"), target["id"]).status_code == 403


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_only_requester_accounts_can_be_reported(client, make_user, role):
    assert _report(client, make_user("coordinator"), make_user(role)["id"]).status_code == 400


def test_other_needs_text_and_others_do_not(client, make_user):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    assert _report(client, coordinator, requester["id"], "other").status_code == 422
    assert _report(client, coordinator, requester["id"], "other", "   ").status_code == 422
    assert _report(client, coordinator, requester["id"], "inventado").status_code == 422
    assert _report(client, coordinator, requester["id"], "other", "Usa varios correos").status_code == 201
    assert _report(client, make_user("admin"), requester["id"], "name_mismatch").status_code == 201


def test_one_open_report_per_person_and_account(client, make_user):
    coordinator = make_user("coordinator")
    other = make_user("coordinator")
    admin = make_user("admin")
    requester = make_user("requester")
    first = _report(client, coordinator, requester["id"]).json()
    duplicate = _report(client, coordinator, requester["id"], "repeated_tickets")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Ya tiene un reporte abierto sobre esta cuenta"
    # Otra persona sí puede
    assert _report(client, other, requester["id"]).status_code == 201
    # Resuelto el primero, quien reportó puede volver a hacerlo
    assert _resolve(client, admin, first["id"], "dismiss").status_code == 200
    assert _report(client, coordinator, requester["id"]).status_code == 201


def test_only_admin_reviews(client, make_user, make_ticket):
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    technician = make_user("technician")
    report = _report(client, technician, requester["id"]).json()
    # Técnico y coordinador crean reportes, pero revisarlos es del administrador
    for actor in (technician, make_user("coordinator")):
        assert client.get("/api/account-reports", headers=actor["headers"]).status_code == 403
        assert client.get("/api/account-reports/open-count", headers=actor["headers"]).status_code == 403
        assert _resolve(client, actor, report["id"], "confirm").status_code == 403
        assert _resolve(client, actor, report["id"], "dismiss").status_code == 403

    coordinator = make_user("admin")
    listed = client.get("/api/account-reports?status=open", headers=coordinator["headers"])
    assert listed.status_code == 200
    item = next(r for r in listed.json() if r["id"] == report["id"])
    # Correo enmascarado, nunca completo
    assert requester["email"] not in listed.text
    assert item["target_email"].startswith(requester["email"][:2]) and "***" in item["target_email"]
    count = client.get("/api/account-reports/open-count", headers=coordinator["headers"]).json()["open"]
    assert count >= 1

    assert _resolve(client, coordinator, report["id"], "confirm", note="  ").status_code == 422
    confirmed = _resolve(client, coordinator, report["id"], "confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert _resolve(client, coordinator, report["id"], "dismiss").status_code == 409
    after = client.get("/api/account-reports/open-count", headers=coordinator["headers"]).json()["open"]
    assert after == count - 1


def test_reporter_cannot_confirm_own_report(client, make_user):
    # Solo el administrador revisa: si él mismo reportó, lo confirma otro administrador
    admin = make_user("admin")
    requester = make_user("requester")
    report = _report(client, admin, requester["id"]).json()
    response = _resolve(client, admin, report["id"], "confirm")
    assert response.status_code == 403
    assert response.json()["detail"] == "Otro administrador debe confirmar este reporte"
    # Otra persona sí puede confirmarlo
    assert _resolve(client, make_user("admin"), report["id"], "confirm").status_code == 200


def test_reporter_can_withdraw_own_report(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester")
    report = _report(client, admin, requester["id"]).json()
    response = _resolve(client, admin, report["id"], "dismiss", note="Me equivoqué de cuenta")
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


def _directory_row(client, viewer, requester):
    rows = client.get(f"/api/directory?role=requester&q={requester['email']}", headers=viewer["headers"]).json()
    return next(r for r in rows if r["id"] == requester["id"])


def test_reported_label_while_open_or_confirmed(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester, ticket = _requester_with_ticket(make_user, make_ticket)
    assert _directory_row(client, coordinator, requester)["reported"] is False

    first = _report(client, coordinator, requester["id"]).json()
    assert _directory_row(client, coordinator, requester)["reported"] is True
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=coordinator["headers"]).json()
    assert detail["requester_flags"]["reported"] is True

    _resolve(client, admin, first["id"], "confirm")
    assert _directory_row(client, coordinator, requester)["reported"] is True

    second = _report(client, admin, requester["id"]).json()
    _resolve(client, admin, second["id"], "dismiss")
    # Confirmado sigue marcando
    assert _directory_row(client, coordinator, requester)["reported"] is True

    other, _ = _requester_with_ticket(make_user, make_ticket)
    third = _report(client, coordinator, other["id"]).json()
    _resolve(client, admin, third["id"], "dismiss")
    # Solo descartados: sin etiqueta
    assert _directory_row(client, coordinator, other)["reported"] is False


def test_reported_account_is_not_blocked(client, make_user, make_ticket):
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    report = _report(client, coordinator, requester["id"]).json()
    _resolve(client, make_user("admin"), report["id"], "confirm")
    assert client.get("/api/auth/me", headers=requester["headers"]).status_code == 200
    login = client.post(
        "/api/auth/login", json={"email": requester["email"], "password": "clave-segura-123", "portal": "requester"}
    )
    assert login.status_code == 200
    assert _directory_row(client, coordinator, requester)["is_active"] is True


def test_reports_are_recorded_without_comment_or_note(client, make_user):
    coordinator = make_user("coordinator")
    admin = make_user("admin")
    requester = make_user("requester")
    first = _report(client, coordinator, requester["id"], "other", "Comentario privado 1234").json()
    _resolve(client, admin, first["id"], "confirm", note="Nota privada 5678")
    second = _report(client, coordinator, requester["id"], "fake_data").json()
    _resolve(client, admin, second["id"], "dismiss", note="Otra nota privada")
    rows = _actions(requester["id"])
    assert [(a, actor) for a, actor, _, _ in rows] == [
        ("report_created", coordinator["id"]), ("report_confirmed", admin["id"]),
        ("report_created", coordinator["id"]), ("report_dismissed", admin["id"]),
    ]
    assert rows[0][3] == "Otro" and rows[2][3] == "Datos falsos"
    text = " ".join(f"{old} {new}" for _, _, old, new in rows)
    assert "privad" not in text


# ----- El solicitante nunca se entera -----

def test_requester_never_sees_reports(client, make_user, make_ticket, sent):
    requester, ticket = _requester_with_ticket(make_user, make_ticket)
    coordinator = make_user("coordinator")
    before = len(sent)
    report = _report(client, coordinator, requester["id"], "other", "Parece una cuenta falsa").json()
    _resolve(client, make_user("admin"), report["id"], "confirm")
    # Ningún correo, ni al solicitante ni a nadie
    assert len(sent) == before

    responses = [
        client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"]),
        client.get("/api/tickets", headers=requester["headers"]),
        client.get("/api/auth/me", headers=requester["headers"]),
    ]
    for response in responses:
        assert response.status_code == 200
        assert "requester_flags" not in response.text
        assert "reported" not in response.text.lower()
        assert "Parece una cuenta falsa" not in response.text
    assert client.get("/api/account-reports", headers=requester["headers"]).status_code == 403

    db = SessionLocal()
    try:
        context = chatbot.build_context(db, db.get(models.User, requester["id"]))
    finally:
        db.close()
    assert "report" not in context.lower()
    assert "Parece una cuenta falsa" not in context


def test_technician_cannot_report_unknown_requester(client, make_user):
    # Sin tickets ni borradores el técnico no pudo haberla visto
    requester = make_user("requester")
    assert _report(client, make_user("technician"), requester["id"]).status_code == 404


# ----- Etiqueta de identidad -----

def _set_identity(user_id, result):
    db = SessionLocal()
    try:
        row = db.query(models.RequesterIdentity).filter(models.RequesterIdentity.user_id == user_id).first()
        if row is None:
            row = models.RequesterIdentity(user_id=user_id)
            db.add(row)
        row.hacienda_result = result
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("result", ["match", "mismatch", "not_found", "unverified", None])
def test_staff_sees_identity_label(client, make_user, make_ticket, result):
    requester, ticket = _requester_with_ticket(make_user, make_ticket)
    _set_identity(requester["id"], result)
    expected = result or "unverified"
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    # El técnico ve solo sus tickets; el administrador no abre tickets (solo con "Ver conversación")
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]}, headers=coordinator["headers"])
    for viewer in (technician, coordinator):
        detail = client.get(f"/api/tickets/{ticket['id']}", headers=viewer["headers"]).json()
        assert detail["requester_flags"]["identity"] == expected
    assert _directory_row(client, make_user("coordinator"), requester)["identity"] == expected


def test_draft_shows_identity_and_reported(client, make_user):
    requester = make_user("requester")
    _set_identity(requester["id"], "not_found")
    db = SessionLocal()
    try:
        draft = models.EmailDraft(
            message_id=f"<etiquetas-{requester['id']}@test>", requester_id=requester["id"], status="pending_review",
            title="Sin internet", description="No hay red.", category_id=1, priority_id=1,
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id
    finally:
        db.close()
    technician = make_user("technician")
    _report(client, technician, requester["id"])
    drafts = client.get("/api/drafts", headers=make_user("coordinator")["headers"]).json()
    item = next(d for d in drafts if d["id"] == draft_id)
    assert item["requester_flags"] == {"identity": "not_found", "reported": True}


def test_requester_never_sees_identity_label_or_hacienda_name(client, make_user, make_ticket, monkeypatch):
    from app import hacienda

    monkeypatch.setattr(
        hacienda, "lookup_id", lambda id_number: {"tipo": "01", "name_tokens": frozenset({"NOMBREHACIENDA"})}
    )
    requester, ticket = _requester_with_ticket(make_user, make_ticket)
    _set_identity(requester["id"], "mismatch")
    own = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"])
    assert "requester_flags" not in own.text and "mismatch" not in own.text
    staff = client.get(f"/api/tickets/{ticket['id']}", headers=make_user("coordinator")["headers"])
    assert staff.json()["requester_flags"]["identity"] == "mismatch"
    assert "NOMBREHACIENDA" not in staff.text.upper()


# ----- Otros reportes abiertos sobre la misma cuenta -----

def _count_for(client, viewer, target_id):
    return client.get(f"/api/account-reports/open-count?target_user_id={target_id}", headers=viewer["headers"])


def test_reviewer_sees_open_reports_by_others(client, make_user, make_ticket):
    # Quien revisa es el administrador
    coordinator = make_user("admin")
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    other_requester, _ = _requester_with_ticket(make_user, make_ticket)
    _report(client, make_user("technician"), requester["id"])
    second = _report(client, make_user("admin"), requester["id"]).json()
    _report(client, coordinator, requester["id"])
    _report(client, make_user("admin"), other_requester["id"])

    # Los de otras personas: no cuenta el suyo
    assert _count_for(client, coordinator, requester["id"]).json() == {"open": 2}
    _resolve(client, coordinator, second["id"], "dismiss")
    assert _count_for(client, coordinator, requester["id"]).json() == {"open": 1}


def test_list_filters_by_account_and_shows_other_open(client, make_user, make_ticket):
    # La lista de reportes es del administrador
    coordinator = make_user("admin")
    admin = make_user("admin")
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    other_requester, _ = _requester_with_ticket(make_user, make_ticket)
    first = _report(client, make_user("technician"), requester["id"]).json()
    _report(client, admin, requester["id"])
    third = _report(client, make_user("coordinator"), requester["id"]).json()
    _report(client, admin, other_requester["id"])
    _resolve(client, admin, third["id"], "dismiss")

    response = client.get(f"/api/account-reports?target_user_id={requester['id']}", headers=coordinator["headers"])
    assert response.status_code == 200
    items = {r["id"]: r for r in response.json()}
    assert {r["target_user_id"] for r in items.values()} == {requester["id"]}
    assert len(items) == 3
    # Dos abiertos: cada uno ve al otro; el descartado ve los dos
    assert items[first["id"]]["other_open"] == 1
    assert items[third["id"]]["other_open"] == 2

    only_open = client.get(
        f"/api/account-reports?status=open&target_user_id={requester['id']}", headers=coordinator["headers"]
    ).json()
    assert {r["status"] for r in only_open} == {"open"} and len(only_open) == 2


def test_technician_never_gets_count_of_other_reports(client, make_user, make_ticket):
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    _report(client, make_user("coordinator"), requester["id"])
    _report(client, make_user("admin"), requester["id"])
    technician = make_user("technician")

    assert _count_for(client, technician, requester["id"]).status_code == 403
    assert client.get(
        f"/api/account-reports?target_user_id={requester['id']}", headers=technician["headers"]
    ).status_code == 403
    created = _report(client, technician, requester["id"])
    assert created.status_code == 201
    assert "other_open" not in created.json()
    assert "open" not in {k for k in created.json() if k != "status"}


def test_list_includes_reporter_role(client, make_user, make_ticket):
    # La pestaña Reportes pone primero los de coordinadores, que solo puede confirmar otra persona
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    by_coordinator = _report(client, make_user("coordinator"), requester["id"]).json()
    by_technician = _report(client, make_user("technician"), requester["id"]).json()
    items = {
        r["id"]: r
        for r in client.get(
            f"/api/account-reports?target_user_id={requester['id']}", headers=make_user("admin")["headers"]
        ).json()
    }
    assert items[by_coordinator["id"]]["reporter_role"] == "coordinator"
    assert items[by_technician["id"]]["reporter_role"] == "technician"


def test_admin_resolves_reports_from_technicians(client, make_user, make_ticket):
    coordinator = make_user("admin")
    requester, _ = _requester_with_ticket(make_user, make_ticket)
    first = _report(client, make_user("technician"), requester["id"]).json()
    second = _report(client, make_user("technician"), requester["id"]).json()
    assert _resolve(client, coordinator, first["id"], "confirm").json()["status"] == "confirmed"
    assert _resolve(client, coordinator, second["id"], "dismiss").json()["status"] == "dismissed"
