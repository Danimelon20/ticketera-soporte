import asyncio
import email
import logging
from datetime import datetime, timezone
from email import policy
from urllib.parse import quote, urlencode

from . import config, email_analysis, graph
from .database import SessionLocal
from .email_processing import process_message
from .graph import GraphAuthRequired

# Estado de la última revisión (se muestra en el panel del Administrador)
last_run: datetime | None = None
last_error: str | None = None
last_processed: int = 0

# Nombre de la carpeta -> id en Graph, para no buscarla en cada revisión
_folder_ids: dict[str, str] = {}

PAGE_SIZE = 25

# Nunca registrar en logs el remitente ni el contenido de los correos.


def _query(params: dict) -> str:
    return urlencode(params, quote_via=quote, safe="$")


def _find_folder(path: str, name: str) -> str | None:
    response = graph.graph_request("GET", f"{path}?{_query({'$select': 'id,displayName', '$top': 100})}")
    for folder in response.json().get("value", []):
        if (folder.get("displayName") or "").lower() == name.lower():
            return folder["id"]
    return None


def get_folder_id() -> str:
    name = config.MAIL_FOLDER
    if name.lower() == "inbox":
        return "inbox"
    if name in _folder_ids:
        return _folder_ids[name]

    folder_id = _find_folder("/me/mailFolders", name) or _find_folder("/me/mailFolders/inbox/childFolders", name)
    if folder_id is None:
        raise ValueError(f"No existe la carpeta {name} en el buzón")
    _folder_ids[name] = folder_id
    return folder_id


def _process_one(message_id: str) -> str:
    raw = graph.graph_request("GET", f"/me/messages/{message_id}/$value").content
    msg = email.message_from_bytes(raw, policy=policy.default)
    db = SessionLocal()
    try:
        return process_message(db, msg)
    finally:
        db.close()


def poll_once() -> int:
    global last_run, last_error, last_processed
    processed = 0
    try:
        folder_id = get_folder_id()
        params = {"$filter": "isRead eq false", "$select": "id", "$top": PAGE_SIZE}
        response = graph.graph_request("GET", f"/me/mailFolders/{folder_id}/messages?{_query(params)}")

        for item in response.json().get("value", []):
            message_id = item["id"]
            try:
                result = _process_one(message_id)
                logging.info("Correo procesado: %s", result)
                processed += 1
            except GraphAuthRequired:
                raise
            except Exception:
                logging.exception("No se pudo procesar un correo")
            # Leído aunque haya fallado, para no procesarlo en cada revisión
            graph.graph_request("PATCH", f"/me/messages/{message_id}", json={"isRead": True})
    except Exception as exc:
        last_run = datetime.now(timezone.utc)
        last_processed = processed
        last_error = str(exc)
        raise

    last_run = datetime.now(timezone.utc)
    last_processed = processed
    last_error = None
    return processed


async def poll_forever() -> None:
    global last_error
    auth_warned = False
    while True:
        try:
            await asyncio.to_thread(poll_once)
            auth_warned = False
        except GraphAuthRequired as exc:
            last_error = str(exc)
            # Un solo aviso hasta que vuelva a funcionar, no uno por revisión
            if not auth_warned:
                logging.warning("%s", exc)
                auth_warned = True
        except Exception as exc:
            last_error = str(exc)
            logging.exception("Falló la revisión del buzón")
        if config.AI_ANALYSIS_ENABLED:
            # Un error del análisis nunca detiene la revisión del correo
            try:
                await asyncio.to_thread(email_analysis.analyze_pending)
            except Exception:
                logging.exception("Falló el análisis de correos con IA")
            try:
                await asyncio.to_thread(email_analysis.analyze_pending_drafts)
            except Exception:
                logging.exception("Falló el análisis de los borradores con IA")
        await asyncio.sleep(config.EMAIL_POLL_SECONDS)
