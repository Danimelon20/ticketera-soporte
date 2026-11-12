"""Simula un correo que llega al buzón de soporte, sin leer el buzón real. Solo para el entorno de pruebas.

Uso (entorno de pruebas encendido):
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.simulate_email \\
        --from empleado@demo.invalid --subject "Sin internet" --body "Desde las 8 no tengo internet"
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.simulate_email --file /ruta/correo.eml

Arma el mismo mensaje que llegaría del buzón y lo procesa con process_message (el mismo camino que la lectura
del buzón): crea el borrador, lo analiza con la IA y, con «Tickets por departamento» encendido, lo clasifica.
Las respuestas automáticas («Recibimos su correo») salen a Mailpit.

Nunca toca Microsoft Graph: solo corre con APP_ENV=pruebas, MAILBOX_POLL_ENABLED=false y MAIL_TRANSPORT distinto
de graph. Si falta una de las tres, termina sin tocar la base.
"""
import argparse
import sys
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid

from app import config, draft_classification, email_analysis, models, routing
from app.database import SessionLocal
from app.email_processing import _message_id, process_message

DOMAIN = "pruebas.local"


def unsafe_reason() -> str | None:
    """Por qué no se puede simular aquí (None: se puede)."""
    if config.APP_ENV != "pruebas":
        return "solo corre en el entorno de pruebas (APP_ENV=pruebas)"
    if config.MAILBOX_POLL_ENABLED:
        return "MAILBOX_POLL_ENABLED=true: este entorno lee un buzón real"
    if config.MAIL_TRANSPORT == "graph":
        return "MAIL_TRANSPORT=graph: este entorno envía correos reales"
    return None


def build_message(sender: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = config.SUPPORT_MAILBOX or f"soporte@{DOMAIN}"
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=DOMAIN)
    msg.set_content(body)
    return msg


def read_file(path: str) -> EmailMessage:
    with open(path, "rb") as handle:
        return BytesParser(policy=policy.default).parse(handle)


def _report(db, draft_id: int) -> None:
    draft = db.get(models.EmailDraft, draft_id)
    print(f"Borrador #{draft.id}: {draft.status}")
    row = draft_classification.row_of(db, draft.id)
    if row is None:
        if routing.enforced(db):
            print("Sin clasificación (no es un pedido de soporte: no se clasifica)")
        else:
            print("Tickets por departamento apagado: el borrador sigue como antes (sin clasificar)")
        return
    by_id = routing.departments_by_id(db)
    name = lambda dept_id: routing.path_label(by_id.get(dept_id), by_id) or "—"  # noqa: E731
    if row.status == "routed":
        print(f"Clasificado por la IA: {name(row.department_id)} (va a su coordinador)")
    else:
        print(f"Por clasificar: {models.DRAFT_PENDING_REASONS.get(row.pending_reason, row.pending_reason)}")
    margin = "—" if row.ai_margin is None else f"{row.ai_margin:.3f}"
    print(f"IA: {name(row.ai_department_id)} · margen {margin} · umbral {row.threshold}")
    print(f"Opciones: 1.ª {name(row.option1_id)} · 2.ª {name(row.option2_id)}")
    if row.ai_reason:
        print(f"Motivo: {row.ai_reason}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Simula un correo entrante (solo en el entorno de pruebas)")
    parser.add_argument("--from", dest="sender", help="Correo de un solicitante registrado y activo")
    parser.add_argument("--subject", default="", help="Asunto")
    parser.add_argument("--body", help="Texto del correo")
    parser.add_argument("--file", help="Archivo .eml en lugar de --from/--subject/--body")
    parser.add_argument("--no-analyze", action="store_true", help="Solo crear el borrador, sin la IA")
    args = parser.parse_args(argv)

    reason = unsafe_reason()
    if reason:
        print(f"No se simuló nada: {reason}.")
        return 1
    if args.file:
        msg = read_file(args.file)
    elif args.sender and args.body:
        msg = build_message(args.sender, args.subject, args.body)
    else:
        parser.error("use --from y --body, o --file")
    if not config.AI_ANALYSIS_ENABLED:
        print("Aviso: AI_ANALYSIS_ENABLED=false: los correos sin plantilla no se vuelven borradores.")

    db = SessionLocal()
    try:
        result = process_message(db, msg)
        print(f"Resultado: {result}")
        draft = db.query(models.EmailDraft).filter(models.EmailDraft.message_id == _message_id(msg)).first() \
            if result == "draft_created" else None
        draft_id = draft.id if draft else None
    finally:
        db.close()
    if draft_id is None:
        if result == "unregistered":
            print("El remitente no es un solicitante registrado y activo.")
        return 0 if result != "unregistered" else 1
    if not args.no_analyze:
        # En pruebas no corre la revisión del buzón: el análisis de los borradores se hace aquí
        email_analysis.analyze_pending_drafts(limit=50)
    db = SessionLocal()
    try:
        _report(db, draft_id)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
