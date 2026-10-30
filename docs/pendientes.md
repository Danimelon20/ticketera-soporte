# Pendientes

Lo que quedó fuera de las fases ya entregadas, con lo que haría falta para cada uno.

## Funciones

- **Reportes de operación (coordinador)**: el módulo Reportes del prototipo (Rendimiento y Vencimientos) no está
  en el menú. Haría falta definir las métricas (tiempo de respuesta, resueltos por técnico, vencidos por causa),
  sus consultas y una pantalla con filtros por fechas.
- **Correo e IA en Administración**: el prototipo tenía secciones propias para el correo y la IA. Hoy solo
  aparecen dentro de Estado del sistema. Faltaría la configuración visible (carpeta, frecuencia, modelo, umbrales)
  y sus pruebas, sin mostrar secretos.
- **Respuestas predefinidas**: plantillas de comentarios frecuentes para técnicos y coordinadores. Haría falta una
  tabla de plantillas, quién las administra y un selector en el detalle del ticket.
- **Encuesta de satisfacción**: al resolver un ticket, una pregunta corta al cliente. Haría falta la tabla de
  respuestas, el enlace en el correo de "resuelto" y su lugar en los reportes de operación.
- **Posible incidente con 3 tickets**: con la configuración por defecto (`GROUPING_INCIDENT_MIN=3`), el incidente
  necesita 3 tickets relacionados además del principal. Hay que decidir si el umbral debe contar al principal.
- **Cambiar correo del personal que entra con Microsoft**: "Cambiar correo" pide la contraseña actual, que ese
  personal no tiene. Haría falta que el cambio lo haga el administrador o que el correo salga de Microsoft.
- **Un solo reporte automático abierto por cuenta**: si llega otro motivo, se anota como "También: …" en el mismo
  reporte. Para tener reportes separados por motivo haría falta cambiar el índice de la tabla (requiere borrar la
  base o migrarla).

## Instalación y servidor

- **HTTPS y servidor**: hoy la página corre en una PC con `http://localhost:8000`. Para usarla desde otras PCs hace
  falta un servidor (o una PC fija) con un dominio, un certificado HTTPS y un proxy delante de la app. Después,
  registrar la nueva URI de redirección en Azure y actualizar `MS_LOGIN_REDIRECT_URI` y `APP_URL`.
- **`APP_URL` en `.env.example`**: la app la lee (es el enlace de los correos), pero no está en el ejemplo.
  Agregarla con su comentario.
- **Migraciones de la base**: `create_all` no altera tablas existentes, por eso cada cambio de columnas obliga a
  borrar la base. Haría falta una herramienta de migraciones (por ejemplo, Alembic) antes de tener datos reales
  que no se puedan perder.
- **Respaldo automático**: hoy el respaldo es a mano (botón o comando). Haría falta una tarea programada que copie
  la base y los adjuntos a otro disco, con rotación de copias.
- **Usuario dentro del contenedor**: la app corre como `root` y los archivos de `data/` quedan de `root` (por eso
  el respaldo usa `sudo`). Haría falta un usuario sin privilegios en el `Dockerfile`.
- **Avisos por vencer del secreto de Microsoft**: el sistema no avisa cuando el secreto de cliente está por vencer.
  Haría falta guardar la fecha y mostrar una alerta en Estado del sistema.

## Seguridad y correo

- **Remitente falsificado**: no se revisan SPF ni DKIM, así que alguien podría escribir como si fuera un cliente.
  Haría falta leer los encabezados de autenticación que entrega Microsoft y rechazar o marcar los que fallan.
- **Límite de intentos por IP real**: hoy el bloqueo por intentos fallidos se cuenta solo por correo (5 fallos,
  15 minutos), así que no frena a quien prueba una contraseña contra muchas cuentas. La app no puede usar la IP:
  todo llega desde la puerta de enlace de Docker (172.18.0.1), por Funnel y también por conexión directa al puerto,
  y por eso tampoco puede confiar en `X-Forwarded-For` (cualquiera que llegue directo lo podría inventar). Haría
  falta: (1) publicar el puerto 8000 solo en la interfaz local o de Tailscale (por ejemplo `127.0.0.1:8000:8000`)
  para que nadie llegue sin pasar por el proxy; (2) una lista de proxies de confianza y tomar de
  `X-Forwarded-For` la primera dirección de la derecha que no esté en esa lista; (3) un segundo límite por esa IP,
  aparte del de cada correo.
- **Límites de envío de Microsoft 365**: si se superan, las respuestas y avisos no salen. Haría falta una cola de
  reintentos y un aviso en Estado del sistema.
- **Administrador DEMO para `verificar`**: dos comprobaciones de `scripts.demo_data verificar` (la cola del admin y
  quién abrió el reporte) quedan como OMITIDO porque no hay un administrador DEMO. Decidir si se cubren solo con
  pruebas automáticas.

## Calidad

- **Pruebas de la página**: la pantalla se revisa a mano en el navegador (también a 375 px). Haría falta una
  herramienta de pruebas de navegador para los recorridos principales de cada rol.
- **Advertencias de librerías**: las pruebas muestran advertencias de starlette y httpx que dependen de sus
  versiones. Se resuelven al actualizar esas librerías (con su prueba completa).
- **Tiempo de la agrupación con el Ollama real**: quedó por medir cuánto tarda la detección por ticket en la PC con
  correo (nota de `docs/PENDIENTE-agrupacion.md`, que por lo demás ya está terminada).
