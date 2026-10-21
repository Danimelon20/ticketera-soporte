def test_team_lists_staff_without_requesters(make_user, client):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    admin = make_user("admin")
    requester = make_user("requester")

    response = client.get("/api/team", headers=coordinator["headers"])
    assert response.status_code == 200
    rows = response.json()
    ids = {row["id"] for row in rows}
    assert {coordinator["id"], technician["id"], admin["id"]} <= ids
    assert requester["id"] not in ids
    assert all(row["role"] in ("technician", "coordinator", "admin") for row in rows)
    # Técnicos primero, después coordinadores y administradores
    order = [row["role"] for row in rows]
    assert order == sorted(order, key=["technician", "coordinator", "admin"].index)


def test_team_masks_email_and_hides_personal_data(make_user, client):
    admin = make_user("admin")
    technician = make_user("technician")
    response = client.get("/api/team", headers=admin["headers"])
    assert response.status_code == 200
    row = next(r for r in response.json() if r["id"] == technician["id"])
    local, domain = technician["email"].split("@")
    assert row["email"] == f"{local[:2]}***@{domain}"
    assert "phone" not in row and "id_number" not in row
    assert technician["email"] not in response.text


def test_team_only_for_coordinator_and_admin(make_user, client):
    for role in ("technician", "requester"):
        user = make_user(role)
        assert client.get("/api/team", headers=user["headers"]).status_code == 403
