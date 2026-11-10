import base64
import logging
import smtplib
from contextlib import suppress
from email import policy
from email.message import EmailMessage

from . import config, graph

SIGNATURE = "Agente virtual — Ticketera de Soporte"
# Hasta este total, cada adjunto va en un POST; con más, por sesión de carga (límite de 4 MB por petición en Graph)
UPLOAD_SESSION_OVER_BYTES = 3 * 1024 * 1024
# Más que esto no sale por correo (notifications manda el enlace al portal en su lugar)
MAX_ATTACHMENTS_BYTES = 20 * 1024 * 1024


def send_email(to: str, subject: str, body: str, attachments=None, html: str | None = None) -> bool:
    """attachments: [(nombre, contenido, Content-Type)]. Nunca se registran sus nombres ni su contenido.

    html: versión HTML opcional del cuerpo (va junto al texto, como multipart/alternative).
    """
    body = f"{body.rstrip()}\n\n{SIGNATURE}\n"
    # .invalid nunca existe (RFC 2606): son las cuentas de prueba de scripts.demo_data. En el entorno de pruebas
    # con SMTP sí salen, porque el único destino posible es Mailpit (check_mail_config no deja arrancar con otro)
    if to.strip().lower().endswith(".invalid") and not _mailpit_catches_all():
        logging.info("Correo no enviado (dominio .invalid): %s", subject)
        return False
    if config.MAIL_TRANSPORT not in ("graph", "smtp"):
        if attachments:
            logging.info("Correo no enviado (MAIL_TRANSPORT=log): %s (con %d adjuntos)", subject, len(attachments))
        else:
            logging.info("Correo no enviado (MAIL_TRANSPORT=log): %s", subject)
        return False

    try:
        if config.MAIL_TRANSPORT == "smtp":
            return _send_smtp(_build_message(config.MAIL_FROM, to, subject, body, html), attachments)
        msg = _build_message(config.SUPPORT_MAILBOX, to, subject, body, html)
        mime = base64.b64encode(msg.as_bytes(policy=policy.SMTP))
        if attachments:
            return _send_with_attachments(mime, attachments)
        response = graph.graph_request(
            "POST",
            "/me/sendMail",
            content=mime,
            headers={"Content-Type": "text/plain"},
        )
        return response.status_code == 202
    except Exception:
        # Sin el cuerpo ni el destinatario: solo el tipo de error
        logging.exception("No se pudo enviar el correo")
        return False


def _build_message(sender: str, to: str, subject: str, body: str, html: str | None) -> EmailMessage:
    """El mismo mensaje para Graph y SMTP."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    # Evita que otros sistemas respondan automáticamente y se forme un bucle
    msg["Auto-Submitted"] = "auto-generated"
    # base64 y saltos \r\n: con quoted-printable y \n, Graph cambiaba letras por "="
    msg.set_content(body, charset="utf-8", cte="base64")
    if html:
        msg.add_alternative(html, subtype="html", charset="utf-8", cte="base64")
    return msg


def _send_smtp(msg: EmailMessage, attachments) -> bool:
    """Envío por SMTP sin TLS ni usuario (pensado para Mailpit dentro de la red de Docker)."""
    if attachments:
        if sum(len(data) for _, data, _ in attachments) > MAX_ATTACHMENTS_BYTES:
            logging.warning("Correo no enviado: los adjuntos pasan de 20 MB")
            return False
        for name, data, content_type in attachments:
            maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_SECONDS) as server:
        server.send_message(msg)
    return True


def _mailpit_catches_all() -> bool:
    return config.APP_ENV == "pruebas" and config.MAIL_TRANSPORT == "smtp"


def check_mail_config() -> None:
    """Se llama al arrancar: con una configuración peligrosa la app no arranca."""
    if config.MAIL_TRANSPORT not in config.MAIL_TRANSPORTS:
        raise RuntimeError(
            f"MAIL_TRANSPORT={config.MAIL_TRANSPORT} no es válido: use graph, smtp o log."
        )
    if config.APP_ENV != "pruebas":
        return
    if config.MAIL_TRANSPORT == "graph":
        raise RuntimeError(
            "APP_ENV=pruebas con MAIL_TRANSPORT=graph: el entorno de pruebas no puede enviar por Microsoft Graph "
            "(saldrían correos reales). Use MAIL_TRANSPORT=smtp con Mailpit o MAIL_TRANSPORT=log."
        )
    if config.MAIL_TRANSPORT == "smtp" and config.SMTP_HOST.strip().lower() not in config.SMTP_ALLOWED_HOSTS:
        raise RuntimeError(
            f"APP_ENV=pruebas: SMTP_HOST={config.SMTP_HOST} no está en SMTP_ALLOWED_HOSTS "
            f"({', '.join(config.SMTP_ALLOWED_HOSTS) or 'vacío'}). El entorno de pruebas solo envía a Mailpit "
            "para que ningún correo salga a internet."
        )
    if config.MAILBOX_POLL_ENABLED:
        raise RuntimeError(
            "APP_ENV=pruebas con MAILBOX_POLL_ENABLED=true: la lectura del buzón usa Microsoft Graph y marcaría "
            "como leídos los correos reales de soporte. Use MAILBOX_POLL_ENABLED=false."
        )


def describe_mail_config() -> str:
    """Configuración efectiva para el log de arranque: sin buzones, remitentes ni secretos."""
    transport = config.MAIL_TRANSPORT
    if transport == "smtp":
        transport = f"smtp ({config.SMTP_HOST}:{config.SMTP_PORT})"
    return (f"Correo: transporte={transport}, lectura del buzón={'sí' if config.MAILBOX_POLL_ENABLED else 'no'}, "
            f"entorno={config.APP_ENV or 'normal'}")


def _send_with_attachments(mime: bytes, attachments) -> bool:
    """El mismo MIME se crea como borrador en Graph, se le agregan los archivos y se envía.

    Un solo sendMail con todo adentro (base64 sobre base64) pasa del límite de 4 MB de Graph.
    """
    total = sum(len(data) for _, data, _ in attachments)
    if total > MAX_ATTACHMENTS_BYTES:
        logging.warning("Correo no enviado: los adjuntos pasan de 20 MB")
        return False
    draft = graph.graph_request("POST", "/me/messages", content=mime, headers={"Content-Type": "text/plain"})
    message_id = draft.json()["id"]
    try:
        for name, data, content_type in attachments:
            # Con más de 3 MB en total, los archivos de 3 MB o más van por sesión de carga
            if total > UPLOAD_SESSION_OVER_BYTES and len(data) >= UPLOAD_SESSION_OVER_BYTES:
                session = graph.graph_request(
                    "POST",
                    f"/me/messages/{message_id}/attachments/createUploadSession",
                    json={"AttachmentItem": {
                        "attachmentType": "file", "name": name, "size": len(data), "contentType": content_type,
                    }},
                )
                graph.upload_to_session(session.json()["uploadUrl"], data)
            else:
                graph.graph_request("POST", f"/me/messages/{message_id}/attachments", json={
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": name,
                    "contentType": content_type,
                    "contentBytes": base64.b64encode(data).decode("ascii"),
                })
        response = graph.graph_request("POST", f"/me/messages/{message_id}/send")
        return response.status_code == 202
    except Exception:
        # Que no quede un borrador a medias en el buzón
        with suppress(Exception):
            graph.graph_request("DELETE", f"/me/messages/{message_id}")
        raise
