# Pendiente: agrupación automática de tickets similares

Trabajo detenido a mitad, **sin commit**. Plan aprobado: `~/.claude/plans/pasted-content-id-e8b0-nueva-funci-n-mellow-shannon.md`.
Rama `v2`. Último commit bueno: `aa821fc` (optimización de la IA).

## Hecho

### Paso 0: medición (ya decidido: embeddings)
- Modelo descargado en el contenedor de Ollama: `qwen3-embedding:0.6b` (639 MB en disco).
- Memoria con los dos modelos cargados (`docker stats`):
  - Con `num_ctx` 4096 en embeddings: Ollama 2,57 GB + app 0,10 GB = **2,67 GB**.
  - Con `num_ctx` 512 en embeddings: Ollama 2,15 GB + app 0,11 GB = **2,26 GB** ← el que queda.
  - Ambos están por debajo del límite de ~3,0 GB, así que se usan **embeddings** (no TF-IDF).
- Similitud medida (coseno), en ~0,3 a 0,5 s por par con el modelo cargado:
  - Mismo problema, otras palabras: 0,914 (Outlook no envía), 0,869 (permisos de carpeta), 0,729 (sin internet).
  - Problemas distintos: 0,434, 0,493 y 0,541.
- Umbrales iniciales elegidos: fusión (mismo cliente) **0,80**; incidente (otros clientes) **0,85**.
- La primera carga en frío del modelo de embeddings tardó 8,6 s.

### Archivos ya cambiados (sin probar todavía)
- `docker-compose.yml`: `OLLAMA_MAX_LOADED_MODELS=2`. El contenedor `ollama` ya se recreó con ese valor.
- `app/config.py` y `.env.example`: `GROUPING_ENABLED` (false), `EMBEDDING_MODEL`, `GROUPING_MERGE_THRESHOLD`
  (0.80), `GROUPING_INCIDENT_THRESHOLD` (0.85), `GROUPING_INCIDENT_MIN` (3), `GROUPING_INCIDENT_HOURS` (2) y
  `GROUPING_TIMEOUT_SECONDS` (15).
- `app/models.py`:
  - Tablas nuevas `TicketEmbedding`, `TicketMerge`, `TicketLink` y `TicketIncident`; se agregó `Float` al import.
  - Acciones nuevas del registro: `ticket_merged`, `ticket_merge_undone`, `ticket_linked`, `ticket_link_undone`
    e `incident_marked`.
- `app/chatbot.py`:
  - `NUM_PREDICT["group_check"] = 80`.
  - `post_chat(..., timeout=None)`.
  - `warm_up` precarga también el modelo de embeddings si `GROUPING_ENABLED`, con `num_ctx` 512.
- `app/main.py`: `start_warm_up` también corre si `GROUPING_ENABLED`.
- `app/email_attachments.py`: `move_to_ticket(..., comment=None)` acepta el comentario destino.
- `app/similarity.py` (nuevo): `embed()` llama a `/api/embed` con keep_alive y `num_ctx` 512 y deja
  `log_timing`. `score()` guarda en caché los vectores en `ticket_embeddings`, y `remaining(deadline)` controla el plazo.
- `app/grouping.py` (nuevo), con:
  - `group_text` (sin citas, sin firma, enmascarado) y `detect()`: candidatos A (7 días) y B (48 h),
    mejor candidato y confirmación de la IA con esquema JSON. Nunca lanza excepciones y respeta el plazo de 15 s.
  - Caso A: `merge_ticket`, `merge_draft`, `send_merge_notice` y `undo_merge`.
  - Caso B: `link_ticket`, `refresh_incident`, `undo_link`, `propagate_assignment`, `final_reply`
    (quita el nombre del cliente del principal y aplica `_redact`) y `resolve_related`.
  - Después de crear: `after_ticket_created(ticket_id, cases)` y `after_draft_created(draft_id)`.
  - Lectura y permisos: `can_separate`, `staff_view`, `incidents` y `merged_source_ids`.

## Falta

1. **Esquemas** (`app/schemas.py`, antes de `TicketDetail` en la línea ~367):
   - `GroupItem`: `merge_id`, `link_id`, `ticket_id`, `draft_id`, `title`, `status`, `similarity` (todos
     opcionales) y `can_separate`.
   - `GroupingRead`: `merged_from`, `merged_into`, `principal`, `related` e `incident`.
   - `TicketDetail` += `merged_into: Optional[int]` y `grouping: Optional[GroupingRead]`.
   - `QueueItem` (línea ~639) += `incident: bool = False`.
   - `DraftRead` += `merged_into: Optional[int]`.
   - `IncidentRead`: `principal_id`, `title` y `clients`.
2. **`app/routers/tickets.py`**:
   - `create_ticket`: si `config.GROUPING_ENABLED`, `background_tasks.add_task(grouping.after_ticket_created, id)`
     **antes** de las tareas de análisis y asignación.
   - `get_ticket`: `merged_into` para todos; `grouping` solo para el personal (`staff_view`).
   - `update_ticket`, después del commit:
     - Si pasó a `resolved`: `grouping.resolve_related`, commit y un `mailer.send_email` por correo en `background_tasks`.
     - Si cambió el responsable: `grouping.propagate_assignment`, commit y `assignment_notice.schedule` por cada uno.
3. **`app/email_processing.py`**:
   - Camino `created`: después del commit, `grouping.after_ticket_created(ticket.id)`. Si devuelve `"merged"`,
     no se manda `send_ticket_created` ni `assign_new`.
   - Camino `draft_created`: después del commit, `grouping.after_draft_created(draft.id)`.
4. **`app/routers/drafts.py`**:
   - `confirm_draft`: tarea de fondo `grouping.after_ticket_created(ticket.id, ("link",))` antes de `assign_new`.
   - `_draft_read`: `merged_into`.
5. **`app/nav.py`**:
   - Excluir `grouping.merged_source_ids(db)` en `queue()` y en `_resolved_today_ids`.
   - En los items de la cola, `"incident": ticket.id in grouping.incident_ids(db)`.
6. **Router nuevo `app/routers/grouping.py`** (registrarlo en `app/main.py`):
   - `POST /api/merges/{id}/undo` y `POST /api/links/{id}/undo`: 403 si `can_separate` es falso; commit y
     después `attachments.remove_files`.
   - `GET /api/incidents`: solo coordinador y admin.
7. **`static/app.js`** y `static/styles.css`:
   - Detalle del personal: "Fusionado desde #15 · Separar", "Fusionado en #12", "Relacionado con #N" e
     "Incidente · N tickets relacionados" con la lista y "Separar" en cada uno.
   - Portal: "Su mensaje se agregó a su solicitud #12".
   - Borradores: "Agregado a #12".
   - Etiqueta "Incidente" en la cola y franja de Inicio (coordinador y admin) con `/api/incidents`.
   - Textos de History: `merged`, `merged_from`, `unmerged`, `unmerged_from`, `linked` y `unlinked`.
   - Registro de cambios: las 5 acciones nuevas en `CHANGE_ACTIONS`.
8. **Pruebas** `tests/test_grouping.py`: todas las de la sección 5 del plan, con `similarity.score` y
   `chatbot.post_chat` simulados (aceptar `task` y `timeout` como kwargs) y `GROUPING_ENABLED` con monkeypatch.
9. **Verificación**:
   - `docker compose up --build -d`, esperar 5 s, `docker compose exec app pytest -q` y `node --check` de `app.js`.
   - Medir con el Ollama real cuánto tarda la detección por ticket.
   - Commit aparte ("Agrupación de tickets similares"), sin push.

## Siguiente paso exacto
Abrir `app/schemas.py` y agregar `GroupItem`, `GroupingRead` e `IncidentRead` justo antes de
`class TicketDetail(TicketRead):`. Después, sumar los campos nuevos a `TicketDetail`, `QueueItem` y `DraftRead`
(punto 1 de "Falta"). Luego seguir en orden con los puntos 2 a 9.

## Notas
- Si se decide **no** seguir con esta función, hay que revertir:
  - Volver `docker-compose.yml` a `OLLAMA_MAX_LOADED_MODELS=1` y `docker compose up -d ollama`.
  - Borrar el modelo: `docker compose exec ollama ollama rm qwen3-embedding:0.6b`.
  - `git checkout -- .env.example app/chatbot.py app/config.py app/email_attachments.py app/main.py app/models.py docker-compose.yml`
    y borrar `app/grouping.py` y `app/similarity.py`.
- No hace falta borrar la base: solo hay tablas nuevas (`create_all` las crea).
- Nada de esto se probó todavía con pytest.
