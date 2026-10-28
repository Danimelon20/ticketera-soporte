import getpass
from .main import init_db
from .database import SessionLocal
from . import models
from .security import hash_password


def main():
    init_db()
    name = input("Nombre: ").strip()
    email = input("Correo: ").strip().lower()

    db = SessionLocal()
    try:
        existente = db.query(models.User).filter(models.User.email == email).first()
        if existente:
            print("Ese correo ya está registrado")
            return

        pwd1 = getpass.getpass("Contraseña (mínimo 8 caracteres): ")
        if len(pwd1) < 8:
            print("La contraseña debe tener al menos 8 caracteres")
            return

        pwd2 = getpass.getpass("Confirma la contraseña: ")
        if pwd1 != pwd2:
            print("Las contraseñas no coinciden")
            return

        pwd_hash = hash_password(pwd1)
        admin_user = models.User(
            name=name if name else "Administrador",
            email=email,
            role="admin",
            password_hash=pwd_hash
        )
        db.add(admin_user)
        db.commit()
        print(f"Administrador creado: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
