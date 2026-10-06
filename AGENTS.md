# AGENTS.md — Ticketera de Soporte

## Project scope
- Support ticket system with: create ticket (title+description), select category+priority, change state (Nuevo/En proceso/Resuelto/Cerrado), assign to person, add comments, list/search/filter tickets, view change history.
- Tech stack: Python + FastAPI, SQLite/PostgreSQL, HTML/CSS/JS frontend, Docker.
- Assistant: Open Code.

## Directory layout
- `/` — root (this file, README, .gitignore, docs/)
- `/docs/` — project requirements and planning
- `/app/` — will contain the FastAPI backend and frontend frontend (created when development starts)

## When development begins
- Backend entrypoint: `app/main.py` (FastAPI app)
- Database: `app/db.py` or similar (SQLAlchemy or raw SQLite)
- Migrations: will use Alembic when DB model is defined
- Docker: `docker-compose.yml` will orchestrate services
- Frontend: static files served from FastAPI or separate dev server

## Common commands (to be added as project grows)
- `make dev` or `uvicorn app.main:app --reload` — start dev server
- `pytest` — run tests
- `make lint` / `make typecheck` — code quality
- `docker compose up` — start all services
- `alembic migrate` — run DB migrations

## Notes
- No code exists yet — this file will be updated as the project evolves.
- See `docs/requisitos.md` for full requirement list.
- `.env` files (DB_URL, SECRET_KEY, etc.) are loaded at runtime; never commit them (see `.gitignore`).