"""El comentario interno nunca le llega al solicitante, por ningún camino; el personal sí lo ve."""
import pytest

from app import accounts, models
from app.database import SessionLocal

TEXT = accounts.RELEASED_COMMENT


@pytest.fixture
def released(client, make_user, make_ticket, sent):
    """Ticket con un comentario normal y el interno de "técnico desactivado"."""
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    leaving = make_user("technician")
    ticket = make_ticket(requester, title="Correo no sincroniza")
    # El contacto sigue su ticket (como al llegar por correo)
    with SessionLocal() as db:
        db.add(models.TicketFollower(ticket_id=ticket["id"], user_id=requester["id"]))
        db.commit()
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": leaving["id"]}, headers=coordinator["headers"])
    client.post(f"/api/tickets/{ticket['id']}/comments", json={"content": "Comentario público"},
                headers=coordinator["headers"])
    sent.clear()
    # Desactivar cuentas es del administrador
    admin = make_user("admin")
    assert client.post(f"/api/accounts/{leaving['id']}/deactivate", headers=admin["headers"]).status_code == 204
    return {"ticket": ticket, "requester": requester, "coordinator": coordinator}


@pytest.mark.parametrize("role", ["technician", "coordinator", "admin"])
def test_staff_sees_it_marked_internal(client, make_user, released, role):
    ticket_id = released["ticket"]["id"]
    viewer = released["coordinator"] if role == "coordinator" else make_user(role)
    if role == "technician":
        # El técnico ve los tickets que tiene asignados
        client.patch(f"/api/tickets/{ticket_id}", json={"assigned_to": viewer["id"]},
                     headers=released["coordinator"]["headers"])
    if role == "admin":
        # El administrador, solo con "Ver conversación"
        response = client.post(f"/api/tickets/{ticket_id}/view-conversation", json={"reason": "Auditoría interna"},
                               headers=viewer["headers"])
    else:
        response = client.get(f"/api/tickets/{ticket_id}", headers=viewer["headers"])
    comments = response.json()["comments"]
    assert [(c["content"], c["internal"]) for c in comments] == [("Comentario público", False), (TEXT, True)]


def test_not_sent_to_followers_or_requester_by_email(client, released, sent):
    requester = released["requester"]
    assert all(to != requester["email"] for to, _, _ in sent)
    # El único correo es el aviso al equipo, y no lo recibe ningún seguidor que no sea del equipo
    assert all("liberados" in subject for _, subject, _ in sent)
    assert all(TEXT not in body for _, _, body in sent)
