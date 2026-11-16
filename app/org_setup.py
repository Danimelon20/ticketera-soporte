"""Estructura inicial de los departamentos. Corre en cada arranque y no duplica nada.

- Tablas: create_all crea las nuevas. No hay columnas nuevas en tablas que ya existían en la ticketera, así que
  no hace falta borrar la base. Si la base ya tenía tablas de departamentos de una versión anterior (rama
  areas-y-clientes), se agregan las columnas que les faltan con ALTER TABLE ... ADD COLUMN.
- Interruptor routing_enforced: en una base nueva arranca encendido; en una que ya tenía tickets o cuentas,
  apagado (todo sigue como antes hasta que el administrador revise la lista de verificación y lo encienda).
  La primera vez que corre esta versión en una base con datos lo deja apagado aunque venga encendido de una
  versión anterior; después nunca lo pisa (marca SETUP_VERSION en app_settings).
- El personal que ya existía queda sin departamento y aparece en la alerta del administrador.

Adaptado de la rama areas-y-clientes sin áreas, organizaciones ni clientes.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from . import models, routing

SETUP_VERSION_KEY = "departments_setup_version"
SETUP_VERSION = "1"
# Columnas de las tablas de departamentos que una versión anterior pudo crear sin ellas: (tabla, columna, DDL)
MISSING_COLUMNS = (
    ("departments", "handles_tickets", "BOOLEAN NOT NULL DEFAULT 0"),
)


def _has_data(db: Session) -> bool:
    return (
        db.query(models.Ticket.id).first() is not None
        or db.query(models.User.id).filter(models.User.role != "agent").first() is not None
    )


def _ensure_columns(db: Session) -> None:
    inspector = inspect(db.get_bind())
    tables = set(inspector.get_table_names())
    for table, column, ddl in MISSING_COLUMNS:
        if table in tables and column not in {c["name"] for c in inspector.get_columns(table)}:
            db.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logging.warning("Tabla %s de una versión anterior: se agregó la columna %s", table, column)
    db.commit()


def _ensure_switches(db: Session) -> None:
    first_run = routing.get_setting(db, SETUP_VERSION_KEY) is None
    has_data = _has_data(db)
    for key in routing.SWITCHES:
        current = routing.get_setting(db, key)
        if current is None:
            value = "false" if has_data else "true"
        elif first_run and has_data and current == "true":
            # Venía encendido de una versión anterior: con datos, esta versión arranca apagada
            value = "false"
        else:
            continue
        routing.set_setting(db, key, value)
        logging.info("Interruptor %s %s (%s)", key, "encendido" if value == "true" else "apagado",
                     "base nueva" if not has_data else "la base ya tenía datos")
    if first_run:
        routing.set_setting(db, SETUP_VERSION_KEY, SETUP_VERSION)


def ensure_structure(db: Session) -> None:
    _ensure_columns(db)
    _ensure_switches(db)
    db.commit()
