import json
import logging
import urllib.error
import urllib.request

from . import config

HACIENDA_URL = "https://api.hacienda.go.cr/fe/ae?identificacion={}"
TIMEOUT_SECONDS = 5

# Caché en memoria: cédula -> {"tipo": ...} o None. Nunca se guarda el nombre.
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
    result = {"tipo": data.get("tipoIdentificacion")}
    _cache[id_number] = result
    return result


def check_id_number(id_number: str) -> str | None:
    """Verifica en Hacienda que la cédula (ya normalizada) sea física o DIMEX.

    Devuelve el mensaje de error, o None si se acepta.
    """
    if not config.hacienda_check_enabled():
        return None

    try:
        result = lookup_id(id_number)
    except HaciendaUnavailable:
        logger.warning("Hacienda no disponible; se acepta la cédula sin verificar")
        return None

    if result is None:
        if config.hacienda_strict():
            return "La cédula no está registrada en Hacienda"
        return None

    expected = "01" if len(id_number) == 9 else "03"
    if result.get("tipo") != expected:
        return "La cédula no corresponde a una persona física o DIMEX"
    return None
