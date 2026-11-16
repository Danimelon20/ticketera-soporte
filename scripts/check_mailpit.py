"""Comprueba de punta a punta que el entorno de pruebas entrega sus correos a Mailpit. No es parte de pytest.

Uso (con el entorno de pruebas encendido):
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.check_mailpit

Envía con mailer.send_email (el mismo camino que invitaciones, códigos y avisos) un correo con HTML y un
adjunto a una dirección .local, y consulta la API de Mailpit hasta encontrarlo. Solo corre con
APP_ENV=pruebas y MAIL_TRANSPORT=smtp: nunca envía nada por Microsoft Graph.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

from app import config, mailer

MAILPIT_URL = os.getenv("MAILPIT_URL", "http://mailpit:8025").rstrip("/")
TO = "prueba@pruebas.local"
ATTACHMENT = ("prueba-mailpit.txt", "Adjunto de prueba de la Ticketera\n".encode("utf-8"), "text/plain")
WAIT_SECONDS = 10


def _find(token: str) -> dict | None:
    query = urllib.parse.urlencode({"query": f'subject:"{token}"'})
    with urllib.request.urlopen(f"{MAILPIT_URL}/api/v1/search?{query}", timeout=5) as response:
        messages = json.load(response).get("messages") or []
    return messages[0] if messages else None


def main() -> int:
    if config.APP_ENV != "pruebas" or config.MAIL_TRANSPORT != "smtp":
        print(f"Solo para el entorno de pruebas con Mailpit (APP_ENV={config.APP_ENV or '(vacío)'}, "
              f"MAIL_TRANSPORT={config.MAIL_TRANSPORT}). No se envió nada.")
        return 1
    # La misma protección del arranque: nunca a un servidor que no sea Mailpit
    try:
        mailer.check_mail_config()
    except RuntimeError as exc:
        print(exc)
        return 1

    token = uuid.uuid4().hex[:12]
    subject = f"Prueba de Mailpit {token}"
    print(f"Enviando «{subject}» a {TO} por {config.SMTP_HOST}:{config.SMTP_PORT}...")
    if not mailer.send_email(TO, subject, "Correo de prueba de la Ticketera.",
                             attachments=[ATTACHMENT], html="<p>Correo de prueba de la <b>Ticketera</b>.</p>"):
        print("La ticketera no pudo enviar el correo: revise los logs de app y que mailpit esté encendido.")
        return 1

    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            message = _find(token)
        except OSError as exc:
            print(f"No se pudo consultar la API de Mailpit en {MAILPIT_URL}: {exc}")
            return 1
        if message or time.monotonic() > deadline:
            break
        time.sleep(1)

    if not message:
        print(f"El correo no apareció en Mailpit después de {WAIT_SECONDS} segundos.")
        return 1
    if message.get("Attachments", 0) < 1:
        print("El correo llegó a Mailpit, pero sin el adjunto.")
        return 1
    print(f"Llegó a Mailpit con {message['Attachments']} adjunto(s). Véalo en http://127.0.0.1:8025")
    return 0


if __name__ == "__main__":
    sys.exit(main())
