OpenCode /init generó comandos inexistentes (make, alembic). Se reemplazó a mano.

## 12/10
- Se compararon tres propuestas de diseño (barra lateral, lista y detalle, tablero por estado).
- Se eligió lista y detalle.
- Se implementó en 6 pasos con OpenCode en la rama rediseno-b.
- Lección aprendida: dividir un cambio grande de interfaz en pasos pequeños y probar cada uno en el navegador.

## 14/10 — Versión 2 (rama v2)
Pasos implementados, cada uno en su propio commit:
1. Tablas y roles de la versión 2 (admin y Agente virtual) y comando `python -m app.create_admin`.
2. Registro sin elección de rol y códigos de invitación de un solo uso.
3. Teléfono y cédula en los tickets, validación de formato y verificación con Hacienda.
4. El coordinador crea tickets a nombre de un solicitante (buscador de solicitantes).
5. Tickets por correo: el Agente virtual lee el buzón con Microsoft Graph, crea el ticket y responde.
6. Verificación por correo con código de 6 dígitos para coordinadores y administradores.
7. "Dar seguimiento" a un ticket y avisos por correo a los seguidores.
8. Directorio de usuarios y panel de Administración (estado, respaldo y registros).
9. Asistente de la página con un modelo local (Qwen3 con Ollama) según el rol.

Problemas encontrados:
- OpenCode borraba código y clases que no tenían que ver con la tarea.
- Los modelos gratuitos tenían límites de uso.
- Por eso se cambió a Claude Code y se agregó `CLAUDE.md` con las reglas del proyecto (cambios mínimos, no tocar `.env`, datos personales, comandos de prueba).
- Con Claude Code, una tarea interrumpida dejó un archivo sin guardar aunque el plan decía que ya existía; el error apareció al reconstruir el contenedor.
- El botón "Asignarme a mí" no hacía nada: el manejador de los botones de estado lo capturaba primero y salía antes de asignar. El error venía de un commit anterior y se corrigió con una sola condición.

Lección aprendida: escribir las reglas del proyecto en un archivo que el asistente lea siempre (`CLAUDE.md`, `AGENTS.md`) y verificar cada paso con las pruebas, el contenedor reconstruido y el navegador, en vez de confiar en el resumen de la herramienta.
