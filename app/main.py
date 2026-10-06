import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import engine, SessionLocal, get_db
from . import models
from .routers.users import router as users_router
from .routers.catalogs import router as catalogs_router

app = FastAPI()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.include_router(users_router)
app.include_router(catalogs_router)


@app.on_event("startup")
def on_startup():
    models.Base.metadata.create_all(bind=engine)
    os.makedirs("data", exist_ok=True)

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
    finally:
        db.close()



app.mount("/", StaticFiles(directory="static", html=True), name="static")