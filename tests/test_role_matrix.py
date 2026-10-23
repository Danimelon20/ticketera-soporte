"""Matriz de permisos de la fase 1: cada acción para técnico, coordinador, administrador y solicitante.

- Técnico: atiende. Solo sus tickets (y en solo lectura los que tuvo antes) y sus borradores.
- Coordinador: dirige la operación. Todos los tickets y borradores, reasignar, vencidos, incidentes.
- Administrador: cuida el sistema. Cuentas, invitaciones, reportes de cuentas; nunca el flujo de tickets.
- Solicitante: sus propios tickets.

"technician" es el técnico asignado al ticket y revisor del borrador; "other_technician", uno ajeno.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import config, models
from app.database import SessionLocal

from conftest import assign_draft_to

ROLES = ("technician", "other_technician", "coordinator", "admin", "requester")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _codes(technician, other_technician, coordinator, admin, requester):
    return dict(zip(ROLES, (technician, other_technician, coordinator, admin, requester)))


@pytest.fixture
def world(client, make_user, make_ticket, monkeypatch, sent):
    # Correo simulado: "Enviar resumen ahora" llega hasta el envío
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    users = {role: make_user("technician" if role.endswith("technician") else role) for role in ROLES}
    users["target_technician"] = make_user("technician")
    users["target_coordinator"] = make_user("coordinator")
    ticket = make_ticket(users["requester"], title="Matriz de permisos")
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": users["technician"]["id"]},
                            headers=users["coordinator"]["headers"])
    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        # Vencido hace dos horas: el reporte y la causa de vencimiento aplican
        db.get(models.Ticket, ticket["id"]).created_at = _now() - timedelta(days=3)
        db.add(models.TicketAIAnalysis(ticket_id=ticket["id"], status="done", ai_level=2,
                                       due_at=_now() - timedelta(hours=2)))
        draft = models.EmailDraft(
            message_id=f"<matriz-{ticket['id']}@test>", requester_id=users["requester"]["id"], status="pending_review",
            subject="Sin red", body="No tengo red", title="Sin red", description="No hay red.", category_id=1,
            priority_id=1,
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id
    finally:
        db.close()
    assign_draft_to(draft_id, users["technician"]["id"])
    report = client.post("/api/account-reports", json={"target_user_id": users["requester"]["id"], "reason": "fake_data"},
                         headers=users["coordinator"]["headers"])
    assert report.status_code == 201, report.text
    return {"users": users, "ticket_id": ticket["id"], "draft_id": draft_id, "report_id": report.json()["id"]}


def _future(days):
    return (date.today() + timedelta(days=days)).isoformat()


# (nombre, método, ruta, cuerpo, códigos por rol). La ruta y el cuerpo reciben el mundo de la prueba.
MATRIX = [
    # ----- Tickets -----
    ("ver detalle", "get", lambda w: f"/api/tickets/{w['ticket_id']}", None, _codes(200, 403, 200, 403, 200)),
    ("lista de tickets", "get", lambda w: "/api/tickets", None, _codes(200, 200, 200, 403, 200)),
    ("cola", "get", lambda w: "/api/queue", None, _codes(200, 200, 200, 403, 403)),
    ("cola sin asignar", "get", lambda w: "/api/queue?view=unassigned", None, _codes(403, 403, 200, 403, 403)),
    ("cola cancelados", "get", lambda w: "/api/queue?view=cancelled", None, _codes(403, 403, 200, 403, 403)),
    ("comentar", "post", lambda w: f"/api/tickets/{w['ticket_id']}/comments", lambda w: {"content": "Hola"},
     _codes(200, 403, 200, 403, 200)),
    ("nota interna", "post", lambda w: f"/api/tickets/{w['ticket_id']}/comments",
     lambda w: {"content": "Nota", "internal": True}, _codes(200, 403, 200, 403, 403)),
    ("cambiar prioridad", "patch", lambda w: f"/api/tickets/{w['ticket_id']}", lambda w: {"priority_id": 2},
     _codes(200, 403, 200, 403, 403)),
    ("reasignar", "patch", lambda w: f"/api/tickets/{w['ticket_id']}",
     lambda w: {"assigned_to": w["users"]["target_technician"]["id"], "confirm_unavailable": True},
     _codes(403, 403, 200, 403, 403)),
    ("corregir nivel IA", "patch", lambda w: f"/api/tickets/{w['ticket_id']}/ai-level", lambda w: {"level": 3},
     _codes(200, 403, 200, 403, 403)),
    ("sugerencia", "get", lambda w: f"/api/tickets/{w['ticket_id']}/suggestion", None, _codes(403, 403, 200, 403, 403)),
    ("seguir", "post", lambda w: f"/api/tickets/{w['ticket_id']}/follow", None, _codes(204, 403, 204, 403, 204)),
    ("cancelar", "post", lambda w: "/api/tickets/cancel",
     lambda w: {"ticket_ids": [w["ticket_id"]], "reason": "duplicate", "notify_requester": False},
     _codes(403, 403, 200, 403, 403)),
    ("crear a nombre de un cliente", "post", lambda w: "/api/tickets",
     lambda w: {"title": "Por teléfono", "description": "Llamó", "category_id": 1, "priority_id": 1,
                "requester_id": w["users"]["requester"]["id"]},
     _codes(403, 403, 200, 403, 403)),
    ("buscar solicitantes", "get", lambda w: "/api/requesters", None, _codes(403, 403, 200, 403, 403)),
    ("ver conversación", "post", lambda w: f"/api/tickets/{w['ticket_id']}/view-conversation",
     lambda w: {"reason": "Revisión de una queja"}, _codes(403, 403, 403, 200, 403)),
    # ----- Vencidos e incidentes -----
    ("reporte de vencimiento", "get", lambda w: f"/api/tickets/{w['ticket_id']}/overdue-report", None,
     _codes(200, 403, 200, 403, 403)),
    ("causa del vencimiento", "post", lambda w: f"/api/tickets/{w['ticket_id']}/overdue-cause",
     lambda w: {"cause": "high_load"}, _codes(403, 403, 200, 403, 403)),
    ("incidentes", "get", lambda w: "/api/incidents", None, _codes(403, 403, 200, 403, 403)),
    ("campana", "get", lambda w: "/api/notifications/overdue", None, _codes(200, 200, 200, 200, 403)),
    ("contadores", "get", lambda w: "/api/nav/counts", None, _codes(200, 200, 200, 200, 403)),
    # ----- Borradores -----
    ("lista de borradores", "get", lambda w: "/api/drafts", None, _codes(200, 200, 200, 403, 403)),
    ("confirmar borrador", "post", lambda w: f"/api/drafts/{w['draft_id']}/confirm",
     lambda w: {"title": "Sin red", "description": "No hay red.", "category_id": 1, "priority_id": 1},
     _codes(200, 403, 200, 403, 403)),
    ("descartar borrador", "post", lambda w: f"/api/drafts/{w['draft_id']}/discard", None,
     _codes(200, 403, 200, 403, 403)),
    ("reasignar borrador", "post", lambda w: f"/api/drafts/{w['draft_id']}/assign",
     lambda w: {"user_id": w["users"]["target_technician"]["id"], "confirm_unavailable": True},
     _codes(403, 403, 200, 403, 403)),
    # ----- Cuentas (decisión 4: solo el administrador las gestiona) -----
    ("lista de cuentas", "get", lambda w: "/api/accounts", None, _codes(403, 403, 200, 200, 403)),
    ("resumen de una cuenta", "get", lambda w: f"/api/accounts/{w['users']['target_technician']['id']}", None,
     _codes(403, 403, 200, 200, 403)),
    ("desactivar", "post", lambda w: f"/api/accounts/{w['users']['target_technician']['id']}/deactivate", None,
     _codes(403, 403, 403, 204, 403)),
    ("restablecer acceso", "post", lambda w: f"/api/accounts/{w['users']['target_technician']['id']}/reset-access",
     None, _codes(403, 403, 403, 202, 403)),
    ("desbloquear", "post", lambda w: f"/api/accounts/{w['users']['requester']['id']}/unlock", None,
     _codes(403, 403, 403, 204, 403)),
    ("cerrar sesiones", "post", lambda w: f"/api/accounts/{w['users']['target_technician']['id']}/close-sessions",
     None, _codes(403, 403, 403, 204, 403)),
    ("invitar", "post", lambda w: "/api/staff-invitations",
     lambda w: {"email": f"nueva-{w['ticket_id']}@test.com", "name": "Nueva", "role": "technician"},
     _codes(403, 403, 403, 201, 403)),
    ("lista de invitaciones", "get", lambda w: "/api/staff-invitations", None, _codes(403, 403, 403, 200, 403)),
    # ----- Turnos y disponibilidad (decisión 5: los de coordinadores, el administrador) -----
    ("turno de un técnico", "put", lambda w: f"/api/accounts/{w['users']['target_technician']['id']}/schedule",
     lambda w: {"days": [{"weekday": 0, "start": "08:00", "end": "17:00"}]}, _codes(403, 403, 204, 204, 403)),
    ("vacaciones de un técnico", "post",
     lambda w: f"/api/accounts/{w['users']['target_technician']['id']}/availability-periods",
     lambda w: {"kind": "vacation", "start_date": _future(30), "end_date": _future(31)},
     _codes(403, 403, 201, 201, 403)),
    ("turno de un coordinador", "put", lambda w: f"/api/accounts/{w['users']['target_coordinator']['id']}/schedule",
     lambda w: {"days": [{"weekday": 0, "start": "08:00", "end": "17:00"}]}, _codes(403, 403, 403, 204, 403)),
    ("vacaciones de un coordinador", "post",
     lambda w: f"/api/accounts/{w['users']['target_coordinator']['id']}/availability-periods",
     lambda w: {"kind": "vacation", "start_date": _future(30), "end_date": _future(31)},
     _codes(403, 403, 403, 201, 403)),
    # ----- Reportes de cuentas (decisión 6: crear el personal, revisar el administrador) -----
    ("crear reporte de cuenta", "post", lambda w: "/api/account-reports",
     lambda w: {"target_user_id": w["users"]["requester"]["id"], "reason": "repeated_tickets"},
     # El coordinador ya tiene uno abierto sobre esta cuenta (409): sí puede crearlos
     _codes(201, 201, 409, 201, 403)),
    ("lista de reportes", "get", lambda w: "/api/account-reports", None, _codes(403, 403, 403, 200, 403)),
    ("descartar reporte", "post", lambda w: f"/api/account-reports/{w['report_id']}/dismiss",
     lambda w: {"note": "Revisado"}, _codes(403, 403, 403, 200, 403)),
    # ----- Sistema -----
    ("enviar resumen ahora", "post", lambda w: "/api/admin/daily-summary/send-now", None,
     _codes(403, 403, 200, 403, 403)),
    ("estado del sistema", "get", lambda w: "/api/admin/health", None, _codes(403, 403, 403, 200, 403)),
    ("registro de cambios", "get", lambda w: "/api/admin/account-changes", None, _codes(403, 403, 403, 200, 403)),
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("name,method,path,body,codes", MATRIX, ids=[row[0] for row in MATRIX])
def test_permission_matrix(client, world, role, name, method, path, body, codes):
    actor = world["users"][role]
    kwargs = {"headers": actor["headers"]}
    if body is not None:
        kwargs["json"] = body(world)
    response = getattr(client, method)(path(world), **kwargs)
    assert response.status_code == codes[role], (name, role, response.text)


def test_technician_lists_only_own_tickets_and_drafts(client, world):
    users = world["users"]
    for role, sees in (("technician", True), ("other_technician", False)):
        headers = users[role]["headers"]
        tickets = {t["id"] for t in client.get("/api/tickets", headers=headers).json()}
        # El ticket está vencido: sale en la vista Vencidos
        queue = {t["id"] for t in client.get("/api/queue?view=overdue", headers=headers).json()["items"]}
        drafts = {d["id"] for d in client.get("/api/drafts", headers=headers).json()}
        assert (world["ticket_id"] in tickets) is sees
        assert (world["ticket_id"] in queue) is sees
        assert (world["draft_id"] in drafts) is sees
    coordinator = {d["id"] for d in client.get("/api/drafts", headers=users["coordinator"]["headers"]).json()}
    assert world["draft_id"] in coordinator


def test_staff_creates_account_reports_and_admin_sees_them(client, make_user, make_ticket):
    requester = make_user("requester")
    make_ticket(requester)
    created = []
    for role in ("technician", "coordinator"):
        response = client.post("/api/account-reports", json={"target_user_id": requester["id"], "reason": "fake_data"},
                               headers=make_user(role)["headers"])
        assert response.status_code == 201, (role, response.text)
        created.append(response.json()["id"])
    listed = client.get(f"/api/account-reports?target_user_id={requester['id']}", headers=make_user("admin")["headers"])
    assert listed.status_code == 200
    assert set(created) <= {r["id"] for r in listed.json()}


def test_admin_account_actions_never_include_tickets(client, world):
    # "Ver sus tickets" no le sirve al administrador: la cola le da 403
    summary = client.get(f"/api/accounts/{world['users']['requester']['id']}",
                         headers=world["users"]["admin"]["headers"]).json()
    assert "view-tickets" not in summary["actions"] and "report" not in summary["actions"]
    coordinator = client.get(f"/api/accounts/{world['users']['requester']['id']}",
                             headers=world["users"]["coordinator"]["headers"]).json()
    assert {"view-tickets", "report"} <= set(coordinator["actions"])
    assert not {"deactivate", "close-sessions", "unlock", "reset-access"} & set(coordinator["actions"])
