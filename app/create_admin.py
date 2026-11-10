"""Crea el administrador. Es la única forma de obtener ese rol y solo puede haber uno activo.

Uso: docker compose exec -it app python -m app.create_admin
"""
import getpass

from .main import init_db
from .database import SessionLocal
from . import accounts, audit, models
from .security import hash_password, mask_email, password_min

ADMIN_EXISTS = "\n".join([
    "Ya existe un administrador activo ({email}). Solo puede haber uno.",
    "- Para reemplazarlo: python -m scripts.admin_tools demote --email <correo> (o deactivate) y vuelva a ejecutar este comando.",
    "- Si perdió el acceso: python -m scripts.admin_tools reset-password --email <correo>.",
    "- Para ver los administradores: python -m scripts.admin_tools list-admins",
])
INACTIVE_ADMIN = "Esa cuenta es de un administrador desactivado: use python -m scripts.admin_tools reactivate --email <correo>."


def main(read=input, read_secret=getpass.getpass) -> int:
    """Devuelve 0 si creó el administrador y 1 si no hizo nada."""
    init_db()
    db = SessionLocal()
    try:
        current = db.query(models.User).filter(models.User.role == "admin", models.User.is_active.is_(True)).first()
        if current is not None:
            print(ADMIN_EXISTS.format(email=mask_email(current.email)))
            return 1

        name = read("Nombre: ").strip()
        email = read("Correo: ").strip().lower()
        existing = db.query(models.User).filter(models.User.email == email).first()
        if existing:
            print(INACTIVE_ADMIN if existing.role == "admin" else "Ese correo ya está registrado")
            return 1

        minimum = password_min("admin")
        pwd1 = read_secret(f"Contraseña (mínimo {minimum} caracteres): ")
        if len(pwd1) < minimum:
            print(f"La contraseña debe tener al menos {minimum} caracteres")
            return 1

        pwd2 = read_secret("Confirma la contraseña: ")
        if pwd1 != pwd2:
            print("Las contraseñas no coinciden")
            return 1

        admin_user = models.User(
            name=name if name else "Administrador",
            email=email,
            role="admin",
            password_hash=hash_password(pwd1),
        )
        db.add(admin_user)
        db.flush()
        # Por si otra ejecución lo creó mientras se escribía la contraseña
        if accounts.active_admins(db, excluding=admin_user.id) > 0:
            db.rollback()
            print(ADMIN_EXISTS.format(email="otro"))
            return 1
        audit.record(db, None, "server_admin_created", target=admin_user)
        db.commit()
        print(f"Administrador creado: {email}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
