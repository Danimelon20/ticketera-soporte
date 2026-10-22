import os

import httpx
import msal

from . import config

SCOPES = ["Mail.ReadWrite", "Mail.Send"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEOUT_SECONDS = 30
# Partes de una sesión de carga: múltiplo de 320 KiB y menos de 4 MB
UPLOAD_CHUNK_BYTES = 320 * 1024 * 12
LOGIN_REQUIRED_MESSAGE = (
    "Falta iniciar sesión en Microsoft: ejecuta docker compose exec -it app python -m app.ms_login"
)

# Nunca registrar en logs el token, el contenido del caché ni el cuerpo de los correos.


class GraphAuthRequired(Exception):
    """No hay una sesión de Microsoft válida para el buzón de soporte."""


class GraphError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"Graph respondió {status_code}: {message}")
        self.status_code = status_code
        self.message = message


def load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(config.MS_TOKEN_CACHE):
        with open(config.MS_TOKEN_CACHE, encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache


def save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    path = config.MS_TOKEN_CACHE
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(cache.serialize())
    os.chmod(path, 0o600)


def build_app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        config.MS_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
        token_cache=cache,
    )


def get_token() -> str:
    # Sin caché no hay sesión: se evita construir msal (y cualquier llamada de red)
    if not os.path.exists(config.MS_TOKEN_CACHE):
        raise GraphAuthRequired(LOGIN_REQUIRED_MESSAGE)

    cache = load_cache()
    app = build_app(cache)
    account = next(
        (a for a in app.get_accounts() if (a.get("username") or "").lower() == config.SUPPORT_MAILBOX),
        None,
    )
    if account is None:
        raise GraphAuthRequired(LOGIN_REQUIRED_MESSAGE)

    result = app.acquire_token_silent(SCOPES, account=account)
    save_cache(cache)
    if not result or "access_token" not in result:
        raise GraphAuthRequired(LOGIN_REQUIRED_MESSAGE)
    return result["access_token"]


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason_phrase or "Error desconocido"


def graph_request(method, path, json=None, content=None, headers=None) -> httpx.Response:
    url = f"{GRAPH_BASE}{path}"
    response = None
    for _attempt in range(2):
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {get_token()}"
        response = httpx.request(
            method,
            url,
            json=json,
            content=content,
            headers=request_headers,
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 401:
            break
    if not 200 <= response.status_code < 300:
        raise GraphError(response.status_code, _error_message(response))
    return response


def upload_to_session(upload_url: str, data: bytes) -> None:
    """Sube un adjunto por partes a la uploadUrl de una sesión de carga.

    La uploadUrl ya trae su autorización: no se le manda el token.
    """
    total = len(data)
    start = 0
    while start < total:
        chunk = data[start:start + UPLOAD_CHUNK_BYTES]
        end = start + len(chunk) - 1
        response = httpx.put(
            upload_url,
            content=chunk,
            headers={"Content-Range": f"bytes {start}-{end}/{total}", "Content-Type": "application/octet-stream"},
            timeout=TIMEOUT_SECONDS,
        )
        if not 200 <= response.status_code < 300:
            raise GraphError(response.status_code, _error_message(response))
        start = end + 1
