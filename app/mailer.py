import base64
import logging
from contextlib import suppress
from email import policy
from email.message import EmailMessage

from . import config, graph

SIGNATURE = "Agente virtual — Ticketera de Soporte"
# Hasta este total, cada adjunto va en un POST; con más, por sesión de carga (límite de 4 MB por petición en Graph)
UPLOAD_SESSION_OVER_BYTES = 3 * 1024 * 1024
# Más que esto no sale por correo (notifications manda el enlace al portal en su lugar)
MAX_ATTACHMENTS_BYTES = 20 * 1024 * 1024


def send_email(to: str, subject: str, body: str, attachments=None) -> bool:
    """attachments: [(nombre, contenido, Content-Type)]. Nunca se registran sus nombres ni su contenido."""
    body = f"{body.rstrip()}\n\n{SIGNATURE}\n"
    if not config.EMAIL_ENABLED:
        if attachments:
            logging.info("Correo no enviado (EMAIL_ENABLED=false): %s (con %d adjuntos)", subject, len(attachments))
        else:
            logging.info("Correo no enviado (EMAIL_ENABLED=false): %s", subject)
        return False

    try:
        msg = EmailMessage()
        msg["From"] = config.SUPPORT_MAILBOX
        msg["To"] = to
        msg["Subject"] = subject
        # Evita que otros sistemas respondan automáticamente y se forme un bucle
        msg["Auto-Submitted"] = "auto-generated"
        # base64 y saltos \r\n: con quoted-printable y \n, Graph cambiaba letras por "="
        msg.set_content(body, charset="utf-8", cte="base64")

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
