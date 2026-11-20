// Bienvenida y sugerencias del asistente por rol (antes CHAT_WELCOME y CHAT_SUGGESTIONS en app.js).
// Lo que el asistente sabe calcular sale del catálogo de app/assistant_queries.py; lo demás es ayuda general.
export const CHAT_WELCOME = {
    requester: 'Hola. Puedo contarte cómo van tus tickets o ayudarte a describir un problema.',
    technician: 'Hola. Puedo darte cifras de tus tickets (abiertos, vencidos, por vencer, resueltos y tu carga) o ayudarte con pasos de diagnóstico.',
    coordinator: 'Hola. Puedo darte cifras del equipo: tickets abiertos y por departamento, vencidos, por vencer, resueltos, carga por técnico y quién está ausente.',
    admin: 'Hola. Puedo darte cifras del equipo (abiertos, vencidos, resueltos, carga y ausencias), de los correos por clasificar y de la precisión de la IA.',
};

export const CHAT_SUGGESTIONS = {
    requester: ['¿Cómo va mi ticket?', '¿Qué significa En proceso?', 'Ayúdame a describir mi problema'],
    technician: ['Resume mis tickets abiertos', '¿Tengo tickets vencidos?', '¿Cuántos tickets resolví esta semana?'],
    coordinator: ['Resume el estado del equipo', '¿Quién tiene más carga?', '¿Quién está de vacaciones?'],
    admin: ['Resume el estado del equipo', '¿Cuántos correos hay por clasificar?', '¿Qué tan precisa fue la IA esta semana?'],
};

// Lo que se manda como historial: los últimos 10 turnos, cada uno de hasta 2000 caracteres
export const HISTORY_LIMIT = 10;
export const TURN_CHARS = 2000;
export const MESSAGE_CHARS = 1000;
export const PENDING_TEXT = 'Pensando... (puede tardar hasta un minuto)';
export const DATA_TEXT = 'Consultando datos…';
export const OFFLINE_TEXT = 'No se pudo conectar con el servidor';
