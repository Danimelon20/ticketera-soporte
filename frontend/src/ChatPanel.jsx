// Contenido del panel del asistente: cabecera, conversación, sugerencias y el cuadro para escribir.
//
// A diferencia del menú y la campana, aquí React tiene ESTADO propio (useState): los mensajes, el texto que se está
// escribiendo y si espera una respuesta. Nacen dentro del panel, no vienen de app.js.
//
// app.js sigue a cargo de: el botón flotante que se arrastra, abrir y cerrar el panel (prop open, onClose), el
// token y el cierre de sesión ante un 401 (fetchChat), y el texto de los errores del servidor (errorMessage, que es
// getErrorMessage). Al cerrar sesión, app.js cambia la «key» del componente y React lo crea de nuevo, vacío.
import { useEffect, useRef, useState } from 'react';
import { readReply } from './chatStream.js';
import {
    CHAT_SUGGESTIONS, CHAT_WELCOME, DATA_TEXT, HISTORY_LIMIT, MESSAGE_CHARS, OFFLINE_TEXT, PENDING_TEXT, TURN_CHARS,
} from './chatTexts.js';

let nextId = 1;
const newMessage = (kind, text) => ({ id: nextId++, kind, text });

export default function ChatPanel({ open, role, roleLabel, fetchChat, errorMessage, onClose, onResetPosition }) {
    // useState(valor inicial) devuelve [valor, función para cambiarlo]. Cambiarlo vuelve a dibujar el componente.
    // Con una función como valor inicial, se calcula solo la primera vez
    const [messages, setMessages] = useState(() => [newMessage('model', CHAT_WELCOME[role] || CHAT_WELCOME.requester)]);
    const [text, setText] = useState('');
    const [busy, setBusy] = useState(false);
    const [showSuggestions, setShowSuggestions] = useState(true);

    // useRef guarda algo que NO se dibuja: el historial que se manda al servidor (solo las preguntas que tuvieron
    // respuesta) y la forma de cancelar una respuesta en curso. Cambiarlos no vuelve a dibujar
    const historyRef = useRef([]);
    const abortRef = useRef(null);
    const messagesRef = useRef(null);
    const inputRef = useRef(null);

    // Cambia un mensaje ya dibujado (el «Pensando...» pasa a ser la respuesta o el error)
    const updateMessage = (id, kind, value) => {
        setMessages(list => list.map(m => (m.id === id ? { ...m, kind, text: value } : m)));
    };

    // Efecto con limpieza: lo que devuelve corre cuando el componente desaparece (cerrar sesión). Cancela una
    // respuesta en curso para que no se escriba en la conversación de otra cuenta
    useEffect(() => () => abortRef.current?.abort(), []);

    // Cada mensaje nuevo (o cada parte de la respuesta) baja la conversación hasta el final
    useEffect(() => {
        const box = messagesRef.current;
        if (box) box.scrollTop = box.scrollHeight;
    }, [messages]);

    // Al abrir el panel: al final de la conversación y con el cursor en el cuadro para escribir
    useEffect(() => {
        if (!open) return;
        const box = messagesRef.current;
        if (box) box.scrollTop = box.scrollHeight;
        inputRef.current?.focus();
    }, [open]);

    async function send(raw) {
        const message = raw.trim().slice(0, MESSAGE_CHARS);
        if (!message || busy) return;
        setBusy(true);
        setShowSuggestions(false);
        const pending = newMessage('pending', PENDING_TEXT);
        setMessages(list => [...list, newMessage('user', message), pending]);
        const history = historyRef.current.slice(-HISTORY_LIMIT);
        const controller = new AbortController();
        abortRef.current = controller;
        try {
            const response = await fetchChat({ message, history }, controller.signal);
            // 401: app.js ya cerró la sesión
            if (!response) return;
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                updateMessage(pending.id, 'notice', errorMessage(data));
                return;
            }
            // Pregunta de datos: el servidor ya eligió la consulta; la respuesta llega completa (5 a 15 s)
            if (response.headers.get('X-Chat-Kind') === 'data') updateMessage(pending.id, 'pending', DATA_TEXT);
            // El texto aparece mientras el modelo lo escribe
            const reply = await readReply(response, partial => {
                if (partial) updateMessage(pending.id, 'model', partial);
            });
            updateMessage(pending.id, 'model', reply);
            historyRef.current = [
                ...historyRef.current,
                { role: 'user', text: message.slice(0, TURN_CHARS) },
                { role: 'model', text: reply.slice(0, TURN_CHARS) },
            ].slice(-HISTORY_LIMIT);
        } catch (err) {
            // Cancelada al cerrar sesión: el componente ya no está
            if (controller.signal.aborted) return;
            updateMessage(pending.id, 'notice', OFFLINE_TEXT);
        } finally {
            if (!controller.signal.aborted) setBusy(false);
        }
    }

    function onSubmit(e) {
        e.preventDefault();
        if (!text.trim() || busy) return;
        const value = text;
        setText('');
        send(value);
    }

    // Enter envía; Mayúsculas + Enter hace un salto de línea
    function onKeyDown(e) {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            e.currentTarget.form.requestSubmit();
        }
    }

    const suggestions = CHAT_SUGGESTIONS[role] || [];
    return (
        <>
            <header className="chat-header">
                <div>
                    <h2 id="chatTitle" className="chat-title">Asistente</h2>
                    <p id="chatRole" className="chat-role">Te ayuda según tu rol: {roleLabel}</p>
                    <button type="button" id="chatResetPosition" className="chat-reset" onClick={onResetPosition}>
                        Restablecer posición
                    </button>
                </div>
                <button type="button" id="chatClose" className="chat-close" aria-label="Cerrar asistente" onClick={onClose}>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2"
                        strokeLinecap="round" aria-hidden="true">
                        <path d="M6 6l12 12M18 6L6 18" />
                    </svg>
                </button>
            </header>
            <div id="chatMessages" ref={messagesRef} className="chat-messages" aria-live="polite">
                {messages.map(m => <div key={m.id} className={`chat-msg ${m.kind}`}>{m.text}</div>)}
            </div>
            <div id="chatSuggestions" className="chat-suggestions" hidden={!showSuggestions}>
                {suggestions.map(s => (
                    <button key={s} type="button" className="chat-suggestion" disabled={busy} onClick={() => send(s)}>{s}</button>
                ))}
            </div>
            <form id="chatForm" className="chat-form" onSubmit={onSubmit}>
                <label htmlFor="chatInput" className="sr-only">Escribe tu pregunta</label>
                {/* Controlado: lo que se ve en el cuadro es el estado «text» */}
                <textarea id="chatInput" ref={inputRef} rows="2" maxLength={MESSAGE_CHARS} placeholder="Escribe tu pregunta"
                    value={text} onChange={e => setText(e.target.value)} onKeyDown={onKeyDown} />
                <button type="submit" id="chatSend" className="chat-send" disabled={busy}>Enviar</button>
            </form>
        </>
    );
}
