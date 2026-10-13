from email.message import EmailMessage

from app.email_parser import extract_sender, get_plain_body, parse_ticket_email

CATEGORIES = ["Hardware", "Software", "Red", "Accesos"]
PRIORITIES = ["Baja", "Media", "Alta", "Urgente"]


def _parse(body, subject="[Soporte] No puedo entrar"):
    return parse_ticket_email(subject, body, CATEGORIES, PRIORITIES)


def test_complete_email_without_errors():
    result = _parse(
        "Categoría: Software\n"
        "Prioridad: Alta\n"
        "Teléfono: 8888-1234\n"
        "Cédula: 1-0234-0567\n"
        "Descripción: No abre el sistema de planillas.\n"
        "Pasa desde ayer."
    )
    assert result["errors"] == []
    assert result["title"] == "No puedo entrar"
    assert result["category"] == "Software"
    assert result["priority"] == "Alta"
    assert result["phone"] == "88881234"
    assert result["id_number"] == "102340567"
    assert result["description"] == "No abre el sistema de planillas.\nPasa desde ayer."


def test_lowercase_and_uppercase_keys_are_recognized():
    result = _parse("categoria: accesos\nPRIORIDAD: alta\nDescripción: Sin acceso")
    assert result["category"] == "Accesos"
    assert result["priority"] == "Alta"
    assert result["errors"] == []


def test_unknown_category_lists_valid_ones():
    result = _parse("Categoría: Impresoras\nDescripción: No imprime")
    assert result["category"] is None
    assert result["errors"] == ["La categoría debe ser una de: Hardware, Software, Red, Accesos"]


def test_missing_category_and_description():
    result = _parse("Prioridad: Alta\nHola, necesito ayuda")
    assert result["errors"] == ["Falta la línea Categoría", "Falta la línea Descripción"]


def test_missing_priority_defaults_to_media():
    result = _parse("Categoría: Red\nDescripción: Sin internet")
    assert result["priority"] == "Media"
    assert result["errors"] == []


def test_invalid_phone():
    result = _parse("Categoría: Red\nTeléfono: 123\nDescripción: Sin internet")
    assert result["phone"] is None
    assert "El teléfono debe tener 8 dígitos" in result["errors"]


def test_title_prefixes_and_default():
    body = "Categoría: Hardware\nDescripción: No imprime"
    assert _parse(body, subject="RE: Fwd: [Soporte] Impresora")["title"] == "Impresora"
    assert _parse(body, subject="")["title"] == "Ticket recibido por correo"


def test_signature_after_dashes_is_excluded():
    result = _parse("Categoría: Red\nDescripción: Sin internet\nEn todo el piso\n--\nDaniel\nTel. 8888-0000")
    assert result["description"] == "Sin internet\nEn todo el piso"


def test_extract_sender():
    assert extract_sender("Nombre <A@B.com>") == "a@b.com"
    assert extract_sender("") == ""


def test_get_plain_body_prefers_text_plain():
    msg = EmailMessage()
    msg.set_content("Texto plano")
    msg.add_alternative("<p>Texto <b>HTML</b></p>", subtype="html")
    assert get_plain_body(msg).strip() == "Texto plano"
