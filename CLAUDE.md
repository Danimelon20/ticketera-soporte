# Ticketera de soporte

FastAPI + SQLAlchemy + SQLite. Página en HTML/CSS/JS vanilla dentro de static/. Corre con Docker Compose dentro de WSL. Rama de trabajo: v2.

Roles: requester (solicitante), technician (técnico), coordinator (coordinador), admin (administrador) y agent (Agente virtual: usuario del sistema que no inicia sesión).

## Comandos
- El código se copia dentro de la imagen: después de CUALQUIER cambio (también en tests/ o static/) ejecuta `docker compose up --build -d` y espera 5 segundos antes de probar.
- Pruebas: `docker compose exec app pytest -q`
- Si el contenedor no arranca: `docker compose logs --tail 30 app`
- Revisar JavaScript: `docker run --rm -v "$PWD/static:/s" node:20-alpine node --check /s/app.js`
- Antes de terminar una tarea: reconstruye, corre pytest (todo debe pasar) y, si tocaste static/, corre la revisión de JavaScript.

## Reglas de código
- Cambios mínimos y enfocados. No borres ni reescribas funciones, clases, rutas o estilos que no tienen que ver con la tarea.
- En app/schemas.py una clase debe estar definida antes de que otra la use.
- Desde app/routers/ los módulos de app/ se importan con dos puntos: `from ..validators import ...`.
- create_all no altera tablas existentes. Si agregas o cambias columnas, avisa en el resumen que hay que borrar la base de datos. Nunca la borres tú.
- Las pruebas usan una base temporal en archivo (nunca sqlite:///:memory:) y nunca llaman a internet: Hacienda, Microsoft Graph y Gemini se simulan con monkeypatch.
- En el navegador: textContent en vez de innerHTML con datos, cada listener se registra una sola vez y los errores del servidor se muestran con getErrorMessage.

## Seguridad y privacidad
- Nunca leas, muestres ni modifiques .env ni data/ms_token_cache.json. Las variables nuevas se documentan en .env.example.
- Nunca agregues a Git .env, data/ ni archivos .db.
- Contraseñas, códigos de invitación y códigos de verificación se guardan con hash.
- Teléfono y cédula son datos personales (Ley 8968): no van en logs, no se envían al chatbot y solo los ve quien tiene permiso.

## Git
- No hagas commit ni push salvo que te lo pida. Nunca push --force.

## Respuestas
- En español, con un resumen corto al final: qué cambiaste, resultado de pytest y qué debo probar en el navegador.
