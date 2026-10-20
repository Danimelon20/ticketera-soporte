import hashlib
import json
import logging
import re
import urllib.error
import urllib.request

from . import config
from .email_parser import normalize

HACIENDA_URL = "https://api.hacienda.go.cr/fe/ae?identificacion={}"
TIMEOUT_SECONDS = 5

# Caché en memoria: cédula -> {"tipo": ..., "name_tokens": ...} o None.
# Nunca se guarda el nombre: solo el hash de cada palabra, para compararlo.
_cache: dict[str, dict | None] = {}


logger = logging.getLogger(__name__)


class HaciendaUnavailable(Exception):
    """Hacienda no respondió (red, timeout o error 5xx)."""


def lookup_id(id_number: str) -> dict | None:
    if id_number in _cache:
        return _cache[id_number]

    request = urllib.request.Request(
        HACIENDA_URL.format(id_number),
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            _cache[id_number] = None
            return None
        raise HaciendaUnavailable(f"Hacienda respondió {exc.code}") from None
    except (OSError, ValueError):
        # OSError cubre URLError y timeouts; ValueError cubre JSON inválido.
        # "from None" evita arrastrar la URL (con la cédula) en la traza.
        raise HaciendaUnavailable("No se pudo consultar Hacienda") from None

    if not isinstance(data, dict):
        raise HaciendaUnavailable("Respuesta inesperada de Hacienda")
    nombre = data.get("nombre")
    result = {
        "tipo": data.get("tipoIdentificacion"),
        "name_tokens": name_tokens(nombre) if isinstance(nombre, str) else frozenset(),
    }
    _cache[id_number] = result
    return result


def name_tokens(name: str) -> frozenset[str]:
    """Hash de cada palabra del nombre, sin tildes ni mayúsculas."""
    words = re.findall(r"[a-zñ]+", normalize(name or ""))
    return frozenset(hashlib.sha256(w.encode("utf-8")).hexdigest() for w in words)


def identity_result(id_number: str, name: str) -> str:
    """Compara el nombre con el de Hacienda: match, mismatch, not_found o unverified.

    Coincide si las palabras de un nombre están todas en el otro (en cualquier
    dirección) y comparten al menos 2. El resultado nunca se muestra al usuario.
    """
    if not config.hacienda_check_enabled():
        return "unverified"
    try:
        result = lookup_id(id_number)
    except HaciendaUnavailable:
        logger.warning("Hacienda no disponible; la identidad queda sin verificar")
        return "unverified"
    if result is None:
        return "not_found"
    theirs = result.get("name_tokens") or frozenset()
    ours = name_tokens(name)
    if len(ours & theirs) >= 2 and (ours <= theirs or theirs <= ours):
        return "match"
    return "mismatch"


def check_id_number(id_number: str, tolerant: bool = False) -> str | None:
    """Verifica en Hacienda que la cédula (ya normalizada) sea física o DIMEX.

    Devuelve el mensaje de error, o None si se acepta. tolerant: una cédula que Hacienda
    no encuentra se acepta aunque HACIENDA_STRICT esté encendido.
    """
    if not config.hacienda_check_enabled():
        return None

    try:
        result = lookup_id(id_number)
    except HaciendaUnavailable:
        logger.warning("Hacienda no disponible; se acepta la cédula sin verificar")
        return None

    if result is None:
        if config.hacienda_strict() and not tolerant:
            return "La cédula no está registrada en Hacienda"
        return None

    expected = "01" if len(id_number) == 9 else "03"
    if result.get("tipo") != expected:
        return "La cédula no corresponde a una persona física o DIMEX"
    return None
