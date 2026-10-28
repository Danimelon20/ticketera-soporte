"""Adjuntos de los comentarios: qué se acepta, cómo se revisa y dónde se guarda.

El tipo se revisa por el contenido (firma del archivo), no solo por la extensión. El Content-Type que se
guarda sale de ALLOWED, nunca del que manda el navegador. En disco el archivo se llama con un uuid; el
nombre original solo queda en la base.
"""
import hashlib
import io
import os
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import audit, config, models

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 5

REJECTED_TYPE = "Este tipo de archivo no se puede adjuntar. Se permiten imágenes, documentos y .zip."
ZIP_REJECTED = "El .zip contiene archivos que no se permiten."
MACROS_REJECTED = "Este documento contiene macros y no se puede adjuntar. Guárdelo sin macros e inténtelo de nuevo."
TOO_MANY = f"Puede adjuntar hasta {MAX_FILES} archivos por comentario."

OLE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis"}
# Dentro de un .zip no se permite nada de esto (ni otro .zip)
ZIP_BLOCKED = {
    "exe", "msi", "bat", "cmd", "ps1", "sh", "js", "vbs", "jar", "apk", "dll", "scr",
    "html", "htm", "svg", "docm", "xlsm", "pptm", "zip",
}
# Un .txt o .csv que empieza así no es texto
BINARY_STARTS = (b"MZ", b"\x7fELF", b"ID3", b"PK\x03\x04", b"%PDF", OLE, b"\x89PNG", b"GIF8", b"\xFF\xD8\xFF", b"RIFF", b"#!")
VBA_MARKERS = ("_VBA_PROJECT".encode("utf-16-le"), "Macros".encode("utf-16-le"))


def _is_jpg(data: bytes) -> bool:
    return data.startswith(b"\xFF\xD8\xFF")


def _is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_gif(data: bytes) -> bool:
    return data[:6] in (b"GIF87a", b"GIF89a")


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _is_heic(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS


def _is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def _is_ole(data: bytes) -> bool:
    return data.startswith(OLE)


def _is_zip(data: bytes) -> bool:
    return data[:4] in (b"PK\x03\x04", b"PK\x05\x06")


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data and not data.startswith(BINARY_STARTS)


def _office_xml(folder: str):
    def check(data: bytes) -> bool:
        names = _zip_names(data)
        return names is not None and any(name.startswith(folder) for name in names)
    return check


# extensión → (Content-Type, revisión de la firma)
ALLOWED = {
    "jpg": ("image/jpeg", _is_jpg),
    "jpeg": ("image/jpeg", _is_jpg),
    "png": ("image/png", _is_png),
    "gif": ("image/gif", _is_gif),
    "webp": ("image/webp", _is_webp),
    "heic": ("image/heic", _is_heic),
    "pdf": ("application/pdf", _is_pdf),
    "doc": ("application/msword", _is_ole),
    "xls": ("application/vnd.ms-excel", _is_ole),
    "ppt": ("application/vnd.ms-powerpoint", _is_ole),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _office_xml("word/")),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _office_xml("xl/")),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", _office_xml("ppt/")),
    "txt": ("text/plain; charset=utf-8", _is_text),
    "csv": ("text/csv; charset=utf-8", _is_text),
    "zip": ("application/zip", _is_zip),
}
# Se muestran dentro del ticket; todo lo demás solo se descarga (HEIC casi ningún navegador lo muestra)
INLINE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"}


def _zip_names(data: bytes) -> list[str] | None:
    """Nombres dentro del zip, sin extraer nada. None si no se puede abrir."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return None


def clean_name(filename: str | None) -> str:
    """Solo el nombre (sin carpetas ni caracteres de control), máximo 255."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[-255:] or "archivo"


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


class Rejected(ValueError):
    """Archivo rechazado. str(exc): el mensaje de la pantalla; reason: el motivo corto (notas del correo)."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def validate(filename: str, data: bytes) -> str:
    """Devuelve el Content-Type o lanza Rejected con el mensaje para la persona."""
    name = clean_name(filename)
    ext = extension(name)
    if ext not in ALLOWED:
        raise Rejected(REJECTED_TYPE, "no es un tipo de archivo permitido")
    if not data:
        raise Rejected(f"«{name}» está vacío.", "está vacío")
    if len(data) > MAX_BYTES:
        raise Rejected(f"«{name}» pesa más de 10 MB.", "pesa más de 10 MB")
    content_type, matches = ALLOWED[ext]
    mismatch = Rejected(f"«{name}» no es realmente un archivo .{ext}. No se adjuntó.",
                        f"su contenido no coincide con la extensión .{ext}")
    if not matches(data):
        raise mismatch
    if ext in ("doc", "xls", "ppt") and any(marker in data for marker in VBA_MARKERS):
        raise Rejected(MACROS_REJECTED, "contiene macros")
    if ext in ("docx", "xlsx", "pptx"):
        if any(n.replace("\\", "/").rsplit("/", 1)[-1].lower() == "vbaproject.bin" for n in _zip_names(data)):
            raise Rejected(MACROS_REJECTED, "contiene macros")
    if ext == "zip":
        names = _zip_names(data)
        if names is None:
            raise mismatch
        if any(not n.endswith("/") and extension(n) in ZIP_BLOCKED for n in names):
            raise Rejected(ZIP_REJECTED, "el .zip contiene archivos que no se permiten")
    return content_type


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB".replace(".", ",")


def path_of(row) -> str:
    """Ruta en disco de un Attachment o un DraftAttachment."""
    return os.path.join(config.ATTACHMENTS_DIR, row.stored_name)


def is_inline(row) -> bool:
    return row.content_type in INLINE_TYPES


def store_file(data: bytes) -> tuple[str, str]:
    """Escribe el archivo con un nombre aleatorio. Devuelve (stored_name, sha256)."""
    os.makedirs(config.ATTACHMENTS_DIR, exist_ok=True)
    stored_name = uuid.uuid4().hex
    with open(os.path.join(config.ATTACHMENTS_DIR, stored_name), "xb") as handle:
        handle.write(data)
    return stored_name, hashlib.sha256(data).hexdigest()


def remove_files(stored_names) -> None:
    for stored_name in stored_names:
        try:
            os.remove(os.path.join(config.ATTACHMENTS_DIR, stored_name))
        except FileNotFoundError:
            pass


def file_response(row) -> FileResponse:
    """Imágenes y PDF dentro de la página; lo demás solo descarga. Siempre nosniff y sin caché compartida."""
    return FileResponse(
        path_of(row),
        media_type=row.content_type,
        filename=row.original_name,
        content_disposition_type="inline" if is_inline(row) else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


def save(db: Session, comment: models.Comment, user_id: int, files: list[tuple[str, bytes, str]]) -> list[models.Attachment]:
    """Escribe los archivos ya validados (nombre, contenido, Content-Type) y agrega sus filas. No hace commit.

    Si algo falla, borra lo que alcanzó a escribir. Si el commit falla después, quien llama usa discard().
    """
    rows = []
    try:
        for filename, data, content_type in files:
            stored_name, sha256 = store_file(data)
            row = models.Attachment(
                ticket_id=comment.ticket_id,
                comment_id=comment.id,
                original_name=clean_name(filename),
                content_type=content_type,
                size=len(data),
                sha256=sha256,
                stored_name=stored_name,
                uploaded_by=user_id,
                uploaded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            rows.append(row)
            db.add(row)
            db.flush()
            # Sin el nombre original: puede traer datos personales
            audit.record(
                db, user_id, "attachment_uploaded", target_label=f"Ticket #{comment.ticket_id}",
                new=f"Adjunto #{row.id} · .{extension(row.original_name)} · {format_size(row.size)}",
            )
    except Exception:
        discard(rows)
        raise
    return rows


def discard(rows) -> None:
    remove_files(row.stored_name for row in rows)


def by_comment(db: Session, comment_ids: list[int]) -> dict[int, list[models.Attachment]]:
    if not comment_ids:
        return {}
    rows = (
        db.query(models.Attachment)
        .filter(models.Attachment.comment_id.in_(comment_ids))
        .order_by(models.Attachment.id.asc())
        .all()
    )
    result: dict[int, list[models.Attachment]] = {}
    for row in rows:
        result.setdefault(row.comment_id, []).append(row)
    return result
