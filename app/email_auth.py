"""¿El correo viene de verdad de quien dice el «De:»? El «De:» se puede falsificar; Exchange Online deja marcas que no.

El buzón se lee con Microsoft Graph en MIME completo (/messages/{id}/$value), con todas las cabeceras que puso
Exchange Online al recibir el correo:

- X-MS-Exchange-Organization-AuthAs: «Internal» cuando el remitente se autenticó dentro de la organización (un
  empleado que escribe desde su buzón). Exchange quita las cabeceras X-MS-Exchange-Organization-* que llegan
  de afuera (header firewall) y pone las suyas, así que un correo externo no puede traerla falsificada.
- Authentication-Results: resultados de SPF, DKIM y DMARC (y compauth, el resultado compuesto de Microsoft) para
  el correo que llega de internet. Las cabeceras nuevas van arriba: la primera es la de Microsoft al recibirlo;
  las de más abajo pudo escribirlas el remitente y no se usan.

Se acepta si es interno, o si la primera Authentication-Results dice dmarc=pass o compauth=pass para el mismo
dominio del «De:». Con EMAIL_SENDER_CHECK=domain no se revisa (solo el dominio), por ejemplo si el buzón no está
en Exchange Online. simulate_email no pasa por aquí.
"""
import re

from . import config

_HEADER_FROM_RE = re.compile(r"\bheader\.from=([^\s;]+)", re.IGNORECASE)
_PASS_RE = re.compile(r"\b(dmarc|compauth)=pass\b", re.IGNORECASE)


def is_internal(msg) -> bool:
    values = [str(v).strip().lower() for v in (msg.get_all("X-MS-Exchange-Organization-AuthAs") or [])]
    return bool(values) and all(v == "internal" for v in values)


def passes_authentication(msg, sender: str) -> bool:
    results = msg.get_all("Authentication-Results") or []
    if not results:
        return False
    first = str(results[0])
    domain = sender.rpartition("@")[2].lower()
    header_from = _HEADER_FROM_RE.search(first)
    if header_from is None or header_from.group(1).lower().rstrip(".") != domain:
        return False
    return _PASS_RE.search(first) is not None


def sender_verified(msg, sender: str) -> bool:
    """True si el remitente es auténtico (o si la revisión está apagada con EMAIL_SENDER_CHECK=domain)."""
    if config.EMAIL_SENDER_CHECK == "domain":
        return True
    return is_internal(msg) or passes_authentication(msg, sender)
