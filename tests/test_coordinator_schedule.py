from app import auto_assign, config, models, nav
from app.database import SessionLocal

ALL_DAY = [{"weekday": d, "start": "00:00", "end": "23:59"} for d in range(7)]


def _put(client, actor, target_id, days=ALL_DAY):
    return client.put(f"/api/accounts/{target_id}/schedule", json={"days": days}, headers=actor["headers"])


def _rows(user_id):
    db = SessionLocal()
    try:
        return db.query(models.TechnicianSchedule).filter(models.TechnicianSchedule.user_id == user_id).count()
    finally:
        db.close()


def test_admin_can_set_coordinator_schedule(make_user, client, monkeypatch):
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    assert _put(client, admin, coordinator["id"]).status_code == 204
    assert _rows(coordinator["id"]) == 7

    db = SessionLocal()
    try:
        change = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.target_user_id == coordinator["id"],
                    models.AccountChange.action == "schedule_changed")
            .first()
        )
        assert change is not None and change.new_value == "Lun–Dom 00:00–23:59"
    finally:
        db.close()

    row = next(r for r in client.get("/api/team", headers=admin["headers"]).json() if r["id"] == coordinator["id"])
    assert row["schedule_today"] == "00:00–23:59"
    assert row["on_shift"] is True


def test_coordinator_cannot_set_other_coordinator_or_own_schedule(make_user, client):
    coordinator = make_user("coordinator")
    other = make_user("coordinator")
    assert _put(client, coordinator, other["id"]).status_code == 403
    assert _put(client, coordinator, coordinator["id"]).status_code == 403
    assert _rows(other["id"]) == 0 and _rows(coordinator["id"]) == 0


def test_admin_cannot_set_admin_schedule(make_user, client):
    admin = make_user("admin")
    other = make_user("admin")
    assert _put(client, admin, other["id"]).status_code == 403
    assert _put(client, admin, admin["id"]).status_code == 403


def test_coordinator_still_sets_technician_schedule(make_user, client):
    coordinator = make_user("coordinator")
    technician = make_user("technician")
    assert _put(client, coordinator, technician["id"]).status_code == 204


def test_auto_assign_never_picks_coordinator_on_shift(make_user, client, monkeypatch):
    monkeypatch.setattr(config, "OFFICE_HOLIDAYS", "")
    admin = make_user("admin")
    coordinator = make_user("coordinator")
    assert _put(client, admin, coordinator["id"]).status_code == 204

    db = SessionLocal()
    try:
        now = nav._now()
        candidates = auto_assign.candidates(db, now)
        assert coordinator["id"] not in [u.id for u in candidates]
        assert all(u.role == "technician" for u in candidates)
        chosen = auto_assign.choose(db, now)
        assert chosen is None or chosen.role == "technician"
        # Técnicos en turno de la barra de estado: tampoco cuenta al coordinador
        assert nav.system_status(db, now)["technicians_on_shift"] == len(candidates)
    finally:
        db.close()
