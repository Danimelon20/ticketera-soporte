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

function mountSideNav(container, callbacks) {
    const root = createRoot(container);
    return {
        update(state) {
            root.render(
                <StrictMode>
                    <SideNav {...state} {...callbacks} />
                </StrictMode>,
            );
        },
    };
}

window.TicketeraIslands = { mountSideNav };
window.dispatchEvent(new Event('ticketera:islands-ready'));
