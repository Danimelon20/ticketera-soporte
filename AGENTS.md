# Ticketera de Soporte

## Fuente de verdad
El diseño aprobado está en docs/diseno.md, incluida la sección
"Decisiones tomadas durante el desarrollo" (roles y permisos).
No agregues tablas, campos, endpoints ni autenticación que no estén
ahí. Trabaja solo en la tarea que se pida en cada mensaje.

## Stack
- Backend: Python 3.12 + FastAPI
- Base de datos: SQLite con SQLAlchemy
- Frontend: HTML, CSS y JavaScript sin frameworks, en static/
- Entorno: Docker y docker compose (dentro de WSL)
- Pruebas: pytest + TestClient de FastAPI
- requirements.txt tiene todas las versiones fijadas: no las cambies.

## Estructura
- app/main.py       creación de la app, lifespan e init_db
- app/models.py     tablas
- app/schemas.py    esquemas Pydantic (usar model_config = ConfigDict)
- app/routers/      users.py, catalogs.py, tickets.py
- static/           index.html, app.js, styles.css
- tests/            conftest.py (fábricas make_user y make_ticket) y pruebas
- docs/             requisitos, diseño y bitácora

## Comandos
- Levantar: docker compose up --build -d
- Detener: docker compose down
- Pruebas: docker compose exec app pytest -v

## Reglas que no se deben romper
- En main.py, app.mount de static va SIEMPRE al final, después de
  todos los routers.
- Las rutas de la API no llevan barra final (/api/tickets, no
  /api/tickets/).
- Roles: requester, technician, coordinator. Las reglas de permisos
  se validan en el backend y responden 403.
- Toda petición de tickets usa actor_id: listar, ver detalle,
  crear, modificar y comentar.
- Cada cambio de status, assigned_to, priority_id o category_id se
  registra en history. Los comentarios no van a history.
- Las pruebas usan la base temporal de conftest.py, nunca
  data/tickets.db. Para crear datos, usar make_user y make_ticket.
- En el frontend, mostrar datos con textContent, nunca con
  innerHTML. No registrar eventos de elementos fijos dentro de
  renderTickets ni de showTicketDetail.

## Convenciones
- Código y nombres en inglés; textos de la interfaz y mensajes de
  error en español.
- Estados internos: new, in_progress, resolved, closed.
