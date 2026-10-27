import uuid

import pytest

from app import hacienda, identity, models
from app.database import SessionLocal
from conftest import code_from

PASSWORD = "clave-segura-123"


def _rows(target_id=None, action=None, label=None):
    db = SessionLocal()
    try:
        query = db.query(models.AccountChange)
        if target_id is not None:
            query = query.filter(models.AccountChange.target_user_id == target_id)
        if label is not None:
            query = query.filter(models.AccountChange.target_label == label)
        if action is not None:
            query = query.filter(models.AccountChange.action == action)
        return [
            {"actor_id": r.actor_id, "action": r.action, "old": r.old_value, "new": r.new_value, "label": r.target_label}
            for r in query.order_by(models.AccountChange.id).all()
        ]
    finally:
        db.close()


def _text(rows):
    return " ".join(str(v) for row in rows for v in row.values())


def test_password_change_is_recorded_without_values(client, make_user):
    user = make_user("technician")
    new_password = "nueva-clave-larga-1"
    client.put(
        "/api/account/password", json={"current_password": PASSWORD, "new_password": new_password},
        headers=user["headers"],
    )
    rows = _rows(user["id"], "password_changed")
    assert rows == [{"actor_id": user["id"], "action": "password_changed", "old": None, "new": None, "label": None}]
    assert PASSWORD not in _text(_rows(user["id"])) and new_password not in _text(_rows(user["id"]))


def test_email_change_is_recorded_masked_without_code(client, make_user, sent):
    user = make_user("requester")
    new_email = f"nuevo-{uuid.uuid4().hex}@example.com"
    client.post("/api/account/email", json={"current_password": PASSWORD, "new_email": new_email}, headers=user["headers"])
    code = code_from(sent[-1][2])
    client.post("/api/account/email/verify", json={"code": code}, headers=user["headers"])
    rows = _rows(user["id"], "email_changed")
    assert len(rows) == 1
    assert rows[0]["old"] == user["email"][:2] + "***@test.com"
    assert rows[0]["new"] == new_email[:2] + "***@example.com"
    text = _text(_rows(user["id"]))
    assert user["email"] not in text and new_email not in text and code not in text


def test_phone_change_keeps_last_4_digits(client, make_user):
    user = make_user("coordinator")
    client.put("/api/account/phone", json={"phone": "8777-6655"}, headers=user["headers"])
    requester = make_user("requester", "Laura Mora Vargas")
    client.put(
        "/api/profile", json={"name": "Laura Mora Vargas", "phone": "7111-2233", "id_number": requester["id_number"]},
        headers=requester["headers"],
    )
    assert _rows(user["id"], "phone_changed") == [
        {"actor_id": user["id"], "action": "phone_changed", "old": None, "new": "****6655", "label": None}
    ]
    rows = _rows(requester["id"], "phone_changed")
    assert rows[0]["old"] == "****1234" and rows[0]["new"] == "****2233"
    assert "87776655" not in _text(_rows(user["id"])) and "71112233" not in _text(rows)


def test_name_and_id_number_changes(client, make_user):
    user = make_user("requester", "", profile=False)
    client.put(
        "/api/profile", json={"name": "Daniel Sáenz Maroto", "phone": "8888-1234", "id_number": "1-0234-0567"},
        headers=user["headers"],
    )
    client.put(
        "/api/profile", json={"name": "Daniel Alejandro Sáenz Maroto", "phone": "8888-1234", "id_number": "1-0987-0654"},
        headers=user["headers"],
    )
    names = _rows(user["id"], "name_changed")
    assert [(r["old"], r["new"]) for r in names] == [
        (None, "Daniel Sáenz Maroto"), ("Daniel Sáenz Maroto", "Daniel Alejandro Sáenz Maroto")
    ]
    ids = _rows(user["id"], "id_number_changed")
    assert [(r["old"], r["new"]) for r in ids] == [(None, "****0567"), ("****0567", "****0654")]
    text = _text(_rows(user["id"]))
    assert "102340567" not in text and "109870654" not in text


def test_contact_from_email_is_recorded_masked(client, make_user, monkeypatch):
    # Nombre reconocido por Hacienda: sin el reporte automático de "El nombre no coincide" (test_suspicion.py)
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: {"tipo": "01", "name_tokens": hacienda.name_tokens("Ana Mora Vargas")})
    user = make_user("requester", "Ana Mora Vargas", profile=False)
    db = SessionLocal()
    try:
        account = db.get(models.User, user["id"])
        identity.apply_contact_from_email(db, account, "86665544", "310123456")
        db.commit()
    finally:
        db.close()
    rows = _rows(user["id"])
    assert {r["action"] for r in rows} == {"phone_changed", "id_number_changed"}
    assert {r["new"] for r in rows} == {"****5544", "****3456"}
    assert "86665544" not in _text(rows) and "310123456" not in _text(rows)


def test_role_and_activation_changes(client, make_user):
    admin = make_user("admin")
    user = make_user("technician")
    client.patch(f"/api/admin/users/{user['id']}", json={"role": "coordinator"}, headers=admin["headers"])
    client.patch(f"/api/admin/users/{user['id']}", json={"is_active": False}, headers=admin["headers"])
    client.patch(f"/api/admin/users/{user['id']}", json={"is_active": True}, headers=admin["headers"])
    rows = _rows(user["id"])
    assert [(r["action"], r["old"], r["new"]) for r in rows] == [
        ("role_changed", "Técnico", "Coordinador"),
        ("account_deactivated", None, None),
        ("account_activated", None, None),
    ]
    assert all(r["actor_id"] == admin["id"] for r in rows)


def test_invitation_sent_and_used(client, make_user, sent):
    admin = make_user("admin")
    email = f"personal-{uuid.uuid4().hex}@test.com"
    client.post("/api/staff-invitations", json={"email": email, "name": "Nueva Persona", "role": "technician"},
                headers=admin["headers"])
    code = code_from(sent[-1][2])
    label = email[:2] + "***@test.com"
    sent_rows = _rows(label=label, action="invitation_sent")
    assert sent_rows == [{"actor_id": admin["id"], "action": "invitation_sent", "old": None, "new": "Técnico", "label": label}]

    activated = client.post("/api/auth/activate", json={"email": email, "code": code})
    password = activated.json()["password"]
    db = SessionLocal()
    try:
        new_id = db.query(models.User.id).filter(models.User.email == email).scalar()
        all_rows = db.query(models.AccountChange).all()
        text = " ".join(f"{r.target_label} {r.old_value} {r.new_value}" for r in all_rows)
    finally:
        db.close()
    assert _rows(new_id, "invitation_used") == [
        {"actor_id": new_id, "action": "invitation_used", "old": None, "new": "Técnico", "label": None}
    ]
    assert email not in text and code not in text and password not in text


@pytest.mark.parametrize("exists", [True, False])
def test_lockout_is_recorded(client, make_user, exists):
    email = make_user("requester")["email"] if exists else f"nadie-{uuid.uuid4().hex}@test.com"
    for _ in range(5):
        client.post("/api/auth/login", json={"email": email, "password": "otra-clave-999", "portal": "requester"})
    db = SessionLocal()
    try:
        rows = db.query(models.AccountChange).filter(models.AccountChange.action == "login_locked").all()
        mine = [r for r in rows if (r.target is not None and r.target.email == email) or r.target_label == email[:2] + "***@test.com"]
        assert len(mine) >= 1
        row = mine[-1]
        assert row.actor_id is None
        assert (row.target_user_id is not None) == exists
        assert email not in f"{row.target_label} {row.old_value} {row.new_value}"
        assert "otra-clave-999" not in f"{row.target_label} {row.old_value} {row.new_value}"
    finally:
        db.close()


# ----- Panel del administrador -----

@pytest.mark.parametrize("role", ["coordinator", "technician", "requester"])
def test_only_admin_sees_account_changes(client, make_user, role):
    user = make_user(role)
    assert client.get("/api/admin/account-changes", headers=user["headers"]).status_code == 403


def test_admin_list_filters_and_pages(client, make_user):
    admin = make_user("admin")
    user = make_user("coordinator", f"Persona {uuid.uuid4().hex[:8]}")
    for i in range(23):
        client.put("/api/account/phone", json={"phone": f"8{i:07d}"}, headers=user["headers"])
    client.patch(f"/api/admin/users/{user['id']}", json={"role": "technician"}, headers=admin["headers"])

    page1 = client.get("/api/admin/account-changes", params={"account": user["name"]}, headers=admin["headers"]).json()
    assert page1["total"] == 24 and page1["pages"] == 2 and page1["page"] == 1
    assert len(page1["items"]) == 20
    # Lo más reciente primero
    assert page1["items"][0]["action"] == "role_changed"
    assert page1["items"][0]["actor"] == admin["name"]
    assert page1["items"][1]["new_value"] == "****0022"
    assert user["name"] in page1["items"][0]["account"]
    assert user["email"] not in str(page1)

    page2 = client.get(
        "/api/admin/account-changes", params={"account": user["name"], "page": 2}, headers=admin["headers"]
    ).json()
    assert len(page2["items"]) == 4
    assert page2["items"][-1]["new_value"] == "****0000"

    only_roles = client.get(
        "/api/admin/account-changes", params={"account": user["name"], "action": "role_changed"},
        headers=admin["headers"],
    ).json()
    assert only_roles["total"] == 1
