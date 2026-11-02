"""Tickets a nombre de un cliente: búsqueda por correo exacto y verificación de identidad por teléfono.

La misma batería corre para el técnico y el coordinador (STAFF).
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app import models, registrations
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
