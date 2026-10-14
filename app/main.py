import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import engine, SessionLocal, get_db
from . import config, models
from .email_poller import poll_forever
from .routers.users import router as users_router
from .routers.catalogs import router as catalogs_router
from .routers.tickets import router as tickets_router
from .routers.auth import router as auth_router
from .routers.invites import router as invites_router
from .routers.admin import router as admin_router

logging.basicConfig(level=logging.INFO)

LOG_FILE = "data/app.log"


def setup_file_logging() -> None:
    # Además de la consola; el panel del Administrador lee este archivo
    os.makedirs("data", exist_ok=True)
    root = logging.getLogger()
    target = os.path.abspath(LOG_FILE)
    if any(isinstance(h, RotatingFileHandler) and h.baseFilename == target for h in root.handlers):
        return
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


setup_file_logging()


def init_db():
    # Primero la carpeta donde vive la base de datos, después las tablas
    os.makedirs("data", exist_ok=True)
    models.Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        categorias_iniciales = ["Hardware", "Software", "Red", "Accesos"]
        for nombre in categorias_iniciales:
            existente = db.query(models.Category).filter(models.Category.name == nombre).first()
            if not existente:
                db.add(models.Category(name=nombre))
        db.commit()

        prioridades_iniciales = [
            ("Baja", 1, None),
            ("Media", 2, None),
            ("Alta", 3, None),
            ("Urgente", 4, None),
        ]
        for nombre, nivel, color in prioridades_iniciales:
            existente = db.query(models.Priority).filter(models.Priority.name == nombre).first()
            if not existente:
                db.add(models.Priority(name=nombre, level=nivel, color=color))
        db.commit()

        # Asegurar usuario agente virtual
        agente = db.query(models.User).filter(models.User.role == "agent").first()
        if not agente:
            agente = models.User(
                name="Agente virtual",
                email="agente@ticketera.local",
                role="agent",
                password_hash="!"
            )
            db.add(agente)
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    poller = None
    if config.EMAIL_ENABLED:
        if config.MS_TENANT_ID and config.MS_CLIENT_ID and config.SUPPORT_MAILBOX:
            poller = asyncio.create_task(poll_forever())
            logging.info("Revisando la carpeta %s cada %s segundos", config.MAIL_FOLDER, config.EMAIL_POLL_SECONDS)
        else:
            logging.warning("EMAIL_ENABLED=true pero faltan datos de Microsoft 365 en .env")

    yield

    if poller is not None:
        poller.cancel()
        with suppress(asyncio.CancelledError):
            await poller


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.include_router(users_router)
app.include_router(catalogs_router)
app.include_router(tickets_router)
app.include_router(auth_router)
app.include_router(invites_router)
app.include_router(admin_router)


# Debe ir al final: si va antes, captura las rutas de la API
app.mount("/", StaticFiles(directory="static", html=True), name="static")
