# Guía por rol

Qué ve cada persona, qué puede hacer y cómo se atiende un caso típico. Todo lo que aquí dice "no puede" lo
revisa también el servidor (responde `403`), no solo la página.

Cómo se entra, según el rol:

| Rol | Pestaña de inicio | Cómo entra |
|---|---|---|
| Técnico y coordinador | **Personal** | Con su cuenta Microsoft (si `MS_LOGIN_ENABLED=true`) o con correo y contraseña |
| Administrador | **Administración** | Con su cuenta Microsoft; si no se puede, **Acceso de emergencia** (contraseña y código por correo) |
| Cliente | **Cliente** | Correo y contraseña |

Para todos: **Mi cuenta** (perfil, correo y, si aplica, contraseña) y el **asistente** (botón abajo a la derecha,
si está encendido). El asistente solo responde: no cambia tickets.

---

## Técnico

**Qué ve**
- **Mi trabajo**: Mis tickets, Mis borradores, Por vencer y Vencidos. Solo lo que tiene asignado (o tuvo antes,
  en modo de lectura).
- La **campana** con los avisos de sus tickets: al 50 % y al 80 % del tiempo meta y al vencerse, y los borradores
  que le asignan.
- En **Mi cuenta › Disponibilidad y horario**: su turno y sus vacaciones o ausencias.

**Qué puede hacer**
- En sus tickets: cambiar el estado, comentar (también notas internas que el cliente no ve), adjuntar archivos,
  ajustar el nivel que sugirió la IA y **Separar** una agrupación de un ticket suyo.
- En sus borradores: completar los datos, **confirmar** (el ticket queda a su nombre), **Pedir más detalles** al
  cliente o descartarlo.
- Registrar sus propias vacaciones o ausencias.
- **Reportar cuenta** de un cliente desde un ticket o borrador (por ejemplo, datos que parecen falsos).

**Qué no puede**: abrir tickets de otros técnicos, ver los que no tienen responsable, crear tickets ni
cancelarlos.

**Caso típico: un ticket nuevo asignado**
1. Le llega el ticket a **Mis tickets** (la asignación automática eligió al técnico disponible con menos carga).
2. Lo abre: lee la descripción, el nivel y el tiempo meta. Ve el teléfono del cliente para llamarlo.
3. Lo pasa a **En proceso** y deja un comentario al cliente con lo que va a hacer.
4. Si necesita algo interno (por ejemplo, para el coordinador), lo escribe como nota interna.
5. Cuando lo resuelve, comenta la solución y lo marca **Resuelto**. El cliente recibe el aviso.
6. Si la campana le avisa que va al 80 % del tiempo y no llegará, comenta el motivo: el coordinador lo ve.

---

## Coordinador

**Qué ve**
- **Inicio**: lo urgente del día (vencidos, por vencer, reportes de vencimiento, incidentes y borradores).
- **Tickets**: Todos, Sin asignar, Vencidos, Incidentes y Clientes.
- **Borradores**: Todos y Sin revisor (los que llegaron sin nadie en turno).
- **Equipo**: Turnos, Carga y Disponibilidad de los técnicos.
- Todos los tickets, con los datos de contacto completos.

**Qué puede hacer**
- **Nuevo ticket**, también a nombre de un cliente (con su perfil completo).
- Asignar y reasignar tickets y borradores; cancelar y reabrir tickets.
- Registrar la **causa del vencimiento** de un ticket vencido (y ver su reporte).
- Ver y **Separar** fusiones de tickets y posibles incidentes.
- Cargar horarios, vacaciones e incapacidades de los técnicos.
- Pedir al administrador cambios de rol, nombre o cédula (quedan en **Aprobaciones**).
- **Reportar cuenta** de un cliente.
- **Enviar resumen ahora** (el resumen diario por correo, desde Inicio).

**Qué no puede**: gestionar cuentas (desactivar, restablecer, desbloquear), invitar personal ni revisar los
reportes de cuentas. Eso lo hace el administrador.

**Caso típico: un ticket vencido**
1. En **Inicio** o **Tickets › Vencidos** aparece el ticket en rojo y la campana trae su reporte de vencimiento.
2. Abre el reporte: ve cuándo se creó, cuándo vencía, quién lo tenía y si hubo respuesta.
3. Registra la causa (por ejemplo, "Responsable no disponible" porque el técnico está de vacaciones).
4. Reasigna el ticket a un técnico disponible (en **Equipo › Carga** ve quién tiene menos trabajo).
5. Deja un comentario al cliente explicando el nuevo plazo.

**Caso típico: un borrador sin revisor**
1. En **Borradores › Sin revisor** está el correo que llegó fuera de horario.
2. Lo revisa: el resumen y el nivel de la IA, y los datos de contacto.
3. Lo asigna a un técnico, o lo confirma él mismo y luego asigna el ticket.

---

## Administrador

**Qué ve**
- **Administración**: Cuentas, Invitaciones, Reportes de cuentas, Aprobaciones, Equipo y turnos, Registro de
  cambios y Estado del sistema.
- No entra al flujo de tickets (no ve la cola). Para revisar un caso usa **Ver conversación**, que pide un motivo y
  queda en el Registro de cambios.

**Qué puede hacer**
- **Cuentas**: activar o desactivar, cambiar el rol, desbloquear, cerrar sesiones y forzar el restablecimiento del
  acceso (esto último no aplica al personal que entra con Microsoft).
- **Invitaciones** para técnicos y coordinadores.
- **Reportes de cuentas**: confirmar o descartar, incluidos los automáticos del Agente virtual (por ejemplo, una
  cuenta que intentó usar la cédula de otra persona). Un reporte automático muestra los intentos sumados y, con
  3 o más cédulas distintas, la etiqueta de señal fuerte.
- **Aprobaciones**: aprobar o rechazar lo que piden los coordinadores.
- **Registro de cambios**: quién hizo qué sobre cada cuenta, con los datos enmascarados.
- **Estado del sistema**: correo, IA, asignación automática, copia de seguridad (**Descargar copia**), resumen
  diario y registros del servidor.

**Qué no puede**: actuar sobre otro administrador (ni sobre sí mismo) desde la página. Eso se hace en el
servidor con `scripts.admin_tools` (ver el README).

**Caso típico: un reporte automático de suplantación**
1. En **Reportes de cuentas** aparece un reporte del Agente virtual: "Cédula ajena", con los intentos.
2. Lo abre: el comentario dice qué cuenta es dueña de esa cédula (sin mostrar la cédula completa).
3. Revisa las dos cuentas: el suplantador puede ser cualquiera de las dos.
4. Confirma el reporte (y, si corresponde, desactiva la cuenta) o lo descarta con una nota. Solo al confirmarlo
   aparece la etiqueta "Reportada" para el resto del personal.

**Caso típico: un técnico nuevo**
1. En **Invitaciones**, escribe el correo, el nombre y el rol.
2. A la persona le llega un correo con un código; en la página usa **Activar mi cuenta** (con Microsoft, si está
   encendido).
3. En **Equipo y turnos** se carga su horario para que reciba asignaciones.

---

## Cliente

**Qué ve**
- **Mis solicitudes**: solo sus tickets, con el estado, los comentarios del equipo y los adjuntos.
- Si escribió dos veces por el mismo problema, el aviso de que su mensaje se agregó a la solicitud que ya tenía.

**Qué puede hacer**
- Crear su cuenta en la pestaña **Cliente** (correo, contraseña y un código que llega al correo).
- Completar su perfil: nombre, cédula y teléfono. Desde su primer ticket, el nombre y la cédula quedan fijos.
- **Nueva solicitud** desde la página, o escribir al correo de soporte (con la plantilla o con sus palabras).
- Comentar y adjuntar archivos en sus solicitudes.

**Qué no puede**: ver tickets de otras personas ni las notas internas del equipo.

**Caso típico: pedir ayuda**
1. Entra con su correo y contraseña. La primera vez completa su perfil.
2. **Nueva solicitud**: un título corto, qué pasa, desde cuándo y qué ya intentó.
3. Le llega un correo con el número de su solicitud.
4. Sigue el avance en **Mis solicitudes** y responde ahí si el técnico le pregunta algo.
5. Cuando el técnico la marca como resuelta, recibe el aviso.
