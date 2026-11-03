"""Tickets a nombre de un cliente: búsqueda por correo exacto y verificación de identidad por teléfono.

La misma batería corre para el técnico y el coordinador (STAFF).
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app import availability, models, registrations
from app.database import SessionLocal

STAFF = ("technician", "coordinator")
LOOKUP = "/api/requesters/lookup"
PERSONAL_KEYS = {"id", "email", "phone", "id_number"}


def _lookup(client, actor, email):
    return client.post(LOOKUP, json={"email": email}, headers=actor["headers"])


def _check(actor, client_user, channel="phone", method="id_last4", last4=None):
    db = SessionLocal()
    try:
        return registrations.check_identity(
            db, db.get(models.User, actor["id"]), db.get(models.User, client_user["id"]), channel, method, last4,
        )
    finally:
        db.close()


def _error(actor, client_user, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        _check(actor, client_user, **kwargs)
    return caught.value


def _right(client_user):
    return client_user["id_number"][-4:]


def _wrong(client_user):
    return "0000" if _right(client_user) != "0000" else "1111"


def _failures(client_user):
    db = SessionLocal()
    try:
        return (
            db.query(models.AccountChange)
            .filter(models.AccountChange.action == "identity_check_failed",
                    models.AccountChange.target_user_id == client_user["id"])
            .order_by(models.AccountChange.id)
            .all()
        )
    finally:
        db.close()


def _deactivate(user):
    db = SessionLocal()
    try:
        db.get(models.User, user["id"]).is_active = False
        db.commit()
    finally:
        db.close()


# ----- Búsqueda por correo exacto -----

@pytest.mark.parametrize("role", STAFF)
def test_lookup_exact_email(client, make_user, make_ticket, role):
    staff = make_user(role)
    customer = make_user("requester", "Ana Cliente")
    ticket = make_ticket(customer, title="Sin internet")

    for email in (customer["email"], f"  {customer['email'].upper()}  "):
        response = _lookup(client, staff, email)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Ana Cliente"
        assert data["profile_complete"] is True
        # Nunca el id, el correo, el teléfono ni la cédula del cliente
        assert not PERSONAL_KEYS & set(data)
        if role == "coordinator":
            assert data["open_tickets"] == [{"id": ticket["id"], "title": "Sin internet"}]
        else:
            assert "open_tickets" not in data


@pytest.mark.parametrize("role", STAFF)
def test_lookup_partial_finds_nothing(client, make_user, role):
    staff = make_user(role)
    customer = make_user("requester", "Beto Parcial")
    local, domain = customer["email"].split("@")
    for text in (local, f"@{domain}", local[:10], customer["email"][:-1], "Beto Parcial", "Beto", "%", "_"):
        response = _lookup(client, staff, text)
        assert response.status_code in (404, 422), text
        if response.status_code == 404:
            assert response.json()["detail"].startswith(registrations.NOT_REGISTERED)


@pytest.mark.parametrize("role", STAFF)
def test_lookup_not_registered_gives_register_address(client, make_user, role):
    staff = make_user(role)
    response = _lookup(client, staff, "nadie-registrado@ejemplo.com")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail.startswith("El cliente debe registrarse primero en la página")
    assert registrations.register_url() in detail


@pytest.mark.parametrize("role", STAFF)
def test_lookup_only_active_requesters(client, make_user, role):
    staff = make_user(role)
    inactive = make_user("requester")
    _deactivate(inactive)
    other_staff = make_user("technician")
    for email in (inactive["email"], other_staff["email"], staff["email"]):
        assert _lookup(client, staff, email).status_code == 404


@pytest.mark.parametrize("role", ("requester", "admin"))
def test_lookup_forbidden_for_other_roles(client, make_user, role):
    actor = make_user(role)
    customer = make_user("requester")
    response = _lookup(client, actor, customer["email"])
    assert response.status_code == 403
    assert response.json()["detail"] == registrations.NO_PERMISSION


def test_lookup_is_post_only(client, make_user):
    # El correo nunca viaja en la URL
    staff = make_user("technician")
    customer = make_user("requester")
    response = client.get(f"{LOOKUP}?email={customer['email']}", headers=staff["headers"])
    assert response.status_code in (404, 405)
    assert "profile_complete" not in response.text


# ----- Verificación de identidad -----

@pytest.mark.parametrize("role", STAFF)
def test_email_channel_needs_no_check(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    assert _check(staff, customer, channel="email", method=None) is None


@pytest.mark.parametrize("role", STAFF)
def test_phone_without_method_is_rejected(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    error = _error(staff, customer, method=None)
    assert (error.status_code, error.detail) == (422, registrations.METHOD_REQUIRED)


@pytest.mark.parametrize("role", STAFF)
def test_phone_with_registered_email(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    assert _check(staff, customer, method="email") == "email"


@pytest.mark.parametrize("role", STAFF)
def test_phone_with_last4(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    assert _check(staff, customer, last4=_right(customer)) == "id_last4"
    assert _failures(customer) == []


@pytest.mark.parametrize("role", STAFF)
@pytest.mark.parametrize("last4", (None, "", "12", "12345", "12a4"))
def test_last4_bad_format_is_not_a_failure(make_user, role, last4):
    staff, customer = make_user(role), make_user("requester")
    error = _error(staff, customer, last4=last4)
    assert (error.status_code, error.detail) == (422, registrations.LAST4_FORMAT)
    assert _failures(customer) == []


@pytest.mark.parametrize("role", STAFF)
def test_wrong_last4_is_recorded(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    error = _error(staff, customer, last4=_wrong(customer))
    assert (error.status_code, error.detail) == (400, registrations.LAST4_MISMATCH)
    [row] = _failures(customer)
    # Persona, cliente y hora; nunca los dígitos
    assert row.actor_id == staff["id"]
    assert row.target_user_id == customer["id"]
    assert row.created_at is not None
    for value in (row.old_value, row.new_value, row.target_label):
        assert _wrong(customer) not in (value or "")
        assert _right(customer) not in (value or "")


@pytest.mark.parametrize("role", STAFF)
def test_client_without_id_number(make_user, role):
    staff = make_user(role)
    customer = make_user("requester", profile=False)
    error = _error(staff, customer, last4="1234")
    assert (error.status_code, error.detail) == (400, registrations.NO_ID_NUMBER)


@pytest.mark.parametrize("role", STAFF)
def test_limit_blocks_after_five_failures(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    for _ in range(registrations.MAX_FAILURES - 1):
        assert _error(staff, customer, last4=_wrong(customer)).status_code == 400
    # El quinto fallo ya bloquea
    error = _error(staff, customer, last4=_wrong(customer))
    assert (error.status_code, error.detail) == (429, "Verificación bloqueada: use el correo registrado del cliente")
    # El sexto intento, aunque sea correcto, también
    error = _error(staff, customer, last4=_right(customer))
    assert (error.status_code, error.detail) == (429, registrations.BLOCKED)
    assert len(_failures(customer)) == registrations.MAX_FAILURES
    # Por correo registrado se sigue pudiendo
    assert _check(staff, customer, method="email") == "email"
    # El bloqueo es por cliente: otro cliente no se ve afectado
    other = make_user("requester")
    assert _check(staff, other, last4=_right(other)) == "id_last4"


def test_limit_adds_up_all_staff(make_user):
    technician, coordinator, customer = make_user("technician"), make_user("coordinator"), make_user("requester")
    for _ in range(3):
        _error(technician, customer, last4=_wrong(customer))
    _error(coordinator, customer, last4=_wrong(customer))
    assert _error(coordinator, customer, last4=_wrong(customer)).status_code == 429
    assert _error(technician, customer, last4=_right(customer)).status_code == 429
    actors = [row.actor_id for row in _failures(customer)]
    assert actors == [technician["id"]] * 3 + [coordinator["id"]] * 2


@pytest.mark.parametrize("role", STAFF)
def test_old_failures_do_not_count(make_user, role):
    staff, customer = make_user(role), make_user("requester")
    for _ in range(registrations.MAX_FAILURES):
        _error(staff, customer, last4=_wrong(customer))
    assert _error(staff, customer, last4=_right(customer)).status_code == 429
    db = SessionLocal()
    try:
        for row in db.query(models.AccountChange).filter(models.AccountChange.target_user_id == customer["id"]):
            row.created_at -= registrations.FAILURE_WINDOW + timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert _check(staff, customer, last4=_right(customer)) == "id_last4"


# ----- Crear a nombre del cliente (POST /api/tickets/for-client) -----

CREATE = "/api/tickets/for-client"


def _payload(customer_email, **extra):
    payload = {
        "client_email": customer_email,
        "channel": "email",
        "title": "No imprime",
        "description": "La impresora no responde desde ayer",
        "category_id": 1,
        "priority_id": 1,
    }
    payload.update(extra)
    return payload


def _create(client, actor, customer_email, **extra):
    return client.post(CREATE, json=_payload(customer_email, **extra), headers=actor["headers"])


def _ticket_rows(ticket_id):
    db = SessionLocal()
    try:
        ticket = db.get(models.Ticket, ticket_id)
        registration = db.query(models.TicketRegistration).filter_by(ticket_id=ticket_id).first()
        history = db.query(models.History).filter_by(ticket_id=ticket_id).order_by(models.History.id).all()
        return ticket, registration, [(h.field, h.old_value, h.new_value, h.changed_by) for h in history]
    finally:
        db.close()


def _ticket_count(owner_id):
    db = SessionLocal()
    try:
        return db.query(models.Ticket).filter(models.Ticket.created_by == owner_id).count()
    finally:
        db.close()


@pytest.mark.parametrize("role", STAFF)
@pytest.mark.parametrize("channel,method,expected_check", (
    ("email", None, None),
    ("phone", "email", "email"),
    ("phone", "id_last4", "id_last4"),
))
def test_staff_creates_for_registered_client(client, make_user, role, channel, method, expected_check):
    staff, customer = make_user(role, "Persona de Soporte"), make_user("requester", "Ana Cliente")
    extra = {"channel": channel, "identity_check": method}
    if method == "id_last4":
        extra["id_last4"] = _right(customer)
    response = _create(client, staff, f" {customer['email'].upper()} ", **extra)
    assert response.status_code == 200, response.text
    data = response.json()
    # Solo el número y quién lo atiende: nada del cliente
    assert set(data) == {"ticket_id", "assigned_name"}
    assert data["assigned_name"] is None

    ticket, registration, history = _ticket_rows(data["ticket_id"])
    # El solicitante es el cliente; quien lo creó queda en el registro
    assert ticket.created_by == customer["id"]
    assert ticket.contact_phone == customer["phone"]
    assert ticket.source == "web"
    assert (registration.registered_by, registration.channel, registration.identity_check) == (
        staff["id"], channel, expected_check)
    assert history == [("created_on_behalf", None, channel, staff["id"])]


@pytest.mark.parametrize("role", STAFF)
def test_unregistered_client_creates_nothing(client, make_user, role):
    staff, customer = make_user(role), make_user("requester")
    local = customer["email"].split("@")[0]
    for email in ("nadie@ejemplo.com", local, customer["email"][:-1]):
        response = _create(client, staff, email)
        assert response.status_code == 404
        assert response.json()["detail"].startswith(registrations.NOT_REGISTERED)
    assert _ticket_count(customer["id"]) == 0


@pytest.mark.parametrize("role", STAFF)
def test_incomplete_profile_creates_nothing(client, make_user, role):
    staff, customer = make_user(role), make_user("requester", profile=False)
    response = _create(client, staff, customer["email"])
    assert response.status_code == 400
    assert _ticket_count(customer["id"]) == 0


@pytest.mark.parametrize("role", STAFF)
def test_phone_without_method_creates_nothing(client, make_user, role):
    staff, customer = make_user(role), make_user("requester")
    response = _create(client, staff, customer["email"], channel="phone")
    assert response.status_code == 422
    assert response.json()["detail"] == registrations.METHOD_REQUIRED
    assert _ticket_count(customer["id"]) == 0


@pytest.mark.parametrize("role", STAFF)
def test_wrong_digits_create_nothing_and_block(client, make_user, role):
    staff, customer = make_user(role), make_user("requester")
    wrong = {"channel": "phone", "identity_check": "id_last4", "id_last4": _wrong(customer)}
    codes = [_create(client, staff, customer["email"], **wrong).status_code for _ in range(5)]
    assert codes == [400, 400, 400, 400, 429]
    right = dict(wrong, id_last4=_right(customer))
    response = _create(client, staff, customer["email"], **right)
    assert response.status_code == 429
    assert response.json()["detail"] == registrations.BLOCKED
    assert _ticket_count(customer["id"]) == 0
    assert len(_failures(customer)) == 5
    # Por el correo registrado sí se crea
    response = _create(client, staff, customer["email"], channel="phone", identity_check="email")
    assert response.status_code == 200


@pytest.mark.parametrize("role", ("requester", "admin"))
def test_create_forbidden_for_other_roles(client, make_user, role):
    actor, customer = make_user(role), make_user("requester")
    response = _create(client, actor, customer["email"])
    assert response.status_code == 403
    assert _ticket_count(customer["id"]) == 0


def test_technician_cannot_choose_assignee(client, make_user):
    technician, other, customer = make_user("technician"), make_user("technician"), make_user("requester")
    for assignee in (other["id"], technician["id"]):
        response = _create(client, technician, customer["email"], assigned_to=assignee)
        assert response.status_code == 403
        assert response.json()["detail"] == "Solo un coordinador puede asignar tickets"
    assert _ticket_count(customer["id"]) == 0


def test_coordinator_assigns_manually(client, make_user, sent, monkeypatch):
    coordinator = make_user("coordinator")
    technician = make_user("technician", "Técnica Elegida")
    customer = make_user("requester")
    monkeypatch.setattr(availability, "is_available", lambda db, user, now=None: True)
    response = _create(client, coordinator, customer["email"], assigned_to=technician["id"])
    assert response.status_code == 200, response.text
    assert response.json()["assigned_name"] == "Técnica Elegida"
    ticket, _, history = _ticket_rows(response.json()["ticket_id"])
    assert ticket.assigned_to == technician["id"]
    assert history == [
        ("created_on_behalf", None, "email", coordinator["id"]),
        ("assigned_to", None, str(technician["id"]), coordinator["id"]),
    ]
    # El técnico recibe su aviso de asignación y el cliente su confirmación con el nombre
    assert sorted(to for to, _, _ in sent) == sorted([technician["email"], customer["email"]])
    [body] = [body for to, _, body in sent if to == customer["email"]]
    assert "Lo atiende: Técnica Elegida" in body.splitlines()


def test_coordinator_assign_unavailable_needs_confirmation(client, make_user, monkeypatch):
    monkeypatch.setattr(availability, "is_available", lambda db, user, now=None: False)
    coordinator, technician, customer = make_user("coordinator"), make_user("technician"), make_user("requester")
    response = _create(client, coordinator, customer["email"], assigned_to=technician["id"])
    assert response.status_code == 409
    assert _ticket_count(customer["id"]) == 0
    response = _create(client, coordinator, customer["email"], assigned_to=technician["id"], confirm_unavailable=True)
    assert response.status_code == 200


def test_coordinator_cannot_assign_to_requester(client, make_user):
    coordinator, customer = make_user("coordinator"), make_user("requester")
    response = _create(client, coordinator, customer["email"], assigned_to=customer["id"])
    assert response.status_code == 400
    assert _ticket_count(customer["id"]) == 0


@pytest.mark.parametrize("role", STAFF)
def test_detail_shows_registration_to_staff_only(client, make_user, make_ticket, role):
    staff, coordinator, customer = make_user(role, "Quien Registra"), make_user("coordinator"), make_user("requester")
    response = _create(client, staff, customer["email"], channel="phone", identity_check="email")
    ticket_id = response.json()["ticket_id"]
    detail = client.get(f"/api/tickets/{ticket_id}", headers=coordinator["headers"]).json()
    assert detail["registration"] == {
        "registered_by_name": "Quien Registra", "channel": "phone", "identity_check": "email",
    }
    own = client.get(f"/api/tickets/{ticket_id}", headers=customer["headers"]).json()
    assert "registration" not in own
    assert [h["field"] for h in own["history"]] == ["created_on_behalf"]
    # Un ticket de antes (o que el cliente creó solo) queda "sin registrar"
    older = make_ticket(customer)
    detail = client.get(f"/api/tickets/{older['id']}", headers=coordinator["headers"]).json()
    assert detail["registration"] is None
