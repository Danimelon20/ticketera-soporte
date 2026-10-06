# Ticketera de Soporte

## Fuente de verdad
El diseño aprobado está en docs/diseno.md. No agregues tablas,
campos, endpoints ni autenticación que no estén ahí.
Trabaja solo en la tarea que se pida en cada mensaje.

## Stack
- Backend: Python 3.12 + FastAPI
- Base de datos: SQLite con SQLAlchemy
- Frontend: HTML, CSS y JavaScript sin frameworks, en static/
- Entorno: Docker y docker compose (dentro de WSL)
- Pruebas: pytest + TestClient de FastAPI

## Estructura
- app/          código del backend
- app/routers/  endpoints por recurso
- static/       frontend
- tests/        pruebas
- docs/         requisitos, diseño y bitácora

## Comandos
- Levantar: docker compose up --build
- Detener: docker compose down
- Pruebas: docker compose run --rm app pytest

## Convenciones
- Código y nombres en inglés; textos de la interfaz en español.
- Estados internos: new, in_progress, resolved, closed.
- Cada cambio de status, assigned_to, priority_id o category_id
  se registra en la tabla history.
- No hay login: las acciones reciben actor_id (id de un usuario).
