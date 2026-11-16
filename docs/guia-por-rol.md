# Guía por rol

Qué ve cada persona, qué puede hacer y cómo se atiende un caso típico. Todo lo que aquí dice "no puede" lo
revisa también el servidor (responde `403`), no solo la página.

Cómo se entra, según el rol:

| Rol | Pestaña de inicio | Cómo entra |
|---|---|---|
| Técnico y coordinador | **Personal** | Con su cuenta Microsoft (si `MS_LOGIN_ENABLED=true`) o con correo y contraseña |
| Administrador | **Administración** | Con su cuenta Microsoft; si no se puede, **Acceso de emergencia** (contraseña y código por correo) |
| Quien reporta (contacto) | — | **No inicia sesión**: escribe al correo de soporte desde su correo de la empresa |

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
- En sus tickets: cambiar el estado, comentar (también notas internas que quien reporta no recibe), adjuntar archivos,
  ajustar el nivel que sugirió la IA y **Separar** una agrupación de un ticket suyo.
- En sus borradores: completar los datos, **confirmar** (el ticket queda a su nombre), **Pedir más detalles** a
  quien reportó o descartarlo.
- Registrar sus propias vacaciones o ausencias.
- **Reportar cuenta** de un contacto desde un ticket o borrador (por ejemplo, datos que parecen falsos).

**Qué no puede**: abrir tickets de otros técnicos, ver los que no tienen responsable, crear tickets ni
cancelarlos.

**Caso típico: un ticket nuevo asignado**
1. Le llega el ticket a **Mis tickets** (la asignación automática eligió al técnico disponible con menos carga).
2. Lo abre: lee la descripción, el nivel y el tiempo meta. Ve el teléfono de quien reporta (si lo dio) para llamarlo.
3. Lo pasa a **En proceso** y deja un comentario a quien reportó con lo que va a hacer (le llega por correo).
4. Si necesita algo interno (por ejemplo, para el coordinador), lo escribe como nota interna.
5. Cuando lo resuelve, comenta la solución y lo marca **Resuelto**. Quien reportó recibe el aviso por correo.
6. Si la campana le avisa que va al 80 % del tiempo y no llegará, comenta el motivo: el coordinador lo ve.

---

## Coordinador

**Qué ve**
- **Inicio**: lo urgente del día (vencidos, por vencer, reportes de vencimiento, incidentes y borradores).
- **Tickets**: Todos, Sin asignar, Vencidos, Incidentes y Contactos.
- **Borradores**: Todos y Sin revisor (los que llegaron sin nadie en turno).
- **Equipo**: Turnos, Carga y Disponibilidad de los técnicos.
- Todos los tickets, con los datos de contacto completos.

**Qué puede hacer**
- **Nuevo ticket**, también a nombre de un contacto.
- Asignar y reasignar tickets y borradores; cancelar y reabrir tickets.
- Registrar la **causa del vencimiento** de un ticket vencido (y ver su reporte).
- Ver y **Separar** fusiones de tickets y posibles incidentes.
- Cargar horarios, vacaciones e incapacidades de los técnicos.
- Pedir al administrador cambios de rol, nombre o cédula (quedan en **Aprobaciones**).
- **Reportar cuenta** de un contacto.
- **Enviar resumen ahora** (el resumen diario por correo, desde Inicio).

**Qué no puede**: gestionar cuentas (desactivar, restablecer, desbloquear), invitar personal ni revisar los
reportes de cuentas. Eso lo hace el administrador.

**Caso típico: un ticket vencido**
1. En **Inicio** o **Tickets › Vencidos** aparece el ticket en rojo y la campana trae su reporte de vencimiento.
2. Abre el reporte: ve cuándo se creó, cuándo vencía, quién lo tenía y si hubo respuesta.
3. Registra la causa (por ejemplo, "Responsable no disponible" porque el técnico está de vacaciones).
4. Reasigna el ticket a un técnico disponible (en **Equipo › Carga** ve quién tiene menos trabajo).
5. Deja un comentario a quien reportó explicando el nuevo plazo.

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

**Caso típico: un contacto pasa a ser personal**
Se invita con su mismo correo, como a cualquiera. Mientras no active la invitación sigue siendo contacto; al
activarla se convierte en técnico o coordinador en la misma cuenta, y los tickets que reportó siguen siendo
suyos. Un cliente archivado o un contacto desactivado no se puede invitar.

---

## Quien reporta por correo (contacto)

La Ticketera es solo para uso interno: no hay portal de clientes ni se crean cuentas. Quien tiene un problema
escribe al correo de soporte desde su correo de la empresa (`INTERNAL_DOMAINS`), o el personal registra el caso
en la página. La primera vez se crea su **contacto**: una ficha sin contraseña ni acceso.

**Qué recibe por correo**
- «Recibimos su correo» y, al confirmarse, el número de su ticket.
- Las respuestas del equipo y el aviso de **Resuelto** con la solución documentada.
- Si escribió dos veces por el mismo problema, el aviso de que su mensaje se agregó a su ticket.

**Qué puede hacer**: responder esos correos para agregar información o adjuntos. No se le pide cédula; el
teléfono es opcional.

**Qué no puede**: iniciar sesión (recibe el mismo mensaje que una contraseña incorrecta), ver tickets ni notas
internas.

**Cuentas de cliente archivadas**: las cuentas de cliente de antes de quitar el portal quedan como contactos
archivados (**Cuentas › Archivados**). Sus tickets, comentarios, historial y reportes se conservan y el personal
los sigue viendo. Si tenían un ticket abierto, se atiende como siempre y siguen recibiendo sus correos.
