def ids(response):
    return [ticket["id"] for ticket in response.json()]


def list_as(client, actor, **filters):
    return client.get("/api/tickets", params={"actor_id": actor["id"], **filters})


def test_list_includes_created_ticket(client, make_user, make_ticket):
    user = make_user()
    ticket = make_ticket(user, "Listado basico LST1")
    response = list_as(client, user)
    assert response.status_code == 200
    assert ticket["id"] in ids(response)


def test_filter_by_priority(client, make_user, make_ticket):
    user = make_user()
    baja = make_ticket(user, "Prioridad baja PRI1", priority_id=1)
    alta = make_ticket(user, "Prioridad alta PRI1", priority_id=3)
    result = ids(list_as(client, user, priority_id=3))
    assert alta["id"] in result
    assert baja["id"] not in result


def test_filter_invalid_status(client, make_user):
    user = make_user()
    assert list_as(client, user, status="volador").status_code == 422


def test_search_ignores_case(client, make_user, make_ticket):
    user = make_user()
    buscado = make_ticket(user, "Teclado roto SRC1")
    otro = make_ticket(user, "Monitor apagado SRC1")
    result = ids(list_as(client, user, search="TECLADO ROTO"))
    assert buscado["id"] in result
    assert otro["id"] not in result


def test_combined_filters(client, make_user, make_ticket):
    user = make_user()
    correcto = make_ticket(user, "Red caida CMB1", priority_id=3)
    otra_prioridad = make_ticket(user, "Red caida CMB1 bis", priority_id=1)
    otro_texto = make_ticket(user, "Mouse roto CMB1", priority_id=3)
    result = ids(list_as(client, user, priority_id=3, search="red caida"))
    assert correcto["id"] in result
    assert otra_prioridad["id"] not in result
    assert otro_texto["id"] not in result


def test_newest_first(client, make_user, make_ticket):
    user = make_user()
    primero = make_ticket(user, "Orden primero ORD1")
    segundo = make_ticket(user, "Orden segundo ORD1")
    result = ids(list_as(client, user, search="ORD1"))
    assert result.index(segundo["id"]) < result.index(primero["id"])


def test_list_without_actor(client):
    assert client.get("/api/tickets").status_code == 422


def test_requester_sees_only_own_tickets(client, make_user, make_ticket):
    ana = make_user("requester")
    luis = make_user("requester")
    de_ana = make_ticket(ana, "Ticket de Ana OWN1")
    de_luis = make_ticket(luis, "Ticket de Luis OWN1")
    result = ids(list_as(client, ana))
    assert de_ana["id"] in result
    assert de_luis["id"] not in result


def test_technician_sees_all_tickets(client, make_user, make_ticket):
    tecnico = make_user("technician")
    solicitante = make_user("requester")
    ticket = make_ticket(solicitante, "Ticket visible ALL1")
    assert ticket["id"] in ids(list_as(client, tecnico))
