"""Importación del organigrama: vista previa con normalización, duplicados, equivalencias y alias."""
from app import departments, models
from app.database import SessionLocal
from org_helpers import unique


def _csv(*paths, header="ruta"):
    lines = [header] + [f'"{p}"' for p in paths]
    return "\n".join(lines).encode("utf-8")


def _preview(client, admin, content):
    return client.post("/api/admin/departments/import/preview", headers=admin["headers"],
                       files={"file": ("organigrama.csv", content, "text/csv")})


def _paths_in_db(prefix):
    db = SessionLocal()
    try:
        from app import routing
        by_id = routing.departments_by_id(db)
        return sorted(routing.path_label(d, by_id) for d in by_id.values()
                      if routing.path_label(d, by_id).startswith(prefix))
    finally:
        db.close()


def test_normalize_name_ignores_accents_case_and_abbreviations():
    assert departments.normalize_name("Soporte Nivel 2") == departments.normalize_name("soporte N2")
    assert departments.normalize_name("Administración") == departments.normalize_name("ADMINISTRACION")
    assert departments.normalize_name("Departamento de Redes") == departments.normalize_name("Dpto. Redes")
    assert departments.normalize_name("Soporte N2") != departments.normalize_name("Soporte N3")


def test_clean_segments_removes_consecutive_repeats():
    segments, notes = departments.clean_segments("Gerencias / Gerencias / General")
    assert segments == ["Gerencias", "General"]
    assert notes == ["Se quitó el segmento repetido «Gerencias»"]
    assert departments.clean_segments("  Gerencias/ GERÉNCIAS /General ")[0] == ["Gerencias", "General"]


def test_preview_does_not_save_and_flags_duplicates_and_suggestions(client, make_user):
    admin = make_user("admin")
    root = unique("Gerencias")
    content = _csv(
        f"{root} / Operaciones / Fibra Óptica / Mantenimiento",
        f"{root} / {root} / General",
        f"{root} / Operaciones / Fibra Óptica / Mantenimiento",
        f"{root} / Soporte N2",
        f"{root} / Soporte Nivel 2 / Soporte N2",
        f"{root} / Facturacion",
        f"{root} / Facturación",
        " / ",
    )
    response = _preview(client, admin, content)
    assert response.status_code == 200, response.text
    data = response.json()
    rows = data["rows"]
    assert rows[1]["path"] == f"{root} / General"
    assert rows[1]["notes"] == [f"Se quitó el segmento repetido «{root}»"]
    assert rows[2]["status"] == "duplicate" and rows[2]["notes"] == ["Repite la fila 1"]
    assert rows[7]["status"] == "error"
    pairs = {(s["merge"], s["into"]) for s in data["suggestions"]}
    assert (f"{root} / Soporte Nivel 2 / Soporte N2", f"{root} / Soporte N2") in pairs
    assert (f"{root} / Facturación", f"{root} / Facturacion") in pairs
    # Solo vista previa: nada en la base
    assert _paths_in_db(root) == []


def test_confirm_creates_tree_and_merged_names_become_aliases(client, make_user):
    admin = make_user("admin")
    root = unique("Gerencias")
    paths = [f"{root} / Operaciones / NOC", f"{root} / Soporte N2", f"{root} / Soporte Nivel 2 / Soporte N2"]
    merges = [{"merge": f"{root} / Soporte Nivel 2 / Soporte N2", "into": f"{root} / Soporte N2"}]
    response = client.post("/api/admin/departments/import/confirm", headers=admin["headers"],
                           json={"paths": paths, "merges": merges})
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 4, "aliases": 1}
    assert _paths_in_db(root) == [root, f"{root} / Operaciones", f"{root} / Operaciones / NOC", f"{root} / Soporte N2"]

    listed = {d["path"]: d for d in client.get("/api/admin/departments", headers=admin["headers"]).json()}
    assert listed[f"{root} / Soporte N2"]["aliases"] == [f"{root} / Soporte Nivel 2 / Soporte N2"]

    # Importar otra vez reconoce las rutas existentes y el alias: no crea nada
    again = _preview(client, admin, _csv(f"{root} / Operaciones / NOC", f"{root} / soporte nivel 2")).json()
    assert again["rows"][0]["status"] == "exists"
    response = client.post("/api/admin/departments/import/confirm", headers=admin["headers"],
                           json={"paths": [f"{root} / Operaciones / NOC", f"{root} / soporte nivel 2"], "merges": []})
    assert response.json()["created"] == 0


def test_import_is_admin_only(client, make_user):
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert _preview(client, user, _csv("X / Y")).status_code == 403
        assert client.post("/api/admin/departments/import/confirm", headers=user["headers"],
                           json={"paths": ["X"], "merges": []}).status_code == 403


def test_crud_move_and_cycle(client, make_user):
    admin = make_user("admin")
    parent = client.post("/api/admin/departments", headers=admin["headers"], json={"name": unique("Redes")}).json()
    noc = unique("NOC")
    child = client.post("/api/admin/departments", headers=admin["headers"],
                        json={"name": noc, "parent_id": parent["id"]}).json()
    assert child["path"] == f"{parent['name']} / {noc}"
    # Mismo nombre (sin tildes ni mayúsculas) en el mismo lugar
    assert client.post("/api/admin/departments", headers=admin["headers"],
                       json={"name": noc.lower(), "parent_id": parent["id"]}).status_code == 409
    # Un departamento no puede quedar dentro de su propio hijo
    response = client.patch(f"/api/admin/departments/{parent['id']}", headers=admin["headers"],
                            json={"parent_id": child["id"]})
    assert response.status_code == 400
    moved = client.patch(f"/api/admin/departments/{child['id']}", headers=admin["headers"], json={"parent_id": 0})
    assert moved.json()["parent_id"] is None
    off = client.patch(f"/api/admin/departments/{parent['id']}", headers=admin["headers"], json={"is_active": False})
    assert off.json()["is_active"] is False
    alias = client.patch(f"/api/admin/departments/{child['id']}", headers=admin["headers"],
                         json={"add_alias": f"Centro de operaciones {parent['name']}"})
    assert alias.json()["aliases"] == [f"Centro de operaciones {parent['name']}"]


def test_real_org_chart_is_never_stored(client, make_user):
    """La vista previa solo lee en memoria: no deja archivos ni filas."""
    admin = make_user("admin")
    db = SessionLocal()
    try:
        before = db.query(models.Department).count()
    finally:
        db.close()
    _preview(client, admin, _csv(unique("Secreto") + " / Área confidencial"))
    db = SessionLocal()
    try:
        assert db.query(models.Department).count() == before
    finally:
        db.close()
