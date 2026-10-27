"""scripts.demo_data: cargar y limpiar no dejan nada DEMO y no tocan los datos reales."""
import os

from sqlalchemy import func

from app import attachments, audit, config, graph, mailer, models
from app.alerts import due_state
from app.database import SessionLocal, engine
from scripts import demo_data

DEMO = "%@demo.invalid"


def _fk_violations():
    with engine.connect() as conn:
        return set(conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall())


def _real_snapshot(db):
    users = {
        (u.id, u.email, u.role, u.name, u.is_active)
        for u in db.query(models.User).filter(~func.lower(models.User.email).like(DEMO))
    }
    tickets = {(t.id, t.title, t.status, t.created_by) for t in db.query(models.Ticket)
               if not t.title.startswith("[DEMO]")}
    return users, tickets


def _demo_users(db):
    return db.query(models.User).filter(func.lower(models.User.email).like(DEMO)).all()


def _demo_tickets(db):
    return db.query(models.Ticket).filter(models.Ticket.title.like("[DEMO]%")).all()


def test_load_creates_the_scenarios(client, sent):
    assert demo_data.run("cargar") == 0
    db = SessionLocal()
    try:
        users = _demo_users(db)
        assert all(u.name.startswith("[DEMO] ") for u in users)
        roles = sorted(u.role for u in users)
        assert roles == ["coordinator"] + ["requester"] * 4 + ["technician"] * 3
        demo_ids = {u.id for u in users}

        tickets = _demo_tickets(db)
        assert len(tickets) == 8
        ticket_ids = {t.id for t in tickets}
        # Agrupación: una fusión del mismo cliente y dos tickets ligados de clientes distintos
        merges = db.query(models.TicketMerge).filter(models.TicketMerge.target_ticket_id.in_(ticket_ids)).all()
        assert len(merges) == 1
        links = db.query(models.TicketLink).filter(models.TicketLink.principal_ticket_id.in_(ticket_ids)).all()
        assert len(links) == 2
        owners = {db.get(models.Ticket, link.related_ticket_id).created_by for link in links}
        owners.add(db.get(models.Ticket, links[0].principal_ticket_id).created_by)
        assert len(owners) == 3

        # Vencimientos
        now = demo_data._now()
        states = [due_state(t, now) for t in tickets if t.ai_analysis.due_at]
        assert sum(1 for s, pct in states if s == "due_soon" and pct >= 80) == 1
        assert sum(1 for s, _ in states if s == "overdue") == 2
        causes = db.query(models.OverdueCause).filter(models.OverdueCause.ticket_id.in_(ticket_ids)).count()
        assert causes == 1

        # Borradores: uno con revisor y uno sin revisor
        drafts = db.query(models.EmailDraft).filter(models.EmailDraft.requester_id.in_(demo_ids)).all()
        assignments = sorted(
            db.query(models.DraftAssignment).filter(models.DraftAssignment.draft_id == d.id).one().status
            for d in drafts
        )
        assert assignments == ["assigned", "waiting"]

        # Suplantación: reporte automático del Agente virtual sobre el que intentó usar la cédula
        agent = db.query(models.User).filter(models.User.role == "agent").one()
        reports = db.query(models.AccountReport).filter(
            models.AccountReport.reporter_id == agent.id, models.AccountReport.target_user_id.in_(demo_ids)
        ).all()
        assert [r.reason for r in reports] == ["foreign_id"]

        # Vacaciones de uno de los técnicos
        assert db.query(models.AvailabilityPeriod).filter(
            models.AvailabilityPeriod.user_id.in_(demo_ids), models.AvailabilityPeriod.kind == "vacation"
        ).count() == 1
    finally:
        db.close()
    # Ni un correo, y las banderas del proceso vuelven a como estaban
    assert sent == []
    assert config.EMAIL_ENABLED is False
    demo_data.run("limpiar")


def test_quiet_turns_off_and_restores_flags(monkeypatch):
    monkeypatch.setattr(config, "AUTO_ASSIGN_ENABLED", True)
    monkeypatch.setattr(config, "GROUPING_ENABLED", True)
    with demo_data.quiet():
        assert all(getattr(config, name) is False for name in demo_data.QUIET_FLAGS)
    assert config.AUTO_ASSIGN_ENABLED is True and config.GROUPING_ENABLED is True


def test_load_twice_and_clean_leave_nothing_demo_and_keep_real_data(client, make_user, make_ticket, sent):
    real_requester = make_user("requester")
    real_admin = make_user("admin")
    real_ticket = make_ticket(real_requester)
    violations_before = _fk_violations()

    demo_data.run("cargar")
    # Cargar otra vez no duplica
    demo_data.run("cargar")
    db = SessionLocal()
    try:
        before_users, before_tickets = _real_snapshot(db)
        assert len(_demo_users(db)) == 8 and len(_demo_tickets(db)) == 8

        # Uso de la página mezclado con datos reales: ticket real asignado a un técnico DEMO,
        # cambio de un admin real sobre una cuenta DEMO y un adjunto en un ticket DEMO
        technician = next(u for u in _demo_users(db) if u.role == "technician")
        client_demo = next(u for u in _demo_users(db) if u.role == "requester")
        db.get(models.Ticket, real_ticket["id"]).assigned_to = technician.id
        audit.record(db, real_admin["id"], "phone_changed", target=client_demo, old="****0001", new="****0009")
        demo_ticket = _demo_tickets(db)[0]
        comment = models.Comment(ticket_id=demo_ticket.id, user_id=client_demo.id, content="Adjunto")
        db.add(comment)
        db.flush()
        stored_name, sha = attachments.store_file(b"%PDF-1.4 demo")
        db.add(models.Attachment(
            ticket_id=demo_ticket.id, comment_id=comment.id, original_name="demo.pdf",
            content_type="application/pdf", size=13, sha256=sha, stored_name=stored_name,
            uploaded_by=client_demo.id,
        ))
        db.commit()
        demo_user_ids = {u.id for u in _demo_users(db)}
    finally:
        db.close()
    stored_path = os.path.join(config.ATTACHMENTS_DIR, stored_name)
    assert os.path.exists(stored_path)

    assert demo_data.run("limpiar") == 0

    db = SessionLocal()
    try:
        assert _demo_users(db) == []
        assert _demo_tickets(db) == []
        # Nada en el registro de cambios que apunte a lo DEMO
        changes = db.query(models.AccountChange).all()
        assert not any(c.target_user_id in demo_user_ids for c in changes)
        assert not any((c.target_label or "").endswith("@demo.invalid") for c in changes)
        assert db.query(models.EmailDraft).filter(models.EmailDraft.requester_id.in_(demo_user_ids)).count() == 0
        assert db.query(models.AccountReport).filter(
            models.AccountReport.target_user_id.in_(demo_user_ids)).count() == 0

        # Lo real sigue igual; el ticket real solo pierde al responsable DEMO
        after_users, after_tickets = _real_snapshot(db)
        assert after_users == before_users
        assert after_tickets == before_tickets
        assert db.get(models.Ticket, real_ticket["id"]).assigned_to is None
    finally:
        db.close()
    assert not os.path.exists(stored_path)
    assert _fk_violations() <= violations_before
    assert sent == []

    # Limpiar sin nada DEMO no hace nada
    assert demo_data.run("limpiar") == 0


def test_mailer_never_sends_to_invalid_domain(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)

    def _fail(*args, **kwargs):
        raise AssertionError("No debe llamar a Microsoft Graph")

    monkeypatch.setattr(graph, "graph_request", _fail)
    assert mailer.send_email("tecnica.manana@demo.invalid", "Asunto", "Cuerpo") is False
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
