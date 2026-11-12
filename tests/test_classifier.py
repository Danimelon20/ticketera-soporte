"""Fase 1 de la clasificación de correos: ficha por departamento (solo el administrador) y «Probar clasificación»
(IA + similitud con los ejemplos, sin crear nada). Ollama y los embeddings se simulan: nunca hay red."""
import json

import pytest

from app import chatbot, classifier, models, routing, similarity
from app.database import SessionLocal
from app.routers import org
from org_helpers import changes, department, unique

PHONE = "8888-1234"
ID_NUMBER = "1-1234-5678"
EMAIL = "persona@empresa.test"
KEYWORDS = (("internet", "red", "wifi", "conexion", "conexión"), ("impresora", "imprime", "monitor", "pantalla"))


def _vector(text):
    clean = text.lower()
    return [sum(clean.count(k) for k in group) for group in KEYWORDS] + [0.1]


@pytest.fixture
def ai(monkeypatch):
    """IA y embeddings simulados. choice: ruta del departamento que «elige» la IA."""
    state = {"choice": None, "reason": "Es un problema de conexión según la ficha.", "payloads": [], "embedded": [],
             "chat_error": None, "embed_error": None}

    def _post_chat(payload, task=None):
        state["payloads"].append(payload)
        if state["chat_error"] is not None:
            raise state["chat_error"]
        enum = payload["format"]["properties"]["department"]["enum"]
        chosen = state["choice"] if state["choice"] in enum else classifier.NONE_CHOICE
        return json.dumps({"department": chosen, "reason": state["reason"]})

    def _embed(texts, deadline):
        state["embedded"].extend(texts)
        if state["embed_error"] is not None:
            raise state["embed_error"]
        return [_vector(t) for t in texts]

    monkeypatch.setattr(chatbot, "post_chat", _post_chat)
    monkeypatch.setattr(similarity, "embed", _embed)
    # Solo las fichas de cada prueba (la base es compartida entre pruebas)
    with SessionLocal() as db:
        db.query(models.DepartmentExampleEmbedding).delete()
        db.query(models.DepartmentProfile).delete()
        db.commit()
    return state


def _path(dept_id):
    with SessionLocal() as db:
        return routing.path_label(db.get(models.Department, dept_id), routing.departments_by_id(db))


def _put(client, user, dept_id, **body):
    payload = {"handles": "", "examples": "", "not_handles": "", **body}
    return client.put(f"/api/admin/departments/{dept_id}/profile", headers=user["headers"], json=payload)


def _test(client, user, body, subject=""):
    return client.post("/api/admin/classification/test", headers=user["headers"],
                       json={"subject": subject, "body": body})


def _two_departments(client, admin):
    redes, soporte = department(unique("Redes")), department(unique("Soporte N1"))
    assert _put(client, admin, redes, handles="Internet, wifi y cableado",
                examples="Sin internet en el piso\nEl wifi no conecta",
                not_handles="Impresoras → Soporte N1").status_code == 200
    assert _put(client, admin, soporte, handles="Equipos, impresoras y monitores",
                examples="La impresora no imprime\nEl monitor no da imagen").status_code == 200
    return redes, soporte


def _counts():
    with SessionLocal() as db:
        return (db.query(models.EmailDraft).count(), db.query(models.Ticket).count(),
                db.query(models.History).count())


# ----- Ficha -----

@pytest.mark.parametrize("role", ["coordinator", "technician", "requester"])
def test_only_the_admin_edits_profiles_and_tests(client, make_user, ai, role):
    user = make_user(role)
    dept = department()
    assert client.get(f"/api/admin/departments/{dept}/profile", headers=user["headers"]).status_code == 403
    assert _put(client, user, dept, handles="Algo").status_code == 403
    assert _test(client, user, "No tengo internet").status_code == 403
    assert ai["payloads"] == [] and ai["embedded"] == []


def test_profile_round_trip_with_audit_without_text(client, make_user, ai):
    admin = make_user("admin")
    dept = department(unique("Redes"))
    response = _put(client, admin, dept, handles="Internet y wifi", examples="  Sin internet \n\n El wifi no conecta ",
                    not_handles="Impresoras → Soporte N1")
    assert response.status_code == 200, response.text
    data = client.get(f"/api/admin/departments/{dept}/profile", headers=admin["headers"]).json()
    assert data["examples"] == "Sin internet\nEl wifi no conecta" and data["handles"] == "Internet y wifi"
    assert data["not_handles"] == "Impresoras → Soporte N1" and data["updated_at"]
    [change] = [c for c in changes("department_profile_changed") if c.target_label == data["path"]]
    # Sin el texto de la ficha en el registro
    assert change.new_value == "Ficha actualizada: 2 ejemplo(s)"


def test_profile_validations(client, make_user, ai):
    admin = make_user("admin")
    not_serving = department(handles=False)
    assert _put(client, admin, not_serving, handles="Algo").json()["detail"] == org.PROFILE_NOT_SERVING
    assert client.get(f"/api/admin/departments/{not_serving}/profile", headers=admin["headers"]).status_code == 422
    dept = department()
    too_many = "\n".join(f"Ejemplo {n}" for n in range(classifier.MAX_EXAMPLES + 1))
    assert _put(client, admin, dept, examples=too_many).json()["detail"] == org.TOO_MANY_EXAMPLES
    assert _put(client, admin, dept, examples="x" * (classifier.EXAMPLE_CHARS + 1)).json()["detail"] == org.LONG_EXAMPLE
    assert client.get("/api/admin/departments/999999/profile", headers=admin["headers"]).status_code == 404


# ----- Probar clasificación -----

def test_clear_email_goes_direct_and_creates_nothing(client, make_user, ai):
    admin = make_user("admin")
    redes, soporte = _two_departments(client, admin)
    ai["choice"] = _path(redes)
    before = _counts()
    response = _test(client, admin, "Desde esta mañana no tengo internet ni wifi", subject="Sin conexión")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["llm_department_id"] == redes and data["agrees"] is True and data["passes"] is True
    assert [o["department_id"] for o in data["options"]] == [redes, soporte]
    assert data["options"][0]["similarity"] > data["options"][1]["similarity"]
    assert data["threshold"] == classifier.DEFAULT_THRESHOLD and data["margin"] >= data["threshold"]
    assert data["reason"]
    # Sin borradores, tickets ni historial nuevos
    assert _counts() == before


def test_personal_data_is_masked_before_ai_and_embeddings(client, make_user, ai):
    admin = make_user("admin")
    redes, _ = _two_departments(client, admin)
    _put(client, admin, redes, examples=f"Sin internet, llamar al {PHONE}\nEl wifi no conecta",
         handles=f"Internet; responsable {EMAIL}", not_handles=f"Cédula {ID_NUMBER} → nadie")
    ai["choice"] = _path(redes)
    _test(client, admin, f"No hay internet. Mi cédula {ID_NUMBER}, teléfono {PHONE}, correo {EMAIL}",
          subject=f"Urgente {PHONE}")
    assert ai["payloads"] and ai["embedded"]
    sent = json.dumps(ai["payloads"], ensure_ascii=False) + " ".join(ai["embedded"])
    for secret in (PHONE, ID_NUMBER, EMAIL):
        assert secret not in sent
    assert "[dato oculto]" in sent and "[correo oculto]" in sent


def test_without_embeddings_it_degrades_to_pending(client, make_user, ai):
    admin = make_user("admin")
    redes, _ = _two_departments(client, admin)
    ai["choice"] = _path(redes)
    ai["embed_error"] = RuntimeError("model not found")
    response = _test(client, admin, "No tengo internet")
    assert response.status_code == 200
    data = response.json()
    assert data["passes"] is False and data["agrees"] is False and data["margin"] is None
    assert [o["department_id"] for o in data["options"]] == [redes]
    assert any("embeddings" in note for note in data["notes"])


def test_without_ai_it_degrades_to_pending(client, make_user, ai):
    admin = make_user("admin")
    redes, soporte = _two_departments(client, admin)
    ai["chat_error"] = chatbot.ChatbotUnavailable()
    data = _test(client, admin, "La impresora no imprime").json()
    assert data["passes"] is False and data["llm_department_id"] is None
    # Las opciones salen de la similitud
    assert [o["department_id"] for o in data["options"]] == [soporte, redes]
    assert classifier.NO_LLM in data["notes"]


def test_disagreement_and_threshold_send_to_pending(client, make_user, ai):
    admin = make_user("admin")
    redes, soporte = _two_departments(client, admin)
    # La IA elige Soporte pero el correo se parece a Redes
    ai["choice"] = _path(soporte)
    data = _test(client, admin, "No tengo internet").json()
    assert data["agrees"] is False and data["passes"] is False and classifier.DISAGREE in data["notes"]
    assert [o["department_id"] for o in data["options"]] == [soporte, redes]
    # Coinciden pero el umbral es más alto que el margen
    ai["choice"] = _path(redes)
    with SessionLocal() as db:
        routing.set_setting(db, classifier.THRESHOLD_KEY, "0.99")
        db.commit()
    try:
        data = _test(client, admin, "No tengo internet ni impresora").json()
        assert data["agrees"] is True and data["passes"] is False and data["threshold"] == 0.99
    finally:
        with SessionLocal() as db:
            routing.set_setting(db, classifier.THRESHOLD_KEY, None)
            db.commit()


def test_department_without_examples_never_goes_direct(client, make_user, ai):
    admin = make_user("admin")
    _two_departments(client, admin)
    bare = department(unique("Ingeniería N3"))
    ai["choice"] = _path(bare)
    data = _test(client, admin, "El servidor de base de datos está lento").json()
    assert data["llm_department_id"] == bare and data["passes"] is False
    assert any("Sin ejemplos" in note for note in data["notes"])


def test_profile_change_applies_on_the_next_email(client, make_user, ai):
    admin = make_user("admin")
    redes, _ = _two_departments(client, admin)
    ai["choice"] = _path(redes)
    _test(client, admin, "No tengo internet")
    with SessionLocal() as db:
        assert db.query(models.DepartmentExampleEmbedding).filter_by(department_id=redes).count() == 2
    # Cambia la ficha: los vectores de los ejemplos quitados se borran y el siguiente correo ya la usa
    _put(client, admin, redes, handles="Solo cableado", examples="El cable de red está roto")
    with SessionLocal() as db:
        assert db.query(models.DepartmentExampleEmbedding).filter_by(department_id=redes).count() == 0
    ai["payloads"].clear()
    _test(client, admin, "No tengo internet")
    system = ai["payloads"][0]["messages"][0]["content"]
    assert "Solo cableado" in system and "El cable de red está roto" in system and "El wifi no conecta" not in system
    with SessionLocal() as db:
        assert db.query(models.DepartmentExampleEmbedding).filter_by(department_id=redes).count() == 1


def test_only_serving_departments_participate(client, make_user, ai):
    admin = make_user("admin")
    redes, _ = _two_departments(client, admin)
    off = department(unique("Archivo"), handles=False)
    ai["choice"] = _path(redes)
    _test(client, admin, "No tengo internet")
    enum = ai["payloads"][-1]["format"]["properties"]["department"]["enum"]
    assert _path(redes) in enum and _path(off) not in enum and classifier.NONE_CHOICE in enum


# ----- Corrección de la Fase 1: el modelo pequeño elegía «Ninguno» -----

def _people(*paths):
    return [classifier.Participant(n, path, "", [], "") for n, path in enumerate(paths, start=1)]


def test_reason_goes_before_the_department_in_the_schema(client, make_user, ai):
    admin = make_user("admin")
    redes, _ = _two_departments(client, admin)
    ai["choice"] = _path(redes)
    _test(client, admin, "No tengo internet")
    payload = ai["payloads"][-1]
    assert list(payload["format"]["properties"]) == ["reason", "department"]
    assert payload["format"]["required"] == ["reason", "department"]


def test_short_labels_without_tags_and_without_repeats():
    assert classifier.labels(_people("[DEMO] Gerencia General / [DEMO] Redes",
                                     "[DEMO] Gerencia General / [DEMO] Soporte N1")) == ["Redes", "Soporte N1"]
    # Mismo nombre en dos lugares: se agrega el nivel de arriba
    assert classifier.labels(_people("Sede Norte / Redes", "Sede Sur / Redes", "Soporte")) == [
        "Sede Norte / Redes", "Sede Sur / Redes", "Soporte"]
    # Un departamento que se llama como la opción "Ninguno" no se confunde con ella
    assert classifier.NONE_CHOICE not in classifier.labels(_people("Ninguno"))


def test_short_label_maps_back_to_the_nested_department(client, make_user, ai):
    admin = make_user("admin")
    parent = department(f"[DEMO] {unique('Gerencia')}", handles=False)
    leaf = unique("Redes")
    nested = department(f"[DEMO] {leaf}", parent_id=parent)
    _put(client, admin, nested, handles="Internet", examples="Sin internet en el piso")
    ai["choice"] = leaf
    data = _test(client, admin, "No tengo internet").json()
    enum = ai["payloads"][-1]["format"]["properties"]["department"]["enum"]
    assert leaf in enum and not any("[DEMO]" in label for label in enum)
    assert data["llm_department_id"] == nested
    # En pantalla sigue la ruta completa
    assert data["llm_department_path"] == _path(nested)


def test_not_handled_goes_outside_each_profile(client, make_user, ai):
    admin = make_user("admin")
    redes, soporte = _two_departments(client, admin)
    ai["choice"] = _path(redes)
    _test(client, admin, "No tengo internet")
    system = ai["payloads"][-1]["messages"][0]["content"]
    rules = system.index("Reglas para casos que se confunden")
    # La regla queda al final, después de todas las fichas, y no dentro del bloque de Redes
    assert "Impresoras → Soporte N1" in system[rules:]
    assert system.index(f"Departamento: {_path(soporte)}") < rules
    assert "No atiende" not in system[:rules]
    assert "no lo busques en el texto" in system


def test_long_reason_is_not_cut_at_300(client, make_user, ai):
    admin = make_user("admin")
    redes, _ = _two_departments(client, admin)
    ai["choice"] = _path(redes)
    ai["reason"] = "palabra " * 50  # 400 caracteres
    data = _test(client, admin, "No tengo internet").json()
    assert len(data["reason"]) > 300 and not data["reason"].endswith("…")
    ai["reason"] = "palabra " * 100  # 800 caracteres
    data = _test(client, admin, "No tengo internet").json()
    assert data["reason"].endswith("…") and len(data["reason"]) <= classifier.REASON_CHARS + 1


def test_examples_lose_leading_bullets(client, make_user, ai):
    admin = make_user("admin")
    dept = department(unique("Redes"))
    _put(client, admin, dept, examples="- -Sin internet\n• El wifi no conecta\n* La VPN no entra\nTexto - con guion")
    data = client.get(f"/api/admin/departments/{dept}/profile", headers=admin["headers"]).json()
    assert data["examples"] == "Sin internet\nEl wifi no conecta\nLa VPN no entra\nTexto - con guion"
