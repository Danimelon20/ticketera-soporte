# Ticketera de Soporte

Sistema web para registrar, asignar y dar seguimiento a tickets de
soporte técnico. Proyecto de pasantía desarrollado por tareas con
asistentes de programación (primero OpenCode y después Claude Code)
y validado manualmente en cada paso.

## Funcionalidades

- Crear tickets con título, descripción, categoría, prioridad,
  teléfono y cédula de contacto.
- Cambiar el estado: Nuevo → En proceso → Resuelto → Cerrado,
  con la opción de reabrir un ticket resuelto.
- Asignar tickets a técnicos o coordinadores.
- Agregar comentarios y dar seguimiento a un ticket para recibir
  avisos por correo.
- Listar, buscar y filtrar por estado, categoría, prioridad y texto.
- Consultar el historial de cambios de cada ticket.
- Inicio de sesión con contraseña, códigos de invitación y
  verificación por correo para coordinadores y administradores.
- Tickets por correo atendidos por el Agente virtual (Microsoft 365).
- Directorio de usuarios y panel de Administración.
- Asistente de la página con un modelo local (Qwen3 con Ollama).
- Interfaz con lista a la izquierda y detalle a la derecha,
  adaptable a celular.

## Tecnologías

| Parte | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Base de datos | SQLite con SQLAlchemy |
| Frontend | HTML, CSS y JavaScript sin frameworks |
| Pruebas | pytest + TestClient de FastAPI |
| Entorno | Docker y Docker Compose dentro de WSL |
| Correo | Microsoft Graph con permisos delegados (msal + httpx) |
| Asistente de la página | Ollama con el modelo Qwen3 (servicio `ollama`) |
| Asistentes de desarrollo | OpenCode y Claude Code |

## Requisitos previos

- Windows con WSL2 y Ubuntu.
- Docker Engine instalado **dentro de WSL** (no se usa Docker Desktop).
- Git.

## Instalación y ejecución

Todos los comandos se ejecutan en la terminal de Ubuntu (WSL).

```bash
git clone https://github.com/Danimelon20/ticketera-soporte.git
cd ticketera-soporte
cp .env.example .env
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
detener y volver a encender el contenedor. En `data/` también
quedan el registro del servidor (`app.log`) y la sesión de
Microsoft; esa carpeta nunca se sube a Git.

## Roles

| Rol | Cómo obtiene la cuenta | Cómo entra | Qué puede hacer |
|---|---|---|---|
| Solicitante | Se registra en la página sin código de invitación | Correo y contraseña | Crear tickets (en la página o por correo), ver y comentar solo los suyos, dar seguimiento y usar el asistente |
| Técnico | Se registra con un código de invitación `TEC-` que genera un coordinador o el Administrador | Correo y contraseña | Ver todos los tickets, cambiar estado, prioridad y categoría, asignarse tickets libres y comentar. No crea tickets. Del ticket que tiene asignado ve el teléfono completo y la cédula enmascarada |
| Coordinador | Se registra con un código de invitación `COO-` que genera el Administrador | Correo, contraseña y código de 6 dígitos por correo | Crear tickets (también a nombre de un solicitante), ver todos los tickets con los datos de contacto completos, cambiar estado, prioridad y categoría, asignar a técnicos o coordinadores, comentar, generar códigos para técnicos y ver el Directorio |
| Agente virtual | Se crea solo al arrancar el sistema | No inicia sesión | Lee el buzón de soporte, crea tickets a partir de correos de solicitantes registrados y responde por correo |
| Administrador | Se crea con el comando `python -m app.create_admin` | Correo, contraseña y código de 6 dígitos por correo | Todo lo del coordinador, generar códigos para coordinadores, cambiar roles, activar o desactivar cuentas en el Directorio y usar el panel de Administración |

Las reglas se validan en el backend; una acción no permitida
responde `403`. La pantalla además oculta los botones que el rol
actual no puede usar. Una cuenta desactivada pierde sus sesiones
abiertas y no puede volver a entrar hasta que la activen.

## Primer uso

La base de datos empieza sin personas. Al arrancar se crean solas
las categorías (Hardware, Software, Red, Accesos), las prioridades
(Baja, Media, Alta, Urgente) y el usuario del Agente virtual.

1. Crear el Administrador (pide nombre, correo y contraseña de al
   menos 8 caracteres):

   ```bash
   docker compose exec -it app python -m app.create_admin
   ```

2. Entrar en la página con ese correo y contraseña. Llega un código
   de 6 dígitos al correo; vence en 10 minutos. Con
   `EMAIL_ENABLED=false` el código no se envía y aparece en el
   registro del servidor marcado como `[SOLO DESARROLLO]`:

   ```bash
   docker compose logs app | grep "SOLO DESARROLLO"
   ```

3. En la pestaña "Usuarios", generar códigos de invitación: de
   coordinador (`COO-`, solo el Administrador) o de técnico (`TEC-`).
   Cada código sirve una sola vez, vence en 7 días y se muestra
   solo al generarlo.
4. Cada persona se registra en "Registrarse" con su código. Los
   solicitantes se registran sin código.

## Tickets por correo

El Agente virtual revisa la carpeta `MAIL_FOLDER` del buzón de
soporte y procesa los correos no leídos. El asunto se convierte en
el título del ticket (se quitan `RE:`, `RV:`, `FW:` y `[Soporte]`).
El cuerpo usa este formato:

```
Asunto: [Soporte] <título corto del problema>
Categoría: <una de: Hardware, Software, Red, Accesos>
Prioridad: <una de: Baja, Media, Alta, Urgente; si no la pones queda Media>
Teléfono: <8 dígitos>
Cédula: <9 dígitos, o 11 o 12 si es DIMEX>
Descripción: <qué pasa, desde cuándo y qué intentaste>
```

- **Obligatorio:** Categoría y Descripción. La descripción puede
  ocupar varias líneas y termina en una línea con `--`.
- **Teléfono y Cédula:** obligatorios si no están ya en el perfil
  del solicitante. La cédula se verifica con Hacienda.
- **Prioridad:** opcional; si falta o no se reconoce queda Media.

Qué responde el Agente:

- **Ticket creado:** "[Ticket #N] Recibimos tu solicitud" con el
  título, la categoría, la prioridad y el estado. El solicitante
  queda como seguidor del ticket.
- **Faltan datos:** "Faltan datos para crear tu ticket" con la
  lista de errores y la plantilla de arriba.
- **Remitente no registrado:** "No pudimos crear tu ticket" y una
  invitación a registrarse con ese mismo correo.

Solo se aceptan solicitantes registrados y activos; un correo de un
técnico, coordinador o administrador se trata como no registrado.
No se responden los correos del propio buzón, las respuestas
automáticas (`Auto-Submitted`, `Precedence: bulk/junk/auto_reply`)
ni los duplicados (mismo `Message-ID`). Cada correo procesado se
marca como leído.

Para conectar el buzón, iniciar sesión una vez con la cuenta de
soporte:

```bash
docker compose exec -it app python -m app.ms_login
```

## Configuración

Copiar `.env.example` como `.env` y completar los valores. `.env`
nunca se sube a Git.

| Variable | Qué hace |
|---|---|
| `EMAIL_ENABLED` | `true`: el Agente virtual revisa la carpeta y envía correos (respuestas, avisos a seguidores y códigos de verificación). `false`: no lee ni envía nada, solo lo anota en el registro |
| `MS_TENANT_ID` | Id. de directorio (inquilino) del registro de la aplicación en Microsoft Entra ID |
| `MS_CLIENT_ID` | Id. de aplicación (cliente) del mismo registro |
| `SUPPORT_MAILBOX` | Buzón que lee el Agente y desde el que responde; es la cuenta con la que se ejecuta `app.ms_login` |
| `MAIL_FOLDER` | Carpeta del buzón donde llegan los correos de soporte (por defecto `Ticketera`; `inbox` usa la bandeja de entrada) |
| `EMAIL_POLL_SECONDS` | Cada cuántos segundos se revisa la carpeta (por defecto 60) |
| `HACIENDA_CHECK` | `true`: verifica la cédula con la API pública de Hacienda. `false`: no consulta (útil sin internet) |
| `HACIENDA_STRICT` | `true`: rechaza las cédulas que Hacienda no encuentra. `false`: las acepta igual |
| `CHATBOT_ENABLED` | `true`: el asistente de la página responde. `false`: responde que no está disponible |
| `LLM_BASE_URL` | Dirección de Ollama dentro de la red de Docker Compose (`http://ollama:11434`) |
| `LLM_MODEL` | Modelo que usa el asistente (`qwen3:1.7b`) |

`APP_URL` (por defecto `http://localhost:8000`) es el enlace a la
página que aparece en los correos. Con `EMAIL_ENABLED=true` y sin
`MS_TENANT_ID`, `MS_CLIENT_ID` o `SUPPORT_MAILBOX`, el servidor
avisa en el registro y no revisa el buzón.

## Asistente

Botón flotante abajo a la derecha, visible solo con la sesión
iniciada. Usa un modelo local (Qwen3) servido por Ollama en el
contenedor `ollama`, que no publica su puerto: solo lo usa la app.
Antes de encenderlo hay que descargar el modelo:

```bash
docker compose exec ollama ollama pull qwen3:1.7b
```

Qué recibe el modelo, según el rol de quien pregunta:

- **Solicitante:** sus 10 tickets más recientes (número, título,
  estado, prioridad, categoría, asignado) con sus 3 últimos
  comentarios.
- **Técnico:** sus tickets asignados sin cerrar (hasta 15) con el
  último comentario, y cuántos tickets nuevos sin asignar hay
  (hasta 10 de ellos).
- **Coordinador y Administrador:** tickets por estado, abiertos por
  prioridad, sin asignar, abiertos por técnico y los 10 abiertos
  más antiguos con sus días abiertos.

Nunca recibe datos personales: ni correos, ni teléfonos, ni
cédulas, ni contraseñas, ni códigos. De las personas solo van los
nombres, y los correos o números largos que alguien haya escrito
en un título o comentario se reemplazan por `[correo oculto]` o
`[dato oculto]`. El asistente no cambia tickets, responde en
español y permite 20 mensajes cada 10 minutos por usuario. La
conversación vive solo en la página: se borra al recargar o al
cambiar de cuenta. El servidor registra únicamente el usuario y el
largo del mensaje.

## Pruebas automáticas

```bash
docker compose exec app pytest -q
```

Usan una base de datos temporal, por lo que nunca modifican
`data/tickets.db`, y nunca se conectan a Microsoft Graph, Hacienda
ni Ollama: esas llamadas se simulan.

## Decisiones de diseño

- **Sesión con token.** Cada persona inicia sesión con correo y
  contraseña; el token se guarda en la pestaña del navegador.
- **Estado cerrado es final.** Un ticket cerrado no se modifica ni
  se comenta.
- **Historial y comentarios separados.** El historial registra
  cambios de estado, asignación, prioridad y categoría (quién,
  cuándo, valor anterior y nuevo). Los comentarios no van al
  historial.
- **Horas en UTC.** El servidor guarda las fechas en UTC y la
  pantalla las muestra en la hora local del navegador.
- **Versiones fijadas.** `requirements.txt` fija la versión de
  cada librería, para que la instalación sea igual en cualquier
  computadora.

El diseño completo y las decisiones de la versión 2 están en
`docs/diseno.md`.

## API

Salvo `health`, `register`, `login`, `verify-code` y `resend-code`,
todas las rutas exigen el encabezado `Authorization: Bearer <token>`.

| Método | Ruta | Descripción |
|---|---|---|
| GET | /api/health | Estado del servidor |
| POST | /api/auth/register | Registrar usuario (con código de invitación opcional) |
| POST | /api/auth/login | Iniciar sesión; coordinador y administrador reciben `requires_code` |
| POST | /api/auth/verify-code | Confirmar el código de 6 dígitos y obtener la sesión |
| POST | /api/auth/resend-code | Enviar un código nuevo (una vez por minuto) |
| GET | /api/auth/me | Obtener usuario actual |
| POST | /api/auth/logout | Cerrar sesión |
| POST | /api/invites | Generar un código de invitación (coordinador y administrador) |
| GET | /api/invites | Listar códigos generados, sin el código |
| GET | /api/users | Usuarios activos |
| GET | /api/requesters | Buscar solicitantes por nombre o correo (coordinador y administrador) |
| GET | /api/categories | Categorías |
| GET | /api/priorities | Prioridades ordenadas por nivel |
| GET | /api/tickets | Listar con filtros |
| POST | /api/tickets | Crear ticket |
| GET | /api/tickets/{id} | Detalle con historial, comentarios y `following` |
| PATCH | /api/tickets/{id} | Cambiar estado, asignación, prioridad o categoría |
| POST | /api/tickets/{id}/comments | Agregar comentario |
| POST | /api/tickets/{id}/follow | Dar seguimiento al ticket |
| DELETE | /api/tickets/{id}/follow | Dejar de dar seguimiento |
| GET | /api/directory | Directorio de usuarios con filtros `role` y `q` (coordinador y administrador) |
| PATCH | /api/admin/users/{id} | Cambiar rol o activar/desactivar una cuenta (administrador) |
| GET | /api/admin/health | Estado de la base de datos, el correo y el asistente, y conteos (administrador) |
| GET | /api/admin/backup | Descargar una copia de la base de datos (administrador) |
| GET | /api/admin/logs | Últimas 200 líneas del registro del servidor (administrador) |
| POST | /api/chat | Preguntar al asistente |

## Uso de asistentes de desarrollo

El proyecto se construyó por tareas pequeñas: cada tarea se pidió
con un prompt específico, se verificó con `git diff`, `grep`,
pruebas automáticas y pruebas en el navegador, y se guardó en su
propio commit. La primera versión se hizo con OpenCode y la
versión 2 continuó con Claude Code (ver `docs/bitacora-ia.md`).

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

Con OpenCode se usó el modelo gratuito Nemotron 3.5 Lightning y, al
agotarse su límite, otro modelo disponible en OpenCode.

## Limitaciones

- El remitente de un correo se puede falsificar: no se revisan SPF
  ni DKIM.
- El buzón se revisa cada minuto (`EMAIL_POLL_SECONDS`), no al
  instante.
- Microsoft 365 limita cuántos correos se pueden enviar; si se
  superan, las respuestas y avisos no salen.
- La sesión de Microsoft guardada puede vencer (por ejemplo, al
  cambiar la contraseña del buzón) y hay que repetir
  `docker compose exec -it app python -m app.ms_login`.
- Los adjuntos de los correos se ignoran.
- Responder un correo del Agente no agrega un comentario al ticket.
- La API de Hacienda solo conoce a los contribuyentes inscritos; si
  no responde, la cédula se acepta sin verificar.
- El asistente usa un modelo local (Qwen3 con Ollama) que responde
  lento en computadoras sin tarjeta gráfica.
- Sin HTTPS los datos (contraseñas incluidas) viajan sin cifrar.
- El Administrador no modifica código desde la página: los cambios
  al código se hacen con Git y GitHub.
- No hay recuperación de contraseña.
- La sesión dura mientras la pestaña esté abierta.
- La pantalla no tiene pruebas automáticas; se verifica a mano en
  el navegador.
- Las pruebas muestran advertencias de librerías externas
  (starlette y httpx) que dependen de sus versiones.
