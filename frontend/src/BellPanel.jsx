// Contenido del panel de avisos (campana): el título, «Marcar todas como leídas» y la lista. SOLO dibuja.
// app.js decide qué avisos hay (polling de cada minuto), arma el texto de cada uno («vence en 2 h 05»), abre y
// cierra el panel y hace lo que pide cada clic (onOpenNotice, onReassign, onReadAll).
// Usa las mismas clases que antes (bell-head, bell-list, bell-item…), así que el CSS no cambia.
import { useEffect, useRef } from 'react';

function Notice({ item, onOpenNotice, onReassign }) {
    const classes = ['bell-item'];
    if (!item.read) classes.push('is-unread');
    if (item.urgent) classes.push('is-urgent');
    return (
        <li className={classes.join(' ')}>
            <button type="button" className="bell-open" onClick={() => onOpenNotice(item.key)}>
                <span className="bell-text">{item.text}</span>
                {item.needsReview && <span className="ui-tag is-warn bell-review">Pendiente de revisar</span>}
                {!item.read && <span className="sr-only"> (sin leer)</span>}
            </button>
            {item.suggestionName && (
                <button type="button" className="ui-btn ui-btn-secondary ui-btn-sm bell-reassign"
                    onClick={() => onReassign(item.key)}>
                    Reasignar a {item.suggestionName}
                </button>
            )}
        </li>
    );
}

export default function BellPanel({ open, items, onOpenNotice, onReassign, onReadAll }) {
    // useRef: una referencia a un elemento del DOM que no provoca un nuevo dibujo
    const readAllRef = useRef(null);
    const listRef = useRef(null);
    const anyUnread = items.some(item => !item.read);

    // useEffect: corre DESPUÉS de dibujar. Al abrir el panel, el foco va al primer botón habilitado (como antes),
    // para que quien usa teclado o lector de pantalla quede dentro del panel
    useEffect(() => {
        if (!open) return;
        const readAll = readAllRef.current;
        const first = readAll && !readAll.disabled ? readAll : listRef.current?.querySelector('button');
        first?.focus();
    }, [open]);

    return (
        <>
            <div className="bell-head">
                <h2 id="bellTitle" className="bell-title">Avisos</h2>
                <button type="button" id="bellReadAll" ref={readAllRef} className="link-btn bell-read-all"
                    disabled={!anyUnread} onClick={onReadAll}>
                    Marcar todas como leídas
                </button>
            </div>
            <ul id="bellList" ref={listRef} className="bell-list">
                {items.length === 0
                    ? <li className="bell-empty">No hay avisos de vencimiento.</li>
                    : items.map(item => (
                        <Notice key={item.key} item={item} onOpenNotice={onOpenNotice} onReassign={onReassign} />
                    ))}
            </ul>
        </>
    );
}
