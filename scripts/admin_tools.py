"""Gestión de cuentas de administrador, solo desde el servidor (la página nunca actúa sobre un administrador).

Uso:
    docker compose exec -it app python -m scripts.admin_tools deactivate --email persona@empresa.com
    docker compose exec -it app python -m scripts.admin_tools reactivate --email persona@empresa.com
    docker compose exec -it app python -m scripts.admin_tools demote --email persona@empresa.com [--to coordinator]
    docker compose exec -it app python -m scripts.admin_tools reset-password --email persona@empresa.com
    docker compose exec -it app python -m scripts.admin_tools list-admins

Pide escribir el correo para confirmar, nunca deja el sistema sin un administrador activo, cierra las
sesiones de la cuenta y queda en el registro de cambios como hecho por "Servidor".
Solo puede haber un administrador activo: reactivate se niega si ya hay otro. Si hay varios (bases
anteriores a esta regla), list-admins los muestra y se corrige a mano con demote o deactivate.
reset-password es la recuperación del administrador que perdió el acceso: genera una contraseña nueva, la
muestra una sola vez y quita el bloqueo por intentos fallidos.
list-admins solo lista, con el correo completo (hace falta para los demás comandos).

Solicitantes con correo de un dominio interno (INTERNAL_DOMAINS):
    docker compose exec -it app python -m scripts.admin_tools internal-requesters
    docker compose exec -it app python -m scripts.admin_tools delete-requester --email persona@empresa.com
    docker compose exec -it app python -m scripts.admin_tools archive-requester --email persona@empresa.com

internal-requesters solo lista. delete-requester borra la cuenta solo si es de solicitante y nunca tuvo
tickets, comentarios, borradores ni otra actividad; pide escribir el correo y queda en el registro de cambios.
archive-requester es para el solicitante que sí tiene actividad: desactiva la cuenta, cierra sus sesiones y
cambia su correo por archivado-<id>@archivo.invalid para liberar el original. Conserva tickets, comentarios
y reportes; pide escribir el correo y el registro de cambios guarda el correo original.
"""
import argparse
import sys

from sqlalchemy import func, or_

from app import accounts, audit, config, models, throttle
from app.database import SessionLocal
from app.main import init_db
from app.security import generate_password, hash_password, mask_email
from app.validators import is_internal_email

DEMOTE_ROLES = ("coordinator", "technician", "requester")
ADMIN_COMMANDS = ("deactivate", "reactivate", "demote", "reset-password")
ONE_ADMIN = "Ya hay un administrador activo y solo puede haber uno. Desactívelo o cámbiele el rol primero."
# Columnas que apuntan a la cuenta y se borran con ella: sesiones, códigos pendientes y datos propios
DELETED_WITH_ACCOUNT = {
    ("sessions", "user_id"), ("login_challenges", "user_id"), ("password_resets", "user_id"),
    ("email_changes", "user_id"), ("email_change_attempts", "user_id"), ("requester_identities", "user_id"),
    ("ticket_followers", "user_id"), ("unclear_notices", "user_id"), ("overdue_notices_seen", "user_id"),
    ("user_microsoft_links", "user_id"), ("microsoft_login_handoffs", "user_id"),
    ("auto_report_id_owners", "owner_user_id"), ("availability_periods", "user_id"),
    ("technician_availability", "user_id"), ("technician_schedules", "user_id"),
}
# Las que se informan por nombre al negarse
COUNTED = {("tickets", "created_by"), ("tickets", "assigned_to"), ("comments", "user_id"), ("email_drafts", "requester_id")}
ARCHIVE_DOMAIN = "archivo.invalid"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.admin_tools", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=ADMIN_COMMANDS + ("list-admins", "internal-requesters", "delete-requester", "archive-requester"))
    parser.add_argument("--email", help="Correo de la cuenta (todos los comandos menos list-admins e internal-requesters)")
    parser.add_argument("--to", choices=DEMOTE_ROLES, default="coordinator",
                        help="Solo con demote: rol nuevo (por defecto, coordinator)")
    return parser


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def run(command: str, email: str, new_role: str = "coordinator", confirm=input) -> int:
    """Devuelve 0 si se hizo el cambio y 1 si no se hizo nada."""
    email = email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
        if user is None:
            return _fail("No hay ninguna cuenta con ese correo")
        if user.role != "admin":
            return _fail("Esa cuenta no es de administrador. Gestiónela desde la página (Cuentas).")
        if command == "deactivate" and not user.is_active:
            return _fail("La cuenta ya está desactivada")
        if command == "reactivate" and user.is_active:
            return _fail("La cuenta ya está activa")
        if command == "reactivate" and accounts.active_admins(db) > 0:
            return _fail(ONE_ADMIN)
        if command == "reset-password" and not user.is_active:
            return _fail("La cuenta está desactivada: reactívela primero con reactivate")
        # Desactivar o quitar el rol a un administrador activo no puede dejar el sistema sin ninguno
        if command in ("deactivate", "demote") and user.is_active and accounts.active_admins(db, excluding=user.id) == 0:
            return _fail(accounts.LAST_ADMIN)

        label = {"deactivate": "desactivar", "reactivate": "reactivar",
                 "demote": f"cambiar a {audit.role_name(new_role)}",
                 "reset-password": "restablecer la contraseña de"}[command]
        typed = confirm(f"Va a {label} la cuenta de administrador {user.name or email}. Escriba el correo para confirmar: ")
        if typed.strip().lower() != email:
            return _fail("El correo no coincide. No se hizo ningún cambio.")

        password = None
        if command == "demote":
            audit.record(db, None, "server_role_changed", target=user,
                         old=audit.role_name(user.role), new=audit.role_name(new_role))
            user.role = new_role
        elif command == "reset-password":
            password = generate_password()
            user.password_hash = hash_password(password)
            # Los códigos pendientes y el bloqueo por intentos fallidos dejan de valer
            db.query(models.PasswordReset).filter(models.PasswordReset.user_id == user.id).delete()
            throttle.reset(db, email)
            audit.record(db, None, "server_password_reset", target=user)
        else:
            active = command == "reactivate"
            audit.record(db, None, "server_account_activated" if active else "server_account_deactivated", target=user)
            user.is_active = active
        accounts.close_sessions(db, user.id)
        db.commit()
        if password is not None:
            # Solo en esta pantalla: no se guarda ni va a los registros
            print(f"Listo. Contraseña nueva de {email} (se muestra una sola vez): {password}")
            print("Se cerraron sus sesiones. Al entrar, cámbiela desde Mi cuenta.")
            return 0
        done = {"deactivate": "desactivada", "reactivate": "reactivada",
                "demote": f"como {audit.role_name(new_role)}"}[command]
        print(f"Listo. La cuenta {email} quedó {done} y se cerraron sus sesiones.")
        return 0
    finally:
        db.close()


def list_admins() -> int:
    """Cuentas de administrador, activas e inactivas, con el correo completo. Solo lectura."""
    db = SessionLocal()
    try:
        admins = db.query(models.User).filter(models.User.role == "admin").order_by(models.User.id).all()
        if not admins:
            print("No hay cuentas de administrador. Cree una con: python -m app.create_admin")
            return 0
        for user in admins:
            state = "activo" if user.is_active else "inactivo"
            created = user.created_at.strftime("%Y-%m-%d") if user.created_at else "-"
            print(f"{user.id}\t{user.name or '-'}\t{user.email}\t{state}\t{created}")
        active = sum(1 for user in admins if user.is_active)
        if active > 1:
            print(f"\nAVISO: hay {active} administradores activos y solo debe haber uno. "
                  "Deje uno y corrija los demás con demote (o deactivate).")
        return 0
    finally:
        db.close()


def internal_requesters() -> int:
    """Lista los solicitantes con correo de un dominio interno. Solo lectura."""
    if not config.INTERNAL_DOMAINS:
        print("INTERNAL_DOMAINS está vacía: no hay dominios internos configurados.")
        return 0
    db = SessionLocal()
    try:
        users = [u for u in db.query(models.User).filter(models.User.role == "requester").order_by(models.User.id)
                 if is_internal_email(u.email)]
        if not users:
            print("No hay solicitantes con correo de un dominio interno.")
            return 0
        print("id\tnombre\tcorreo\tactiva\tcreada (UTC)\ttickets")
        for u in users:
            tickets = db.query(models.Ticket).filter(models.Ticket.created_by == u.id).count()
            created = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—"
            print(f"{u.id}\t{u.name or '—'}\t{u.email}\t{'sí' if u.is_active else 'no'}\t{created}\t{tickets}")
        return 0
    finally:
        db.close()


def _references(db, user_id: int) -> list[tuple]:
    """(tabla, columna, cantidad) de cada fila de otra tabla que apunta a la cuenta."""
    found = []
    for table in models.Base.metadata.sorted_tables:
        for column in table.columns:
            if any(fk.column.table.name == "users" for fk in column.foreign_keys):
                count = db.query(table).filter(column == user_id).count()
                if count:
                    found.append((table, column, count))
    return found


def delete_requester(email: str, confirm=input) -> int:
    """Borra una cuenta de solicitante sin actividad. Devuelve 0 si la borró y 1 si no hizo nada."""
    email = email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
        if user is None:
            return _fail("No hay ninguna cuenta con ese correo")
        if user.role != "requester":
            return _fail(f"Esa cuenta es de {audit.role_name(user.role)}, no de solicitante. No se borró.")

        tickets = db.query(models.Ticket).filter(
            or_(models.Ticket.created_by == user.id, models.Ticket.assigned_to == user.id)).count()
        comments = db.query(models.Comment).filter(models.Comment.user_id == user.id).count()
        drafts = db.query(models.EmailDraft).filter(models.EmailDraft.requester_id == user.id).count()
        refs = _references(db, user.id)
        others = [f"{t.name}.{c.name}: {n}" for t, c, n in refs
                  if (t.name, c.name) not in DELETED_WITH_ACCOUNT | COUNTED and t.name != "account_changes"]
        if tickets or comments or drafts or others:
            message = f"La cuenta tiene actividad y no se borró. Tickets: {tickets}, comentarios: {comments}, borradores: {drafts}."
            if others:
                message += " Otros registros: " + ", ".join(others) + "."
            return _fail(message + " Para retirarla conservando todo, use archive-requester.")

        typed = confirm(f"Va a BORRAR la cuenta de solicitante {user.name or email}. Escriba el correo para confirmar: ")
        if typed.strip().lower() != email:
            return _fail("El correo no coincide. No se hizo ningún cambio.")

        label = mask_email(user.email)
        label = f"{user.name} ({label})"[:150] if user.name else label
        for table, column, _count in refs:
            if table.name == "account_changes":
                # El historial se conserva: sin el id de la cuenta, con su nombre y correo enmascarado
                if column.name == "target_user_id":
                    db.execute(table.update().where(column == user.id, table.c.target_label.is_(None))
                               .values(target_label=label))
                db.execute(table.update().where(column == user.id).values({column.name: None}))
            else:
                db.execute(table.delete().where(column == user.id))
        db.query(models.PendingSignup).filter(models.PendingSignup.email == email).delete()
        db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).delete()
        db.delete(user)
        audit.record(db, None, "server_account_deleted", target_label=label, old=audit.role_name("requester"))
        db.commit()
        print(f"Listo. La cuenta {email} se borró junto con sus sesiones y códigos pendientes.")
        return 0
    finally:
        db.close()


def archive_requester(email: str, confirm=input) -> int:
    """Archiva una cuenta de solicitante con actividad. Devuelve 0 si la archivó y 1 si no hizo nada."""
    email = email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
        if user is None:
            return _fail("No hay ninguna cuenta con ese correo")
        if user.role != "requester":
            return _fail(f"Esa cuenta es de {audit.role_name(user.role)}, no de solicitante. No se archivó.")
        if email.endswith("@" + ARCHIVE_DOMAIN):
            return _fail("La cuenta ya está archivada")

        typed = confirm(f"Va a ARCHIVAR la cuenta de solicitante {user.name or email}. Escriba el correo para confirmar: ")
        if typed.strip().lower() != email:
            return _fail("El correo no coincide. No se hizo ningún cambio.")

        archived = f"archivado-{user.id}@{ARCHIVE_DOMAIN}"
        # El correo original queda en claro en el registro: es la constancia de a quién pertenecía la cuenta
        audit.record(db, None, "server_account_archived", target=user, old=user.email, new=archived)
        user.email = archived
        user.is_active = False
        accounts.close_sessions(db, user.id)
        # El correo original queda libre: sin registros pendientes ni intentos fallidos que hereden otra cuenta
        db.query(models.PendingSignup).filter(models.PendingSignup.email == email).delete()
        db.query(models.LoginThrottle).filter(models.LoginThrottle.email == email).delete()
        db.commit()
        print(f"Listo. La cuenta {email} quedó desactivada y archivada como {archived}; se cerraron sus sesiones.")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None, confirm=input) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command not in ("internal-requesters", "list-admins") and not args.email:
        parser.error(f"{args.command} necesita --email")
    init_db()
    if args.command == "list-admins":
        return list_admins()
    if args.command == "internal-requesters":
        return internal_requesters()
    if args.command == "delete-requester":
        return delete_requester(args.email, confirm)
    if args.command == "archive-requester":
        return archive_requester(args.email, confirm)
    return run(args.command, args.email, args.to, confirm)


if __name__ == "__main__":
    sys.exit(main())
