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

Archivar las cuentas de cliente al quitar el portal (todas las de solicitante que no son de INTERNAL_DOMAINS):
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.admin_tools archive-clients
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.admin_tools archive-clients --apply
  En la instancia real, el mismo comando con el compose de la real (sin -f).

  POR DEFECTO SOLO MUESTRA qué cambiaría (cuántas cuentas, correos enmascarados, tickets abiertos y sesiones
  abiertas) y no cambia nada. SOLO --apply CAMBIA: marca esas cuentas como archivadas y lo deja en el registro
  de cambios como hecho por "Servidor". No borra nada: ni cuentas, ni tickets, ni sesiones; no cambia el correo
  ni desactiva la cuenta (sigue recibiendo los correos de su ticket abierto hasta que se cierre). El acceso ya
  está cerrado por el rol: un contacto nunca inicia sesión. Volver a correrlo no repite las ya archivadas.
"""
import argparse
import sys

from sqlalchemy import func

from app import accounts, audit, models, throttle
from app.database import SessionLocal
from app.main import init_db
from app.security import generate_password, hash_password, mask_email
from app.validators import is_internal_email

DEMOTE_ROLES = ("coordinator", "technician", "requester")
ADMIN_COMMANDS = ("deactivate", "reactivate", "demote", "reset-password")
ONE_ADMIN = "Ya hay un administrador activo y solo puede haber uno. Desactívelo o cámbiele el rol primero."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.admin_tools", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=ADMIN_COMMANDS + ("list-admins", "archive-clients"))
    parser.add_argument("--email", help="Correo de la cuenta (deactivate, reactivate, demote y reset-password)")
    parser.add_argument("--to", choices=DEMOTE_ROLES, default="coordinator",
                        help="Solo con demote: rol nuevo (por defecto, coordinator)")
    parser.add_argument("--apply", action="store_true",
                        help="Solo con archive-clients: SIN --apply solo muestra; CON --apply archiva")
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


def _clients_to_archive(db) -> list[models.User]:
    """Cuentas de cliente (solicitante de otro dominio que INTERNAL_DOMAINS) que todavía no están archivadas."""
    archived = db.query(models.ArchivedAccount.user_id)
    users = (db.query(models.User).filter(models.User.role == "requester", models.User.id.notin_(archived))
             .order_by(models.User.id).all())
    return [u for u in users if not is_internal_email(u.email)]


def archive_clients(apply: bool = False) -> int:
    """Sin apply solo muestra; con apply las archiva. Devuelve 0 (también si no hay nada que archivar)."""
    db = SessionLocal()
    try:
        users = _clients_to_archive(db)
        if not users:
            print("No hay cuentas de cliente por archivar.")
            return 0
        ids = [u.id for u in users]
        open_tickets = dict(db.query(models.Ticket.created_by, func.count(models.Ticket.id)).filter(
            models.Ticket.created_by.in_(ids), models.Ticket.status.in_(accounts.OPEN_STATUSES))
            .group_by(models.Ticket.created_by).all())
        sessions = dict(db.query(models.Session.user_id, func.count(models.Session.id)).filter(
            models.Session.user_id.in_(ids)).group_by(models.Session.user_id).all())
        print(("Se archivan" if apply else "MODO DE PRUEBA (no cambia nada). Se archivarían")
              + f" {len(users)} cuenta(s) de cliente:")
        for u in users:
            print(f"  #{u.id} {mask_email(u.email)} · {open_tickets.get(u.id, 0)} ticket(s) abierto(s) · "
                  f"{sessions.get(u.id, 0)} sesión(es) abierta(s){'' if u.is_active else ' · desactivada'}")
        total_open = sum(open_tickets.values())
        if total_open:
            print(f"Sus {total_open} ticket(s) abierto(s) siguen abiertos y los atiende el personal; si alguno ya no "
                  "aplica, el coordinador lo cancela con «Ya no aplica».")
        if not apply:
            print("No se cambió nada. Para archivarlas, vuelva a correrlo con --apply.")
            return 0
        for u in users:
            db.add(models.ArchivedAccount(user_id=u.id))
            audit.record(db, None, "server_account_archived", target=u, new="Cliente archivado al quitar el portal")
        db.commit()
        print(f"Listo: {len(users)} cuenta(s) archivada(s). No se borró nada.")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None, confirm=input) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "archive-clients":
        init_db()
        return archive_clients(apply=args.apply)
    if args.command != "list-admins" and not args.email:
        parser.error(f"{args.command} necesita --email")
    init_db()
    if args.command == "list-admins":
        return list_admins()
    return run(args.command, args.email, args.to, confirm)


if __name__ == "__main__":
    sys.exit(main())
