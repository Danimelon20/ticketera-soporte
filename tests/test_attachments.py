import hashlib
import io
import os
import zipfile

import pytest

from app import attachments, config, models
from app.database import SessionLocal

OLE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xFF\xD8\xFF\xE0" + b"\x00" * 32
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def make_zip(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"contenido")
    return buffer.getvalue()


ALLOWED_FILES = [
    ("foto.png", PNG),
    ("foto.jpg", JPG),
    ("foto.jpeg", JPG),
    ("anim.gif", b"GIF89a" + b"\x00" * 32),
    ("foto.webp", b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32),
    ("foto.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32),
    ("factura.pdf", PDF),
    ("viejo.doc", OLE + b"\x00" * 64),
    ("viejo.xls", OLE + b"\x00" * 64),
    ("viejo.ppt", OLE + b"\x00" * 64),
    ("informe.docx", make_zip(["[Content_Types].xml", "word/document.xml"])),
    ("tabla.xlsx", make_zip(["[Content_Types].xml", "xl/workbook.xml"])),
    ("charla.pptx", make_zip(["[Content_Types].xml", "ppt/presentation.xml"])),
    ("notas.txt", "Hola, adjunto el detalle del error.\n".encode("utf-8")),
    ("datos.csv", b"nombre;cantidad\nTeclado;2\n"),
    ("paquete.zip", make_zip(["factura.pdf", "fotos/foto.png"])),
]

REJECTED_FILES = [
    ("cancion.mp3", b"ID3\x03\x00\x00\x00" + b"\x00" * 32),
    ("video.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32),
    ("programa.exe", b"MZ\x90\x00" + b"\x00" * 32),
    ("pagina.html", b"<html><script>alert(1)</script></html>"),
    ("dibujo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
    ("script.js", b"alert(1)"),
    ("sinextension", b"hola"),
]


@pytest.fixture(autouse=True)
def no_email(sent):
    """Los avisos a seguidores nunca salen a Microsoft Graph."""
    return sent


def upload(client, ticket_id, actor, files, content="Ver adjunto", internal=False):
    return client.post(
        f"/api/tickets/{ticket_id}/comments/with-attachments",
        data={"content": content, "internal": "true" if internal else "false"},
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=actor["headers"],
    )


def stored_files() -> set[str]:
    folder = config.ATTACHMENTS_DIR
    return set(os.listdir(folder)) if os.path.isdir(folder) else set()


def count_changes(action: str) -> int:
    db = SessionLocal()
    try:
        return db.query(models.AccountChange).filter(models.AccountChange.action == action).count()
    finally:
        db.close()


@pytest.fixture
def setup(client, make_user, make_ticket):
    requester = make_user("requester", name="Cliente Uno")
    other = make_user("requester", name="Cliente Dos")
    technician = make_user("technician", name="Técnico Uno")
    coordinator = make_user("coordinator", name="Coordinadora Una")
    ticket = make_ticket(requester)
    # El técnico solo ve (y descarga) los adjuntos de sus tickets
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"assigned_to": technician["id"]},
                            headers=coordinator["headers"])
    assert response.status_code == 200, response.text
    return {"requester": requester, "other": other, "technician": technician,
            "coordinator": coordinator, "ticket": ticket}


# ---------- Tipos ----------

@pytest.mark.parametrize("name,data", ALLOWED_FILES, ids=[n for n, _ in ALLOWED_FILES])
def test_allowed_types(client, setup, name, data):
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [(name, data)])
    assert response.status_code == 200, response.text
    files = response.json()["attachments"]
    assert [f["original_name"] for f in files] == [name]
    assert files[0]["size"] == len(data)


@pytest.mark.parametrize("name,data", REJECTED_FILES, ids=[n for n, _ in REJECTED_FILES])
def test_rejected_types(client, setup, name, data):
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [(name, data)])
    assert response.status_code == 400
    assert response.json()["detail"] == attachments.REJECTED_TYPE


@pytest.mark.parametrize("name,data", [
    ("foto.jpg", b"MZ\x90\x00" + b"\x00" * 32),  # .exe renombrado
    ("foto.png", JPG),
    ("foto.heic", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32),  # mp4 renombrado
    ("informe.docx", make_zip(["algo.txt"])),  # zip cualquiera renombrado
    ("factura.pdf", b"MZ\x90\x00"),
    ("notas.txt", b"MZ\x90\x00\x03\x00"),
    ("notas.txt", b"texto\x00binario"),
    ("paquete.zip", b"PK\x03\x04roto"),
])
def test_content_must_match_extension(client, setup, name, data):
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [(name, data)])
    assert response.status_code == 400
    assert "no es realmente un archivo" in response.json()["detail"]


# ---------- .zip y macros ----------

@pytest.mark.parametrize("inner", [
    "setup.exe", "instalar.msi", "x.JS", "carpeta/script.ps1", "correr.sh", "macro.docm",
    "tabla.xlsm", "pagina.htm", "dibujo.svg", "otro.zip",
])
def test_zip_with_blocked_content(client, setup, inner):
    data = make_zip(["factura.pdf", inner])
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [("paquete.zip", data)])
    assert response.status_code == 400
    assert response.json()["detail"] == attachments.ZIP_REJECTED


@pytest.mark.parametrize("name,data", [
    ("informe.docx", make_zip(["[Content_Types].xml", "word/document.xml", "word/vbaProject.bin"])),
    ("tabla.xlsx", make_zip(["[Content_Types].xml", "xl/workbook.xml", "xl/vbaProject.bin"])),
    ("charla.pptx", make_zip(["[Content_Types].xml", "ppt/presentation.xml", "ppt/vbaProject.bin"])),
    ("viejo.doc", OLE + b"\x00" * 32 + "_VBA_PROJECT".encode("utf-16-le") + b"\x00" * 32),
    ("viejo.xls", OLE + b"\x00" * 32 + "Macros".encode("utf-16-le") + b"\x00" * 32),
])
def test_documents_with_macros(client, setup, name, data):
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [(name, data)])
    assert response.status_code == 400
    assert response.json()["detail"] == attachments.MACROS_REJECTED


# ---------- Límites ----------

def test_ten_mb_limit(client, setup):
    ticket_id = setup["ticket"]["id"]
    exact = PNG + b"\x00" * (attachments.MAX_BYTES - len(PNG))
    assert upload(client, ticket_id, setup["coordinator"], [("grande.png", exact)]).status_code == 200
    response = upload(client, ticket_id, setup["coordinator"], [("enorme.png", exact + b"\x00")])
    assert response.status_code == 400
    assert response.json()["detail"] == "«enorme.png» pesa más de 10 MB."


def test_five_files_limit(client, setup):
    ticket_id = setup["ticket"]["id"]
    five = [(f"foto{i}.png", PNG) for i in range(5)]
    response = upload(client, ticket_id, setup["coordinator"], five)
    assert response.status_code == 200
    assert len(response.json()["attachments"]) == 5
    response = upload(client, ticket_id, setup["coordinator"], five + [("foto5.png", PNG)])
    assert response.status_code == 400
    assert response.json()["detail"] == attachments.TOO_MANY


def test_all_or_nothing(client, setup):
    ticket_id = setup["ticket"]["id"]
    before = stored_files()
    response = upload(client, ticket_id, setup["coordinator"],
                      [("bien.png", PNG), ("mal.exe", b"MZ")], content="No debe quedar")
    assert response.status_code == 400
    assert stored_files() == before
    detail = client.get(f"/api/tickets/{ticket_id}", headers=setup["coordinator"]["headers"]).json()
    assert all(c["content"] != "No debe quedar" for c in detail["comments"])


def test_only_attachments_without_text(client, setup):
    ticket_id = setup["ticket"]["id"]
    response = upload(client, ticket_id, setup["requester"], [("captura.png", PNG)], content="")
    assert response.status_code == 200
    assert response.json()["content"] == ""
    response = upload(client, ticket_id, setup["requester"], [], content="  ")
    assert response.status_code == 400


def test_stored_with_random_name_and_hash(client, setup):
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [("Cédula de Juan.pdf", PDF)])
    attachment_id = response.json()["attachments"][0]["id"]
    db = SessionLocal()
    try:
        row = db.get(models.Attachment, attachment_id)
        assert row.original_name == "Cédula de Juan.pdf"
        assert len(row.stored_name) == 32 and row.stored_name.isalnum()
        with open(os.path.join(config.ATTACHMENTS_DIR, row.stored_name), "rb") as handle:
            assert hashlib.sha256(handle.read()).hexdigest() == row.sha256 == hashlib.sha256(PDF).hexdigest()
        assert row.uploaded_by == setup["coordinator"]["id"]
    finally:
        db.close()
    assert not any("Cédula" in name for name in stored_files())


# ---------- Permisos ----------

def test_requester_never_sees_internal_note_attachments(client, setup):
    ticket_id = setup["ticket"]["id"]
    note = upload(client, ticket_id, setup["coordinator"], [("interno.png", PNG)], content="Solo equipo", internal=True)
    assert note.status_code == 200
    attachment_id = note.json()["attachments"][0]["id"]

    detail = client.get(f"/api/tickets/{ticket_id}", headers=setup["requester"]["headers"]).json()
    ids = [a["id"] for c in detail["comments"] for a in c["attachments"]]
    assert attachment_id not in ids
    assert client.get(f"/api/attachments/{attachment_id}", headers=setup["requester"]["headers"]).status_code == 404
    assert client.get(f"/api/attachments/{attachment_id}?preview=1", headers=setup["requester"]["headers"]).status_code == 404

    for staff in ("technician", "coordinator"):
        assert client.get(f"/api/attachments/{attachment_id}", headers=setup[staff]["headers"]).status_code == 200
    staff_detail = client.get(f"/api/tickets/{ticket_id}", headers=setup["technician"]["headers"]).json()
    assert attachment_id in [a["id"] for c in staff_detail["comments"] for a in c["attachments"]]


def test_requester_cannot_add_internal_note_with_attachments(client, setup):
    response = upload(client, setup["ticket"]["id"], setup["requester"], [("x.png", PNG)], internal=True)
    assert response.status_code == 403


def test_requester_only_sees_own_ticket_attachments(client, setup):
    public = upload(client, setup["ticket"]["id"], setup["requester"], [("mio.png", PNG)])
    attachment_id = public.json()["attachments"][0]["id"]
    assert client.get(f"/api/attachments/{attachment_id}", headers=setup["requester"]["headers"]).status_code == 200
    assert client.get(f"/api/attachments/{attachment_id}", headers=setup["other"]["headers"]).status_code == 403
    assert upload(client, setup["ticket"]["id"], setup["other"], [("ajeno.png", PNG)]).status_code == 403


def test_missing_attachment_and_no_session(client, setup):
    assert client.get("/api/attachments/999999", headers=setup["coordinator"]["headers"]).status_code == 404
    assert client.get("/api/attachments/1").status_code == 401


# ---------- Cabeceras ----------

@pytest.mark.parametrize("name,data,content_type,disposition", [
    ("foto.png", PNG, "image/png", "inline"),
    ("foto.jpg", JPG, "image/jpeg", "inline"),
    ("factura.pdf", PDF, "application/pdf", "inline"),
    ("foto.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32, "image/heic", "attachment"),
    ("informe.docx", make_zip(["word/document.xml"]),
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "attachment"),
    ("paquete.zip", make_zip(["factura.pdf"]), "application/zip", "attachment"),
    ("notas.txt", b"hola", "text/plain; charset=utf-8", "attachment"),
])
def test_download_headers(client, setup, name, data, content_type, disposition):
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [(name, data)])
    attachment_id = response.json()["attachments"][0]["id"]
    assert response.json()["attachments"][0]["inline"] == (disposition == "inline")
    download = client.get(f"/api/attachments/{attachment_id}", headers=setup["coordinator"]["headers"])
    assert download.status_code == 200
    assert download.content == data
    assert download.headers["content-type"] == content_type
    assert download.headers["content-disposition"].startswith(f'{disposition}; filename="{name}"')
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in download.headers["cache-control"]


def test_content_type_ignores_browser(client, setup):
    """Aunque el navegador diga text/html, se guarda y se sirve como image/png."""
    response = client.post(
        f"/api/tickets/{setup['ticket']['id']}/comments/with-attachments",
        data={"content": "x"},
        files=[("files", ("foto.png", PNG, "text/html"))],
        headers=setup["coordinator"]["headers"],
    )
    attachment_id = response.json()["attachments"][0]["id"]
    download = client.get(f"/api/attachments/{attachment_id}", headers=setup["coordinator"]["headers"])
    assert download.headers["content-type"] == "image/png"


# ---------- Registro de cambios ----------

def test_upload_and_download_are_recorded(client, setup):
    uploaded = count_changes("attachment_uploaded")
    downloaded = count_changes("attachment_downloaded")
    response = upload(client, setup["ticket"]["id"], setup["coordinator"], [("a.png", PNG), ("b.pdf", PDF)])
    attachment_id = response.json()["attachments"][0]["id"]
    assert count_changes("attachment_uploaded") == uploaded + 2

    client.get(f"/api/attachments/{attachment_id}?preview=1", headers=setup["technician"]["headers"])
    assert count_changes("attachment_downloaded") == downloaded
    client.get(f"/api/attachments/{attachment_id}", headers=setup["technician"]["headers"])
    assert count_changes("attachment_downloaded") == downloaded + 1

    db = SessionLocal()
    try:
        row = (
            db.query(models.AccountChange)
            .filter(models.AccountChange.action == "attachment_downloaded")
            .order_by(models.AccountChange.id.desc())
            .first()
        )
        assert row.actor_id == setup["technician"]["id"]
        assert row.target_label == f"Ticket #{setup['ticket']['id']}"
        # El nombre original no queda en el registro
        assert "a.png" not in (row.new_value or "")
    finally:
        db.close()
