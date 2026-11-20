// Lee la respuesta del asistente mientras el modelo la escribe (/api/chat/stream manda texto por partes).
// onText recibe el texto acumulado cada vez que llega una parte. Devuelve el texto completo.
// Sin React a propósito: se puede probar en Node con una Response falsa.
export async function readReply(response, onText) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let reply = '';
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        reply += decoder.decode(value, { stream: true });
        onText(reply);
    }
    reply += decoder.decode();
    onText(reply);
    return reply;
}
