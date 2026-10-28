# Ticketera de Soporte

Sistema web para recibir, asignar y dar seguimiento a las solicitudes de soporte técnico.
Los clientes piden ayuda desde la página o por correo; el Agente virtual convierte los correos en
tickets o borradores, la IA local sugiere el nivel y el tiempo meta, y el sistema asigna cada ticket
al técnico disponible con menos carga. Coordinadores y técnicos atienden, avisan de los vencimientos y
dejan registro de todo; el administrador cuida las cuentas y la seguridad.

Guías por rol: [docs/guia-por-rol.md](docs/guia-por-rol.md) · Lo que falta: [docs/pendientes.md](docs/pendientes.md) ·
Diseño: [docs/diseno.md](docs/diseno.md) · Bitácora con asistentes de IA: [docs/bitacora-ia.md](docs/bitacora-ia.md)

## Contenido

1. [Requisitos](#requisitos)
2. [Instalación desde cero](#instalación-desde-cero)
3. [Variables del archivo .env](#variables-del-archivo-env)
4. [Operación diaria](#operación-diaria)
5. [Respaldo y restauración](#respaldo-y-restauración)
6. [Microsoft: buzón e inicio de sesión del personal](#microsoft-buzón-e-inicio-de-sesión-del-personal)
7. [Tickets por correo](#tickets-por-correo)
8. [Datos de prueba](#datos-de-prueba)
9. [Pruebas automáticas](#pruebas-automáticas)
10. [Seguridad](#seguridad)
11. [Problemas comunes](#problemas-comunes)
12. [Tecnologías y decisiones](#tecnologías-y-decisiones)

## Requisitos

- Windows con **WSL2** y Ubuntu. Todos los comandos de este documento se escriben en la terminal de Ubuntu.
- **Docker Engine instalado dentro de WSL** (con el complemento `docker compose`). No se usa Docker Desktop.
- **Git**.
- Memoria: el modelo de IA local ocupa alrededor de 2,4 GB. Con menos de 8 GB de RAM en la PC, la IA y las
  pruebas van lentas (ver [Problemas comunes](#problemas-comunes)).

## Instalación desde cero

1. Clone el repositorio y entre a la carpeta:

   ```bash
   git clone git@github.com:Danimelon20/ticketera-soporte.git
   cd ticketera-soporte
   ```

2. Cree su archivo de configuración a partir del ejemplo y complételo (vea la
   [tabla de variables](#variables-del-archivo-env)). Para empezar a probar, los valores del ejemplo sirven tal cual:

   ```bash
   cp .env.example .env
   ```

3. Construya y encienda los contenedores (`app` y `ollama`):

   ```bash
   docker compose up -d --build
   ```

4. Descargue los modelos de IA (una sola vez; quedan guardados en el volumen de Ollama):

   ```bash
   docker compose exec ollama ollama pull qwen3:1.7b
   docker compose exec ollama ollama pull qwen3-embedding:0.6b
   ```

   El primero es para el asistente y el análisis de correos; el segundo, para la agrupación de tickets similares.

5. Cree el primer administrador. Pide nombre, correo y contraseña (mínimo 8 caracteres):

   ```bash
   docker compose exec -it app python -m app.create_admin
   ```

   El rol de administrador **nunca** se obtiene registrándose ni con una invitación: solo con este comando.

6. Entre a **http://localhost:8000**, pestaña **Administración**. Después de la contraseña se pide un código
   de 6 dígitos que llega al correo. Mientras `EMAIL_ENABLED=false`, el código no se envía y aparece en el
   registro del servidor:

   ```bash
   docker compose logs app | grep "SOLO DESARROLLO"
   ```

   La documentación interactiva de la API está en http://localhost:8000/docs.

7. Desde **Administración › Invitaciones**, invite al personal (técnicos y coordinadores). Los clientes crean su
   propia cuenta en la pestaña **Cliente** de la pantalla de inicio.

## Variables del archivo .env

Copie `.env.example` como `.env`. En la tabla, **Desarrollo** es la laptop donde se programa y se prueba, y
**PC con correo** es la única máquina que atiende el buzón real (ver [Seguridad](#seguridad)). Nunca escriba en
este documento, en un ticket ni en un chat los valores reales de su `.env`.

| Variable | Para qué sirve | Desarrollo | PC con correo |
|---|---|---|---|
| `EMAIL_ENABLED` | Lee el buzón y envía correos (respuestas, avisos, códigos). Con `false` solo lo anota en el registro | `false` | `true` |
| `MS_TENANT_ID` | Id. de directorio (inquilino) del registro de la app en Microsoft Entra ID, para el buzón | el del ejemplo | el id. de su organización |
| `MS_CLIENT_ID` | Id. de aplicación (cliente) del mismo registro, para el buzón | el del ejemplo | el id. del registro |
| `SUPPORT_MAILBOX` | Buzón de soporte que lee el Agente virtual y desde el que responde | el del ejemplo | el buzón de soporte |
| `MAIL_FOLDER` | Carpeta del buzón donde llegan los correos de soporte (`inbox` usa la bandeja de entrada) | `Ticketera` | `Ticketera` |
| `EMAIL_POLL_SECONDS` | Cada cuántos segundos se revisa la carpeta | `60` | `60` |
| `MS_LOGIN_ENABLED` | El personal entra con su cuenta Microsoft; el admin además tiene "Acceso de emergencia" | `false` | `true` |
| `MS_LOGIN_TENANT_ID` | Id. de directorio para el inicio de sesión (el mismo registro del buzón) | vacío | el mismo id. de directorio |
| `MS_LOGIN_CLIENT_ID` | Id. de aplicación para el inicio de sesión (el mismo registro) | vacío | el mismo id. de aplicación |
| `MS_LOGIN_CLIENT_SECRET` | Secreto de cliente propio del inicio de sesión. **Vence**: anote la fecha | vacío | el secreto vigente |
| `MS_LOGIN_REDIRECT_URI` | Dirección de vuelta registrada en Azure | `http://localhost:8000/auth/microsoft/callback` | la registrada en Azure |
| `HACIENDA_CHECK` | Verifica la cédula con la API pública de Hacienda | `false` (sin internet) o `true` | `true` |
| `HACIENDA_STRICT` | Rechaza las cédulas que Hacienda no encuentra | `false` | `false` |
| `CHATBOT_ENABLED` | Asistente de la página con el modelo local | `false` | `true` |
| `LLM_BASE_URL` | Dirección de Ollama dentro de Docker Compose | `http://ollama:11434` | `http://ollama:11434` |
| `LLM_MODEL` | Modelo del asistente y del análisis (debe estar descargado) | `qwen3:1.7b` | `qwen3:1.7b` |
| `AI_ANALYSIS_ENABLED` | Nivel, categoría, resumen y tiempo meta sugeridos por la IA. Necesita `EMAIL_ENABLED=true` | `false` | `true` |
| `OFFICE_DAYS` | Días hábiles (`1-5` = lunes a viernes) para los tiempos meta | `1-5` | `1-5` |
| `OFFICE_START` / `OFFICE_END` | Horario de oficina | `08:00` / `17:00` | el de la oficina |
| `OFFICE_TZ` | Zona horaria de la oficina | `America/Costa_Rica` | `America/Costa_Rica` |
| `OFFICE_HOLIDAYS` | Feriados que no cuentan (`AAAA-MM-DD` separados por coma) | vacío | los del año |
| `DAILY_SUMMARY_ENABLED` | Resumen diario por correo a coordinadores y admins. Necesita `EMAIL_ENABLED=true` | `false` | `true` |
| `DAILY_SUMMARY_HOUR` | Hora local del resumen (0 a 23) | `8` | `8` |
| `ALERTS_ENABLED` | Alertas por correo: crítico, por vencer y vencido. Necesita `EMAIL_ENABLED=true` | `false` | `true` |
| `ALERT_WARN_PERCENT` | Porcentaje del tiempo meta a partir del cual un ticket está "por vencer" | `75` | `75` |
| `ALERTS_CHECK_SECONDS` | Cada cuántos segundos se revisan las alertas | `120` | `120` |
| `AUTO_ASSIGN_ENABLED` | Asignación automática al técnico disponible, en turno y con menos carga | `false` | `true` |
| `AUTO_ASSIGN_LOAD_MODE` | `levels` (suma de niveles) o `count` (cantidad de tickets) | `levels` | `levels` |
| `AUTO_ASSIGN_CHECK_SECONDS` | Cada cuántos segundos se reintentan los que quedaron sin técnico | `60` | `60` |
| `SESSION_IDLE_MINUTES` | Minutos sin uso tras los que vence una sesión | `240` | `240` |
| `SESSION_MAX_HOURS` | Horas máximas de una sesión aunque se use | `12` | `12` |
| `ATTACHMENTS_DIR` | Carpeta de los adjuntos (debe quedar dentro de `data/`) | `data/attachments` | `data/attachments` |
| `GROUPING_ENABLED` | Agrupación de tickets similares (fusión del mismo cliente y posibles incidentes) | `false` | `true` |
| `EMBEDDING_MODEL` | Modelo de la agrupación (debe estar descargado) | `qwen3-embedding:0.6b` | `qwen3-embedding:0.6b` |
| `GROUPING_MERGE_THRESHOLD` | Similitud mínima para fusionar tickets del mismo cliente | `0.80` | `0.80` |
| `GROUPING_INCIDENT_THRESHOLD` | Similitud mínima para ligar tickets de clientes distintos | `0.85` | `0.85` |
| `GROUPING_INCIDENT_MIN` | Tickets relacionados para marcar "Posible incidente" | `3` | `3` |
| `GROUPING_INCIDENT_HOURS` | Ventana de horas para contar el incidente | `2` | `2` |
| `GROUPING_TIMEOUT_SECONDS` | Tiempo máximo de la búsqueda por ticket | `15` | `15` |

Variable que la app lee pero que **no está en `.env.example`**: `APP_URL`, el enlace a la página que va en los
correos (por defecto `http://localhost:8000`). En la PC con correo, agréguela a su `.env` con la dirección con la
que el personal y los clientes entran a la página.

## Operación diaria

| Para | Comando |
|---|---|
| Encender | `docker compose up -d` |
| Apagar | `docker compose down` |
| Ver qué está corriendo | `docker compose ps` |
| Ver el registro en vivo | `docker compose logs -f app` |
| Ver las últimas líneas (si no arranca) | `docker compose logs --tail 30 app` |

Los datos quedan en la carpeta `data/` (base de datos, adjuntos, registro y sesión del buzón) y se conservan al
apagar y encender. El administrador también ve el estado y las últimas líneas del registro en
**Administración › Estado del sistema**.

**Después de `git pull`.** El código se copia dentro de la imagen, así que después de cualquier actualización
hay que reconstruir:

```bash
docker compose up -d --build
```

Use `--force-recreate` cuando lo que cambió fue el `.env` (y no el código), o cuando un contenedor quedó en un
estado raro: crea los contenedores de nuevo y así leen el `.env` otra vez. `docker compose restart` **no** vuelve
a leer el `.env`.

```bash
docker compose up -d --force-recreate
```

Si una actualización agrega o cambia columnas de una tabla existente, el mensaje del commit lo avisa: la base
no se actualiza sola (las tablas nuevas sí se crean solas). En ese caso, haga un respaldo antes de seguir.

## Respaldo y restauración

**Qué respaldar:** `data/tickets.db` (la base) y `data/attachments/` (los adjuntos). Contienen datos personales:
guarde el respaldo en un lugar seguro y nunca en GitHub.

**Respaldo desde la página:** **Administración › Estado del sistema › Copia de seguridad › Descargar copia**.
Descarga una copia completa de la base, hecha con la app encendida. No incluye los adjuntos.

**Respaldo completo desde la terminal** (base y adjuntos, con la app detenida un momento):

```bash
docker compose stop app
sudo tar czf respaldo-$(date +%F).tar.gz data/tickets.db data/attachments
docker compose start app
```

**Restaurar:**

```bash
docker compose stop app
sudo tar xzf respaldo-AAAA-MM-DD.tar.gz
docker compose start app
```

Para restaurar solo una copia descargada desde la página, deténgala igual y reemplace `data/tickets.db` por el
archivo descargado (con el nombre `tickets.db`).

**Qué NO borrar:** `data/ms_token_cache.json` es la sesión del buzón de soporte. Si se borra, el Agente virtual
deja de leer y enviar correos hasta que alguien vuelva a iniciar sesión con
`docker compose exec -it app python -m app.ms_login`. Tampoco lo copie a otra máquina ni lo incluya en respaldos
que salgan de la PC: contiene la sesión de Microsoft.

## Microsoft: buzón e inicio de sesión del personal

Los dos usan **el mismo registro de aplicación** en Microsoft Entra ID (portal de Azure), pero son independientes:
el inicio de sesión del personal nunca toca la sesión del buzón.

### Buzón de soporte (Agente virtual)

- Registro de la app con permisos **delegados** de Microsoft Graph `Mail.ReadWrite` y `Mail.Send`.
- En **Autenticación**, la opción **Permitir flujos de cliente público** queda activada: el buzón inicia sesión
  con un código de dispositivo.
- En el `.env`: `MS_TENANT_ID`, `MS_CLIENT_ID`, `SUPPORT_MAILBOX` y `MAIL_FOLDER`, y `EMAIL_ENABLED=true`.
- Inicio de sesión (una vez, y otra vez si la sesión vence, por ejemplo al cambiar la contraseña del buzón).
  Siga las instrucciones en pantalla e inicie sesión **con la cuenta del buzón de soporte**:

  ```bash
  docker compose exec -it app python -m app.ms_login
  ```

### Inicio de sesión del personal

- En el mismo registro, **Autenticación › Agregar una plataforma › Web**, con la URI de redirección
  `http://localhost:8000/auth/microsoft/callback` (debe ser idéntica a `MS_LOGIN_REDIRECT_URI`).
- **Certificados y secretos**: un secreto de cliente propio para el inicio de sesión (`MS_LOGIN_CLIENT_SECRET`).
- Solo pide `openid`, `profile`, `email` y `User.Read`; nunca permisos de correo.
- En el `.env`: `MS_LOGIN_ENABLED=true`, `MS_LOGIN_TENANT_ID`, `MS_LOGIN_CLIENT_ID`, `MS_LOGIN_CLIENT_SECRET` y
  `MS_LOGIN_REDIRECT_URI`. Después: `docker compose up -d --force-recreate`.
- Solo entra el personal que ya tiene cuenta activa (técnico, coordinador o administrador). La primera vez se
  enlaza por correo; después, por la cuenta Microsoft. El personal nuevo sigue entrando por invitación
  ("Activar con Microsoft"). Los clientes siguen con correo y contraseña.
- Con `MS_LOGIN_ENABLED=true`, técnicos y coordinadores ya no usan contraseña.

**Renovar el secreto antes de que venza.** Anote la fecha de vencimiento al crearlo. Unos días antes:

1. En Azure, **Certificados y secretos › Nuevo secreto de cliente**, y copie el valor (solo se muestra una vez).
2. Póngalo en `MS_LOGIN_CLIENT_SECRET` de su `.env`.
3. `docker compose up -d --force-recreate` y pruebe a entrar con una cuenta del personal.
4. Borre el secreto viejo en Azure.

El buzón no usa este secreto: renovarlo no lo afecta.

**Acceso de emergencia del administrador.** Si Microsoft no responde o el secreto venció, en la pestaña
**Administración** use el enlace **Acceso de emergencia**: correo, contraseña y el código por correo, como antes.
Cada uso queda en el Registro de cambios y avisa por correo a los demás administradores.

**El día que la página use HTTPS:** la URI de redirección pasa a `https://<su dominio>/auth/microsoft/callback`,
hay que registrarla igual en Azure y actualizar `MS_LOGIN_REDIRECT_URI`. Azure solo acepta `http` para
`localhost`: para entrar desde otra PC de la red hace falta HTTPS.

## Tickets por correo

El Agente virtual revisa la carpeta `MAIL_FOLDER` del buzón y procesa los correos no leídos de clientes
registrados. Un correo con esta plantilla crea el ticket directamente:

```
Asunto: [Soporte] <título corto del problema>
Categoría: <Hardware, Software, Red o Accesos>
Prioridad: <Baja, Media, Alta o Urgente; si falta queda Media>
Teléfono: <8 dígitos>
Cédula: <9 dígitos, o 11 o 12 si es DIMEX>
Descripción: <qué pasa, desde cuándo y qué intentó>
```

Un correo escrito con sus propias palabras (sin la plantilla) se convierte en un **borrador** que la IA analiza
y que confirma una persona del equipo. Si faltan datos, el Agente responde pidiéndolos. Los correos de
remitentes no registrados reciben una invitación a crear su cuenta.

## Datos de prueba

Para probar sin usar la página, el comando `scripts.demo_data` crea escenarios marcados con **[DEMO]** y cuentas
`@demo.invalid` (un dominio que no existe: nunca sale un correo real). Mientras corre no envía correos ni usa la
IA, la agrupación ni la asignación automática, y no cambia su `.env`.

```bash
docker compose exec app python -m scripts.demo_data cargar
docker compose exec app python -m scripts.demo_data verificar
docker compose exec app python -m scripts.demo_data limpiar
```

- **cargar**: 3 técnicos (uno de vacaciones), 1 coordinador, clientes, tickets agrupados, vencimientos,
  borradores y un intento de suplantación. Al final imprime cada cuenta, su contraseña y qué probar con ella.
- **verificar**: revisa cada escenario por la API iniciando sesión con cada cuenta DEMO e imprime
  `N de M correctos`. Con `MS_LOGIN_ENABLED=true` avisa y no sigue (el personal DEMO no puede entrar con
  contraseña). No cambia nada.
- **limpiar**: borra solo lo DEMO y sus adjuntos.

Úselo solo en la laptop de desarrollo, nunca en la PC con correo.

## Pruebas automáticas

Después de cualquier cambio (también en `tests/` o `static/`), reconstruya y espere unos segundos antes de probar:

```bash
docker compose up -d --build
docker compose exec app pytest -q
```

Solo un archivo, o solo las pruebas cuyo nombre contiene una palabra:

```bash
docker compose exec app pytest -q tests/test_auth.py
docker compose exec app pytest -q -k microsoft
```

Revisión del JavaScript de la página:

```bash
docker run --rm -v "$PWD/static:/s" node:20-alpine node --check /s/app.js
```

Las pruebas usan una base temporal (nunca `data/tickets.db`) y nunca se conectan a Microsoft, Hacienda ni Ollama:
esas llamadas se simulan y cualquier intento de salir a la red hace fallar la prueba. La suite completa tarda
varios minutos.

## Seguridad

- **Nunca se sube a GitHub:** `.env`, la carpeta `data/` (base, adjuntos, registro y `ms_token_cache.json`) ni
  archivos `.db`. Ya están en `.gitignore`; revise `git status` antes de cada commit.
- **Solo una máquina con `EMAIL_ENABLED=true`:** si dos PCs leen el mismo buzón, los correos se procesan dos veces
  y los clientes reciben respuestas repetidas. En desarrollo, siempre `false`.
- **Cédula y teléfono** son datos personales (Ley 8968): en el Registro de cambios y en los reportes quedan
  enmascarados (últimos 4 dígitos), no van a los registros del servidor ni a la IA, y solo los ve completos quien
  tiene permiso.
- Contraseñas, códigos de invitación y códigos de verificación se guardan solo como hash.
- Al asistente nunca se le envían cédulas, teléfonos, correos ni contraseñas.
- Sin HTTPS, los datos viajan sin cifrar por la red: mientras tanto, use la página solo en la PC o en una red
  de confianza.

## Problemas comunes

**Una variable del `.env` no llega al contenedor** (por ejemplo, el inicio de sesión con Microsoft sigue sin
funcionar después de completar el `.env`).
- `docker compose restart` no vuelve a leer el `.env`. Use `docker compose up -d --force-recreate`.
- Revise que el nombre esté escrito exactamente como en `.env.example` y sin espacios alrededor del `=`.
- Para comprobar si una variable llegó, sin mostrar su valor:

  ```bash
  docker compose exec app sh -c 'test -n "$MS_LOGIN_TENANT_ID" && echo definida || echo vacía'
  ```

**"No se pudo conectar con Microsoft" al entrar con Microsoft.**
- Revise el registro: `docker compose logs app | grep -i microsoft`. Si dice que faltan `MS_LOGIN_TENANT_ID`,
  `MS_LOGIN_CLIENT_ID` o `MS_LOGIN_CLIENT_SECRET`, complételos y recree el contenedor (punto anterior).
- Si el secreto venció, renuévelo (ver [Microsoft](#microsoft-buzón-e-inicio-de-sesión-del-personal)) y, mientras
  tanto, el administrador usa el Acceso de emergencia.
- Si Microsoft muestra un error de URI de redirección, la de Azure y `MS_LOGIN_REDIRECT_URI` no son idénticas.

**El buzón dejó de leer correos.** Si el registro dice "Falta iniciar sesión en Microsoft", vuelva a ejecutar
`docker compose exec -it app python -m app.ms_login` con la cuenta del buzón.

**Las pruebas tardan mucho o se cortan.**
- La suite completa tarda varios minutos. Mientras corre, no reinicie los contenedores: la corrida se corta.
- Con poca memoria, detenga Ollama mientras prueba (las pruebas no lo usan) y enciéndalo después:

  ```bash
  docker compose stop ollama
  docker compose start ollama
  ```

- Mientras trabaja en algo puntual, corra solo el archivo de pruebas de ese tema.
- WSL usa por defecto una parte de la RAM de Windows; si se queda corto, se puede ampliar en el archivo
  `.wslconfig` de su usuario de Windows y reiniciar WSL con `wsl --shutdown` (desde Windows).

**La IA responde lento.**
- Sin tarjeta gráfica, el modelo corre en el procesador. La primera respuesta después de encender tarda más
  porque el modelo se carga en memoria; las siguientes son más rápidas.
- Compruebe que los modelos estén descargados y cuáles están cargados:

  ```bash
  docker compose exec ollama ollama list
  docker compose exec ollama ollama ps
  ```

- Si falta uno, descárguelo (paso 4 de la instalación). Si la PC tiene poca memoria, apague lo que no use:
  `CHATBOT_ENABLED` o `GROUPING_ENABLED` en `false`.

**El contenedor no arranca.** Mire las últimas líneas: `docker compose logs --tail 30 app`.

## Tecnologías y decisiones

| Parte | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Base de datos | SQLite con SQLAlchemy |
| Página | HTML, CSS y JavaScript sin frameworks |
| Pruebas | pytest + TestClient de FastAPI |
| Entorno | Docker y Docker Compose dentro de WSL |
| Correo e inicio de sesión | Microsoft Graph y Entra ID (msal + httpx) |
| IA local | Ollama con Qwen3 (asistente, análisis de correos y agrupación) |

- Cada rol solo ve y hace lo que le corresponde, y **el servidor lo verifica** (una acción no permitida responde
  `403`); la página además oculta lo que no aplica. El detalle por rol está en
  [docs/guia-por-rol.md](docs/guia-por-rol.md).
- Las fechas se guardan en UTC y la página las muestra en la hora local.
- `requirements.txt` fija la versión de cada librería para que la instalación sea igual en cualquier PC.
- El diseño completo está en [docs/diseno.md](docs/diseno.md). El proyecto se construyó por tareas pequeñas con
  asistentes de programación (OpenCode y después Claude Code), verificando cada paso con pruebas y en el
  navegador; la historia está en [docs/bitacora-ia.md](docs/bitacora-ia.md).
