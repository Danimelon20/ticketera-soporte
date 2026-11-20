// Menú lateral del personal: SOLO dibuja. Qué grupos y secciones hay (por rol e interruptor), a dónde lleva cada
// una, qué grupos están abiertos y qué hace un clic lo decide app.js (navModulesFor, goRoute). Este componente
// recibe todo eso como props y avisa de los clics con onModuleClick / onSectionClick.
// Usa las mismas clases y atributos que el menú anterior, así que el CSS no cambia.
import { NAV_ICONS } from './navIcons.js';

function NavIcon({ name }) {
    return (
        <svg className="nav-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={NAV_ICONS[name] || NAV_ICONS.tickets} />
        </svg>
    );
}

function NavCount({ name, tone, counts }) {
    const value = counts[name];
    const classes = ['nav-count'];
    if (tone === 'alert' && value > 0) classes.push('is-alert');
    if (tone === 'warn' && value > 0) classes.push('is-warn');
    return (
        <span className={classes.join(' ')} data-count={name} data-tone={tone || undefined}>
            {typeof value === 'number' ? String(value) : ''}
        </span>
    );
}

// aria-current solo en la sección abierta; los demás botones no llevan el atributo
function current(active) {
    return active ? 'page' : undefined;
}

export default function SideNav({ modules, activeSectionId, activeModuleId, openModuleIds, counts,
    onModuleClick, onSectionClick }) {
    const open = new Set(openModuleIds);
    return (
        <>
            <h2 className="nav-modules-title" id="navModulesTitle">Módulos</h2>
            <ul className="nav-modules" aria-labelledby="navModulesTitle">
                {modules.map(module => {
                    const single = module.sections.length === 1;
                    // Con un solo grupo (técnico) queda siempre abierto
                    const expanded = modules.length === 1 || open.has(module.id);
                    const sectionsId = `navSections-${module.id}`;
                    const singleActive = single && module.sections[0].id === activeSectionId;
                    const moduleClasses = ['nav-module'];
                    if (module.id === activeModuleId) moduleClasses.push('is-active');
                    if (singleActive) moduleClasses.push('active');
                    return (
                        <li key={module.id} className="nav-module-item" data-module={module.id}>
                            <button type="button" className={moduleClasses.join(' ')} data-module-id={module.id}
                                data-nav-id={single ? module.sections[0].id : undefined}
                                data-tooltip={module.label}
                                aria-current={current(singleActive)}
                                aria-expanded={single ? undefined : String(expanded)}
                                aria-controls={single ? undefined : sectionsId}
                                onClick={() => onModuleClick(module.id)}>
                                <NavIcon name={module.icon} />
                                <span className="nav-item-label">{module.label}</span>
                                {!single && <span className="nav-module-caret" aria-hidden="true">▾</span>}
                            </button>
                            {!single && (
                                <ul className="nav-sections" id={sectionsId} hidden={!expanded}>
                                    {module.sections.map(section => {
                                        const active = section.id === activeSectionId;
                                        return (
                                            <li key={section.id}>
                                                <button type="button" className={active ? 'nav-item active' : 'nav-item'}
                                                    data-nav-id={section.id} aria-current={current(active)}
                                                    onClick={() => onSectionClick(section.id)}>
                                                    <span className="nav-item-label">{section.label}</span>
                                                    {section.count && (
                                                        <NavCount name={section.count} tone={section.tone} counts={counts} />
                                                    )}
                                                </button>
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}
                        </li>
                    );
                })}
            </ul>
        </>
    );
}
