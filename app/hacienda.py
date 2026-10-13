import json
import urllib.error
import urllib.request

HACIENDA_URL = "https://api.hacienda.go.cr/fe/ae?identificacion={}"
TIMEOUT_SECONDS = 5

# Caché en memoria: cédula -> {"tipo": ...} o None. Nunca se guarda el nombre.
_cache: dict[str, dict | None] = {}


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
