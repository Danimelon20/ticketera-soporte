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
