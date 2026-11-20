// Puente entre app.js (JavaScript de siempre) y las islas de React.
//
// app.js llama a window.TicketeraIslands.mountSideNav(contenedor, { onModuleClick, onSectionClick }) una sola vez
// y recibe { update(estado) }. Cada vez que algo cambia (inicio de sesión, navegación, contadores), app.js llama a
// update con un estado simple (sin funciones) y React vuelve a dibujar solo lo que cambió.
//
// Este archivo es un <script type="module">: corre después de app.js. Por eso, al terminar, avisa con el evento
// «ticketera:islands-ready»; app.js revisa primero si window.TicketeraIslands ya existe y solo espera el evento si
// todavía no.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import SideNav from './SideNav.jsx';
import BellPanel from './BellPanel.jsx';
import ChatPanel from './ChatPanel.jsx';

// Crea la raíz de React en el contenedor una sola vez y devuelve { update(estado) }: cada update vuelve a dibujar
// el componente con el estado nuevo (React solo cambia lo que difiere).
// resetKey (opcional) es la «key» del componente: si cambia, React descarta el componente con su estado propio y
// crea uno nuevo, vacío (el asistente al cerrar sesión)
function mountIsland(Component, container, callbacks) {
    const root = createRoot(container);
    return {
        update({ resetKey, ...state }) {
            root.render(
                <StrictMode>
                    <Component key={resetKey} {...state} {...callbacks} />
                </StrictMode>,
            );
        },
    };
}

window.TicketeraIslands = {
    mountSideNav: (container, callbacks) => mountIsland(SideNav, container, callbacks),
    mountBellPanel: (container, callbacks) => mountIsland(BellPanel, container, callbacks),
    mountChatPanel: (container, callbacks) => mountIsland(ChatPanel, container, callbacks),
};
window.dispatchEvent(new Event('ticketera:islands-ready'));
