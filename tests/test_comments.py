def comment(client, ticket_id, actor, content):
    return client.post(
        f"/api/tickets/{ticket_id}/comments",
        json={"content": content},
        headers=actor["headers"],
    )


def detail(client, ticket_id, actor):
    return client.get(f"/api/tickets/{ticket_id}", headers=actor["headers"])


def close_ticket(client, ticket_id, actor):
    for status in ["in_progress", "resolved", "closed"]:
        client.patch(
            f"/api/tickets/{ticket_id}",
            json={"status": status},
            headers=actor["headers"],
        )


# ---------- Pruebas de siempre ----------

def test_create_comment(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    response = comment(client, ticket["id"], user, "Revisé el cable")
    assert response.status_code == 200
    data = response.json()
    assert data["ticket_id"] == ticket["id"]
    assert data["user_id"] == user["id"]
    assert data["content"] == "Revisé el cable"


def test_comment_in_detail_not_in_history(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    comment(client, ticket["id"], user, "Nota de prueba")
    data = detail(client, ticket["id"], user).json()
    assert [c["content"] for c in data["comments"]] == ["Nota de prueba"]
    assert data["history"] == []


def test_comments_oldest_first(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    comment(client, ticket["id"], user, "Primero")
    comment(client, ticket["id"], user, "Segundo")
    data = detail(client, ticket["id"], user).json()
    assert [c["content"] for c in data["comments"]] == ["Primero", "Segundo"]


def test_empty_comment_rejected(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    assert comment(client, ticket["id"], user, "").status_code == 422
    assert comment(client, ticket["id"], user, "     ").status_code == 422


def test_comment_is_trimmed(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user)
    response = comment(client, ticket["id"], user, "   hola   ")
    assert response.json()["content"] == "hola"


def test_comment_closed_ticket_rejected(client, make_user, make_ticket):
    coord = make_user("coordinator")
    req = make_user("requester")
    ticket = make_ticket(req)
    close_ticket(client, ticket["id"], coord)
    assert comment(client, ticket["id"], coord, "Después de cerrar").status_code == 400


def test_comment_invalid_actor(client, make_user, make_ticket):
    ticket = make_ticket(make_user())
    response = client.post(f"/api/tickets/{ticket['id']}/comments", json={"content": "Nadie"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Inicia sesión para continuar"


def test_comment_ticket_not_found(client, make_user):
    user = make_user()
    assert comment(client, 999999, user, "Sin ticket").status_code == 404


# ---------- Pruebas nuevas de roles ----------

def test_requester_comments_own_ticket(client, make_user, make_ticket):
    # Regla 7: el solicitante sí comenta lo suyo
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante)
    assert comment(client, ticket["id"], solicitante, "Sigue sin imprimir").status_code == 200


def test_requester_cannot_comment_others_ticket(client, make_user, make_ticket):
    # Regla 7: el solicitante no comenta lo ajeno
    ana = make_user("requester")
    luis = make_user("requester")
    ticket_de_luis = make_ticket(luis)
    assert comment(client, ticket_de_luis["id"], ana, "Me meto").status_code == 403


def test_technician_comments_any_ticket(client, make_user, make_ticket):
    # Cualquier rol comenta; el técnico, en tickets de otros
    tecnico = make_user("technician")
    ticket = make_ticket(make_user("requester"))
    assert comment(client, ticket["id"], tecnico, "Lo reviso mañana").status_code == 200


def test_requester_cannot_open_others_ticket(client, make_user, make_ticket):
    # Decisión b: el solicitante no abre el detalle de un ticket ajeno
    ana = make_user("requester")
    luis = make_user("requester")
    ticket_de_luis = make_ticket(luis)
    assert detail(client, ticket_de_luis["id"], ana).status_code == 403
    assert detail(client, ticket_de_luis["id"], luis).status_code == 200


# ---------- Pruebas adicionales requeridas ----------

def test_unassigned_technician_comments_ticket(client, make_user, make_ticket):
    requester = make_user("requester")
    ticket = make_ticket(requester)
    technician = make_user("technician")
    response = comment(client, ticket["id"], technician, "Comentario de técnico no asignado")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == technician["id"]
    assert data["content"] == "Comentario de técnico no asignado"


def test_two_different_technicians_comment_ticket(client, make_user, make_ticket):
    requester = make_user("requester")
    ticket = make_ticket(requester)
    tech1 = make_user("technician")
    tech2 = make_user("technician")
    res1 = comment(client, ticket["id"], tech1, "Comentario del técnico 1")
    assert res1.status_code == 200
    assert res1.json()["user_id"] == tech1["id"]

    res2 = comment(client, ticket["id"], tech2, "Comentario del técnico 2")
    assert res2.status_code == 200
    assert res2.json()["user_id"] == tech2["id"]

    detail_res = detail(client, ticket["id"], tech1)
    comments = detail_res.json()["comments"]
    assert len(comments) == 2
    assert comments[0]["user_id"] == tech1["id"]
    assert comments[0]["content"] == "Comentario del técnico 1"
    assert comments[1]["user_id"] == tech2["id"]
    assert comments[1]["content"] == "Comentario del técnico 2"
