def comment(client, ticket_id, actor_id, content):
    return client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"actor_id": actor_id, "content": content},
    )


def detail(client, ticket_id, actor):
    return client.get(f"/api/tickets/{ticket_id}", params={"actor_id": actor["id"]})


def close_ticket(client, ticket_id, actor_id):
    for status in ["in_progress", "resolved", "closed"]:
        client.patch(
            f"/api/tickets/{ticket_id}",
            json={"actor_id": actor_id, "status": status},
        )


# ---------- Pruebas de siempre ----------

def test_create_comment(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    response = comment(client, ticket["id"], user["id"], "Revisé el cable")
    assert response.status_code == 200
    data = response.json()
    assert data["ticket_id"] == ticket["id"]
    assert data["user_id"] == user["id"]
    assert data["content"] == "Revisé el cable"


def test_comment_in_detail_not_in_history(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    comment(client, ticket["id"], user["id"], "Nota de prueba")
    data = detail(client, ticket["id"], user).json()
    assert [c["content"] for c in data["comments"]] == ["Nota de prueba"]
    assert data["history"] == []


def test_comments_oldest_first(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    comment(client, ticket["id"], user["id"], "Primero")
    comment(client, ticket["id"], user["id"], "Segundo")
    data = detail(client, ticket["id"], user).json()
    assert [c["content"] for c in data["comments"]] == ["Primero", "Segundo"]


def test_empty_comment_rejected(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    assert comment(client, ticket["id"], user["id"], "").status_code == 422
    assert comment(client, ticket["id"], user["id"], "     ").status_code == 422


def test_comment_is_trimmed(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    response = comment(client, ticket["id"], user["id"], "   hola   ")
    assert response.json()["content"] == "hola"


def test_comment_closed_ticket_rejected(client, make_user, make_ticket):
    coord = make_user("coordinator")
    ticket = make_ticket(coord)
    close_ticket(client, ticket["id"], coord["id"])
    assert comment(client, ticket["id"], coord["id"], "Después de cerrar").status_code == 400


def test_comment_invalid_actor(client, make_user, make_ticket):
    ticket = make_ticket(make_user())
    assert comment(client, ticket["id"], 999999, "Nadie").status_code == 400


def test_comment_ticket_not_found(client, make_user):
    user = make_user()
    assert comment(client, 999999, user["id"], "Sin ticket").status_code == 404


# ---------- Pruebas nuevas de roles ----------

def test_requester_comments_own_ticket(client, make_user, make_ticket):
    # Regla 7: el solicitante sí comenta lo suyo
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert comment(client, ticket["id"], solicitante["id"], "Sigue sin imprimir").status_code == 200


def test_requester_cannot_comment_others_ticket(client, make_user, make_ticket):
    # Regla 7: el solicitante no comenta lo ajeno
    ana = make_user("requester")
    luis = make_user("requester")
    ticket_de_luis = make_ticket(luis)
    assert comment(client, ticket_de_luis["id"], ana["id"], "Me meto").status_code == 403


def test_technician_comments_any_ticket(client, make_user, make_ticket):
    # Cualquier rol comenta; el técnico, en tickets de otros
    tecnico = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    assert comment(client, ticket["id"], tecnico["id"], "Lo reviso mañana").status_code == 200


def test_requester_cannot_open_others_ticket(client, make_user, make_ticket):
    # Decisión b: el solicitante no abre el detalle de un ticket ajeno
    ana = make_user("requester")
    luis = make_user("requester")
    ticket_de_luis = make_ticket(luis)
    assert detail(client, ticket_de_luis["id"], ana).status_code == 403
    assert detail(client, ticket_de_luis["id"], luis).status_code == 200
