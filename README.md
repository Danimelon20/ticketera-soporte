# Ticketera de Soporte

Sistema web para registrar, asignar y dar seguimiento a tickets de
soporte técnico. Proyecto de pasantía desarrollado por tareas con
el asistente OpenCode y validado manualmente en cada paso.

## Funcionalidades

- Crear tickets con título, descripción, categoría y prioridad.
- Cambiar el estado: Nuevo → En proceso → Resuelto → Cerrado,
  con la opción de reabrir un ticket resuelto.
- Asignar tickets a técnicos o coordinadores.
- Agregar comentarios.
- Listar, buscar y filtrar por estado, categoría, prioridad y texto.
- Consultar el historial de cambios de cada ticket.
- Roles de usuario: solicitante, técnico y coordinador, con
  permisos distintos.

## Tecnologías

| Parte | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Base de datos | SQLite con SQLAlchemy |
| Frontend | HTML, CSS y JavaScript sin frameworks |
| Pruebas | pytest + TestClient de FastAPI |
| Entorno | Docker y Docker Compose dentro de WSL |
| Asistente de desarrollo | OpenCode |

## Estructura del proyecto
## Requisitos previos

- Windows con WSL2 y Ubuntu.
- Docker Engine instalado **dentro de WSL** (no se usa Docker Desktop).
- Git.

## Instalación y ejecución

Todos los comandos se ejecutan en la terminal de Ubuntu (WSL).

```bash
git clone https://github.com/Danimelon20/ticketera-soporte.git
cd ticketera-soporte
docker compose up --build -d
```

Abrir en el navegador:

- Pantalla de la ticketera: http://localhost:8000
- Documentación interactiva de la API: http://localhost:8000/docs

Para detener el sistema:

```bash
docker compose down
```

Los datos se guardan en `data/tickets.db` y se conservan al
detener y volver a encender el contenedor.

## Primer uso

La base de datos empieza sin usuarios. Al arrancar se crean solas
las categorías (Hardware, Software, Red, Accesos) y las prioridades
(Baja, Media, Alta, Urgente).

1. En la sección **Usuarios**, crear un usuario con rol
   **Coordinador**.
2. Elegirlo en **Actuando como**.
3. Crear técnicos y solicitantes según se necesite.

Sin un usuario elegido en "Actuando como", la tabla de tickets no
muestra nada.

## Pruebas automáticas

```bash
docker compose exec app pytest -v
```

Son 50 pruebas. Usan una base de datos temporal, por lo que nunca
modifican `data/tickets.db`.

## Roles y permisos

| Acción | Solicitante | Técnico | Coordinador |
|---|---|---|---|
| Crear tickets | Sí | Sí | Sí |
| Ver tickets | Solo los suyos | Todos | Todos |
| Comentar | Solo en los suyos | Todos | Todos |
| Cambiar estado, prioridad o categoría | No | Sí | Sí |
| Ser asignado | No | Sí | Sí |
| Asignar a otra persona | No | No | Sí |
| Asignarse a sí mismo un ticket libre | No | Sí | Sí |

Las reglas se validan en el backend; una acción no permitida
responde `403`. La pantalla además oculta los botones que el rol
actual no puede usar.

## Decisiones de diseño

- **Sin inicio de sesión.** Quien actúa se indica en cada petición
  con `actor_id` (en la pantalla, con "Actuando como"). Los roles
  organizan el trabajo, pero no protegen el sistema de alguien que
  elija otro usuario: eso requeriría autenticación.
- **Estado cerrado es final.** Un ticket cerrado no se modifica ni
  se comenta.
- **Historial y comentarios separados.** El historial registra
  cambios de estado, asignación, prioridad y categoría (quién,
  cuándo, valor anterior y nuevo). Los comentarios no van al
  historial.
- **Horas en UTC.** El servidor guarda las fechas en UTC y la
  pantalla las muestra en la hora local del navegador.
- **Versiones fijadas.** `requirements.txt` fija la versión de las
  22 librerías, para que la instalación sea igual en cualquier
  computadora.

El diseño completo está en `docs/diseno.md`.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | /api/health | Estado del servidor |
| GET | /api/users | Usuarios activos |
| POST | /api/users | Crear usuario (rol obligatorio) |
| GET | /api/categories | Categorías |
| GET | /api/priorities | Prioridades ordenadas por nivel |
| GET | /api/tickets | Listar con filtros (requiere actor_id) |
| POST | /api/tickets | Crear ticket (requiere actor_id) |
| GET | /api/tickets/{id} | Detalle con historial y comentarios (requiere actor_id) |
| PATCH | /api/tickets/{id} | Cambiar estado, asignación, prioridad o categoría |
| POST | /api/tickets/{id}/comments | Agregar comentario |

## Uso de OpenCode

El proyecto se construyó por tareas pequeñas: cada tarea se pidió a
OpenCode con un prompt específico, se verificó con `git diff`,
`grep`, pruebas automáticas y pruebas en el navegador, y se guardó
en su propio commit. Los mensajes de los commits registran qué
generó la herramienta y qué se corrigió a mano.

Errores de la herramienta detectados durante la validación:

- Diseño inicial sin la tabla de historial, pese a pedirla.
- Versión inexistente de una librería (`httpx==0.27.3`).
- Rutas de la API distintas a las del diseño.
- Pruebas que escribían en la base de datos real.
- Uso de `innerHTML` con datos del usuario (riesgo de inyección),
  aunque el resumen afirmaba lo contrario.
- Tareas que quedaron a medias o en las que el modelo se
  descompuso y generó texto sin sentido.

Lecciones aplicadas: pedir una tarea pequeña por prompt, abrir una
sesión nueva cuando el modelo se enreda, indicar explícitamente lo
que no debe tocar y verificar siempre los archivos, no el resumen
de la herramienta.

Se usó el modelo gratuito Nemotron 3.5 Lightning y, al agotarse su
límite, otro modelo disponible en OpenCode.

## Limitaciones conocidas

- No hay autenticación (ver Decisiones de diseño).
- La pantalla no tiene pruebas automáticas; se verifica a mano en
  el navegador.
- Las pruebas muestran advertencias de librerías externas
  (starlette y httpx) que dependen de sus versiones.
