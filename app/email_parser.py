import html
import re
import unicodedata
from email.utils import parseaddr

from .validators import normalize_id_number, normalize_phone

DEFAULT_TITLE = "Ticket recibido por correo"
DEFAULT_PRIORITY = "Media"
TITLE_MAX_LENGTH = 200

_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|rv)\s*:\s*)+", re.IGNORECASE)
_SUPPORT_TAG_RE = re.compile(r"\[soporte\]", re.IGNORECASE)
_BREAK_RE = re.compile(r"<br\s*/?>|</p\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").strip()


def extract_sender(from_header: str | None) -> str:
    _name, address = parseaddr(from_header or "")
    return address.strip().lower()


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def get_plain_body(msg) -> str:
    html_part = None
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            return _decode_part(part)
        if content_type == "text/html" and html_part is None:
            html_part = part
    if html_part is None:
        return ""
    text = _BREAK_RE.sub("\n", _decode_part(html_part))
    return html.unescape(_TAG_RE.sub("", text))


def _clean_title(subject: str) -> str:
    title = subject or ""
    # Repetir hasta que no quede nada que quitar ("RE: [Soporte] Fwd: ...")
    while True:
        cleaned = _SUPPORT_TAG_RE.sub("", _PREFIX_RE.sub("", title)).strip()
        if cleaned == title:
            break
        title = cleaned
    return (title or DEFAULT_TITLE)[:TITLE_MAX_LENGTH]


def _match_name(value: str, names: list[str]) -> str | None:
    wanted = normalize(value)
    return next((name for name in names if normalize(name) == wanted), None)


def parse_ticket_email(subject, body, category_names, priority_names) -> dict:
    errors = []
    fields = {}
    description_lines = None

    for line in (body or "").splitlines():
        if description_lines is not None:
            if line.strip() == "--":
                break
            description_lines.append(line)
            continue
        key = normalize(line)
        for name in ("categoria", "prioridad", "telefono", "cedula", "descripcion"):
            if key.startswith(name + ":"):
                value = line.split(":", 1)[1].strip()
                if name == "descripcion":
                    description_lines = [value]
                elif name not in fields:
                    fields[name] = value
                break

    category = None
    if "categoria" not in fields:
        errors.append("Falta la línea Categoría")
    else:
        category = _match_name(fields["categoria"], category_names)
        if category is None:
            errors.append(f"La categoría debe ser una de: {', '.join(category_names)}")

    priority = None
    if "prioridad" in fields:
        priority = _match_name(fields["prioridad"], priority_names)
    if priority is None:
        priority = DEFAULT_PRIORITY

    phone = None
    if "telefono" in fields:
        try:
            phone = normalize_phone(fields["telefono"])
        except ValueError as exc:
            errors.append(str(exc))

    id_number = None
    if "cedula" in fields:
        try:
            id_number = normalize_id_number(fields["cedula"])
        except ValueError as exc:
            errors.append(str(exc))

    description = None
    if description_lines is None:
        errors.append("Falta la línea Descripción")
    else:
        description = "\n".join(description_lines).strip()
        if not description:
            errors.append("La descripción está vacía")

    return {
        "title": _clean_title(subject),
        "category": category,
        "priority": priority,
        "phone": phone,
        "id_number": id_number,
        "description": description,
        "errors": errors,
    }
