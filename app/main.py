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
from .daily_summary import summary_forever
from .alerts import alerts_forever
from .routers.users import router as users_router
from .routers.catalogs import router as catalogs_router
from .routers.tickets import router as tickets_router
from .routers.auth import router as auth_router
from .routers.invites import router as invites_router
from .routers.admin import router as admin_router
from .routers.chat import router as chat_router
from .routers.drafts import router as drafts_router
from .routers.account import router as account_router

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

    summary = None
    if config.DAILY_SUMMARY_ENABLED:
        if config.EMAIL_ENABLED:
            summary = asyncio.create_task(summary_forever())
            logging.info("Resumen diario activo a partir de las %s:00", config.DAILY_SUMMARY_HOUR)
        else:
            logging.warning("DAILY_SUMMARY_ENABLED=true pero EMAIL_ENABLED=false: no se enviará el resumen")

    alerts_task = None
    if config.ALERTS_ENABLED:
        if config.EMAIL_ENABLED:
            alerts_task = asyncio.create_task(alerts_forever())
            logging.info("Alertas por correo activas cada %s segundos", config.ALERTS_CHECK_SECONDS)
        else:
            logging.warning("ALERTS_ENABLED=true pero EMAIL_ENABLED=false: no se enviarán alertas")

    yield

    for task in (poller, summary, alerts_task):
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


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
app.include_router(chat_router)
app.include_router(drafts_router)
app.include_router(account_router)


# Debe ir al final: si va antes, captura las rutas de la API
app.mount("/", StaticFiles(directory="static", html=True), name="static")
