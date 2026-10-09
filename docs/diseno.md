## Tablas

users: id (PK), name (varchar 100, not null), email (varchar 150,
unique, not null), is_active (boolean, default true), created_at

categories: id (PK), name (varchar 80, unique, not null),
description (text, nullable), is_active (boolean, default true)

priorities: id (PK), name (varchar 30, unique, not null),
level (integer, not null, para ordenar), color (varchar 20, nullable)

tickets: id (PK), title (varchar 200, not null), description (text,
not null), category_id (FK categories, not null), priority_id
(FK priorities, not null), status (new | in_progress | resolved |
closed, default new), assigned_to (FK users, nullable),
created_at, updated_at

comments: id (PK), ticket_id (FK tickets, not null), user_id
(FK users, not null), content (text, not null), created_at

history: id (PK), ticket_id (FK tickets, not null), field
(status | priority_id | category_id | assigned_to), old_value (text,
nullable), new_value (text, nullable), changed_by (FK users,
not null), changed_at

## Usuario que actúa
No hay login. Las peticiones que modifican datos (PATCH de ticket y
POST de comentario) reciben actor_id, que es el id de un usuario
existente. actor_id NO es una columna de ninguna tabla.

## Transiciones de estado (validadas en la aplicación)
new → in_progress
in_progress → resolved
resolved → closed
resolved → in_progress (reabrir)
closed es terminal. Cualquier otra transición devuelve error 400.

## Endpoints
| Método | Ruta | Descripción |
| GET | /api/users | Listar usuarios activos |
| POST | /api/users | Crear usuario |
| GET | /api/categories | Listar categorías activas |
| GET | /api/priorities | Listar prioridades ordenadas por level |
| GET | /api/tickets | Listar con filtros |
| POST | /api/tickets | Crear ticket (title, description, category_id, priority_id) |
| GET | /api/tickets/{id} | Detalle con comentarios e historial |
| PATCH | /api/tickets/{id} | Cambiar status, assigned_to, priority_id o category_id; registra cada cambio en history |
| POST | /api/tickets/{id}/comments | Agregar comentario |

Filtros de GET /api/tickets:
?status=new&priority_id=1&category_id=2&assigned_to=3&search=texto
(search busca en title y description)

## Datos iniciales
Al arrancar, cargar categorías (Hardware, Software, Red, Accesos)
y prioridades (Baja=1, Media=2, Alta=3, Urgente=4) si no existen.

## Mapeo de requisitos

| Requisito | Tabla(s) | Endpoint(s) |
|---|---|---|
| Crear un ticket con titulo y descripcion. | tickets | POST /api/tickets |
| Seleccionar categoria y prioridad. | categories, priorities | GET /api/categories, GET /api/priorities |
| Cambiar estado: Nuevo, En proceso, Resuelto y Cerrado. | tickets, history | PATCH /api/tickets/{id} |
| Asignar el ticket a una persona. | users, tickets, history | PATCH /api/tickets/{id}, GET /api/users |
| Agregar comentarios al ticket. | comments, tickets | POST /api/tickets/{id}/comments |
| Ver listado, buscar y filtrar tickets. | tickets | GET /api/tickets |
| Consultar el historial de cambios de cada ticket. | history, tickets | GET /api/tickets/{id} |

## Decisiones tomadas durante el desarrollo

### Comentarios
- No se puede comentar un ticket cerrado (400).
- Los comentarios no se registran en la tabla history.

### Roles y permisos

Roles: requester (Solicitante), technician (Técnico),
coordinator (Coordinador).

Cambios en datos:
- users.role: obligatorio al crear un usuario.
- tickets.created_by: FK a users, guarda quién creó el ticket.
- La base de datos se recrea desde cero para incluir las columnas.

Cambios en la API:
- POST /api/tickets exige actor_id (se guarda como created_by).
- GET /api/tickets y GET /api/tickets/{id} exigen actor_id.
- Una acción no permitida para el rol responde 403 con mensaje
  en español.

Reglas:
1. Cualquier rol puede crear tickets y comentar.
2. El solicitante solo ve, abre y comenta los tickets que creó.
   Si intenta abrir uno ajeno: 403.
3. El solicitante no puede cambiar estado, prioridad, categoría
   ni asignación.
4. Técnico y coordinador pueden cambiar estado (con las
   transiciones existentes), prioridad y categoría.
5. Solo técnicos y coordinadores pueden ser asignados.
6. El coordinador puede asignar a cualquier técnico o
   coordinador.
7. El técnico solo puede asignarse a sí mismo un ticket sin
   asignar.
8. Todas las reglas se validan en el backend; la pantalla además
   oculta los botones que el rol no puede usar.
9. "Actuando como" muestra el rol junto al nombre. Sin usuario
   elegido, la tabla no muestra tickets.
10. El historial sigue registrando quién hizo cada cambio.
