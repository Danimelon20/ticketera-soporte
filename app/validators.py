def normalize_phone(value: str) -> str:
    digits = "".join(c for c in value if c in "0123456789")
    if digits.startswith("506") and len(digits) == 11:
        digits = digits[3:]
    if len(digits) != 8:
        raise ValueError("El teléfono debe tener 8 dígitos")
    return digits


def normalize_id_number(value: str) -> str:
    digits = "".join(c for c in value if c in "0123456789")
    if len(digits) not in (9, 11, 12):
        raise ValueError("La cédula debe tener 9 dígitos, o 11 o 12 si es DIMEX")
    return digits


def mask_id_number(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) < 3:
        return value
    return "*" * (len(digits) - 3) + digits[-3:]

def mask_last4(value: str | None) -> str | None:
    """Solo los últimos 4 dígitos (registro de cambios de cuentas)."""
    if not value:
        return None
    digits = "".join(c for c in value if c.isdigit())
    return f"****{digits[-4:]}"
