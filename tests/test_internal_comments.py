"""El comentario interno nunca le llega al solicitante, por ningún camino; el personal sí lo ve."""
import pytest

from app import accounts, chatbot, config
from app.routers import chat as chat_router

TEXT = accounts.RELEASED_COMMENT


@pytest.fixture
def prompts(monkeypatch):
    calls = []

    def _fake_llm(system_prompt, history, message):
        calls.append(system_prompt)
        return "Respuesta de prueba"

    monkeypatch.setattr(config, "CHATBOT_ENABLED", True)
    monkeypatch.setattr(chatbot, "call_llm", _fake_llm)
    chat_router.reset_rate_limit()
    yield calls
    chat_router.reset_rate_limit()


@pytest.fixture
def released(client, make_user, make_ticket, sent):
    """Ticket con un comentario normal y el interno de "técnico desactivado"."""
    coordinator = make_user("coordinator")
    requester = make_user("requester")
    leaving = make_user("technician")
    ticket = make_ticket(requester, title="Correo no sincroniza")
    client.post(f"/api/tickets/{ticket['id']}/follow", headers=requester["headers"])
    client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": leaving["id"]}, headers=coordinator["headers"])
    client.post(f"/api/tickets/{ticket['id']}/comments", json={"content": "Comentario público"},
                headers=coordinator["headers"])
    sent.clear()
    # Desactivar cuentas es del administrador
    admin = make_user("admin")
    assert client.post(f"/api/accounts/{leaving['id']}/deactivate", headers=admin["headers"]).status_code == 204
    return {"ticket": ticket, "requester": requester, "coordinator": coordinator}


def test_requester_does_not_get_it_in_ticket_detail_or_list(client, released):
    requester, ticket = released["requester"], released["ticket"]
    detail = client.get(f"/api/tickets/{ticket['id']}", headers=requester["headers"])
    assert detail.status_code == 200
    assert TEXT not in detail.text
    assert [c["content"] for c in detail.json()["comments"]] == ["Comentario público"]
    assert all(c.get("internal") in (None, False) for c in detail.json()["comments"])
    listing = client.get("/api/tickets", headers=requester["headers"])
    assert TEXT not in listing.text


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


def test_not_in_requester_chat_context(client, released, prompts):
    response = client.post("/api/chat", json={"message": "¿Cómo va mi ticket?"}, headers=released["requester"]["headers"])
    assert response.status_code == 200
    prompt = prompts[-1]
    assert "Correo no sincroniza" in prompt
    assert "Comentario público" in prompt
    assert TEXT not in prompt


def test_technician_chat_context_marks_it_internal(client, make_user, released, prompts):
    technician = make_user("technician")
    ticket = released["ticket"]
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]},
                            headers=released["coordinator"]["headers"])
    assert response.status_code == 200
    assert client.post("/api/chat", json={"message": "Resumen"}, headers=technician["headers"]).status_code == 200
    assert f"(interno): {TEXT}" in prompts[-1]
