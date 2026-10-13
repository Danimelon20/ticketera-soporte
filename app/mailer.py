import base64
import logging
from email.message import EmailMessage

from . import config, graph

SIGNATURE = "Agente virtual — Ticketera de Soporte"


def send_email(to: str, subject: str, body: str) -> bool:
    body = f"{body.rstrip()}\n\n{SIGNATURE}\n"
    if not config.EMAIL_ENABLED:
        logging.info("Correo no enviado (EMAIL_ENABLED=false): %s", subject)
        return False

    try:
        msg = EmailMessage()
        msg["From"] = config.SUPPORT_MAILBOX
        msg["To"] = to
        msg["Subject"] = subject
        # Evita que otros sistemas respondan automáticamente y se forme un bucle
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(body, charset="utf-8")

        response = graph.graph_request(
            "POST",
            "/me/sendMail",
            content=base64.b64encode(msg.as_bytes()),
            headers={"Content-Type": "text/plain"},
        )
        return response.status_code == 202
    except Exception:
        # Sin el cuerpo ni el destinatario: solo el tipo de error
        logging.exception("No se pudo enviar el correo")
        return False
