"""Caché de la página después de un despliegue: nombre fijo + revalidar.

La página, el JS (app.js y la isla de React static/islands/islands.js) y el CSS llevan «Cache-Control: no-cache»: el
navegador guarda la copia pero pregunta en cada carga si cambió (ETag) y recibe 304 si no. La API no se toca."""
import os

import pytest


@pytest.mark.parametrize("path", ["/", "/index.html", "/app.js", "/css/base.css"])
def test_page_js_and_css_are_revalidated(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_unchanged_file_answers_304(client):
    first = client.get("/app.js")
    etag = first.headers["etag"]
    again = client.get("/app.js", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"


def test_api_is_not_touched(client):
    response = client.get("/api/health")
    assert response.headers.get("cache-control") != "no-cache"


@pytest.mark.skipif(not os.path.exists("static/islands/islands.js"),
                    reason="la isla se construye en la imagen de Docker (etapa de Node)")
def test_react_island_is_served_and_revalidated(client):
    response = client.get("/islands/islands.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "javascript" in response.headers["content-type"]
