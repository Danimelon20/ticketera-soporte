"""Gestión de cuentas de administrador, solo desde el servidor (la página nunca actúa sobre un administrador).

Uso:
    docker compose exec -it app python -m scripts.admin_tools deactivate --email persona@empresa.com
    docker compose exec -it app python -m scripts.admin_tools reactivate --email persona@empresa.com
    docker compose exec -it app python -m scripts.admin_tools demote --email persona@empresa.com [--to coordinator]

Pide escribir el correo para confirmar, nunca deja el sistema sin un administrador activo, cierra las
sesiones de la cuenta y queda en el registro de cambios como hecho por "Servidor".
"""
import argparse
import sys

from sqlalchemy import func

from app import accounts, audit, models
from app.database import SessionLocal
from app.main import init_db

DEMOTE_ROLES = ("coordinator", "technician", "requester")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.admin_tools", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("deactivate", "reactivate", "demote"))
    parser.add_argument("--email", required=True, help="Correo de la cuenta de administrador")
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
        # Desactivar o quitar el rol a un administrador activo no puede dejar el sistema sin ninguno
        if command in ("deactivate", "demote") and user.is_active and accounts.active_admins(db, excluding=user.id) == 0:
            return _fail(accounts.LAST_ADMIN)

        label = {"deactivate": "desactivar", "reactivate": "reactivar",
                 "demote": f"cambiar a {audit.role_name(new_role)}"}[command]
        typed = confirm(f"Va a {label} la cuenta de administrador {user.name or email}. Escriba el correo para confirmar: ")
        if typed.strip().lower() != email:
            return _fail("El correo no coincide. No se hizo ningún cambio.")

        if command == "demote":
            audit.record(db, None, "server_role_changed", target=user,
                         old=audit.role_name(user.role), new=audit.role_name(new_role))
            user.role = new_role
        else:
            active = command == "reactivate"
            audit.record(db, None, "server_account_activated" if active else "server_account_deactivated", target=user)
            user.is_active = active
        accounts.close_sessions(db, user.id)
        db.commit()
        done = {"deactivate": "desactivada", "reactivate": "reactivada",
                "demote": f"como {audit.role_name(new_role)}"}[command]
        print(f"Listo. La cuenta {email} quedó {done} y se cerraron sus sesiones.")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None, confirm=input) -> int:
    args = _parser().parse_args(argv)
    init_db()
    return run(args.command, args.email, args.to, confirm)


if __name__ == "__main__":
    sys.exit(main())
