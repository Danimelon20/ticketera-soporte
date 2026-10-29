"""Importación del organigrama: vista previa con normalización, duplicados, equivalencias y alias."""
import uuid

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
    # Ante dos nombres equivalentes se propone como oficial el que tiene tildes
    assert (f"{root} / Facturacion", f"{root} / Facturación") in pairs
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


def _confirm(client, admin, paths, merges):
    return client.post("/api/admin/departments/import/confirm", headers=admin["headers"],
                       json={"paths": paths, "merges": merges})


def _listed(client, admin):
    return {d["path"]: d for d in client.get("/api/admin/departments", headers=admin["headers"]).json()}


def test_accented_name_is_proposed_as_official_and_can_be_changed(client, make_user):
    admin = make_user("admin")
    root = unique("Gerencias")
    plain, accented = f"{root} / Fibra Optica", f"{root} / Fibra Óptica"
    data = _preview(client, admin, _csv(plain, accented)).json()
    suggestion = next(s for s in data["suggestions"] if {s["merge"], s["into"]} == {plain, accented})
    assert suggestion["into"] == accented and suggestion["choosable"] is True

    # Con lo propuesto: oficial con tilde, el otro como alias
    assert _confirm(client, admin, [plain, accented], [suggestion]).status_code == 200
    listed = _listed(client, admin)
    assert plain not in listed
    assert listed[accented]["aliases"] == [plain]

    # El administrador puede elegir el otro como oficial
    other = unique("Gerencias")
    plain2, accented2 = f"{other} / Fibra Optica", f"{other} / Fibra Óptica"
    assert _confirm(client, admin, [plain2, accented2], [{"merge": accented2, "into": plain2}]).status_code == 200
    listed = _listed(client, admin)
    assert accented2 not in listed and listed[plain2]["aliases"] == [accented2]


def test_existing_name_without_accent_can_be_renamed_by_the_merge(client, make_user):
    """Lo que dejó la versión anterior: "Fibra Optica" oficial y "Fibra Óptica" como alias."""
    admin = make_user("admin")
    root = unique("Gerencias")
    plain, accented = f"{root} / Fibra Optica", f"{root} / Fibra Óptica"
    _confirm(client, admin, [plain, accented], [{"merge": accented, "into": plain}])
    assert _listed(client, admin)[plain]["aliases"] == [accented]

    data = _preview(client, admin, _csv(accented)).json()
    suggestion = next(s for s in data["suggestions"] if {s["merge"], s["into"]} == {plain, accented})
    assert suggestion == {"merge": plain, "into": accented, "reason": "Solo cambian tildes o mayúsculas",
                          "choosable": True}
    response = _confirm(client, admin, [accented], [suggestion])
    assert response.status_code == 200, response.text
    listed = _listed(client, admin)
    assert plain not in listed
    # Mismo departamento renombrado: el nombre anterior queda como alias
    assert listed[accented]["aliases"] == [plain]


def test_same_leaf_with_different_parents_is_suggested(client, make_user):
    admin = make_user("admin")
    token = uuid.uuid4().hex[:8]
    short = f"Soporte N2 {token}"
    deep = f"Gerencias / Gerencia de Tecnologías / Soporte Nivel 2 {token} / Soporte N2 {token}"
    data = _preview(client, admin, _csv(short, deep)).json()
    suggestion = next(s for s in data["suggestions"] if {s["merge"], s["into"]} == {short, deep})
    # Las dos rutas completas, para que el administrador decida
    assert suggestion["reason"] == "Mismo nombre final con otros departamentos padre: revise las dos rutas"
    assert suggestion["choosable"] is True
    assert _confirm(client, admin, [short, deep], [{"merge": short, "into": deep}]).status_code == 200
    listed = _listed(client, admin)
    assert short not in listed and listed[deep]["aliases"] == [short]


def test_existing_department_with_other_parent_is_kept(client, make_user):
    admin = make_user("admin")
    token = uuid.uuid4().hex[:8]
    short = f"Soporte N2 {token}"
    deep = f"Gerencias / Soporte N2 {token}"
    _confirm(client, admin, [short], [])
    suggestion = next(s for s in _preview(client, admin, _csv(deep)).json()["suggestions"]
                      if {s["merge"], s["into"]} == {short, deep})
    # El que ya existe no puede quedar con un nombre de otro padre: se conserva
    assert suggestion["into"] == short and suggestion["choosable"] is False
    response = _confirm(client, admin, [deep], [{"merge": short, "into": deep}])
    assert response.status_code == 400
