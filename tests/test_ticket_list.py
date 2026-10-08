def create_ticket(client, title, priority_id=1, category_id=1):
    payload = {
        "title": title,
        "description": f"Descripción de {title}",
        "category_id": category_id,
        "priority_id": priority_id,
    }
    return client.post("/api/tickets", json=payload).json()


def ids(response):
    return [ticket["id"] for ticket in response.json()]


def test_list_includes_created_ticket(client):
    ticket = create_ticket(client, "Listado basico LST1")
    response = client.get("/api/tickets")
    assert response.status_code == 200
    assert ticket["id"] in ids(response)


def test_filter_by_priority(client):
    baja = create_ticket(client, "Prioridad baja PRI1", priority_id=1)
    alta = create_ticket(client, "Prioridad alta PRI1", priority_id=3)
    response = client.get("/api/tickets?priority_id=3")
    assert alta["id"] in ids(response)
    assert baja["id"] not in ids(response)


def test_filter_invalid_status(client):
    response = client.get("/api/tickets?status=volador")
    assert response.status_code == 422


def test_search_ignores_case(client):
    buscado = create_ticket(client, "Teclado roto SRC1")
    otro = create_ticket(client, "Monitor apagado SRC1")
    response = client.get("/api/tickets?search=TECLADO ROTO")
    assert buscado["id"] in ids(response)
    assert otro["id"] not in ids(response)


def test_combined_filters(client):
    correcto = create_ticket(client, "Red caida CMB1", priority_id=3)
    otra_prioridad = create_ticket(client, "Red caida CMB1 bis", priority_id=1)
    otro_texto = create_ticket(client, "Mouse roto CMB1", priority_id=3)
    response = client.get("/api/tickets?priority_id=3&search=red caida")
    result = ids(response)
    assert correcto["id"] in result
    assert otra_prioridad["id"] not in result
    assert otro_texto["id"] not in result


def test_newest_first(client):
    primero = create_ticket(client, "Orden primero ORD1")
    segundo = create_ticket(client, "Orden segundo ORD1")
    result = ids(client.get("/api/tickets?search=ORD1"))
    assert result.index(segundo["id"]) < result.index(primero["id"])

