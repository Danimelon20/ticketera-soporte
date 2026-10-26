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


def test_only_the_coordinator_gets_the_load(make_user, make_ticket, client):
    """Carga = la de la asignación automática (tickets abiertos y borradores pendientes); solo el coordinador."""
    from app import auto_assign, config, models
    from app.database import SessionLocal

    coordinator = make_user("coordinator")
    technician = make_user("technician")
    requester = make_user("requester")
    db = SessionLocal()
    try:
        ticket = models.Ticket(title="Carga", description="Abierto", category_id=1, priority_id=1, status="new",
                               created_by=requester["id"], assigned_to=technician["id"], source="web")
        draft = models.EmailDraft(message_id=f"<carga-{technician['id']}@test>", requester_id=requester["id"],
                                  status="pending_review", subject="Borrador", body="Texto", ai_level=1)
        db.add_all([ticket, draft])
        db.flush()
        db.add(models.DraftAssignment(draft_id=draft.id, status="assigned", user_id=technician["id"],
                                      notify=False, created_at=draft.created_at))
        db.commit()
        expected = auto_assign.loads(db, [technician["id"]])[technician["id"]]
    finally:
        db.close()
    # El borrador pendiente también cuenta
    assert expected == (3 if config.AUTO_ASSIGN_LOAD_MODE != "count" else 2)

    rows = client.get("/api/team", headers=coordinator["headers"]).json()
    assert next(r for r in rows if r["id"] == technician["id"])["load"] == expected
    admin_rows = client.get("/api/team", headers=make_user("admin")["headers"]).json()
    assert all("load" not in r for r in admin_rows)
    assert client.get("/api/team", headers=technician["headers"]).status_code == 403
