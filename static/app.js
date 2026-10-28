document.addEventListener('DOMContentLoaded', () => {
    const API_BASE = '/api';

    const categorySelect = document.getElementById('category');
    const prioritySelect = document.getElementById('priority');
    const categoryFilter = document.getElementById('categoryFilter');
    const priorityFilter = document.getElementById('priorityFilter');
    const searchInput = document.getElementById('search');
    const ticketList = document.getElementById('ticketList');
    const detailContent = document.getElementById('detailContent');
    const errorMessage = document.getElementById('errorMessage');
    const successMessage = document.getElementById('successMessage');
    const toggleMoreFiltersBtn = document.getElementById('toggleMoreFilters');
    const moreFiltersBlock = document.getElementById('moreFiltersBlock');
    const backToListBtn = document.getElementById('backToList');

    const authScreen = document.getElementById('authScreen');
    const topbar = document.querySelector('.topbar');
    const mainContent = document.querySelector('main');
    const loginPanel = document.getElementById('loginPanel');
    const signupPanel = document.getElementById('signupPanel');
    const activatePanel = document.getElementById('activatePanel');
    const forgotPanel = document.getElementById('forgotPanel');
    const loginForm = document.getElementById('loginForm');
    const signupForm = document.getElementById('signupForm');
    const activateForm = document.getElementById('activateForm');
    const confirmEmailNotice = document.getElementById('confirmEmailNotice');
    const sessionNotice = document.getElementById('sessionNotice');
    // Mismo texto que app/security.py: la sesión venció por inactividad o por tiempo total
    const SESSION_EXPIRED = 'Su sesión venció. Inicie sesión de nuevo.';
    const switchAccountBtn = document.getElementById('switchAccount');
    const currentUserDisplay = document.getElementById('currentUserDisplay');

    const STATE_MAP = {
        new: 'Nuevo',
        in_progress: 'En proceso',
        resolved: 'Resuelto',
        closed: 'Cerrado',
        // Un ticket cancelado queda "closed"; esta clave solo la usan los conteos y las etiquetas
        cancelled: 'Cancelado'
    };
    const CANCEL_REASON_LABELS = {
        no_longer_applies: 'Ya no aplica',
        duplicate: 'Duplicado',
        no_response: 'Sin respuesta del cliente',
        test_spam: 'Prueba o spam'
    };

    const ROLE_MAP = {
        requester: 'Solicitante',
        technician: 'Técnico',
        coordinator: 'Coordinador',
        agent: 'Agente virtual',
        admin: 'Administrador'
    };

    let categoryMap = {};
    let priorityMap = {};
    let priorityLevelMap = {};
    let userMap = {};
    let userRoleMap = {};
    // Solo técnicos: available, vacation o away
    let userAvailabilityMap = {};
    let currentStatus = '';
    let selectedTicketId = null;
    let activityFilter = 'all';
    let currentTicket = null;

    let selectedRequesterId = null;
    let requesterResults = [];
    let requesterSearchTimer = null;
    let requesterSearchSeq = 0;

    let authToken = sessionStorage.getItem('ticketeraToken');
    // Arriba para que exista antes del primer showAuthScreen
    let draftsPollTimer = null;
    let currentUser = null;

    function getFilters() {
        return {
            status: currentStatus || undefined,
            category_id: categoryFilter.value || undefined,
            priority_id: priorityFilter.value || undefined,
            search: searchInput.value || undefined
        };
    }

    function getErrorMessage(data) {
        const detail = data && data.detail;
        if (typeof detail === 'string' && detail) {
            return detail;
        }
        if (Array.isArray(detail) && detail.length > 0 && detail[0] && typeof detail[0].msg === 'string') {
            return detail[0].msg.replace(/^Value error, /, '');
        }
        return 'No se pudo completar la acción';
    }

    // Mismas reglas que app/validators.py (solo comodidad: el servidor también valida)
    function validatePhone(value) {
        let digits = value.replace(/[^0-9]/g, '');
        if (digits.length === 11 && digits.startsWith('506')) {
            digits = digits.slice(3);
        }
        return digits.length === 8 ? null : 'El teléfono debe tener 8 dígitos';
    }

    function validateIdNumber(value) {
        const digits = value.replace(/[^0-9]/g, '');
        return [9, 11, 12].includes(digits.length)
            ? null
            : 'La cédula debe tener 9 dígitos, o 11 o 12 si es DIMEX';
    }

    // 401: vuelve al inicio de sesión; si la sesión venció, lo avisa ahí mismo
    function handleUnauthorized(detail) {
        sessionStorage.removeItem('ticketeraToken');
        authToken = null;
        currentUser = null;
        showAuthScreen();
        if (detail === SESSION_EXPIRED) {
            sessionNotice.textContent = SESSION_EXPIRED;
            sessionNotice.hidden = false;
        } else {
            showError('Su sesión terminó, inicie sesión de nuevo');
        }
    }

    // silent: no muestra el error (quien llama lo muestra donde corresponde); el error lleva .status
    async function apiFetch(endpoint, options = {}) {
        const { silent, ...fetchOptions } = options;
        hideError();
        const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;
        const headers = {
            'Accept': 'application/json',
            ...(options.headers || {})
        };
        if (authToken) {
            headers['Authorization'] = `Bearer ${authToken}`;
        }

        const response = await fetch(url, {
            ...fetchOptions,
            headers
        });

        if (response.status === 401) {
            const data = await response.json().catch(() => ({}));
            handleUnauthorized(data.detail);
            throw new Error('Unauthorized');
        }

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            let message = errorData.detail;
            if (response.status === 422 && endpoint.startsWith('/auth/') && Array.isArray(message)) {
                message = 'La contraseña debe tener al menos 8 caracteres';
            } else if ((endpoint === '/tickets' || endpoint === '/profile' || endpoint.startsWith('/account/')
                || endpoint.startsWith('/accounts/') || endpoint.startsWith('/account-requests')
                || endpoint.startsWith('/account-reports') || endpoint.startsWith('/drafts/')
                || endpoint.endsWith('/view-conversation'))
                && ['POST', 'PUT'].includes(options.method)) {
                message = getErrorMessage(errorData);
            } else if (response.status === 401 && endpoint.includes('/auth/login')) {
                message = errorData.detail || 'Correo o contraseña incorrectos';
            } else if (Array.isArray(message)) {
                message = message.map(m => (m && m.msg) ? m.msg : String(m)).join('. ');
            }
            if (!silent) showError(message || `Error ${response.status}: ${response.statusText}`);
            const error = new Error(message || `Error ${response.status}`);
            error.status = response.status;
            throw error;
        }

        if (response.status === 204) {
            return null;
        }

        if (options.responseType === 'blob') {
            return response;
        }

        return response.json();
    }

    function getInitials(name) {
        if (!name) return 'US';
        const words = name.trim().split(/\s+/);
        if (words.length >= 2) {
            return (words[0][0] + words[1][0]).toUpperCase();
        } else if (words.length === 1 && words[0].length >= 2) {
            return words[0].substring(0, 2).toUpperCase();
        } else if (words.length === 1 && words[0].length === 1) {
            return words[0][0].toUpperCase();
        }
        return 'US';
    }

    function showApp() {
        if (authScreen) authScreen.style.display = 'none';
        if (topbar) topbar.style.display = 'flex';
        if (mainContent) mainContent.style.display = 'block';

        if (currentUser) {
            const userNameEl = document.querySelector('.topbar-user-name');
            const userRoleEl = document.querySelector('.topbar-user-role');
            const userAvatarEl = document.querySelector('.topbar-avatar');
            const openNewTicketBtn = document.getElementById('openNewTicket');
            if (userNameEl) userNameEl.textContent = currentUser.name || currentUser.email;
            if (userRoleEl) userRoleEl.textContent = ROLE_MAP[currentUser.role] || currentUser.role;
            if (userAvatarEl) {
                userAvatarEl.textContent = getInitials(currentUser.name);
            }
            if (openNewTicketBtn) {
                // Ni el técnico ni el administrador crean tickets
                openNewTicketBtn.style.display = ['technician', 'admin'].includes(currentUser.role) ? 'none' : 'block';
            }
        }

        Promise.all([loadCategories(), loadPriorities(), loadUsers()]).then(async () => {
            await loadTickets(getFilters());
            openLinkedTicket();
        });

        renderInvitesSection();
        updateRoleTabs();
        if (profileRequired()) {
            showProfile();
        } else {
            showView('tickets');
        }
        refreshDraftsBadge();
        refreshReportsBadge();
        // El personal ve los contadores en el menú lateral; el solicitante no tiene borradores
        setupStaffShell();
        // El personal entra por la ruta del enlace (#/módulo/sección) o al inicio de su rol
        if (isStaffShell() && !profileRequired()) startStaffRoute();
        setupPortal();
        setupChat();
    }

    // Enlace de un correo (#ticket/N): se abre ese ticket después de iniciar sesión
    let linkedTicketId = null;
    const ticketLink = window.location.hash.match(/^#ticket\/(\d+)$/);
    if (ticketLink) linkedTicketId = Number(ticketLink[1]);

    function openLinkedTicket() {
        if (!linkedTicketId || !currentUser || profileRequired()) return;
        const ticketId = linkedTicketId;
        linkedTicketId = null;
        // El personal pasa a la ruta del ticket (#/módulo/sección/N); el portal queda sin ruta
        if (!isStaffShell()) history.replaceState(null, '', window.location.pathname + window.location.search);
        if (isRequesterPortal()) openPortalTicket(ticketId);
        else if (isStaffShell()) openTicketById(ticketId);
    }

    function showAuthScreen() {
        document.body.classList.remove('profile-required');
        sessionNotice.hidden = true;
        resetEmailChange();
        loginPortal = null;
        forgotMode = false;
        stopDraftsPolling();
        teardownStaffShell();
        teardownPortal();
        resetChat();
        if (authScreen) authScreen.style.display = 'grid';
        if (topbar) topbar.style.display = 'none';
        setDocumentTitle('Iniciar sesión');
        if (mainContent) mainContent.style.display = 'none';

        ticketList.replaceChildren();
        renderDetailPlaceholder();
        selectedTicketId = null;

        showAuthPanel();
    }

    // ===== Pantallas de inicio: iniciar sesión, crear cuenta de cliente, activar cuenta de personal =====
    const AUTH_ROUTES = { '#crear-cuenta': 'signup', '#activar-cuenta': 'activate' };
    // Enlace del correo de restablecimiento: #restablecer?portal=staff&correo=...
    const RESET_ROUTE = '#restablecer';

    // Cada opción de inicio de sesión solo deja entrar a sus roles (lo verifica el servidor).
    // Son las pestañas Cliente / Personal / Administración de la tarjeta.
    const PORTALS = {
        admin: { title: 'Administración', link: null },
        staff: { title: 'Personal', link: { href: '#activar-cuenta', question: '¿Recibió una invitación?', text: 'Activar mi cuenta' } },
        requester: { title: 'Cliente', link: { href: '#crear-cuenta', question: '¿No tiene cuenta?', text: 'Crear una cuenta' } }
    };
    const LOGIN_PORTAL_KEY = 'ticketeraLoginPortal';

    // Personal y administración entran con Microsoft cuando el servidor lo tiene encendido (MS_LOGIN_ENABLED).
    // Vuelta de Microsoft: #microsoft?code=... (código de un solo uso) o #microsoft?error=clave
    const MICROSOFT_ROUTE = '#microsoft';
    const MICROSOFT_PORTALS = ['staff', 'admin'];
    const MICROSOFT_ERRORS = {
        state: 'El inicio de sesión con Microsoft venció o no es válido. Intente de nuevo.',
        failed: 'Microsoft no confirmó su identidad. Intente de nuevo.',
        unavailable: 'No se pudo conectar con Microsoft. Intente más tarde.',
        tenant: 'Esa cuenta Microsoft no pertenece a la organización.',
        not_staff: 'Esa cuenta Microsoft no corresponde a nadie del personal. Si es nuevo, active su invitación.',
        requester: 'Esa es una cuenta de cliente: entre por la pestaña Cliente.',
        inactive: 'Su cuenta está desactivada. Comuníquese con el administrador.',
        wrong_portal: 'Esta cuenta no entra por esta opción. Pruebe con la otra pestaña.',
        already_linked: 'Su cuenta ya está enlazada a otra cuenta Microsoft. Comuníquese con el administrador.',
        invitation: 'La invitación no es válida o venció. Pida una nueva al administrador.',
        invitation_email: 'Entre con la cuenta Microsoft del correo al que llegó la invitación.'
    };
    const MICROSOFT_AUTH_HOST = 'https://login.microsoftonline.com/';
    const microsoftLoginBox = document.getElementById('microsoftLogin');
    const microsoftLoginBtn = document.getElementById('microsoftLoginBtn');
    const emergencyAccessLink = document.getElementById('emergencyAccessLink');
    let microsoftLogin = false;
    // Administración: el formulario de contraseña y código solo después de "Acceso de emergencia"
    let emergencyAccess = false;
    const loginTabs = document.getElementById('loginTabs');
    const loginExtra = document.getElementById('loginExtra');
    const loginExtraLink = document.getElementById('loginExtraLink');
    let loginPortal = null;
    // "Olvidé mi contraseña" de la opción elegida (loginPortal)
    let forgotMode = false;

    function showAuthPanel() {
        if (window.location.hash.startsWith(MICROSOFT_ROUTE)) {
            finishMicrosoftLogin();
            return;
        }
        if (window.location.hash.startsWith(RESET_ROUTE)) {
            openResetLink();
            return;
        }
        const route = AUTH_ROUTES[window.location.hash];
        const panel = route || (forgotMode && loginPortal ? 'forgot' : 'login');
        // Sin opción elegida: la última usada en este navegador, o Personal
        if (panel === 'login' && !loginPortal) applyPortal(savedLoginPortal());
        loginPanel.hidden = panel !== 'login';
        signupPanel.hidden = panel !== 'signup';
        activatePanel.hidden = panel !== 'activate';
        forgotPanel.hidden = panel !== 'forgot';
        confirmEmailNotice.hidden = true;
        if (panel !== 'activate') clearActivationResult();
        if (panel !== 'forgot') clearForgot();
        hideError();
    }

    function clearHash() {
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }

    function savedLoginPortal() {
        try {
            const saved = localStorage.getItem(LOGIN_PORTAL_KEY);
            return PORTALS[saved] ? saved : 'staff';
        } catch (err) {
            return 'staff';
        }
    }

    // Marca la pestaña elegida y ajusta los textos de la tarjeta
    function applyPortal(portal) {
        const info = PORTALS[portal];
        if (!info) return;
        loginPortal = portal;
        loginTabs.querySelectorAll('[data-portal]').forEach(tab => {
            const selected = tab.dataset.portal === portal;
            tab.setAttribute('aria-selected', selected ? 'true' : 'false');
            tab.tabIndex = selected ? 0 : -1;
            if (selected) loginForm.setAttribute('aria-labelledby', tab.id);
        });
        document.getElementById('loginAdminNotice').hidden = portal !== 'admin';
        applyMicrosoftLogin();
        loginExtra.hidden = !info.link;
        if (info.link) {
            document.getElementById('loginExtraText').textContent = info.link.question;
            loginExtraLink.href = info.link.href;
            loginExtraLink.textContent = info.link.text;
        }
        try {
            localStorage.setItem(LOGIN_PORTAL_KEY, portal);
        } catch (err) {
            // Sin almacenamiento: la próxima vez vuelve a Personal
        }
    }

    function openPortal(portal, focusEmail = true) {
        if (!PORTALS[portal]) return;
        emergencyAccess = false;
        applyPortal(portal);
        // El correo escrito se conserva al cambiar de pestaña; la contraseña no
        document.getElementById('loginPassword').value = '';
        clearHash();
        showAuthPanel();
        if (focusEmail) document.getElementById('loginEmail').focus();
    }

    // Muestra el botón de Microsoft o el formulario según la pestaña elegida
    function applyMicrosoftLogin() {
        const useMicrosoft = microsoftLogin && MICROSOFT_PORTALS.includes(loginPortal);
        microsoftLoginBox.hidden = !useMicrosoft;
        if (useMicrosoft) microsoftLoginBtn.href = `/auth/microsoft/login?portal=${loginPortal}`;
        emergencyAccessLink.hidden = !(useMicrosoft && loginPortal === 'admin' && !emergencyAccess);
        loginForm.hidden = useMicrosoft && !(loginPortal === 'admin' && emergencyAccess);
        document.getElementById('activateSubmit').textContent = microsoftLogin ? 'Activar con Microsoft' : 'Activar cuenta';
    }

    async function loadMicrosoftLogin() {
        try {
            const response = await fetch(`${API_BASE}/auth/microsoft/config`, { headers: { 'Accept': 'application/json' } });
            const data = response.ok ? await response.json() : {};
            microsoftLogin = Boolean(data.enabled);
        } catch (err) {
            microsoftLogin = false;
        }
        applyMicrosoftLogin();
    }

    emergencyAccessLink.addEventListener('click', () => {
        emergencyAccess = true;
        applyMicrosoftLogin();
        document.getElementById('loginEmail').focus();
    });

    // Vuelta de Microsoft: se quita el código de la barra de direcciones antes de canjearlo
    function finishMicrosoftLogin() {
        const params = new URLSearchParams(window.location.hash.slice(MICROSOFT_ROUTE.length).replace(/^\?/, ''));
        clearHash();
        const portal = params.get('portal');
        if (PORTALS[portal]) applyPortal(portal);
        showAuthPanel();
        const code = params.get('code');
        if (code) exchangeMicrosoftCode(code);
        else showError(MICROSOFT_ERRORS[params.get('error')] || MICROSOFT_ERRORS.failed);
    }

    async function exchangeMicrosoftCode(code) {
        let result;
        try {
            result = await postAuth('/auth/microsoft/exchange', { code });
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(getErrorMessage(result.data));
            return;
        }
        completeLogin(result.data);
    }

    loginTabs.addEventListener('click', (e) => {
        const tab = e.target.closest('[data-portal]');
        if (tab) openPortal(tab.dataset.portal, false);
    });

    // Flechas izquierda/derecha, Inicio y Fin entre las pestañas
    loginTabs.addEventListener('keydown', (e) => {
        const tabs = [...loginTabs.querySelectorAll('[data-portal]')];
        const index = tabs.indexOf(document.activeElement);
        if (index === -1) return;
        let next = null;
        if (e.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
        if (e.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
        if (e.key === 'Home') next = tabs[0];
        if (e.key === 'End') next = tabs[tabs.length - 1];
        if (!next) return;
        e.preventDefault();
        openPortal(next.dataset.portal, false);
        next.focus();
    });

    window.addEventListener('hashchange', () => {
        if (currentUser) return;
        // Desde registro o activación, "Volver al inicio de sesión" lleva a las opciones
        loginPortal = null;
        forgotMode = false;
        showAuthPanel();
    });

    // Login submit
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            hideError();
            const email = document.getElementById('loginEmail').value.trim();
            const password = document.getElementById('loginPassword').value;

            try {
                const data = await apiFetch('/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password, portal: loginPortal })
                });
                loginForm.reset();
                if (data.requires_code) {
                    showVerifyScreen(data);
                    return;
                }
                completeLogin(data);
            } catch (err) {
                // apiFetch ya mostró el error; si falta confirmar el correo se ofrece el código
                if (err.message === CONFIRM_EMAIL_MESSAGE) {
                    pendingSignupEmail = email;
                    confirmEmailNotice.hidden = false;
                }
            }
        });
    }

    const CONFIRM_EMAIL_MESSAGE = 'Confirme su correo para iniciar sesión';
    let pendingSignupEmail = null;

    document.getElementById('confirmEmailBtn').addEventListener('click', async () => {
        if (!pendingSignupEmail) return;
        let result;
        try {
            result = await postAuth('/auth/register/resend', { email: pendingSignupEmail });
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(getErrorMessage(result.data));
            return;
        }
        loginForm.reset();
        showSignupVerify(pendingSignupEmail, result.data.email_hint);
    });

    signupForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const email = document.getElementById('signupEmail').value.trim();
        const password = document.getElementById('signupPassword').value;
        let result;
        try {
            result = await postAuth('/auth/register', { email, password });
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(result.status === 422 && Array.isArray(result.data.detail)
                ? 'La contraseña debe tener al menos 8 caracteres'
                : getErrorMessage(result.data));
            return;
        }
        signupForm.reset();
        showSignupVerify(email, result.data.email_hint);
    });

    // ----- Activar cuenta de personal -----
    const activateResult = document.getElementById('activateResult');
    const activatePassword = document.getElementById('activatePassword');
    const activateLinks = document.getElementById('activateLinks');
    const activateNotice = document.getElementById('activateNotice');
    const activateResendBtn = document.getElementById('activateResend');
    const copyPasswordBtn = document.getElementById('copyPasswordBtn');
    let activationPassword = null;
    let activateResendTimer = null;

    function clearActivationResult() {
        // La contraseña se muestra una sola vez: al salir se borra
        activationPassword = null;
        activatePassword.textContent = '';
        activateResult.hidden = true;
        activateForm.hidden = false;
        activateLinks.hidden = false;
        copyPasswordBtn.textContent = 'Copiar';
    }

    activateForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        activateNotice.textContent = '';
        const email = document.getElementById('activateEmail').value.trim();
        const code = document.getElementById('activateCode').value.replace(/\D/g, '');
        if (code.length !== 6) {
            showError('Escriba los 6 dígitos del código');
            return;
        }
        let result;
        try {
            result = await postAuth(microsoftLogin ? '/auth/activate/microsoft' : '/auth/activate', { email, code });
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(getErrorMessage(result.data));
            return;
        }
        if (microsoftLogin) {
            // Sigue en Microsoft; a la vuelta la cuenta queda activada y con la sesión iniciada
            if (typeof result.data.auth_url !== 'string' || !result.data.auth_url.startsWith(MICROSOFT_AUTH_HOST)) {
                showError('No se pudo conectar con Microsoft. Intente más tarde.');
                return;
            }
            window.location.assign(result.data.auth_url);
            return;
        }
        activateForm.reset();
        activationPassword = result.data.password;
        activatePassword.textContent = activationPassword;
        activateForm.hidden = true;
        activateLinks.hidden = true;
        activateResult.hidden = false;
        copyPasswordBtn.focus();
    });

    copyPasswordBtn.addEventListener('click', async () => {
        if (!activationPassword) return;
        try {
            await navigator.clipboard.writeText(activationPassword);
            copyPasswordBtn.textContent = 'Copiada';
        } catch (err) {
            showError('No se pudo copiar. Selecciónela y cópiela a mano.');
        }
    });

    document.getElementById('activateToLogin').addEventListener('click', () => {
        clearActivationResult();
        openPortal('staff');
    });

    activateResendBtn.addEventListener('click', async () => {
        hideError();
        const email = document.getElementById('activateEmail').value.trim();
        if (!email) {
            showError('Escriba su correo para reenviar el código');
            return;
        }
        activateResendBtn.disabled = true;
        let result;
        try {
            result = await postAuth('/auth/activate/resend', { email });
        } catch (err) {
            activateResendBtn.disabled = false;
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            activateResendBtn.disabled = false;
            showError(getErrorMessage(result.data));
            return;
        }
        activateNotice.textContent = 'Si hay una invitación para ese correo, le enviamos un código nuevo.';
        // Misma espera que el servidor: un reenvío por minuto
        let left = RESEND_WAIT_SECONDS;
        clearInterval(activateResendTimer);
        activateResendBtn.textContent = `Reenviar código en ${left} s`;
        activateResendTimer = setInterval(() => {
            left -= 1;
            if (left <= 0) {
                clearInterval(activateResendTimer);
                activateResendBtn.disabled = false;
                activateResendBtn.textContent = 'Reenviar código';
                return;
            }
            activateResendBtn.textContent = `Reenviar código en ${left} s`;
        }, 1000);
    });

    // ----- Olvidé mi contraseña -----
    const forgotTitle = document.getElementById('forgotTitle');
    const forgotEmailForm = document.getElementById('forgotEmailForm');
    const forgotEmailInput = document.getElementById('forgotEmail');
    const forgotCodeForm = document.getElementById('forgotCodeForm');
    const forgotCodeInput = document.getElementById('forgotCode');
    const forgotCodeEmail = document.getElementById('forgotCodeEmail');
    const forgotNotice = document.getElementById('forgotNotice');
    const forgotPasswordFields = document.getElementById('forgotPasswordFields');
    const forgotNewPassword = document.getElementById('forgotNewPassword');
    const forgotConfirmPassword = document.getElementById('forgotConfirmPassword');
    const forgotResendBtn = document.getElementById('forgotResend');
    const forgotResult = document.getElementById('forgotResult');
    const forgotPasswordValue = document.getElementById('forgotPassword');
    const forgotCopyBtn = document.getElementById('forgotCopyBtn');
    const forgotLinks = document.getElementById('forgotLinks');
    let forgotEmail = null;
    let forgotGenerated = null;
    let forgotResendTimer = null;

    function clearForgot() {
        // La contraseña generada se muestra una sola vez: al salir se borra
        forgotGenerated = null;
        forgotEmail = null;
        forgotPasswordValue.textContent = '';
        forgotResult.hidden = true;
        forgotEmailForm.hidden = false;
        forgotCodeForm.hidden = true;
        forgotLinks.hidden = false;
        forgotEmailForm.reset();
        forgotCodeForm.reset();
        forgotNotice.textContent = '';
        forgotCopyBtn.textContent = 'Copiar';
        clearInterval(forgotResendTimer);
        forgotResendBtn.disabled = false;
        forgotResendBtn.textContent = 'Reenviar código';
    }

    function forgotIsRequester() {
        return loginPortal === 'requester';
    }

    function openForgot() {
        if (!PORTALS[loginPortal]) return;
        forgotMode = true;
        clearForgot();
        forgotTitle.textContent = `Olvidé mi contraseña — ${PORTALS[loginPortal].title}`;
        // El solicitante elige su contraseña; al personal se le genera una
        forgotPasswordFields.hidden = !forgotIsRequester();
        forgotNewPassword.required = forgotIsRequester();
        forgotConfirmPassword.required = forgotIsRequester();
        clearHash();
        showAuthPanel();
        forgotEmailInput.focus();
    }

    // Paso de escribir el código sin pedir uno nuevo (enlace del correo o "Ya tengo un código")
    function showForgotCodeStep(email) {
        forgotEmailForm.hidden = true;
        forgotCodeForm.hidden = false;
        forgotCodeEmail.value = email;
        forgotNotice.textContent = 'Escriba el código de 6 dígitos que le llegó al correo. Vence 15 minutos después de enviado.';
        (email ? forgotCodeInput : forgotCodeEmail).focus();
    }

    function openResetLink() {
        const params = new URLSearchParams(window.location.hash.slice(RESET_ROUTE.length).replace(/^\?/, ''));
        // El correo no se queda en la barra de direcciones
        clearHash();
        const portal = params.get('portal');
        if (!PORTALS[portal]) {
            loginPortal = null;
            forgotMode = false;
            showAuthPanel();
            return;
        }
        loginPortal = portal;
        openForgot();
        showForgotCodeStep(params.get('correo') || '');
    }

    function backFromForgot() {
        const portal = loginPortal;
        forgotMode = false;
        clearForgot();
        openPortal(portal);
    }

    function startForgotResendCountdown() {
        // Misma espera que el servidor: un reenvío por minuto
        let left = RESEND_WAIT_SECONDS;
        clearInterval(forgotResendTimer);
        forgotResendBtn.disabled = true;
        forgotResendBtn.textContent = `Reenviar código en ${left} s`;
        forgotResendTimer = setInterval(() => {
            left -= 1;
            if (left <= 0) {
                clearInterval(forgotResendTimer);
                forgotResendBtn.disabled = false;
                forgotResendBtn.textContent = 'Reenviar código';
                return;
            }
            forgotResendBtn.textContent = `Reenviar código en ${left} s`;
        }, 1000);
    }

    document.getElementById('forgotPasswordLink').addEventListener('click', openForgot);
    document.getElementById('forgotBack').addEventListener('click', backFromForgot);
    document.getElementById('forgotHaveCode').addEventListener('click', () => {
        hideError();
        showForgotCodeStep(forgotEmailInput.value.trim());
    });
    document.getElementById('forgotToLogin').addEventListener('click', backFromForgot);

    forgotEmailForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const email = forgotEmailInput.value.trim();
        let result;
        try {
            result = await postAuth('/auth/password-reset', { email, portal: loginPortal });
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(getErrorMessage(result.data));
            return;
        }
        forgotEmail = email;
        forgotCodeEmail.value = email;
        forgotEmailForm.hidden = true;
        forgotCodeForm.hidden = false;
        // Mismo mensaje exista o no la cuenta
        forgotNotice.textContent = 'Si el correo tiene una cuenta activa, le enviamos un código. '
            + 'Vence en 15 minutos. Revise también la carpeta de spam.';
        startForgotResendCountdown();
        forgotCodeInput.focus();
    });

    forgotCodeForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        forgotEmail = forgotCodeEmail.value.trim();
        if (!forgotEmail) return;
        const code = forgotCodeInput.value.replace(/\D/g, '');
        if (code.length !== 6) {
            showError('Escriba los 6 dígitos del código');
            return;
        }
        const body = { email: forgotEmail, portal: loginPortal, code };
        if (forgotIsRequester()) {
            if (forgotNewPassword.value.length < 8) {
                showError('La contraseña debe tener al menos 8 caracteres');
                return;
            }
            if (forgotNewPassword.value !== forgotConfirmPassword.value) {
                showError('Las contraseñas no coinciden');
                return;
            }
            body.new_password = forgotNewPassword.value;
        }
        let result;
        try {
            result = await postAuth('/auth/password-reset/confirm', body);
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(getErrorMessage(result.data));
            return;
        }
        if (forgotIsRequester()) {
            backFromForgot();
            showSuccess('Contraseña restablecida. Inicie sesión con la nueva.');
            return;
        }
        forgotGenerated = result.data.password;
        forgotPasswordValue.textContent = forgotGenerated;
        forgotCodeForm.hidden = true;
        forgotLinks.hidden = true;
        forgotResult.hidden = false;
        forgotCopyBtn.focus();
    });

    forgotResendBtn.addEventListener('click', async () => {
        hideError();
        forgotEmail = forgotCodeEmail.value.trim();
        if (!forgotEmail) {
            showError('Escriba el correo de su cuenta');
            return;
        }
        forgotResendBtn.disabled = true;
        let result;
        try {
            result = await postAuth('/auth/password-reset/resend', { email: forgotEmail, portal: loginPortal });
        } catch (err) {
            forgotResendBtn.disabled = false;
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            forgotResendBtn.disabled = false;
            showError(getErrorMessage(result.data));
            return;
        }
        forgotNotice.textContent = 'Si el correo tiene una cuenta activa, le enviamos un código nuevo.';
        startForgotResendCountdown();
    });

    forgotCopyBtn.addEventListener('click', async () => {
        if (!forgotGenerated) return;
        try {
            await navigator.clipboard.writeText(forgotGenerated);
            forgotCopyBtn.textContent = 'Copiada';
        } catch (err) {
            showError('No se pudo copiar. Selecciónela y cópiela a mano.');
        }
    });

    async function completeLogin(data) {
        sessionStorage.setItem('ticketeraToken', data.token);
        authToken = data.token;
        currentUser = data.user;
        sessionNotice.hidden = true;
        try {
            // Trae también si el perfil está completo
            currentUser = await apiFetch('/auth/me');
        } catch (err) {
            if (!authToken) return; // apiFetch ya volvió al inicio de sesión
        }
        showApp();
    }

    // ===== Verificación en dos pasos =====
    const verifyScreen = document.getElementById('verifyScreen');
    const verifyHint = document.getElementById('verifyHint');
    const verifyForm = document.getElementById('verifyForm');
    const verifyDigits = document.getElementById('verifyDigits');
    const digitInputs = Array.from(document.querySelectorAll('#verifyDigits .code-digit'));
    const verifyError = document.getElementById('verifyError');
    const verifySubmit = document.getElementById('verifySubmit');
    const verifySuccess = document.getElementById('verifySuccess');
    const verifyTimer = document.getElementById('verifyTimer');
    const resendCodeBtn = document.getElementById('resendCode');
    const resendNotice = document.getElementById('resendNotice');
    const backToLoginBtn = document.getElementById('backToLogin');
    const signupHaveAccountBtn = document.getElementById('signupHaveAccount');

    const verifyTitle = document.getElementById('verifyTitle');
    // Inicio de sesión del administrador: 10 minutos. Confirmar una cuenta nueva: 15 minutos.
    const CODE_SECONDS = { login: 10 * 60, signup: 15 * 60 };
    const RESEND_WAIT_SECONDS = 60;

    let challengeId = null;
    let verifyMode = 'login';
    let verifyEmail = null;
    let expiryTimer = null;
    let resendTimer = null;
    let verifyBusy = false;
    let verifyRedirectTimer = null;

    function formatCountdown(seconds) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function startExpiryCountdown() {
        clearInterval(expiryTimer);
        const deadline = Date.now() + CODE_SECONDS[verifyMode] * 1000;
        const tick = () => {
            const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
            if (left === 0) {
                clearInterval(expiryTimer);
                verifyTimer.textContent = verifyMode === 'signup'
                    ? 'El código venció. Pida uno nuevo.'
                    : 'El código venció. Inicie sesión de nuevo.';
                verifySubmit.disabled = true;
                return;
            }
            verifyTimer.textContent = `El código vence en ${formatCountdown(left)}`;
        };
        tick();
        expiryTimer = setInterval(tick, 1000);
    }

    function startResendCountdown() {
        clearInterval(resendTimer);
        const deadline = Date.now() + RESEND_WAIT_SECONDS * 1000;
        resendCodeBtn.disabled = true;
        const tick = () => {
            const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
            if (left === 0) {
                clearInterval(resendTimer);
                resendCodeBtn.disabled = false;
                resendCodeBtn.textContent = 'Reenviar código';
                return;
            }
            resendCodeBtn.textContent = `Reenviar código en ${left} s`;
        };
        tick();
        resendTimer = setInterval(tick, 1000);
    }

    function clearDigits() {
        digitInputs.forEach(input => { input.value = ''; });
    }

    function hideVerifyError() {
        verifyError.hidden = true;
        verifyError.textContent = '';
    }

    function showVerifyError(message) {
        verifyError.textContent = message;
        verifyError.hidden = false;
    }

    function showVerifyScreen(data) {
        verifyMode = 'login';
        verifyEmail = null;
        verifyTitle.textContent = 'Verificación de seguridad';
        openVerifyScreen(data.challenge_id, data.email_hint);
    }

    // Confirmar el correo de una cuenta de cliente nueva
    function showSignupVerify(email, emailHint) {
        verifyMode = 'signup';
        verifyEmail = email;
        verifyTitle.textContent = 'Confirme su correo';
        openVerifyScreen(null, emailHint);
    }

    function openVerifyScreen(newChallengeId, emailHint) {
        resetChat();
        challengeId = newChallengeId;
        // Registro: no se sabe si el correo ya tenía cuenta (la API responde igual)
        verifyHint.textContent = verifyMode === 'signup'
            ? 'Si ese correo está disponible, le enviamos un código de confirmación. '
                + 'Si ya tiene una cuenta con ese correo, le enviamos un aviso para que inicie sesión. '
                + 'Revise también la carpeta de spam.'
            : 'Enviamos un código de 6 dígitos a ' + emailHint;
        signupHaveAccountBtn.hidden = verifyMode !== 'signup';
        clearTimeout(verifyRedirectTimer);
        verifyBusy = false;
        clearDigits();
        hideVerifyError();
        resendNotice.textContent = '';
        verifyForm.hidden = false;
        verifySuccess.hidden = true;
        verifySubmit.disabled = false;
        if (authScreen) authScreen.style.display = 'none';
        verifyScreen.hidden = false;
        startExpiryCountdown();
        startResendCountdown();
        digitInputs[0].focus();
    }

    function backToLogin() {
        clearInterval(expiryTimer);
        clearInterval(resendTimer);
        clearTimeout(verifyRedirectTimer);
        challengeId = null;
        verifyEmail = null;
        verifyBusy = false;
        clearDigits();
        hideVerifyError();
        verifyScreen.hidden = true;
        history.replaceState(null, '', window.location.pathname + window.location.search);
        showAuthScreen();
    }

    // Sin apiFetch: aquí se necesita el código HTTP y no se debe cerrar la sesión ni usar el error global
    async function postAuth(endpoint, body) {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok, status: response.status, data };
    }

    function shakeDigits() {
        verifyDigits.classList.remove('shake');
        void verifyDigits.offsetWidth;
        verifyDigits.classList.add('shake');
    }

    async function submitCode() {
        const code = digitInputs.map(input => input.value).join('');
        if (verifyBusy || !(challengeId || verifyEmail)) return;
        if (!/^\d{6}$/.test(code)) {
            showVerifyError('Escriba los 6 dígitos del código');
            return;
        }
        verifyBusy = true;
        verifySubmit.disabled = true;
        hideVerifyError();

        let result;
        try {
            result = verifyMode === 'signup'
                ? await postAuth('/auth/register/verify', { email: verifyEmail, code })
                : await postAuth('/auth/verify-code', { challenge_id: challengeId, code });
        } catch (err) {
            verifyBusy = false;
            verifySubmit.disabled = false;
            showVerifyError('No se pudo conectar con el servidor');
            return;
        }

        if (result.ok) {
            clearInterval(expiryTimer);
            clearInterval(resendTimer);
            verifyForm.hidden = true;
            verifySuccess.hidden = false;
            verifyRedirectTimer = setTimeout(() => {
                verifyScreen.hidden = true;
                challengeId = null;
                verifyEmail = null;
                verifyBusy = false;
                history.replaceState(null, '', window.location.pathname + window.location.search);
                completeLogin(result.data);
            }, 1500);
            return;
        }

        const message = getErrorMessage(result.data);
        showVerifyError(message);
        if (result.status === 429 || message.includes('Inicie sesión de nuevo')) {
            clearInterval(expiryTimer);
            clearInterval(resendTimer);
            resendCodeBtn.disabled = true;
            verifyRedirectTimer = setTimeout(backToLogin, 3000);
            return;
        }
        shakeDigits();
        clearDigits();
        verifyBusy = false;
        verifySubmit.disabled = false;
        digitInputs[0].focus();
    }

    function fillDigitsFrom(index, text) {
        const digits = text.replace(/\D/g, '').slice(0, digitInputs.length - index).split('');
        digits.forEach((digit, offset) => { digitInputs[index + offset].value = digit; });
        return index + digits.length;
    }

    digitInputs.forEach((input, index) => {
        input.addEventListener('input', () => {
            const value = input.value.replace(/\D/g, '');
            if (value.length > 1) {
                // Autocompletado del sistema o varios dígitos a la vez
                const next = fillDigitsFrom(index, value);
                digitInputs[Math.min(next, digitInputs.length - 1)].focus();
            } else {
                input.value = value;
                if (value && index < digitInputs.length - 1) {
                    digitInputs[index + 1].focus();
                }
            }
            if (digitInputs.every(d => /^\d$/.test(d.value))) {
                submitCode();
            }
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && !input.value && index > 0) {
                e.preventDefault();
                digitInputs[index - 1].value = '';
                digitInputs[index - 1].focus();
            }
        });

        input.addEventListener('paste', (e) => {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text') || '';
            const digits = text.replace(/\D/g, '');
            if (!digits) return;
            const start = digits.length >= digitInputs.length ? 0 : index;
            const next = fillDigitsFrom(start, digits);
            digitInputs[Math.min(next, digitInputs.length - 1)].focus();
            if (digitInputs.every(d => /^\d$/.test(d.value))) {
                submitCode();
            }
        });
    });

    verifyForm.addEventListener('submit', (e) => {
        e.preventDefault();
        submitCode();
    });

    resendCodeBtn.addEventListener('click', async () => {
        if (!(challengeId || verifyEmail)) return;
        resendCodeBtn.disabled = true;
        resendNotice.textContent = '';
        hideVerifyError();

        let result;
        try {
            result = verifyMode === 'signup'
                ? await postAuth('/auth/register/resend', { email: verifyEmail })
                : await postAuth('/auth/resend-code', { challenge_id: challengeId });
        } catch (err) {
            resendCodeBtn.disabled = false;
            showVerifyError('No se pudo conectar con el servidor');
            return;
        }

        if (result.ok) {
            if (verifyMode === 'login') challengeId = result.data.challenge_id;
            verifyBusy = false;
            verifySubmit.disabled = false;
            clearDigits();
            startExpiryCountdown();
            startResendCountdown();
            resendNotice.textContent = verifyMode === 'signup'
                ? 'Si ese correo está disponible, le enviamos un código nuevo. Revise también la carpeta de spam.'
                : 'Le enviamos un código nuevo';
            digitInputs[0].focus();
            return;
        }

        showVerifyError(getErrorMessage(result.data));
        if (result.status === 429) {
            resendCodeBtn.disabled = false;
        } else {
            clearInterval(expiryTimer);
            clearInterval(resendTimer);
            verifyRedirectTimer = setTimeout(backToLogin, 3000);
        }
    });

    backToLoginBtn.addEventListener('click', backToLogin);

    signupHaveAccountBtn.addEventListener('click', () => {
        backToLogin();
        openPortal('requester');
    });

    // ===== Asistente =====
    const chatToggle = document.getElementById('chatToggle');
    const chatPanel = document.getElementById('chatPanel');
    // ----- Ícono del asistente que se arrastra: posición recordada solo en este navegador -----
    const CHAT_POS_KEY = 'ticketeraChatFabPosition';
    const FAB_MARGIN = 12;
    const PANEL_GAP = 12;
    // Menos de esta distancia entre presionar y soltar es un clic, no un arrastre
    const DRAG_THRESHOLD = 6;
    let fabPos = null; // { side: 'left' | 'right', top } o null: abajo a la derecha
    let fabDrag = null;
    let suppressFabClick = false;

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), Math.max(min, max));
    }

    // Espacio libre entre la barra superior y la barra de estado (o el pie del portal),
    // a la derecha de la barra lateral del personal
    function chatBounds() {
        let top = 0;
        let left = 0;
        let bottom = window.innerHeight;
        const nav = document.getElementById('sideNav');
        if (window.innerWidth >= 768 && !nav.hidden && nav.getClientRects().length) left = nav.getBoundingClientRect().right;
        const bar = document.querySelector('.topbar');
        if (bar && bar.getClientRects().length) top = Math.max(0, bar.getBoundingClientRect().bottom);
        ['portalFooter'].forEach(id => {
            const el = document.getElementById(id);
            if (el && !el.hidden && el.getClientRects().length) bottom = Math.min(bottom, el.getBoundingClientRect().top);
        });
        const size = chatToggle.offsetHeight || 56;
        return {
            top, bottom, left, size,
            minTop: top + FAB_MARGIN,
            maxTop: bottom - size - FAB_MARGIN,
        };
    }

    function readFabPos() {
        try {
            const saved = JSON.parse(localStorage.getItem(CHAT_POS_KEY) || 'null');
            if (saved && (saved.side === 'left' || saved.side === 'right') && Number.isFinite(saved.top)) return saved;
        } catch (err) {
            // Sin almacenamiento o dato dañado: posición de siempre
        }
        return null;
    }

    function saveFabPos(pos) {
        try {
            if (pos) localStorage.setItem(CHAT_POS_KEY, JSON.stringify(pos));
            else localStorage.removeItem(CHAT_POS_KEY);
        } catch (err) {
            // No se pudo guardar: dura hasta recargar la página
        }
    }

    function applyFabPos(animate) {
        if (chatToggle.hidden) return;
        const bounds = chatBounds();
        const pos = fabPos || { side: 'right', top: bounds.maxTop };
        chatToggle.classList.toggle('is-settling', Boolean(animate));
        chatToggle.style.right = 'auto';
        chatToggle.style.bottom = 'auto';
        chatToggle.style.left = `${pos.side === 'left' ? bounds.left + FAB_MARGIN : window.innerWidth - bounds.size - FAB_MARGIN}px`;
        chatToggle.style.top = `${clamp(pos.top, bounds.minTop, bounds.maxTop)}px`;
        positionChatPanel();
    }

    // El panel se abre junto al ícono, hacia el centro de la ventana; en celular lo maneja el CSS (pantalla completa)
    function positionChatPanel() {
        if (chatPanel.hidden) return;
        if (window.innerWidth < 768) {
            ['left', 'top', 'right', 'bottom', 'maxHeight'].forEach(prop => { chatPanel.style[prop] = ''; });
            return;
        }
        const bounds = chatBounds();
        chatPanel.style.maxHeight = `${Math.max(240, bounds.bottom - bounds.top - 2 * FAB_MARGIN)}px`;
        const fab = chatToggle.getBoundingClientRect();
        const width = chatPanel.offsetWidth;
        const height = chatPanel.offsetHeight;
        const onLeft = fab.left + fab.width / 2 < window.innerWidth / 2;
        const left = onLeft ? fab.right + PANEL_GAP : fab.left - PANEL_GAP - width;
        // Alineado con la parte de abajo del ícono, sin salir del espacio libre
        const top = fab.bottom - height;
        chatPanel.style.right = 'auto';
        chatPanel.style.bottom = 'auto';
        chatPanel.style.left = `${clamp(left, bounds.left + FAB_MARGIN, window.innerWidth - width - FAB_MARGIN)}px`;
        chatPanel.style.top = `${clamp(top, bounds.top + FAB_MARGIN, bounds.bottom - height - FAB_MARGIN)}px`;
    }

    function endFabDrag(e) {
        if (!fabDrag || e.pointerId !== fabDrag.id) return;
        const moved = fabDrag.moved;
        fabDrag = null;
        if (chatToggle.hasPointerCapture(e.pointerId)) chatToggle.releasePointerCapture(e.pointerId);
        chatToggle.classList.remove('is-dragging');
        if (!moved) return;
        // Fue arrastre: el clic que sigue no abre el chat
        suppressFabClick = true;
        setTimeout(() => { suppressFabClick = false; }, 0);
        const rect = chatToggle.getBoundingClientRect();
        fabPos = { side: rect.left + rect.width / 2 < window.innerWidth / 2 ? 'left' : 'right', top: rect.top };
        const bounds = chatBounds();
        fabPos.top = clamp(fabPos.top, bounds.minTop, bounds.maxTop);
        saveFabPos(fabPos);
        applyFabPos(true);
    }

    chatToggle.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        const rect = chatToggle.getBoundingClientRect();
        fabDrag = {
            id: e.pointerId, startX: e.clientX, startY: e.clientY,
            offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top, moved: false,
        };
        chatToggle.setPointerCapture(e.pointerId);
    });

    chatToggle.addEventListener('pointermove', (e) => {
        if (!fabDrag || e.pointerId !== fabDrag.id) return;
        if (!fabDrag.moved && Math.hypot(e.clientX - fabDrag.startX, e.clientY - fabDrag.startY) < DRAG_THRESHOLD) return;
        fabDrag.moved = true;
        chatToggle.classList.remove('is-settling');
        chatToggle.classList.add('is-dragging');
        const bounds = chatBounds();
        chatToggle.style.left = `${clamp(e.clientX - fabDrag.offsetX, bounds.left, window.innerWidth - bounds.size)}px`;
        chatToggle.style.top = `${clamp(e.clientY - fabDrag.offsetY, bounds.minTop, bounds.maxTop)}px`;
        positionChatPanel();
    });

    chatToggle.addEventListener('pointerup', endFabDrag);
    chatToggle.addEventListener('pointercancel', endFabDrag);
    window.addEventListener('resize', () => applyFabPos(false));
    const chatClose = document.getElementById('chatClose');
    const chatRole = document.getElementById('chatRole');
    const chatMessages = document.getElementById('chatMessages');
    const chatSuggestions = document.getElementById('chatSuggestions');
    const chatForm = document.getElementById('chatForm');
    const chatInput = document.getElementById('chatInput');
    const chatSend = document.getElementById('chatSend');

    const CHAT_WELCOME = {
        requester: 'Hola. Puedo contarte cómo van tus tickets o ayudarte a describir un problema.',
        technician: 'Hola. Puedo resumir tus tickets asignados y sugerir pasos de diagnóstico.',
        coordinator: 'Hola. Puedo resumir el estado del equipo y señalar tickets atrasados.',
        admin: 'Hola. Puedo resumir el estado del equipo y señalar tickets atrasados.'
    };
    const CHAT_SUGGESTIONS = {
        requester: ['¿Cómo va mi ticket?', '¿Qué significa En proceso?', 'Ayúdame a describir mi problema'],
        technician: ['Resume mis tickets abiertos', '¿Qué atiendo primero?', 'Pasos para diagnosticar una red lenta'],
        coordinator: ['Resume el estado del equipo', '¿Qué tickets llevan más tiempo abiertos?', '¿Quién tiene más carga?'],
        admin: ['Resume el estado del equipo', '¿Qué tickets llevan más tiempo abiertos?', '¿Quién tiene más carga?']
    };
    const CHAT_HISTORY_LIMIT = 10;
    const CHAT_TURN_CHARS = 2000;

    // Solo en memoria: se pierde al recargar o al cambiar de cuenta
    let chatHistory = [];
    let chatBusy = false;
    // Cambia al cerrar sesión: una respuesta tardía no se pinta en la conversación de otra cuenta
    let chatSession = 0;

    function addChatMessage(text, kind) {
        const bubble = document.createElement('div');
        bubble.className = `chat-msg ${kind}`;
        bubble.textContent = text;
        chatMessages.appendChild(bubble);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return bubble;
    }

    function renderChatStart() {
        const role = currentUser ? currentUser.role : '';
        chatMessages.replaceChildren();
        chatSuggestions.replaceChildren();
        addChatMessage(CHAT_WELCOME[role] || CHAT_WELCOME.requester, 'model');
        (CHAT_SUGGESTIONS[role] || []).forEach(text => {
            const pill = document.createElement('button');
            pill.type = 'button';
            pill.className = 'chat-suggestion';
            pill.textContent = text;
            chatSuggestions.appendChild(pill);
        });
        chatSuggestions.hidden = false;
    }

    function setupChat() {
        if (!currentUser) return;
        chatRole.textContent = `Te ayuda según tu rol: ${ROLE_MAP[currentUser.role] || currentUser.role}`;
        if (chatHistory.length === 0) renderChatStart();
        chatToggle.hidden = false;
        fabPos = readFabPos();
        applyFabPos(false);
    }

    function resetChat() {
        chatSession += 1;
        chatHistory = [];
        chatBusy = false;
        chatPanel.hidden = true;
        chatToggle.hidden = true;
        chatToggle.setAttribute('aria-expanded', 'false');
        chatMessages.replaceChildren();
        chatSuggestions.replaceChildren();
        chatInput.value = '';
        setChatBusy(false);
    }

    function setChatBusy(busy) {
        chatBusy = busy;
        chatSend.disabled = busy;
        chatSuggestions.querySelectorAll('button').forEach(b => { b.disabled = busy; });
    }

    function openChat() {
        chatPanel.hidden = false;
        positionChatPanel();
        chatToggle.setAttribute('aria-expanded', 'true');
        chatMessages.scrollTop = chatMessages.scrollHeight;
        chatInput.focus();
    }

    function closeChat() {
        chatPanel.hidden = true;
        chatToggle.setAttribute('aria-expanded', 'false');
        chatToggle.focus();
    }

    async function sendChat(text) {
        const message = text.trim().slice(0, 1000);
        if (!message || chatBusy) return;
        setChatBusy(true);
        chatSuggestions.hidden = true;
        addChatMessage(message, 'user');
        const pending = addChatMessage('Pensando... (puede tardar hasta un minuto)', 'pending');
        const history = chatHistory.slice(-CHAT_HISTORY_LIMIT);
        const session = chatSession;

        let bubble = null;
        try {
            // fetch propio: los 429/503 se muestran dentro del chat, no en el aviso rojo
            const response = await fetch(`${API_BASE}/chat/stream`, {
                method: 'POST',
                headers: {
                    'Accept': 'text/plain, application/json',
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ message, history })
            });
            if (response.status === 401) {
                const data = await response.json().catch(() => ({}));
                handleUnauthorized(data.detail);
                return;
            }
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                if (session !== chatSession) return;
                pending.remove();
                addChatMessage(getErrorMessage(data), 'notice');
                return;
            }
            // El texto aparece mientras el modelo lo escribe
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let reply = '';
            for (;;) {
                const { done, value } = await reader.read();
                if (session !== chatSession) {
                    reader.cancel();
                    return;
                }
                if (done) break;
                reply += decoder.decode(value, { stream: true });
                if (!bubble) {
                    pending.remove();
                    bubble = addChatMessage('', 'model');
                }
                bubble.textContent = reply;
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
            reply += decoder.decode();
            if (!bubble) {
                pending.remove();
                bubble = addChatMessage(reply, 'model');
            }
            bubble.textContent = reply;
            chatHistory.push({ role: 'user', text: message.slice(0, CHAT_TURN_CHARS) });
            chatHistory.push({ role: 'model', text: reply.slice(0, CHAT_TURN_CHARS) });
            chatHistory = chatHistory.slice(-CHAT_HISTORY_LIMIT);
        } catch (err) {
            if (session !== chatSession) return;
            pending.remove();
            addChatMessage('No se pudo conectar con el servidor', 'notice');
        } finally {
            if (session === chatSession) setChatBusy(false);
        }
    }

    // Enter y Espacio también llegan como clic (es un <button>); tras un arrastre no se abre
    chatToggle.addEventListener('click', () => {
        if (suppressFabClick) return;
        if (chatPanel.hidden) openChat();
        else closeChat();
    });

    chatClose.addEventListener('click', closeChat);

    // Mover el ícono no toca la conversación
    document.getElementById('chatResetPosition').addEventListener('click', () => {
        fabPos = null;
        saveFabPos(null);
        applyFabPos(true);
    });

    chatPanel.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeChat();
    });

    chatSuggestions.addEventListener('click', (e) => {
        const pill = e.target.closest('.chat-suggestion');
        if (pill) sendChat(pill.textContent);
    });

    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const text = chatInput.value;
        if (!text.trim() || chatBusy) return;
        chatInput.value = '';
        sendChat(text);
    });

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            chatForm.requestSubmit();
        }
    });

    // Switch account / logout
    if (switchAccountBtn) {
        switchAccountBtn.addEventListener('click', async () => {
            // Una reasignación o cancelación que espera su plazo para deshacer se envía antes de salir
            await commitPendingAssign();
            await commitPendingCancel();
            try {
                await apiFetch('/auth/logout', { method: 'POST' });
            } catch (e) {
                // ignore logout errors
            }
            sessionStorage.removeItem('ticketeraToken');
            authToken = null;
            currentUser = null;
            showAuthScreen();
        });
    }

    async function loadCategories() {
        const categories = await apiFetch('/categories');
        categorySelect.innerHTML = '<option value="">Seleccionar</option>';
        categoryFilter.innerHTML = '<option value="">Todas</option>';
        
        categoryMap = {};
        
        categories.forEach(cat => {
            const opt1 = document.createElement('option');
            opt1.value = cat.id;
            opt1.textContent = cat.name;
            categorySelect.appendChild(opt1);

            const opt2 = document.createElement('option');
            opt2.value = cat.id;
            opt2.textContent = cat.name;
            categoryFilter.appendChild(opt2);
            
            categoryMap[cat.id] = cat.name;
        });
    }

    async function loadPriorities() {
        const priorities = await apiFetch('/priorities');
        prioritySelect.innerHTML = '<option value="">Seleccionar</option>';
        priorityFilter.innerHTML = '<option value="">Todas</option>';
        
        priorityMap = {};
        priorityLevelMap = {};
        
        priorities.forEach(pri => {
            const opt1 = document.createElement('option');
            opt1.value = pri.id;
            opt1.textContent = `${pri.name} (${pri.level})`;
            prioritySelect.appendChild(opt1);

            const opt2 = document.createElement('option');
            opt2.value = pri.id;
            opt2.textContent = `${pri.name} (${pri.level})`;
            priorityFilter.appendChild(opt2);
            
            priorityMap[pri.id] = pri.name;
            priorityLevelMap[pri.id] = pri.level;
        });
    }

    async function loadTickets(filters = {}) {
        if (!currentUser) return;
        // El personal usa la cola por vistas; el detalle abierto lo recarga quien llama
        if (isStaffShell()) {
            await loadQueue();
            pollNavCounts(false);
            return;
        }
        if (isRequesterPortal()) {
            await loadPortalList();
            return;
        }
        
        const params = new URLSearchParams();
        if (filters.status) params.append('status', filters.status);
        if (filters.category_id) params.append('category_id', filters.category_id);
        if (filters.priority_id) params.append('priority_id', filters.priority_id);
        if (filters.search) params.append('search', filters.search);

        const tickets = await apiFetch(`/tickets?${params.toString()}`);
        renderTickets(tickets);

        if (selectedTicketId !== null) {
            const exists = tickets.some(t => t.id === selectedTicketId);
            if (!exists) {
                selectedTicketId = null;
                renderDetailPlaceholder();
            } else {
                try {
                    const ticket = await apiFetch(`/tickets/${selectedTicketId}`);
                    showTicketDetail(ticket);
                } catch (err) {
                    selectedTicketId = null;
                    renderDetailPlaceholder();
                }
            }
        }
    }

    function renderTickets(tickets) {
        ticketList.replaceChildren();
        
        if (tickets.length === 0) {
            const filters = getFilters();
            const hasActiveFilter = filters.status || filters.category_id || filters.priority_id || filters.search;
            
            const li = document.createElement('li');
            li.style.padding = '32px 16px';
            li.style.textAlign = 'center';
            li.style.color = 'var(--muted)';
            li.textContent = hasActiveFilter ? 'No hay tickets que coincidan con los filtros' : 'Todavía no hay tickets';
            ticketList.appendChild(li);
            return;
        }
        
        tickets.forEach(ticket => {
            const li = document.createElement('li');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'ticket-item';
            if (ticket.id === selectedTicketId) {
                button.classList.add('selected');
            }
            button.dataset.id = ticket.id;
            
            const line1 = document.createElement('div');
            line1.style.display = 'flex';
            line1.style.justifyContent = 'space-between';
            line1.style.fontSize = '13px';
            line1.style.color = 'var(--muted)';
            line1.style.marginBottom = '4px';

            const leftSpan = document.createElement('span');
            const idSpan = document.createElement('span');
            idSpan.style.fontFamily = 'var(--mono)';
            idSpan.textContent = `#${ticket.id}`;
            leftSpan.appendChild(idSpan);

            const catName = categoryMap[ticket.category_id] || '';
            if (catName) {
                const catText = document.createTextNode(` · ${catName}`);
                leftSpan.appendChild(catText);
            }

            const rightSpan = document.createElement('span');
            rightSpan.textContent = formatDate(ticket.created_at);

            line1.appendChild(leftSpan);
            line1.appendChild(rightSpan);

            const line2 = document.createElement('div');
            line2.style.fontWeight = '500';
            line2.style.fontSize = '15px';
            line2.style.color = 'var(--ink)';
            line2.style.marginBottom = '6px';
            line2.style.textAlign = 'left';
            line2.textContent = ticket.title;

            const line3 = document.createElement('div');
            line3.style.display = 'flex';
            line3.style.alignItems = 'center';
            line3.style.gap = '8px';
            line3.style.flexWrap = 'wrap';
            line3.style.fontSize = '13px';

            const statusBadge = document.createElement('span');
            statusBadge.className = `badge status-${ticket.status}`;
            statusBadge.textContent = STATE_MAP[ticket.status] || ticket.status;
            line3.appendChild(statusBadge);

            const priorityBadge = document.createElement('span');
            priorityBadge.className = `priority-${priorityLevelMap[ticket.priority_id] || 1}`;
            priorityBadge.textContent = priorityMap[ticket.priority_id] || '';
            line3.appendChild(priorityBadge);

            if (ticket.assigned_to !== null && ticket.assigned_to !== undefined) {
                const assignedSpan = document.createElement('span');
                assignedSpan.style.marginLeft = 'auto';
                assignedSpan.style.color = 'var(--muted)';
                assignedSpan.textContent = ticket.assigned_name || userMap[ticket.assigned_to] || ticket.assigned_to;
                line3.appendChild(assignedSpan);
            }

            button.appendChild(line1);
            button.appendChild(line2);
            button.appendChild(line3);
            li.appendChild(button);
            ticketList.appendChild(li);
        });
    }

    function parseServerDate(dateString) {
        const hasZone = dateString.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(dateString);
        return new Date(hasZone ? dateString : dateString + 'Z');
    }

    function formatDate(dateString) {
        if (!dateString) return '';
        return parseServerDate(dateString).toLocaleDateString('es-CR');
    }

    function formatDateTime(dateString) {
        if (!dateString) return '';
        return parseServerDate(dateString).toLocaleString('es-CR', {
            dateStyle: 'short',
            timeStyle: 'short',
        });
    }

    function renderDetailPlaceholder() {
        // Si el ticket abierto ya no está disponible, la página del ticket vuelve a la lista
        if (document.body.classList.contains('ticket-open')) {
            exitTicketPage();
            return;
        }
        detailContent.replaceChildren();
        const div = document.createElement('div');
        div.className = 'detail-placeholder';
        const p = document.createElement('p');
        p.textContent = 'Selecciona un ticket para ver el detalle';
        div.appendChild(p);
        detailContent.appendChild(div);
    }

    async function reloadSelectedTicketAndList() {
        if (!selectedTicketId || !currentUser) return;
        try {
            const ticket = await apiFetch(`/tickets/${selectedTicketId}`);
            showTicketDetail(ticket);
            await loadTickets(getFilters());
        } catch (err) {
            selectedTicketId = null;
            renderDetailPlaceholder();
            await loadTickets(getFilters());
        }
    }

    function renderFollowButton(btn, hint, following) {
        btn.textContent = following ? 'Siguiendo' : 'Dar seguimiento';
        btn.setAttribute('aria-pressed', following ? 'true' : 'false');
        btn.classList.toggle('following', following);
        hint.textContent = following
            ? 'Te avisaremos por correo de cambios de estado, asignación y comentarios nuevos.'
            : '';
        hint.hidden = !following;
    }

    function showTicketDetail(ticket) {
        currentTicket = ticket;
        // Nombres de quienes aparecen en el ticket (el solicitante no recibe la lista de cuentas)
        (ticket.people || []).forEach(person => {
            userMap[person.id] = person.name;
            userRoleMap[person.id] = person.role;
        });
        if (isStaffShell()) {
            renderStaffDetail(ticket);
            return;
        }
        if (isRequesterPortal()) {
            renderPortalDetail(ticket);
            return;
        }
        detailContent.replaceChildren();
        const detailPanelEl = document.getElementById('detailPanel');
        if (detailPanelEl) detailPanelEl.scrollTop = 0;

        const assignedTo = ticket.assigned_to !== null ? userMap[ticket.assigned_to] || ticket.assigned_to : 'Sin asignar';
        const categoryName = categoryMap[ticket.category_id] || '';
        const priorityName = priorityMap[ticket.priority_id] || '';
        const createdByName = userMap[ticket.created_by] || '';

        const topMeta = document.createElement('div');
        topMeta.style.fontFamily = 'var(--mono)';
        topMeta.style.fontSize = '13px';
        topMeta.style.color = 'var(--muted)';
        topMeta.style.marginBottom = '12px';
        topMeta.textContent = `#${ticket.id} · ${categoryName} · Creado por ${createdByName} · ${formatDateTime(ticket.created_at)}`;
        detailContent.appendChild(topMeta);

        // Solo llega al personal: el solicitante nunca recibe requester_flags
        if (ticket.requester_flags) {
            const flagsRow = document.createElement('div');
            flagsRow.className = 'requester-tags';
            const tagList = document.createElement('span');
            tagList.className = 'requester-tag-list';
            requesterTags(ticket.requester_flags).forEach(tag => tagList.appendChild(tag));
            const reportAccountBtn = document.createElement('button');
            reportAccountBtn.type = 'button';
            reportAccountBtn.className = 'btn-secondary btn-small btn-report-account';
            reportAccountBtn.textContent = 'Reportar cuenta';
            flagsRow.append(tagList, reportAccountBtn);
            detailContent.appendChild(flagsRow);
        }

        const h1 = document.createElement('h1');
        h1.style.fontSize = '26px';
        h1.style.fontWeight = '600';
        h1.style.color = 'var(--ink)';
        h1.style.margin = '0';
        h1.textContent = ticket.title;

        const titleRow = document.createElement('div');
        titleRow.className = 'detail-title-row';
        titleRow.appendChild(h1);

        const followBox = document.createElement('div');
        followBox.className = 'follow-box';
        const followBtn = document.createElement('button');
        followBtn.type = 'button';
        followBtn.className = 'follow-btn';
        const followHint = document.createElement('p');
        followHint.className = 'follow-hint';
        followBox.appendChild(followBtn);
        followBox.appendChild(followHint);
        titleRow.appendChild(followBox);
        renderFollowButton(followBtn, followHint, Boolean(ticket.following));
        detailContent.appendChild(titleRow);

        const descP = document.createElement('p');
        descP.style.maxWidth = '64ch';
        descP.style.lineHeight = '1.55';
        descP.style.color = 'var(--ink)';
        descP.style.margin = '0 0 28px 0';
        descP.textContent = ticket.description;
        detailContent.appendChild(descP);

        // State, Priority, Assigned to in ONE single card
        const dl = document.createElement('dl');
        dl.style.background = '#FFFFFF';
        dl.style.border = '1px solid #E3E6EB';
        dl.style.borderRadius = '10px';
        dl.style.display = 'grid';
        dl.style.gridTemplateColumns = 'repeat(3, 1fr)';
        dl.style.margin = '0 0 28px 0';
        dl.style.overflow = 'hidden';

        const statusBadge = document.createElement('span');
        statusBadge.className = `badge status-${ticket.status}`;
        statusBadge.textContent = STATE_MAP[ticket.status] || ticket.status;

        const priorityBadge = document.createElement('span');
        priorityBadge.className = `priority-${priorityLevelMap[ticket.priority_id] || 1}`;
        priorityBadge.textContent = priorityName;

        const fieldsData = [
            { title: 'Estado', content: statusBadge },
            { title: 'Prioridad', content: priorityBadge },
            { title: 'Asignado a', text: assignedTo, content: ticket.auto_assigned ? autoAssignedContent(assignedTo) : null },
            { title: 'Teléfono', text: ticket.contact_phone || '' },
            { title: 'Cédula', text: ticket.contact_id_number || '' }
        ];

        fieldsData.forEach((fData, i) => {
            const box = document.createElement('div');
            box.style.padding = '16px';
            if (i < 2) {
                box.style.borderRight = '1px solid #E3E6EB';
            }
            const dt = document.createElement('dt');
            dt.style.fontSize = '12px';
            dt.style.color = 'var(--muted)';
            dt.style.marginBottom = '4px';
            dt.textContent = fData.title;

            const dd = document.createElement('dd');
            dd.style.margin = '0';
            dd.style.fontWeight = '500';
            if (fData.content) {
                dd.appendChild(fData.content);
            } else {
                dd.textContent = fData.text;
                dd.style.color = 'var(--ink)';
            }
            box.appendChild(dt);
            box.appendChild(dd);
            dl.appendChild(box);
        });

        detailContent.appendChild(dl);

        if (currentUser.role !== 'requester' && ticket.ai_analysis) {
            detailContent.appendChild(renderAiCard(ticket));
        }

        // Actions
        const actionsSection = document.createElement('div');
        actionsSection.style.marginBottom = '28px';
        actionsSection.style.paddingTop = '20px';
        actionsSection.style.borderTop = '1px solid var(--line)';

        const state = ticket.status;
        const isClosed = state === 'closed';

        if (currentUser.role === 'requester') {
            if (!isClosed) {
                const msg = document.createElement('p');
                msg.textContent = 'Solo el equipo de soporte puede cambiar el estado o la asignación';
                msg.className = 'requester-msg';
                msg.style.color = 'var(--muted)';
                actionsSection.appendChild(msg);
            } else {
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionsSection.appendChild(closedMsg);
            }
        } else if (currentUser.role === 'technician') {
            const actionDiv = document.createElement('div');
            actionDiv.style.display = 'flex';
            actionDiv.style.gap = '10px';
            actionDiv.style.flexWrap = 'wrap';
            actionDiv.style.alignItems = 'center';

            if (!isClosed) {
                if (state === 'new') {
                    const startBtn = document.createElement('button');
                    startBtn.className = 'btn-action';
                    startBtn.textContent = 'Iniciar trabajo';
                    actionDiv.appendChild(startBtn);
                } else if (state === 'in_progress') {
                    const resolveBtn = document.createElement('button');
                    resolveBtn.className = 'btn-action';
                    resolveBtn.textContent = 'Marcar como resuelto';
                    actionDiv.appendChild(resolveBtn);
                } else if (state === 'resolved') {
                    const closeBtn = document.createElement('button');
                    closeBtn.className = 'btn-action';
                    closeBtn.textContent = 'Cerrar';
                    actionDiv.appendChild(closeBtn);
                    
                    const reopenBtn = document.createElement('button');
                    reopenBtn.className = 'btn-action';
                    reopenBtn.textContent = 'Reabrir';
                    actionDiv.appendChild(reopenBtn);
                }
                
                if (ticket.assigned_to === null || ticket.assigned_to === undefined) {
                    const assignMeBtn = document.createElement('button');
                    assignMeBtn.className = 'btn-action btn-assign-me';
                    assignMeBtn.textContent = 'Asignarme a mí';
                    actionDiv.appendChild(assignMeBtn);
                }
            } else {
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionDiv.appendChild(closedMsg);
            }
            actionsSection.appendChild(actionDiv);
        } else if (currentUser.role === 'coordinator') {
            const assignmentDiv = document.createElement('div');
            assignmentDiv.style.display = 'flex';
            assignmentDiv.style.gap = '10px';
            assignmentDiv.style.flexWrap = 'wrap';
            assignmentDiv.style.alignItems = 'center';
            assignmentDiv.style.marginBottom = '12px';

            const assignLabel = document.createElement('label');
            assignLabel.textContent = 'Asignar a:';
            assignmentDiv.appendChild(assignLabel);
            
            const assignSelect = document.createElement('select');
            assignSelect.className = 'assign-select';
            
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = '-- Seleccionar --';
            assignSelect.appendChild(defaultOpt);
            
            Object.keys(userMap).forEach(userId => {
                const role = userRoleMap[userId];
                if (role === 'technician' || role === 'coordinator') {
                    const opt = document.createElement('option');
                    opt.value = userId;
                    const availability = userAvailabilityMap[userId];
                    // El estado al lado del nombre: se puede asignar igual, con confirmación
                    opt.textContent = availability && availability !== 'available'
                        ? `${userMap[userId]} — ${AVAILABILITY_LABELS[availability]}`
                        : userMap[userId];
                    assignSelect.appendChild(opt);
                }
            });
            
            const assignBtn = document.createElement('button');
            assignBtn.className = 'btn-assign';
            assignBtn.textContent = 'Asignar';
            assignmentDiv.appendChild(assignSelect);
            assignmentDiv.appendChild(assignBtn);

            if (state === 'new' || state === 'in_progress' || state === 'resolved') {
                actionsSection.appendChild(assignmentDiv);
            }

            const actionDiv = document.createElement('div');
            actionDiv.style.display = 'flex';
            actionDiv.style.gap = '10px';
            actionDiv.style.flexWrap = 'wrap';
            actionDiv.style.alignItems = 'center';

            if (!isClosed) {
                if (state === 'new') {
                    const startBtn = document.createElement('button');
                    startBtn.className = 'btn-action';
                    startBtn.textContent = 'Iniciar trabajo';
                    actionDiv.appendChild(startBtn);
                } else if (state === 'in_progress') {
                    const resolveBtn = document.createElement('button');
                    resolveBtn.className = 'btn-action';
                    resolveBtn.textContent = 'Marcar como resuelto';
                    actionDiv.appendChild(resolveBtn);
                } else if (state === 'resolved') {
                    const closeBtn = document.createElement('button');
                    closeBtn.className = 'btn-action';
                    closeBtn.textContent = 'Cerrar';
                    actionDiv.appendChild(closeBtn);
                    
                    const reopenBtn = document.createElement('button');
                    reopenBtn.className = 'btn-action';
                    reopenBtn.textContent = 'Reabrir';
                    actionDiv.appendChild(reopenBtn);
                }
            } else {
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionDiv.appendChild(closedMsg);
            }
            actionsSection.appendChild(actionDiv);
        } else {
            if (isClosed) {
                const actionDiv = document.createElement('div');
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionDiv.appendChild(closedMsg);
                actionsSection.appendChild(actionDiv);
            }
        }

        detailContent.appendChild(actionsSection);

        // Actividad Section Card
        const activityCard = document.createElement('div');
        activityCard.style.background = '#FFFFFF';
        activityCard.style.border = '1px solid #E3E6EB';
        activityCard.style.borderRadius = '12px';
        activityCard.style.margin = '0 0 28px 0';
        activityCard.style.overflow = 'hidden';

        // Header
        const actHeader = document.createElement('div');
        actHeader.style.display = 'flex';
        actHeader.style.justifyContent = 'space-between';
        actHeader.style.alignItems = 'center';
        actHeader.style.padding = '12px 16px';
        actHeader.style.borderBottom = '1px solid #E3E6EB';

        const h2Act = document.createElement('h2');
        h2Act.style.fontSize = '15px';
        h2Act.style.margin = '0';
        h2Act.textContent = 'Actividad';
        actHeader.appendChild(h2Act);

        const filterGroup = document.createElement('div');
        filterGroup.style.display = 'flex';
        filterGroup.style.gap = '4px';
        filterGroup.style.background = '#EEF0F3';
        filterGroup.style.borderRadius = '8px';
        filterGroup.style.padding = '3px';

        const filtersList = [
            { key: 'all', label: 'Todo' },
            { key: 'comments', label: 'Comentarios' },
            { key: 'history', label: 'Historial' }
        ];

        filtersList.forEach(f => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'activity-filter-btn';
            btn.dataset.filter = f.key;
            btn.textContent = f.label;
            btn.setAttribute('aria-pressed', activityFilter === f.key ? 'true' : 'false');
            btn.style.border = 'none';
            btn.style.background = activityFilter === f.key ? '#FFFFFF' : 'transparent';
            btn.style.fontWeight = activityFilter === f.key ? '600' : '400';
            btn.style.padding = '4px 10px';
            btn.style.borderRadius = '6px';
            btn.style.fontSize = '13px';
            btn.style.cursor = 'pointer';
            filterGroup.appendChild(btn);
        });
        actHeader.appendChild(filterGroup);
        activityCard.appendChild(actHeader);

        // Comment Box (Top)
        const commentBoxWrap = document.createElement('div');
        commentBoxWrap.style.padding = '16px';
        commentBoxWrap.style.borderBottom = '1px solid #EEF0F3';

        if (ticket.status === 'closed') {
            const closedP = document.createElement('p');
            closedP.style.margin = '0';
            closedP.style.color = 'var(--muted)';
            closedP.style.fontSize = '13px';
            closedP.textContent = 'Este ticket está cerrado y ya no admite comentarios.';
            commentBoxWrap.appendChild(closedP);
        } else {
            const commentFormFlex = document.createElement('div');
            commentFormFlex.style.display = 'flex';
            commentFormFlex.style.gap = '12px';
            commentFormFlex.style.alignItems = 'flex-start';

            const avatarDiv = document.createElement('div');
            avatarDiv.style.width = '32px';
            avatarDiv.style.height = '32px';
            avatarDiv.style.borderRadius = '50%';
            avatarDiv.style.background = '#EEF2FC';
            avatarDiv.style.color = '#1F3F99';
            avatarDiv.style.display = 'flex';
            avatarDiv.style.alignItems = 'center';
            avatarDiv.style.justifyContent = 'center';
            avatarDiv.style.fontWeight = '600';
            avatarDiv.style.fontSize = '13px';
            avatarDiv.style.flexShrink = '0';
            avatarDiv.textContent = getInitials(currentUser.name);
            commentFormFlex.appendChild(avatarDiv);

            const formRight = document.createElement('div');
            formRight.style.flex = '1';
            formRight.style.display = 'flex';
            formRight.style.flexDirection = 'column';
            formRight.style.gap = '10px';

            const srLabel = document.createElement('label');
            srLabel.className = 'sr-only';
            srLabel.textContent = 'Comentar';
            formRight.appendChild(srLabel);

            const textarea = document.createElement('textarea');
            textarea.rows = 2;
            textarea.className = 'comment-textarea';
            const userRoleSpanText = ROLE_MAP[currentUser.role] || currentUser.role;
            textarea.placeholder = `Agregar un comentario como ${currentUser.name} — ${userRoleSpanText}`;
            textarea.style.width = '100%';
            textarea.style.padding = '8px 12px';
            textarea.style.border = '1px solid #D5D9E0';
            textarea.style.borderRadius = '8px';
            textarea.style.font = 'inherit';
            textarea.style.fontSize = '14px';
            textarea.style.boxSizing = 'border-box';
            textarea.style.resize = 'vertical';
            formRight.appendChild(textarea);

            const btnRow = document.createElement('div');
            btnRow.style.display = 'flex';
            btnRow.style.justifyContent = 'flex-end';

            const commentBtn = document.createElement('button');
            commentBtn.type = 'button';
            commentBtn.className = 'btn-action btn-add-comment';
            commentBtn.textContent = 'Comentar';
            commentBtn.style.minHeight = '36px';
            commentBtn.style.padding = '6px 16px';
            btnRow.appendChild(commentBtn);

            formRight.appendChild(btnRow);
            commentFormFlex.appendChild(formRight);
            commentBoxWrap.appendChild(commentFormFlex);
        }
        activityCard.appendChild(commentBoxWrap);

        // Events List Container (ol)
        const eventsContainer = document.createElement('div');
        eventsContainer.className = 'activity-events-container';
        activityCard.appendChild(eventsContainer);

        renderEventsList(ticket, eventsContainer);
        detailContent.appendChild(activityCard);
    }

    const AI_LEVEL_NAMES = {
        0: 'No es soporte',
        1: 'Consulta',
        2: 'Solicitud estándar',
        3: 'Incidente',
        4: 'Crítico'
    };

    function aiParagraph(text, className) {
        const p = document.createElement('p');
        if (className) p.className = className;
        p.textContent = text;
        return p;
    }

    function renderAiCard(ticket) {
        const ai = ticket.ai_analysis;
        const card = document.createElement('section');
        card.className = 'ai-card';
        card.setAttribute('aria-label', 'Análisis de la IA');

        const h2 = document.createElement('h2');
        h2.textContent = 'Análisis de la IA';
        card.appendChild(h2);

        if (ai.status === 'pending') {
            card.appendChild(aiParagraph('Analizando...', 'ai-muted'));
            return card;
        }
        if (ai.status === 'error') {
            card.appendChild(aiParagraph('No se pudo analizar', 'ai-muted'));
            return card;
        }

        const head = document.createElement('div');
        head.className = 'ai-head';
        const level = ai.effective_level;
        const badge = document.createElement('span');
        badge.className = `ai-level ai-level-${level === null ? 'none' : level}`;
        badge.textContent = level === null ? 'Sin nivel' : `Nivel ${level} · ${AI_LEVEL_NAMES[level]}`;
        head.appendChild(badge);
        if (ai.needs_review) {
            const review = document.createElement('span');
            review.className = 'ai-review';
            review.textContent = 'Revisar';
            head.appendChild(review);
        }
        card.appendChild(head);

        if (ai.summary) card.appendChild(aiParagraph(ai.summary));

        if (ai.ai_category_name) {
            const cat = aiParagraph(`Categoría sugerida: ${ai.ai_category_name}`);
            if (ai.ai_category_id !== ticket.category_id) {
                const diff = document.createElement('span');
                diff.className = 'ai-muted';
                diff.textContent = ' (distinta a la del ticket)';
                cat.appendChild(diff);
            }
            card.appendChild(cat);
        }

        if (ai.due_at) {
            const due = aiParagraph(`Tiempo meta: vence el ${formatDateTime(ai.due_at)}`);
            if (ai.overdue) {
                const late = document.createElement('span');
                late.className = 'ai-overdue';
                late.textContent = ' · Vencido';
                due.appendChild(late);
            }
            card.appendChild(due);
        } else {
            card.appendChild(aiParagraph('Tiempo meta: sin tiempo meta', 'ai-muted'));
        }

        const correct = document.createElement('div');
        correct.className = 'ai-correct';
        const label = document.createElement('label');
        label.htmlFor = 'aiLevelSelect';
        label.textContent = 'Corregir nivel:';
        const select = document.createElement('select');
        select.id = 'aiLevelSelect';
        select.className = 'ai-level-select';
        if (level === null) {
            const empty = document.createElement('option');
            empty.value = '';
            empty.textContent = '-- Seleccionar --';
            select.appendChild(empty);
        }
        Object.keys(AI_LEVEL_NAMES).forEach(key => {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = `${key} · ${AI_LEVEL_NAMES[key]}`;
            if (Number(key) === level) opt.selected = true;
            select.appendChild(opt);
        });
        correct.appendChild(label);
        correct.appendChild(select);
        card.appendChild(correct);
        return card;
    }

    // Texto de un cambio del historial: "Ana cambió el estado de Nuevo a En proceso"
    function appendHistoryText(target, entry, changedByName) {
        const strongWho = document.createElement('strong');
        strongWho.textContent = changedByName;
        target.appendChild(strongWho);

        if (entry.field === 'status') {
            target.appendChild(document.createTextNode(' cambió el estado de '));
            const sOld = document.createElement('strong');
            sOld.textContent = STATE_MAP[entry.old_value] || entry.old_value || 'Nuevo';
            target.appendChild(sOld);
            target.appendChild(document.createTextNode(' a '));
            const sNew = document.createElement('strong');
            sNew.textContent = STATE_MAP[entry.new_value] || entry.new_value || '';
            target.appendChild(sNew);
        } else if (entry.field === 'assigned_to') {
            if (entry.new_value && parseInt(entry.new_value) === entry.changed_by) {
                target.appendChild(document.createTextNode(' se asignó el ticket'));
            } else if (!entry.new_value) {
                target.appendChild(document.createTextNode(' desasignó el ticket'));
            } else {
                target.appendChild(document.createTextNode(' asignó el ticket a '));
                const sAssign = document.createElement('strong');
                sAssign.textContent = userMap[parseInt(entry.new_value)] || entry.new_value;
                target.appendChild(sAssign);
            }
        } else if (entry.field === 'priority_id') {
            target.appendChild(document.createTextNode(' cambió la prioridad de '));
            const pOld = document.createElement('strong');
            pOld.textContent = priorityMap[entry.old_value] || entry.old_value || '';
            target.appendChild(pOld);
            target.appendChild(document.createTextNode(' a '));
            const pNew = document.createElement('strong');
            pNew.textContent = priorityMap[entry.new_value] || entry.new_value || '';
            target.appendChild(pNew);
        } else if (entry.field === 'category_id') {
            target.appendChild(document.createTextNode(' cambió la categoría de '));
            const cOld = document.createElement('strong');
            cOld.textContent = categoryMap[entry.old_value] || entry.old_value || '';
            target.appendChild(cOld);
            target.appendChild(document.createTextNode(' a '));
            const cNew = document.createElement('strong');
            cNew.textContent = categoryMap[entry.new_value] || entry.new_value || '';
            target.appendChild(cNew);
        } else if (entry.field === 'cancelled') {
            target.appendChild(document.createTextNode(' canceló el ticket'));
            // El motivo solo lo ve el personal
            if (isStaffShell()) {
                target.appendChild(document.createTextNode(' · '));
                target.appendChild(makeEl('strong', '', CANCEL_REASON_LABELS[entry.new_value] || entry.new_value || ''));
            }
        } else if (entry.field === 'reopened') {
            target.appendChild(document.createTextNode(' reabrió el ticket'));
        } else if (entry.field === 'merged') {
            target.appendChild(document.createTextNode(' agregó este ticket a '));
            target.appendChild(makeEl('strong', '', `#${entry.new_value}`));
        } else if (entry.field === 'merged_from') {
            // new_value: "15" (ticket) o "borrador 3" (correo)
            const fromDraft = (entry.new_value || '').startsWith('borrador');
            target.appendChild(document.createTextNode(fromDraft ? ' agregó aquí un correo del cliente' : ' agregó aquí el ticket '));
            if (!fromDraft) target.appendChild(makeEl('strong', '', `#${entry.new_value}`));
        } else if (entry.field === 'unmerged') {
            target.appendChild(document.createTextNode(' separó este ticket de '));
            target.appendChild(makeEl('strong', '', `#${entry.old_value}`));
        } else if (entry.field === 'unmerged_from') {
            target.appendChild(document.createTextNode(' separó '));
            target.appendChild(makeEl('strong', '', (entry.old_value || '').toLowerCase()));
        } else if (entry.field === 'linked') {
            target.appendChild(document.createTextNode(' relacionó este ticket con '));
            target.appendChild(makeEl('strong', '', `#${entry.new_value}`));
        } else if (entry.field === 'unlinked') {
            target.appendChild(document.createTextNode(' separó este ticket de '));
            target.appendChild(makeEl('strong', '', `#${entry.old_value}`));
        }
    }

    function renderEventsList(ticket, container) {
        container.replaceChildren();

        let events = [];
        if (ticket.comments) {
            ticket.comments.forEach(c => {
                events.push({
                    type: 'comment',
                    date: c.created_at,
                    data: c
                });
            });
        }
        if (ticket.history) {
            ticket.history.forEach(h => {
                events.push({
                    type: 'history',
                    date: h.changed_at,
                    data: h
                });
            });
        }
        events.sort((a, b) => new Date(b.date) - new Date(a.date));

        const filtered = events.filter(e => {
            if (activityFilter === 'comments') return e.type === 'comment';
            if (activityFilter === 'history') return e.type === 'history';
            // Pestañas del personal: conversación con el cliente y notas internas por separado
            if (activityFilter === 'public') return e.type === 'comment' && !e.data.internal;
            if (activityFilter === 'internal') return e.type === 'comment' && e.data.internal;
            return true;
        });

        if (filtered.length === 0) {
            const emptyP = document.createElement('p');
            emptyP.style.textAlign = 'center';
            emptyP.style.color = 'var(--muted)';
            emptyP.style.padding = '24px 0';
            emptyP.style.margin = '0';
            emptyP.style.fontSize = '13px';
            emptyP.textContent = 'Todavía no hay actividad.';
            container.appendChild(emptyP);
            return;
        }

        const ol = document.createElement('ol');
        ol.style.listStyle = 'none';
        ol.style.padding = '0 16px';
        ol.style.margin = '0';

        filtered.forEach((ev, idx) => {
            const li = document.createElement('li');
            li.style.display = 'flex';
            li.style.gap = '12px';
            li.style.alignItems = 'flex-start';
            li.style.padding = '12px 0';
            if (idx < filtered.length - 1) {
                li.style.borderBottom = '1px solid #EEF0F3';
            }

            if (ev.type === 'comment') {
                const comment = ev.data;
                const authorName = userMap[comment.user_id] || 'Usuario';
                const authorRole = ROLE_MAP[userRoleMap[comment.user_id]] || userRoleMap[comment.user_id] || '';

                const circle = document.createElement('div');
                circle.style.width = '32px';
                circle.style.height = '32px';
                circle.style.borderRadius = '50%';
                circle.style.background = '#EEF2FC';
                circle.style.color = '#1F3F99';
                circle.style.display = 'flex';
                circle.style.alignItems = 'center';
                circle.style.justifyContent = 'center';
                circle.style.fontWeight = '600';
                circle.style.fontSize = '13px';
                circle.style.flexShrink = '0';
                circle.textContent = getInitials(authorName);
                li.appendChild(circle);

                const rightDiv = document.createElement('div');
                rightDiv.style.flex = '1';
                rightDiv.style.display = 'flex';
                rightDiv.style.flexDirection = 'column';
                rightDiv.style.gap = '4px';

                const metaP = document.createElement('p');
                metaP.style.margin = '0';
                metaP.style.fontSize = '13px';
                metaP.style.color = 'var(--muted)';

                const strongName = document.createElement('strong');
                strongName.textContent = authorName;
                metaP.appendChild(strongName);
                metaP.appendChild(document.createTextNode(` · ${authorRole} · ${formatDateTime(comment.created_at)}`));
                if (comment.internal) {
                    // Solo el personal recibe estos comentarios
                    const badge = document.createElement('span');
                    badge.className = 'comment-internal';
                    badge.textContent = 'Interno';
                    metaP.appendChild(badge);
                }
                rightDiv.appendChild(metaP);

                const contentDiv = document.createElement('div');
                contentDiv.style.color = 'var(--ink)';
                contentDiv.style.fontSize = '14px';
                contentDiv.textContent = comment.content;
                rightDiv.appendChild(contentDiv);

                li.appendChild(rightDiv);

            } else {
                const entry = ev.data;
                // Las asignaciones automáticas las registra el Agente virtual: se muestran como "Sistema"
                const changedByName = entry.field === 'assigned_to' && userRoleMap[entry.changed_by] === 'agent'
                    ? 'Sistema'
                    : userMap[entry.changed_by] || 'Usuario';

                const dotContainer = document.createElement('div');
                dotContainer.style.width = '32px';
                dotContainer.style.height = '32px';
                dotContainer.style.display = 'flex';
                dotContainer.style.alignItems = 'center';
                dotContainer.style.justifyContent = 'center';
                dotContainer.style.flexShrink = '0';

                const dot = document.createElement('div');
                dot.style.width = '10px';
                dot.style.height = '10px';
                dot.style.borderRadius = '50%';
                if (entry.field === 'status') {
                    dot.style.background = '#C77700';
                } else if (entry.field === 'assigned_to') {
                    dot.style.background = '#2E55C9';
                } else {
                    dot.style.background = '#5A6270';
                }
                dotContainer.appendChild(dot);
                li.appendChild(dotContainer);

                const rightP = document.createElement('p');
                rightP.style.margin = '0';
                rightP.style.fontSize = '13px';
                rightP.style.color = 'var(--muted)';
                rightP.style.display = 'flex';
                rightP.style.flexWrap = 'wrap';
                rightP.style.alignItems = 'center';
                rightP.style.gap = '4px';
                rightP.style.lineHeight = '1.4';

                appendHistoryText(rightP, entry, changedByName);

                rightP.appendChild(document.createTextNode(` · ${formatDateTime(entry.changed_at)}`));
                li.appendChild(rightP);
            }

            ol.appendChild(li);
        });

        container.appendChild(ol);
    }

    function showError(message) {
        let text;
        if (Array.isArray(message)) {
            text = message.map(m => (m && m.msg) ? m.msg : String(m)).join('. ');
        } else {
            text = String(message);
        }
        text = text.replace(/Value error, /g, '');
        errorMessage.textContent = text;
        errorMessage.style.display = 'block';
        successMessage.style.display = 'none';
        clearTimeout(showError.timer);
        showError.timer = setTimeout(() => {
            errorMessage.style.display = 'none';
        }, 3000);
    }

    function hideError() {
        errorMessage.style.display = 'none';
        successMessage.style.display = 'none';
    }

    // Los avisos de éxito salen como toast abajo; los de error siguen arriba (showError)
    function showSuccess(message) {
        toast(message);
    }

    // ===== Utilidades del sistema de diseño =====
    const toastRegion = document.getElementById('toastRegion');

    // Aviso breve abajo. Opciones: action (texto del botón, p. ej. "Deshacer"), duration (ms),
    // tone ('error'), onClose(reason) con reason 'action' | 'timeout' | 'dismiss'.
    // Devuelve { close } para cerrarlo antes de tiempo (reason 'dismiss').
    function toast(message, { action = null, duration = 4000, tone = null, onClose = null } = {}) {
        const box = makeEl('div', `ui-toast${tone === 'error' ? ' is-error' : ''}`);
        box.setAttribute('role', tone === 'error' ? 'alert' : 'status');
        box.appendChild(makeEl('span', 'ui-toast-text', message));
        let closed = false;
        let timer = null;
        const close = (reason) => {
            if (closed) return;
            closed = true;
            clearTimeout(timer);
            box.remove();
            if (onClose) onClose(reason);
        };
        if (action) {
            const button = makeEl('button', 'ui-toast-action', action);
            button.type = 'button';
            button.addEventListener('click', () => close('action'));
            box.appendChild(button);
        }
        const dismiss = makeEl('button', 'ui-toast-close', '✕');
        dismiss.type = 'button';
        dismiss.setAttribute('aria-label', 'Cerrar aviso');
        dismiss.addEventListener('click', () => close('dismiss'));
        box.appendChild(dismiss);
        toastRegion.appendChild(box);
        timer = setTimeout(() => close('timeout'), duration);
        return { close: () => close('dismiss') };
    }

    function exactDateTime(dateString) {
        if (!dateString) return '';
        const date = parseServerDate(dateString);
        const day = date.toLocaleDateString('es-CR', { day: '2-digit', month: '2-digit', year: 'numeric' });
        const time = date.toLocaleTimeString('es-CR', { hour: '2-digit', minute: '2-digit', hour12: false });
        return `${day} a las ${time}`;
    }

    // "ahora", "hace 5 min", "hace 2 h", "ayer", "hace 3 días" o la fecha
    function relativeTime(dateString) {
        if (!dateString) return '';
        const date = parseServerDate(dateString);
        const now = new Date();
        const minutes = Math.floor((now - date) / 60000);
        if (minutes < 1) return 'ahora';
        if (minutes < 60) return `hace ${minutes} min`;
        const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
        const days = Math.round((startOfDay(now) - startOfDay(date)) / 86400000);
        if (days === 0) return `hace ${Math.floor(minutes / 60)} h`;
        if (days === 1) return 'ayer';
        if (days < 7) return `hace ${days} días`;
        return date.toLocaleDateString('es-CR', { day: '2-digit', month: '2-digit', year: 'numeric' });
    }

    // <time> con el texto relativo y la fecha y hora exactas en title
    function timeTag(dateString, className = '') {
        const node = makeEl('time', className, relativeTime(dateString));
        if (dateString) {
            node.dateTime = parseServerDate(dateString).toISOString();
            node.title = exactDateTime(dateString);
        }
        return node;
    }

    // Filas grises mientras carga. En un <tbody> pone `columns` celdas por fila;
    // en otro contenedor, bloques de dos líneas.
    function showSkeleton(container, { rows = 6, columns = 4 } = {}) {
        const widths = [62, 48, 70, 55, 66, 44];
        container.replaceChildren();
        container.setAttribute('aria-busy', 'true');
        for (let i = 0; i < rows; i++) {
            if (container.tagName === 'TBODY') {
                const tr = makeEl('tr', 'ui-skeleton-row');
                tr.setAttribute('aria-hidden', 'true');
                for (let c = 0; c < columns; c++) {
                    const td = makeEl('td');
                    const bar = makeEl('span', 'ui-skeleton');
                    bar.style.width = `${c === 1 ? widths[i % widths.length] : 60}%`;
                    td.appendChild(bar);
                    tr.appendChild(td);
                }
                container.appendChild(tr);
            } else {
                const block = makeEl('div', 'ui-skeleton-line');
                block.setAttribute('aria-hidden', 'true');
                const first = makeEl('span', 'ui-skeleton');
                first.style.width = `${widths[i % widths.length]}%`;
                const second = makeEl('span', 'ui-skeleton is-soft');
                second.style.width = '34%';
                block.append(first, second);
                container.appendChild(block);
            }
        }
    }

    function clearSkeleton(container) {
        container.removeAttribute('aria-busy');
        container.querySelectorAll('.ui-skeleton-row, .ui-skeleton-line').forEach(node => node.remove());
    }

    // Estado vacío: qué pasó (title), qué puede hacer (text) y un botón opcional
    function emptyState({ title, text = '', action = null, onAction = null }) {
        const box = makeEl('div', 'ui-empty');
        box.appendChild(makeEl('p', 'ui-empty-title', title));
        if (text) box.appendChild(makeEl('p', 'ui-empty-text', text));
        if (action && onAction) {
            const button = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm', action);
            button.type = 'button';
            button.addEventListener('click', onAction);
            box.appendChild(button);
        }
        return box;
    }

    // Error al cargar: el motivo del servidor y "Reintentar". err es el Error de apiFetch
    // (su mensaje ya viene de getErrorMessage o del detail) o el cuerpo de la respuesta.
    function errorState(err, onRetry) {
        const box = emptyState({
            title: 'No se pudo cargar',
            text: err instanceof Error ? err.message : getErrorMessage(err),
            action: onRetry ? 'Reintentar' : null,
            onAction: onRetry,
        });
        box.classList.add('is-error');
        box.setAttribute('role', 'alert');
        return box;
    }

    // Create ticket submit
    const ticketForm = document.getElementById('ticketForm');
    if (ticketForm) {
        ticketForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            hideError();

            if (!currentUser) {
                showError('Debes iniciar sesión para crear un ticket');
                return;
            }

            const title = document.getElementById('title').value;
            const description = document.getElementById('description').value;
            const categoryId = categorySelect.value;
            const priorityId = prioritySelect.value;

            if (!categoryId || !priorityId) {
                showError('Seleccione categoría y prioridad');
                return;
            }

            // Nombre y cédula salen del perfil; solo el teléfono de contacto se puede cambiar
            const contactPhone = document.getElementById('contactPhone').value.trim();
            const contactError = validatePhone(contactPhone);
            if (contactError) {
                showError(contactError);
                return;
            }

            const submit = document.getElementById('newTicketSubmit');
            submit.disabled = true;
            let created;
            try {
                created = await apiFetch('/tickets', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        title,
                        description,
                        category_id: parseInt(categoryId),
                        priority_id: parseInt(priorityId),
                        contact_phone: contactPhone,
                        ...(selectedRequesterId !== null ? { requester_id: selectedRequesterId } : {})
                    })
                });
            } catch (err) {
                return; // apiFetch ya mostró el error; el formulario conserva lo escrito
            } finally {
                submit.disabled = false;
            }

            ticketForm.reset();
            resetRequesterField();
            await loadTickets(getFilters());
            if (isStaffShell()) {
                // El personal sigue en el ticket que acaba de crear
                openTicketById(created.id);
                showSuccess(`Ticket #${created.id} creado`);
            } else {
                showView('tickets');
                if (isRequesterPortal()) loadPortalList();
                showSuccess('Solicitud enviada');
            }
        });
    }

    // Pill buttons filter handlers
    const pillButtons = document.querySelectorAll('.pill-btn');
    pillButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            pillButtons.forEach(b => {
                b.classList.remove('active');
                b.setAttribute('aria-pressed', 'false');
            });
            btn.classList.add('active');
            btn.setAttribute('aria-pressed', 'true');
            currentStatus = btn.dataset.status;
            loadTickets(getFilters());
        });
    });

    // Toggle More Filters handler
    if (toggleMoreFiltersBtn && moreFiltersBlock) {
        toggleMoreFiltersBtn.addEventListener('click', () => {
            const isExpanded = toggleMoreFiltersBtn.getAttribute('aria-expanded') === 'true';
            toggleMoreFiltersBtn.setAttribute('aria-expanded', !isExpanded);
            moreFiltersBlock.style.display = isExpanded ? 'none' : 'flex';
            toggleMoreFiltersBtn.textContent = isExpanded ? 'Más filtros' : 'Menos filtros';
        });
    }

    categoryFilter.addEventListener('change', () => {
        loadTickets(getFilters());
    });

    priorityFilter.addEventListener('change', () => {
        loadTickets(getFilters());
    });

    searchInput.addEventListener('input', () => {
        loadTickets(getFilters());
    });

    const navTickets = document.getElementById('navTickets');
    const navUsers = document.getElementById('navUsers');
    const openNewTicket = document.getElementById('openNewTicket');

    if (navTickets) {
        navTickets.addEventListener('click', () => {
            showView('tickets');
        });
    }

    if (navUsers) {
        // Invitaciones: pantalla propia (antes era un modal)
        navUsers.addEventListener('click', () => {
            setSideNavActive('invites');
            showView('invites');
            setActiveTab(navUsers);
            renderInvitesSection();
        });
    }

    // ===== Vistas: Tickets, Directorio y Administración =====
    const navDirectory = document.getElementById('navDirectory');
    const navAdmin = document.getElementById('navAdmin');
    const splitView = document.querySelector('main .split');
    const directoryView = document.getElementById('directoryView');
    const listView = document.getElementById('listView');
    const navAccounts = document.getElementById('navAccounts');
    const accountsView = document.getElementById('accountsView');
    const adminView = document.getElementById('adminView');
    const overdueReportView = document.getElementById('overdueReportView');
    const navDrafts = document.getElementById('navDrafts');
    const draftsView = document.getElementById('draftsView');
    const navProfile = document.getElementById('navProfile');
    const profileView = document.getElementById('profileView');
    const navReports = document.getElementById('navReports');
    const reportsView = document.getElementById('reportsView');
    const adminAccountsView = document.getElementById('adminAccountsView');
    const homeView = document.getElementById('homeView');
    const newTicketView = document.getElementById('newTicketView');

    // Título de cada pantalla en la pestaña del navegador
    const SCREEN_TITLES = {
        home: 'Inicio', directory: 'Directorio', drafts: 'Borradores de correo', profile: 'Mi cuenta',
        reports: 'Reportes de cuentas', adminAccounts: 'Cuentas', accounts: 'Cuentas', admin: 'Administración',
        newTicket: 'Nuevo ticket', invites: 'Invitaciones', overdueReport: 'Reporte de vencimiento',
        list: 'Equipo',
    };

    function setDocumentTitle(text) {
        document.title = text ? `${text} · Soporte` : 'Soporte · American Data';
    }

    function setActiveTab(activeTab) {
        document.querySelectorAll('.topbar-tab').forEach(tab => {
            const isActive = tab === activeTab;
            tab.classList.toggle('active', isActive);
            if (isActive) tab.setAttribute('aria-current', 'page');
            else tab.removeAttribute('aria-current');
        });
    }

    function updateRoleTabs() {
        const role = currentUser ? currentUser.role : '';
        navDirectory.hidden = !(role === 'coordinator' || role === 'admin');
        navAdmin.hidden = role !== 'admin';
        navAccounts.hidden = role !== 'coordinator';
        navReports.hidden = !REPORT_REVIEW_ROLES.includes(role);
        // Invitaciones: solo el administrador (cuida las cuentas)
        navUsers.hidden = role !== 'admin';
        navDrafts.hidden = !DRAFT_ROLES.includes(role);
        // Mi cuenta: todos los roles que inician sesión
        navProfile.hidden = !role || role === 'agent';
        // Portal del solicitante: el asistente también como pestaña
        document.getElementById('navAssistant').hidden = role !== 'requester';
    }

    function showView(name) {
        currentViewName = name;
        commitPendingAssign();
        commitPendingCancel();
        // Cualquier otra pantalla cierra la página del ticket
        if (name !== 'tickets') exitTicketPage();
        if (splitView) splitView.style.display = name === 'tickets' ? '' : 'none';
        homeView.hidden = name !== 'home';
        document.getElementById('invitesView').hidden = name !== 'invites';
        newTicketView.hidden = name !== 'newTicket';
        // Nuevo ticket: página propia, con la barra superior y el menú
        document.body.classList.toggle('newticket-open', name === 'newTicket');
        directoryView.hidden = name !== 'directory';
        listView.hidden = name !== 'list';
        accountsView.hidden = name !== 'accounts';
        adminView.hidden = name !== 'admin';
        draftsView.hidden = name !== 'drafts';
        profileView.hidden = name !== 'profile';
        reportsView.hidden = name !== 'reports';
        adminAccountsView.hidden = name !== 'adminAccounts';
        overdueReportView.hidden = name !== 'overdueReport';
        // El reporte de vencimiento: página propia, con la barra superior y el menú
        document.body.classList.toggle('overdue-open', name === 'overdueReport');
        const tabs = {
            tickets: navTickets, directory: navDirectory, accounts: navAccounts,
            admin: navAdmin, drafts: navDrafts, profile: navProfile, reports: navReports
        };
        setActiveTab(tabs[name]);
        if (name === 'tickets') {
            setDocumentTitle(currentUser && currentUser.role === 'requester' ? 'Mis solicitudes' : 'Tickets');
        } else {
            setDocumentTitle(SCREEN_TITLES[name]);
        }
        if (name === 'home') {
            renderHome();
            loadHomeLists();
        }
        if (name === 'directory') loadDirectory();
        if (name === 'accounts') loadAccountsView();
        if (name === 'admin') loadAdminPanel();
        if (name === 'drafts') loadDrafts();
        if (name === 'reports') {
            showReportsList();
            loadReports();
        }
        if (name === 'adminAccounts') loadAdminAccounts();
        if (isStaffShell()) {
            renderPageHead();
            renderMyWork();
        }
    }

    navDirectory.addEventListener('click', () => showView('directory'));
    navAccounts.addEventListener('click', () => showView('accounts'));
    navAdmin.addEventListener('click', () => showView('admin'));
    navDrafts.addEventListener('click', () => showView('drafts'));
    navProfile.addEventListener('click', () => showProfile());
    navReports.addEventListener('click', () => {
        setReportsAccount(null);
        showView('reports');
    });

    // ----- Perfil del solicitante -----
    const profileForm = document.getElementById('profileForm');
    const profileName = document.getElementById('profileName');
    const profileIdNumber = document.getElementById('profileIdNumber');
    const profilePhone = document.getElementById('profilePhone');
    const profileCancel = document.getElementById('profileCancel');

    function profileRequired() {
        return Boolean(currentUser && currentUser.role === 'requester' && !currentUser.profile_complete);
    }

    function showProfile() {
        const required = profileRequired();
        const isRequester = currentUser.role === 'requester';
        // Mientras falte el perfil no se puede usar el resto de la página
        document.body.classList.toggle('profile-required', required);
        document.getElementById('profileTitle').textContent = required ? 'Complete su perfil' : 'Mi cuenta';
        document.getElementById('profileIntro').hidden = !required;
        // El solicitante cambia el teléfono en sus datos personales; el personal, en su propio formulario.
        // Qué secciones existen para este rol; applyProfileTab muestra solo las de la pestaña elegida.
        const technician = hasAvailability(currentUser);
        const allowed = {
            accountPersonal: isRequester,
            accountStaffProfile: !isRequester,
            accountPhone: !isRequester && !required,
            accountAvailability: technician,
            accountSchedule: technician,
            accountEmail: !required,
            // Con Microsoft, técnicos y coordinadores no tienen contraseña (el servidor responde 403)
            accountPassword: !required && !staffUsesMicrosoft(currentUser),
        };
        Object.entries(allowed).forEach(([id, ok]) => { document.getElementById(id).dataset.allowed = ok ? '1' : ''; });
        // Disponibilidad y horario propios: técnicos y coordinadores (el servidor no los da a otros roles)
        document.getElementById('accountTabTurno').hidden = !technician;
        document.getElementById('accountTabSeguridad').hidden = staffUsesMicrosoft(currentUser);
        document.getElementById('accountTabs').hidden = required;
        document.getElementById('accountHome').hidden = required;
        document.getElementById('accountAvatar').textContent = getInitials(currentUser.name);
        document.getElementById('accountWhoName').textContent = currentUser.name || currentUser.email;
        document.getElementById('accountWhoRole').textContent = ROLE_MAP[currentUser.role] || currentUser.role;
        document.getElementById('accountStaffName').textContent = currentUser.name || '—';
        document.getElementById('accountStaffRole').textContent = ROLE_MAP[currentUser.role] || currentUser.role;
        if (required || (profileTab === 'turno' && !technician)
            || (profileTab === 'seguridad' && staffUsesMicrosoft(currentUser))) profileTab = 'perfil';
        applyProfileTab();
        fillAccountSections();
        profileCancel.hidden = required;
        profileName.value = currentUser.name || '';
        profileIdNumber.value = currentUser.id_number || '';
        profilePhone.value = currentUser.phone || '';
        const locked = Boolean(currentUser.identity_locked);
        profileName.readOnly = locked;
        profileIdNumber.readOnly = locked;
        document.getElementById('profileLockedNote').hidden = !locked;
        showView('profile');
        if (required) setDocumentTitle('Complete su perfil');
        else setDocumentTitle(`${PROFILE_TAB_TITLES[profileTab]} · Mi cuenta`);
        if (isRequester) (locked ? profilePhone : profileName).focus();
    }

    // ----- Pestañas de Mi cuenta: Perfil, Correo, Seguridad y (técnico) Disponibilidad y horario -----
    let profileTab = 'perfil';
    const PROFILE_TAB_TITLES = { perfil: 'Perfil', correo: 'Correo', seguridad: 'Seguridad', turno: 'Disponibilidad y horario' };

    function applyProfileTab() {
        profileView.querySelectorAll('.account-section[data-account-tab]').forEach(section => {
            section.hidden = section.dataset.allowed !== '1' || section.dataset.accountTab !== profileTab;
        });
        document.querySelectorAll('#accountTabs .account-tab').forEach(tab => {
            if (tab.dataset.accountTab === profileTab) tab.setAttribute('aria-current', 'page');
            else tab.removeAttribute('aria-current');
        });
        if (!profileRequired()) setDocumentTitle(`${PROFILE_TAB_TITLES[profileTab]} · Mi cuenta`);
    }

    document.getElementById('accountTabs').addEventListener('click', (e) => {
        const tab = e.target.closest('.account-tab');
        if (!tab) return;
        profileTab = tab.dataset.accountTab;
        applyProfileTab();
    });

    document.getElementById('accountHome').addEventListener('click', () => {
        if (isStaffShell()) {
            showStaffHome();
        } else {
            showView('tickets');
        }
    });

    document.getElementById('accountLogout').addEventListener('click', () => switchAccountBtn.click());

    profileForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const name = profileName.value.trim();
        const idNumber = profileIdNumber.value.trim();
        const phone = profilePhone.value.trim();
        if (name.split(/\s+/).length < 2 && !currentUser.identity_locked) {
            showError('Escriba su nombre completo (nombre y apellidos)');
            return;
        }
        const contactError = validatePhone(phone) || validateIdNumber(idNumber);
        if (contactError) {
            showError(contactError);
            return;
        }
        try {
            currentUser = await apiFetch('/profile', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, id_number: idNumber, phone })
            });
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        document.body.classList.remove('profile-required');
        // Nombre e iniciales en la barra
        document.querySelector('.topbar-user-name').textContent = currentUser.name;
        document.querySelector('.topbar-avatar').textContent = getInitials(currentUser.name);
        showView('tickets');
        showSuccess('Perfil guardado');
    });

    profileCancel.addEventListener('click', () => showView('tickets'));

    // ----- Mi cuenta: teléfono (personal), correo y contraseña (todos) -----
    const accountPhoneSection = document.getElementById('accountPhone');
    const accountAvailabilitySection = document.getElementById('accountAvailability');
    const accountScheduleSection = document.getElementById('accountSchedule');
    const accountEmailSection = document.getElementById('accountEmail');
    const accountPasswordSection = document.getElementById('accountPassword');
    const accountPhoneForm = document.getElementById('accountPhoneForm');
    const accountPhoneInput = document.getElementById('accountPhoneInput');
    const accountCurrentEmail = document.getElementById('accountCurrentEmail');
    const emailChangeForm = document.getElementById('emailChangeForm');
    const emailChangeNew = document.getElementById('emailChangeNew');
    const emailChangePassword = document.getElementById('emailChangePassword');
    const emailChangeError = document.getElementById('emailChangeError');
    const emailCodeForm = document.getElementById('emailCodeForm');
    const emailCodeNotice = document.getElementById('emailCodeNotice');
    const emailCodeInput = document.getElementById('emailCodeInput');
    const emailCodeResend = document.getElementById('emailCodeResend');
    const emailCodeCancel = document.getElementById('emailCodeCancel');
    const passwordForm = document.getElementById('passwordForm');
    const passwordCurrent = document.getElementById('passwordCurrent');
    const passwordNew = document.getElementById('passwordNew');
    const passwordConfirm = document.getElementById('passwordConfirm');
    const passwordHint = document.getElementById('passwordHint');
    let emailResendTimer = null;

    // Mismo mínimo que app/security.py
    function passwordMinimum() {
        return currentUser && currentUser.role === 'requester' ? 8 : 12;
    }

    function fillAccountSections() {
        accountPhoneInput.value = currentUser.phone || '';
        accountCurrentEmail.textContent = currentUser.email;
        passwordHint.textContent = `Al menos ${passwordMinimum()} caracteres.`;
        passwordForm.reset();
        if (hasAvailability(currentUser)) {
            renderOwnSchedule(currentUser.schedule || [], currentUser.schedule_summary);
            renderOwnAvailability();
        }
    }

    function refreshTopbarUser() {
        document.querySelector('.topbar-user-name').textContent = currentUser.name || currentUser.email;
        renderNavUser();
    }

    function startEmailResendWait() {
        let left = RESEND_WAIT_SECONDS;
        clearInterval(emailResendTimer);
        emailCodeResend.disabled = true;
        emailCodeResend.textContent = `Reenviar código en ${left} s`;
        emailResendTimer = setInterval(() => {
            left -= 1;
            if (left <= 0) {
                clearInterval(emailResendTimer);
                emailResendTimer = null;
                emailCodeResend.disabled = false;
                emailCodeResend.textContent = 'Reenviar código';
                return;
            }
            emailCodeResend.textContent = `Reenviar código en ${left} s`;
        }, 1000);
    }

    function hideEmailChangeError() {
        emailChangeError.hidden = true;
        emailChangeError.textContent = '';
    }

    function resetEmailChange() {
        clearInterval(emailResendTimer);
        emailResendTimer = null;
        hideEmailChangeError();
        emailChangeForm.reset();
        emailCodeForm.reset();
        emailCodeForm.hidden = true;
        emailChangeForm.hidden = false;
    }

    accountPhoneForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const phone = accountPhoneInput.value.trim();
        const phoneError = validatePhone(phone);
        if (phoneError) {
            showError(phoneError);
            return;
        }
        try {
            currentUser = await apiFetch('/account/phone', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ phone })
            });
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        accountPhoneInput.value = currentUser.phone || '';
        showSuccess('Teléfono guardado');
    });

    // ----- Disponibilidad propia: automática; aquí solo se registran vacaciones e incapacidades -----
    const ownPeriodKind = document.getElementById('ownPeriodKind');
    const ownPeriodStart = document.getElementById('ownPeriodStart');
    const ownPeriodEnd = document.getElementById('ownPeriodEnd');
    const ownPeriods = document.getElementById('ownPeriods');

    function renderOwnAvailability() {
        document.getElementById('ownAvailabilityNow').textContent =
            availabilityNowText(currentUser.availability, currentUser.return_date);
        renderPeriodList(ownPeriods, currentUser.periods || []);
        preparePeriodDates(ownPeriodStart, ownPeriodEnd);
    }

    document.getElementById('accountAvailabilityForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        try {
            currentUser = await apiFetch('/account/availability-periods', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(periodPayload(ownPeriodKind, ownPeriodStart, ownPeriodEnd))
            });
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        document.getElementById('accountAvailabilityForm').reset();
        renderOwnAvailability();
        showSuccess('Periodo registrado');
    });

    ownPeriods.addEventListener('click', async (e) => {
        const button = e.target.closest('button[data-period-id]');
        if (!button || !confirm('¿Quitar este periodo? Su disponibilidad volverá a depender de su horario.')) return;
        button.disabled = true;
        try {
            currentUser = await apiFetch(`/account/availability-periods/${button.dataset.periodId}`, { method: 'DELETE' });
        } catch (err) {
            button.disabled = false;
            return; // apiFetch ya mostró el error
        }
        renderOwnAvailability();
        showSuccess('Periodo quitado');
    });

    emailChangeForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        hideEmailChangeError();
        const newEmail = emailChangeNew.value.trim();
        let result;
        try {
            result = await apiFetch('/account/email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_email: newEmail, current_password: emailChangePassword.value })
            });
        } catch (err) {
            // Correo en uso, espera de 60 s, contraseña incorrecta...: el error va dentro del formulario
            // (err.message ya viene de getErrorMessage en apiFetch)
            if (err.message !== 'Unauthorized') {
                hideError();
                emailChangeError.textContent = err.message;
                emailChangeError.hidden = false;
            }
            return;
        }
        emailChangeForm.hidden = true;
        emailCodeForm.hidden = false;
        emailChangePassword.value = '';
        emailCodeNotice.textContent = 'Si ese correo está disponible, le enviamos un código. '
            + 'Si no le llega en unos minutos, revise la carpeta de spam o pruebe con otro correo.';
        startEmailResendWait();
        emailCodeInput.focus();
    });

    emailCodeForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const code = emailCodeInput.value.trim();
        if (!/^[0-9]{6}$/.test(code)) {
            showError('Escriba el código de 6 dígitos');
            return;
        }
        try {
            currentUser = await apiFetch('/account/email/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code })
            });
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        resetEmailChange();
        fillAccountSections();
        refreshTopbarUser();
        showSuccess('Correo actualizado. Se cerraron sus sesiones en otros dispositivos.');
    });

    emailCodeResend.addEventListener('click', async () => {
        hideError();
        emailCodeResend.disabled = true;
        try {
            const result = await apiFetch('/account/email/resend', { method: 'POST' });
            emailCodeNotice.textContent = `Si el correo se puede usar, le enviamos un código nuevo a ${result.email_hint}.`;
            startEmailResendWait();
        } catch (err) {
            emailCodeResend.disabled = false;
        }
    });

    emailCodeCancel.addEventListener('click', resetEmailChange);

    passwordForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const minimum = passwordMinimum();
        if (passwordNew.value.length < minimum) {
            showError(`La contraseña debe tener al menos ${minimum} caracteres`);
            return;
        }
        if (passwordNew.value !== passwordConfirm.value) {
            showError('Las contraseñas nuevas no coinciden');
            return;
        }
        try {
            await apiFetch('/account/password', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current_password: passwordCurrent.value, new_password: passwordNew.value })
            });
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        passwordForm.reset();
        showSuccess('Contraseña cambiada. Se cerraron sus sesiones en otros dispositivos.');
    });

    // ----- Borradores de correos -----
    // El administrador no entra a los borradores (no confirma ni descarta)
    const DRAFT_ROLES = ['technician', 'coordinator'];
    const DRAFT_STATUS_LABELS = {
        analyzing: 'Analizando',
        pending_review: 'Por revisar',
        waiting_contact: 'Esperando datos de contacto',
        unclear: 'No se entendió (se pidió aclarar)',
        not_support: 'No es soporte',
        status_reply: 'Consulta de estado respondida',
        confirmed: 'Confirmado',
        discarded: 'Descartado',
        error: 'No se pudo analizar'
    };
    const draftsBadge = document.getElementById('draftsBadge');
    const draftsList = document.getElementById('draftsList');
    const draftPlaceholder = document.getElementById('draftPlaceholder');
    const draftContent = document.getElementById('draftContent');
    const draftForm = document.getElementById('draftForm');
    const draftTitleInput = document.getElementById('draftTitle');
    const draftCategorySelect = document.getElementById('draftCategory');
    const draftPrioritySelect = document.getElementById('draftPriority');
    const draftDescriptionInput = document.getElementById('draftDescription');
    const draftPhoneInput = document.getElementById('draftPhone');
    const draftIdNumberInput = document.getElementById('draftIdNumber');
    const draftConfirmBtn = document.getElementById('draftConfirmBtn');
    const draftDiscardBtn = document.getElementById('draftDiscardBtn');
    const draftDetailsBtn = document.getElementById('draftDetailsBtn');
    let drafts = [];
    let selectedDraftId = null;
    let shownDraft = null;
    const draftFlags = document.getElementById('draftFlags');
    const draftFlagTags = document.getElementById('draftFlagTags');

    function renderDraftFlags(draft) {
        draftFlags.hidden = !draft.requester_flags;
        draftFlagTags.replaceChildren(...(draft.requester_flags ? requesterTags(draft.requester_flags) : []));
    }

    document.getElementById('draftReportBtn').addEventListener('click', async () => {
        if (!shownDraft) return;
        const draft = shownDraft;
        if (await openReportDialog(draft.requester_id, draft.requester_name) && draft.requester_flags) {
            draft.requester_flags.reported = true;
            if (shownDraft === draft) renderDraftFlags(draft);
        }
    });

    function updateDraftsBadge(count) {
        draftsBadge.textContent = String(count);
        draftsBadge.hidden = count === 0;
        navDrafts.setAttribute('aria-label', count ? `Borradores, ${count} pendientes` : 'Borradores');
    }

    async function refreshDraftsBadge() {
        if (!currentUser || !DRAFT_ROLES.includes(currentUser.role)) return;
        try {
            const list = await apiFetch('/drafts');
            updateDraftsBadge(list.length);
        } catch (err) {
            // apiFetch ya mostró el error
        }
    }

    // Contador cada minuto, solo con la pestaña visible. No usa apiFetch para no borrar
    // un mensaje de error en pantalla, y no vuelve a dibujar la lista ni el borrador abierto.
    async function pollDraftsBadge() {
        if (document.visibilityState !== 'visible' || !authToken) return;
        try {
            const response = await fetch(`${API_BASE}/drafts`, {
                headers: { 'Accept': 'application/json', 'Authorization': `Bearer ${authToken}` }
            });
            if (response.status === 401) {
                const data = await response.json().catch(() => ({}));
                handleUnauthorized(data.detail);
                return;
            }
            if (!response.ok) return;
            const list = await response.json();
            updateDraftsBadge(list.length);
        } catch (err) {
            // Se reintenta en el siguiente minuto
        }
    }

    function stopDraftsPolling() {
        if (draftsPollTimer !== null) {
            clearInterval(draftsPollTimer);
            draftsPollTimer = null;
        }
    }

    function startDraftsPolling() {
        stopDraftsPolling();
        if (!currentUser || !DRAFT_ROLES.includes(currentUser.role)) return;
        draftsPollTimer = setInterval(pollDraftsBadge, 60000);
    }

    // El coordinador ve todos y filtra; el técnico, solo los que le asignaron
    const draftsFilter = document.getElementById('draftsFilter');
    const draftReassign = document.getElementById('draftReassign');

    function setupDraftsFilter() {
        const manager = OPERATION_ROLES.includes(currentUser.role);
        document.getElementById('draftsFilterField').hidden = !manager;
        document.getElementById('draftsIntro').textContent = manager
            ? 'La IA leyó estos correos. Cada uno tiene un técnico que lo revisa; usted puede reasignarlo.'
            : 'Mis borradores: los correos que le asignaron. Usted decide si se vuelven ticket.';
    }

    // Secciones de Borradores: el filtro del coordinador (el técnico ve siempre los suyos)
    function showDrafts(filter) {
        draftsFilter.value = filter;
        selectedDraftId = null;
        showView('drafts');
    }

    function openDraftById(draftId) {
        selectedDraftId = draftId;
        // Con un filtro puesto podría no aparecer
        draftsFilter.value = 'all';
        setSideNavActive('drafts');
        showView('drafts');
    }

    draftsFilter.addEventListener('change', () => {
        selectedDraftId = null;
        // Todos y Sin revisor tienen su sección en el menú; "Mis borradores" queda en la misma página
        const routes = { all: 'borradores/todos', none: 'borradores/sin-revisor' };
        if (routes[draftsFilter.value]) {
            goRoute(routes[draftsFilter.value]);
            return;
        }
        loadDrafts();
    });

    async function loadDrafts() {
        if (!drafts.length) showSkeleton(draftsList, { rows: 4 });
        setupDraftsFilter();
        const query = OPERATION_ROLES.includes(currentUser.role) && draftsFilter.value !== 'all'
            ? `?assigned=${draftsFilter.value}` : '';
        try {
            drafts = await apiFetch(`/drafts${query}`, { silent: true });
        } catch (err) {
            draftsList.removeAttribute('aria-busy');
            draftsList.replaceChildren();
            draftContent.hidden = true;
            draftPlaceholder.hidden = false;
            draftPlaceholder.replaceChildren(errorState(err, loadDrafts));
            return;
        }
        draftsList.removeAttribute('aria-busy');
        if (!query) updateDraftsBadge(drafts.length);
        renderDraftList();
        const current = drafts.find(d => d.id === selectedDraftId);
        if (current) {
            showDraft(current);
        } else {
            selectedDraftId = null;
            draftContent.hidden = true;
            draftPlaceholder.hidden = false;
            draftPlaceholder.replaceChildren(drafts.length
                ? emptyState({ title: 'Elija un correo de la lista', text: 'Verá el correo original y lo que propone la IA.' })
                : emptyState({ title: 'No hay borradores pendientes', text: 'Cuando llegue un correo que la IA no pueda convertir sola, aparecerá aquí.' }));
        }
    }

    // Etiqueta de cada borrador: [texto, tono de ui-tag]
    function draftTag(draft) {
        if (draft.merged_into) return [`Agregado a #${draft.merged_into}`, 'is-ok'];
        if (draft.status === 'error') return [DRAFT_STATUS_LABELS.error, 'is-danger'];
        if (draft.status === 'unclear') return ['Poco claro', 'is-warn'];
        if (draft.status === 'waiting_contact') return ['Esperando datos', 'is-info'];
        if (draft.needs_review) return ['Revisar', 'is-warn'];
        if (draft.status === 'pending_review') return ['Listo para crear', 'is-ok'];
        return [DRAFT_STATUS_LABELS[draft.status] || draft.status, ''];
    }

    function draftOwnerText(draft) {
        return draft.assigned_name ? `Asignado a ${draft.assigned_name}` : 'Sin revisor';
    }

    // Solo técnicos activos (el servidor lo vuelve a validar)
    function fillDraftReassign(draft) {
        draftReassign.replaceChildren();
        const current = staffOption('', draft.assigned_name ? `${draft.assigned_name} (actual)` : 'Sin revisor', true);
        current.disabled = true;
        draftReassign.appendChild(current);
        Object.keys(userMap).forEach(userId => {
            if (userRoleMap[userId] !== 'technician' || Number(userId) === draft.assigned_to) return;
            const availability = userAvailabilityMap[userId];
            const text = availability && availability !== 'available'
                ? `${userMap[userId]} — ${AVAILABILITY_LABELS[availability]}`
                : userMap[userId];
            draftReassign.appendChild(staffOption(userId, text, false));
        });
    }

    function renderDraftList() {
        draftsList.replaceChildren();
        drafts.forEach(draft => {
            const li = document.createElement('li');
            const btn = makeEl('button', 'draft-item');
            btn.type = 'button';
            btn.dataset.draftId = String(draft.id);
            if (draft.id === selectedDraftId) btn.setAttribute('aria-current', 'true');
            const top = makeEl('span', 'draft-item-top');
            top.appendChild(makeEl('span', 'draft-item-from', draft.requester_name));
            top.appendChild(timeTag(draft.created_at, 'draft-item-time'));
            btn.appendChild(top);
            btn.appendChild(makeEl('span', 'draft-item-subject', draft.subject || '(Sin asunto)'));
            btn.appendChild(makeEl('span', 'draft-item-snippet', (draft.body || '').replace(/\s+/g, ' ').trim()));
            const [tag, tone] = draftTag(draft);
            const tags = makeEl('span', 'draft-item-tags');
            tags.appendChild(makeEl('span', `ui-tag ${tone}`, tag));
            tags.appendChild(makeEl('span', draft.assigned_name ? 'ui-tag' : 'ui-tag is-warn', draftOwnerText(draft)));
            btn.appendChild(tags);
            li.appendChild(btn);
            draftsList.appendChild(li);
        });
    }

    function fillDraftSelect(select, map, selectedId) {
        select.replaceChildren();
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = '-- Seleccionar --';
        select.appendChild(empty);
        Object.keys(map).forEach(id => {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = map[id];
            if (Number(id) === selectedId) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function showDraft(draft) {
        selectedDraftId = draft.id;
        draftPlaceholder.hidden = true;
        draftContent.hidden = false;
        draftsList.querySelectorAll('.draft-item').forEach(btn => {
            if (btn.dataset.draftId === String(draft.id)) btn.setAttribute('aria-current', 'true');
            else btn.removeAttribute('aria-current');
        });

        document.getElementById('draftNumber').textContent = `BORRADOR #${draft.id}`;
        draftDetailsBtn.hidden = !draft.can_review || !['pending_review', 'waiting_contact'].includes(draft.status);
        document.getElementById('draftDetailsHint').hidden = draftDetailsBtn.hidden;
        document.getElementById('draftSubject').textContent = draft.subject || '(Sin asunto)';
        const draftMeta = document.getElementById('draftMeta');
        draftMeta.replaceChildren(document.createTextNode('De '), makeEl('b', '', draft.requester_name),
            document.createTextNode(` · ${DRAFT_STATUS_LABELS[draft.status] || draft.status} · recibido `), timeTag(draft.created_at));
        const [tag, tone] = draftTag(draft);
        const draftTagEl = document.getElementById('draftTag');
        draftTagEl.textContent = tag;
        draftTagEl.className = `ui-tag ${tone}`;
        document.getElementById('draftAssignee').textContent = draftOwnerText(draft);
        // Reasignar: solo el coordinador. Confirmar o descartar: el revisor o un coordinador
        document.getElementById('draftReassignField').hidden = !draft.can_reassign;
        if (draft.can_reassign) fillDraftReassign(draft);
        document.getElementById('draftActions').hidden = !draft.can_review;
        document.getElementById('draftNotYours').hidden = draft.can_review;
        shownDraft = draft;
        renderDraftFlags(draft);
        const draftReason = document.getElementById('draftReason');
        draftReason.textContent = draft.pending_reason ? `Pendiente: ${draft.pending_reason}` : '';
        draftReason.hidden = !draft.pending_reason;
        document.getElementById('draftBody').textContent = draft.body;
        // Adjuntos del correo y lo que no se tomó (rechazados, más de 5, enlaces a la nube)
        const draftAttachments = document.getElementById('draftAttachments');
        draftAttachments.replaceChildren();
        const draftFiles = attachmentList(draft.attachments, '/drafts/attachments');
        if (draftFiles) draftAttachments.appendChild(draftFiles);
        (draft.attachment_notes || []).forEach(note => draftAttachments.appendChild(makeEl('p', 'att-note', note)));
        draftAttachments.hidden = !draftFiles && !(draft.attachment_notes || []).length;

        const ai = document.getElementById('draftAi');
        ai.replaceChildren();
        const level = draft.ai_level;
        const facts = makeEl('dl', 'draft-ai-facts');
        [['Título del ticket', draft.title || '—'],
         ['Nivel', level === null || level === undefined ? 'Sin estimar' : `${level} · ${AI_LEVEL_NAMES[level]}`],
         ['Cliente', draft.requester_name]].forEach(([label, value]) => {
            const box = makeEl('div');
            box.append(makeEl('dt', '', label), makeEl('dd', '', value));
            facts.appendChild(box);
        });
        ai.appendChild(facts);
        const summary = makeEl('p', draft.summary ? 'draft-ai-note' : 'draft-ai-note ai-muted', draft.summary || 'La IA no dejó un resumen.');
        ai.appendChild(summary);
        if (draft.status === 'waiting_contact') {
            const waiting = document.createElement('p');
            waiting.className = 'ai-muted';
            waiting.textContent = 'Se pidió al cliente su teléfono y su cédula. Si los consiguió por otro medio, escríbalos abajo.';
            ai.appendChild(waiting);
        }

        draftTitleInput.value = draft.title || '';
        draftDescriptionInput.value = draft.description || '';
        draftPhoneInput.value = draft.contact_phone || '';
        draftIdNumberInput.value = draft.contact_id_number || '';
        // Si el borrador ya los tiene, vacíos o enmascarados se usan los guardados
        draftPhoneInput.required = !draft.contact_phone;
        draftIdNumberInput.required = !draft.contact_id_number;
        fillDraftSelect(draftCategorySelect, categoryMap, draft.category_id);
        fillDraftSelect(draftPrioritySelect, priorityMap, draft.priority_id);
        draftConfirmBtn.disabled = false;
        draftDiscardBtn.disabled = false;
        draftDetailsBtn.disabled = false;
    }

    function setDraftBusy(busy) {
        draftConfirmBtn.disabled = busy;
        draftDiscardBtn.disabled = busy;
        draftDetailsBtn.disabled = busy;
    }

    draftReassign.addEventListener('change', async () => {
        if (selectedDraftId === null || !draftReassign.value) return;
        const draftId = selectedDraftId;
        const userId = Number(draftReassign.value);
        const send = (confirmUnavailable) => apiFetch(`/drafts/${draftId}/assign`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, confirm_unavailable: confirmUnavailable }),
            silent: true
        });
        draftReassign.disabled = true;
        try {
            try {
                await send(false);
            } catch (err) {
                // De vacaciones, con incapacidad o fuera de su horario: se puede, confirmando
                if (err.status !== 409 || !window.confirm(err.message)) throw err;
                await send(true);
            }
            showSuccess(`Borrador #${draftId} reasignado a ${userMap[userId] || 'otro técnico'}`);
            await loadDrafts();
        } catch (err) {
            if (err.status !== 409) showError(err.message || 'No se pudo reasignar el borrador');
            const current = drafts.find(d => d.id === draftId);
            if (current) fillDraftReassign(current);
        } finally {
            draftReassign.disabled = false;
        }
    });

    draftsList.addEventListener('click', (e) => {
        const btn = e.target.closest('.draft-item');
        if (!btn) return;
        const draft = drafts.find(d => String(d.id) === btn.dataset.draftId);
        if (draft) showDraft(draft);
    });

    draftForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (selectedDraftId === null) return;
        // Solo se valida lo que se escribió nuevo (sin asteriscos)
        const isNew = value => value.trim() !== '' && !value.includes('*');
        const phoneError = isNew(draftPhoneInput.value) ? validatePhone(draftPhoneInput.value) : null;
        const idError = isNew(draftIdNumberInput.value) ? validateIdNumber(draftIdNumberInput.value) : null;
        if (phoneError || idError) {
            showError(phoneError || idError);
            return;
        }
        setDraftBusy(true);
        try {
            const result = await apiFetch(`/drafts/${selectedDraftId}/confirm`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: draftTitleInput.value.trim(),
                    category_id: parseInt(draftCategorySelect.value, 10),
                    priority_id: parseInt(draftPrioritySelect.value, 10),
                    description: draftDescriptionInput.value.trim(),
                    contact_phone: draftPhoneInput.value,
                    contact_id_number: draftIdNumberInput.value
                })
            });
            showSuccess(`Ticket #${result.ticket_id} creado`);
            selectedDraftId = null;
            await loadDrafts();
            loadTickets(getFilters());
        } catch (err) {
            setDraftBusy(false);
            showError(err.message || 'No se pudo confirmar el borrador');
        }
    });

    draftDiscardBtn.addEventListener('click', async () => {
        if (selectedDraftId === null) return;
        if (!window.confirm('¿Descartar este borrador? No se creará ningún ticket ni se avisará al cliente.')) return;
        setDraftBusy(true);
        try {
            await apiFetch(`/drafts/${selectedDraftId}/discard`, { method: 'POST' });
            showSuccess('Borrador descartado');
            selectedDraftId = null;
            await loadDrafts();
        } catch (err) {
            setDraftBusy(false);
            showError(err.message || 'No se pudo descartar el borrador');
        }
    });

    draftDetailsBtn.addEventListener('click', async () => {
        if (selectedDraftId === null) return;
        if (!window.confirm('¿Pedir más detalles al cliente? Se le enviará un correo y este borrador se eliminará.')) return;
        setDraftBusy(true);
        try {
            await apiFetch(`/drafts/${selectedDraftId}/request-details`, { method: 'POST' });
            showSuccess('Se pidieron más detalles al cliente');
            selectedDraftId = null;
            await loadDrafts();
        } catch (err) {
            setDraftBusy(false);
            showError(err.message || 'No se pudo pedir más detalles');
        }
    });

    // ----- Directorio -----
    const directorySearch = document.getElementById('directorySearch');
    const directoryRole = document.getElementById('directoryRole');
    const directoryState = document.getElementById('directoryState');
    const directoryBody = document.getElementById('directoryBody');
    const directoryEmpty = document.getElementById('directoryEmpty');
    const directoryActionsHead = document.querySelector('#directoryHead .admin-only-col');
    let directorySearchTimer = null;
    let directorySeq = 0;

    const ROLE_OPTIONS = [
        ['requester', 'Solicitante'],
        ['technician', 'Técnico'],
        ['coordinator', 'Coordinador'],
        ['admin', 'Administrador']
    ];

    function maskIdNumber(value) {
        if (!value) return '';
        if (value.length <= 3) return value;
        return '*'.repeat(value.length - 3) + value.slice(-3);
    }

    async function loadDirectory() {
        const params = new URLSearchParams();
        const q = directorySearch.value.trim();
        if (q) params.set('q', q);
        if (directoryRole.value) params.set('role', directoryRole.value);
        if (directoryState.value) params.set('state', directoryState.value);
        const seq = ++directorySeq;
        try {
            const query = params.toString();
            const users = await apiFetch(`/directory${query ? `?${query}` : ''}`);
            // Solo la última búsqueda pinta la tabla
            if (seq !== directorySeq) return;
            renderDirectory(users);
        } catch (err) {
            // apiFetch ya mostró el error
        }
    }

    function makeCell(text, className) {
        const td = document.createElement('td');
        if (className) td.className = className;
        td.textContent = text;
        return td;
    }

    function renderDirectory(users) {
        const isAdmin = currentUser && currentUser.role === 'admin';
        directoryActionsHead.hidden = !isAdmin;
        directoryBody.replaceChildren();
        directoryEmpty.hidden = users.length > 0;

        users.forEach(user => {
            const isSelf = currentUser && user.id === currentUser.id;
            const row = document.createElement('tr');
            row.dataset.userId = user.id;
            row.dataset.userName = user.name || user.email;
            row.dataset.userRole = user.role;

            row.appendChild(makeCell(user.name));
            row.appendChild(makeCell(user.email));

            const roleTd = document.createElement('td');
            if (isAdmin && !isSelf) {
                const select = document.createElement('select');
                select.className = 'directory-role-select';
                select.setAttribute('aria-label', `Rol de ${user.name}`);
                ROLE_OPTIONS.forEach(([value, label]) => {
                    const option = document.createElement('option');
                    option.value = value;
                    option.textContent = label;
                    option.selected = value === user.role;
                    select.appendChild(option);
                });
                roleTd.appendChild(select);
            } else {
                roleTd.textContent = ROLE_MAP[user.role] || user.role;
            }
            row.appendChild(roleTd);

            row.appendChild(makeCell(user.phone || '—'));

            const idTd = document.createElement('td');
            if (user.id_number) {
                const wrap = document.createElement('span');
                wrap.className = 'id-cell';
                const value = document.createElement('span');
                value.className = 'id-value';
                value.textContent = maskIdNumber(user.id_number);
                value.dataset.full = user.id_number;
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'link-btn id-toggle';
                toggle.textContent = 'Mostrar';
                toggle.setAttribute('aria-pressed', 'false');
                toggle.setAttribute('aria-label', `Mostrar cédula de ${user.name}`);
                wrap.appendChild(value);
                wrap.appendChild(toggle);
                idTd.appendChild(wrap);
            } else {
                idTd.textContent = '—';
            }
            row.appendChild(idTd);

            row.appendChild(makeCell(String(user.tickets_created), 'num'));
            row.appendChild(makeCell(String(user.tickets_assigned_open), 'num'));

            const stateTd = document.createElement('td');
            const state = document.createElement('span');
            state.className = user.is_active ? 'state-active' : 'state-inactive';
            state.textContent = user.is_active ? 'Activa' : 'Desactivada';
            stateTd.appendChild(state);
            accountTags(user).forEach(tag => stateTd.appendChild(tag));
            row.appendChild(stateTd);

            if (isAdmin) {
                const actionsTd = document.createElement('td');
                if (!isSelf) {
                    actionsTd.appendChild(accountActionsWrap(accountActionButtons(user)));
                }
                row.appendChild(actionsTd);
            }

            directoryBody.appendChild(row);
        });
        labelTableCells(directoryBody.closest('table'));
    }

    async function updateDirectoryUser(userId, changes) {
        try {
            await apiFetch(`/admin/users/${userId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(changes)
            });
            showSuccess('Usuario actualizado');
        } catch (err) {
            // apiFetch ya mostró el error
        }
        await loadDirectory();
    }

    directorySearch.addEventListener('input', () => {
        clearTimeout(directorySearchTimer);
        directorySearchTimer = setTimeout(loadDirectory, 300);
    });

    directoryRole.addEventListener('change', loadDirectory);

    directoryBody.addEventListener('click', (e) => {
        const idToggle = e.target.closest('.id-toggle');
        if (idToggle) {
            const value = idToggle.parentElement.querySelector('.id-value');
            const showing = idToggle.getAttribute('aria-pressed') === 'true';
            value.textContent = showing ? maskIdNumber(value.dataset.full) : value.dataset.full;
            idToggle.textContent = showing ? 'Mostrar' : 'Ocultar';
            idToggle.setAttribute('aria-pressed', showing ? 'false' : 'true');
            return;
        }

        const actionBtn = e.target.closest('.account-action');
        if (actionBtn) {
            runAccountAction(actionBtn).then(done => {
                if (done) loadDirectory();
            });
        }
    });

    directoryState.addEventListener('change', loadDirectory);

    directoryBody.addEventListener('change', (e) => {
        const select = e.target.closest('.directory-role-select');
        if (select) {
            select.disabled = true;
            updateDirectoryUser(select.closest('tr').dataset.userId, { role: select.value });
        }
    });

    // ----- Acciones sobre cuentas (Directorio del administrador y Cuentas del coordinador) -----
    const ACCOUNT_ACTIONS = {
        deactivate: 'Desactivar',
        reactivate: 'Reactivar',
        'reset-access': 'Forzar restablecimiento',
        unlock: 'Desbloquear',
        'close-sessions': 'Cerrar sesiones'
    };
    // Mismas reglas que app/accounts.py (solo comodidad: el servidor también las revisa)
    const MANAGE_RULES = {
        admin: {
            active: ['coordinator', 'technician', 'requester'],
            reset: ['coordinator', 'technician'],
            unlock: ['coordinator', 'technician', 'requester'],
            availability: ['technician', 'coordinator'],
            schedule: ['technician', 'coordinator'],
            sessions: ['coordinator', 'technician', 'requester'],
            role: ['coordinator', 'technician', 'requester']
        },
        // Las cuentas las cuida el administrador: el coordinador maneja turnos y disponibilidad de técnicos
        coordinator: {
            active: [],
            reset: [],
            unlock: [],
            availability: ['technician'],
            schedule: ['technician'],
            sessions: [],
            role: ['technician', 'requester']
        }
    };

    function canManage(user, kind) {
        // Igual que el servidor: con Microsoft no se restablece la contraseña de técnicos y coordinadores
        if (kind === 'reset' && staffUsesMicrosoft(user)) return false;
        const rules = currentUser ? MANAGE_RULES[currentUser.role] : null;
        return Boolean(rules && user.id !== currentUser.id && rules[kind].includes(user.role));
    }

    function makeActionButton(action, label) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn-secondary btn-small account-action';
        button.dataset.action = action;
        button.textContent = label || ACCOUNT_ACTIONS[action];
        return button;
    }

    function accountActionButtons(user) {
        const buttons = [];
        if (canManage(user, 'active')) buttons.push(makeActionButton(user.is_active ? 'deactivate' : 'reactivate'));
        if (user.is_active && canManage(user, 'reset')) buttons.push(makeActionButton('reset-access'));
        if (user.login_locked && canManage(user, 'unlock')) buttons.push(makeActionButton('unlock'));
        if (canManage(user, 'schedule')) {
            const button = makeActionButton('schedule', 'Horario');
            button.dataset.schedule = JSON.stringify(user.schedule || []);
            buttons.push(button);
        }
        if (canManage(user, 'availability')) {
            buttons.push(makeActionButton('availability', 'Vacaciones y ausencias'));
        }
        // Solo cuentas de solicitante; el servidor también lo revisa
        if (user.role === 'requester') buttons.push(makeActionButton('report', 'Reportar cuenta'));
        return buttons;
    }

    function accountActionsWrap(buttons) {
        const wrap = document.createElement('div');
        wrap.className = 'account-actions';
        buttons.forEach(button => wrap.appendChild(button));
        return wrap;
    }

    // Devuelve true si la acción se hizo (para volver a cargar la tabla)
    async function runAccountAction(button) {
        // Fila del directorio o panel de Admin › Cuentas: los dos traen data-user-id
        const row = button.closest('[data-user-id]');
        const name = row.dataset.userName;
        const action = button.dataset.action;
        if (action === 'availability') return openAvailabilityDialog(row.dataset.userId, name);
        if (action === 'schedule') return openScheduleDialog(row.dataset.userId, name, JSON.parse(button.dataset.schedule));
        if (action === 'report') return openReportDialog(row.dataset.userId, name);
        if (action === 'deactivate') {
            const extra = row.dataset.userRole === 'technician' ? ' Sus tickets abiertos quedarán sin asignar.' : '';
            if (!confirm(`¿Desactivar la cuenta de ${name}? No podrá entrar hasta que se reactive.${extra}`)) return false;
        }
        if (action === 'reset-access'
            && !confirm('Se cerrarán todas las sesiones de esta cuenta y su contraseña actual dejará de funcionar. ¿Continuar?')) {
            return false;
        }
        button.disabled = true;
        try {
            await apiFetch(`/accounts/${row.dataset.userId}/${action}`, { method: 'POST' });
        } catch (err) {
            button.disabled = false;
            return false; // apiFetch ya mostró el error
        }
        const messages = {
            deactivate: 'Cuenta desactivada',
            reactivate: 'Cuenta reactivada',
            'reset-access': 'Restablecimiento forzado. Le enviamos el código a su correo',
            unlock: 'Correo desbloqueado',
            'close-sessions': 'Sesiones cerradas'
        };
        showSuccess(messages[action]);
        return true;
    }

    // ----- Disponibilidad, identidad y reportes (solo personal) -----
    const AVAILABILITY_LABELS = { available: 'Disponible', vacation: 'Vacaciones', incapacity: 'Ausencia', away: 'Ausente' };
    const IDENTITY_LABELS = {
        match: 'Verificado', mismatch: 'No coincide', not_found: 'No encontrado', unverified: 'Sin verificar'
    };
    const REPORT_STATUS = { open: 'Abierto', confirmed: 'Confirmado', dismissed: 'Descartado' };
    // Revisar reportes de cuentas: solo el administrador. Dirigir la operación (equipo, incidentes, clientes): el coordinador
    const REPORT_REVIEW_ROLES = ['admin'];
    const OPERATION_ROLES = ['coordinator'];

    // "2026-11-03" → "03/11/2026" sin pasar por la zona horaria
    function formatPlainDate(value) {
        const [year, month, day] = value.split('-');
        return `${day}/${month}/${year}`;
    }

    function localToday() {
        const now = new Date();
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    }

    function makeTag(text, className) {
        const tag = document.createElement('span');
        tag.className = `account-tag ${className}`;
        tag.textContent = text;
        return tag;
    }

    function requesterTags(flags) {
        const identity = flags.identity || 'unverified';
        const tags = [makeTag(IDENTITY_LABELS[identity] || IDENTITY_LABELS.unverified, `tag-identity-${identity}`)];
        if (flags.reported) tags.push(makeTag('Reportada', 'tag-reported'));
        return tags;
    }

    function accountTags(user) {
        if (user.role === 'technician') {
            const tags = [];
            if (user.availability && user.availability !== 'available') {
                const back = user.return_date ? ` · regresa ${formatPlainDate(user.return_date)}` : '';
                tags.push(makeTag(`${AVAILABILITY_LABELS[user.availability]}${back}`, 'tag-away'));
            }
            if (user.schedule_summary) {
                const summary = document.createElement('span');
                summary.className = 'schedule-summary';
                summary.textContent = user.schedule_summary;
                tags.push(summary);
                if (user.schedule && user.schedule.length) {
                    tags.push(user.on_shift ? makeTag('En turno', 'tag-on-shift') : makeTag('Fuera de turno', 'tag-off-shift'));
                }
            }
            return tags;
        }
        if (user.role === 'requester' && user.identity) return requesterTags(user);
        return [];
    }

    // ----- Disponibilidad automática (técnicos y coordinadores) -----
    const AVAILABILITY_ROLES = ['technician', 'coordinator'];
    const PERIOD_KINDS = { vacation: 'Vacaciones', incapacity: 'Ausencia' };

    // Técnicos y coordinadores entran con Microsoft: sin contraseña propia
    function staffUsesMicrosoft(user) {
        return microsoftLogin && Boolean(user) && ['technician', 'coordinator'].includes(user.role);
    }

    function hasAvailability(user) {
        return Boolean(user && AVAILABILITY_ROLES.includes(user.role));
    }

    // "Disponible ahora", "Ausente ahora: fuera de su horario", "De vacaciones · regresa el 03/11/2026"
    function availabilityNowText(status, returnDate) {
        const back = returnDate ? ` · regresa el ${formatPlainDate(returnDate)}` : '';
        if (status === 'vacation') return `De vacaciones${back}`;
        if (status === 'incapacity') return `Con ausencia${back}`;
        if (status === 'away') return 'Ausente ahora: fuera de su horario';
        return 'Disponible ahora';
    }

    function periodText(period) {
        return `${PERIOD_KINDS[period.kind] || period.kind} · del ${formatPlainDate(period.start_date)} al ${formatPlainDate(period.end_date)}`;
    }

    // Lista de periodos vigentes y futuros, cada uno con "Quitar" (data-period-id)
    function renderPeriodList(list, periods) {
        list.replaceChildren();
        if (!periods.length) {
            list.appendChild(makeEl('li', 'periods-empty', 'No hay vacaciones ni ausencias registradas.'));
            return;
        }
        const today = localToday();
        periods.forEach(period => {
            const li = makeEl('li', 'period-item');
            li.appendChild(makeEl('span', 'period-text', periodText(period)));
            if (period.start_date <= today) li.appendChild(makeEl('span', 'ui-tag is-warn', 'En curso'));
            const remove = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm', 'Quitar');
            remove.type = 'button';
            remove.dataset.periodId = String(period.id);
            remove.setAttribute('aria-label', `Quitar ${periodText(period)}`);
            li.appendChild(remove);
            list.appendChild(li);
        });
    }

    function preparePeriodDates(startInput, endInput) {
        startInput.min = localToday();
        endInput.min = localToday();
    }

    function periodPayload(kindSelect, startInput, endInput) {
        return { kind: kindSelect.value, start_date: startInput.value, end_date: endInput.value };
    }

    // Asignar: si el técnico no está disponible, el servidor responde 409 y se pide confirmación
    async function assignTicket(userId) {
        const send = (confirmUnavailable) => apiFetch(`/tickets/${selectedTicketId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(confirmUnavailable
                ? { assigned_to: userId, confirm_unavailable: true }
                : { assigned_to: userId }),
            silent: !confirmUnavailable
        });
        try {
            await send(false);
            return true;
        } catch (err) {
            if (err.status !== 409) {
                if (err.status !== undefined) showError(err.message);
                return false;
            }
            if (!confirm(err.message)) return false;
        }
        try {
            await send(true);
        } catch (err) {
            return false; // apiFetch ya mostró el error
        }
        return true;
    }

    // ----- Horarios (los definen coordinador y admin; el técnico solo los ve) -----
    const WEEKDAY_NAMES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
    const DEFAULT_START = '08:00';
    const DEFAULT_END = '17:00';

    function autoAssignedContent(name) {
        const wrap = document.createElement('span');
        wrap.className = 'assigned-auto';
        wrap.appendChild(document.createTextNode(name));
        wrap.appendChild(makeTag('Asignado automáticamente', 'tag-auto'));
        return wrap;
    }

    function renderOwnSchedule(days, summary) {
        document.getElementById('accountScheduleSummary').textContent = summary || 'Sin horario';
        const byDay = Object.fromEntries(days.map(d => [d.weekday, d]));
        const list = document.getElementById('accountScheduleDays');
        list.replaceChildren(...WEEKDAY_NAMES.map((dayName, weekday) => {
            const li = document.createElement('li');
            const label = document.createElement('span');
            label.className = 'schedule-day';
            label.textContent = dayName;
            const value = document.createElement('span');
            const day = byDay[weekday];
            value.textContent = day ? `${day.start} – ${day.end}` : 'No trabaja';
            li.append(label, value);
            return li;
        }));
    }

    const scheduleDialog = document.getElementById('scheduleDialog');
    const scheduleForm = document.getElementById('scheduleForm');
    const scheduleRows = document.getElementById('scheduleRows');
    const scheduleError = document.getElementById('scheduleError');
    let scheduleUserId = null;
    let scheduleDone = null;

    function syncScheduleRow(row) {
        const works = row.querySelector('.schedule-works').checked;
        row.querySelectorAll('input[type="time"]').forEach(input => {
            input.disabled = !works;
            input.required = works;
        });
    }

    // Las 7 filas se crean una sola vez; al abrir solo se llenan
    WEEKDAY_NAMES.forEach((dayName, weekday) => {
        const row = document.createElement('fieldset');
        row.className = 'schedule-row';
        row.dataset.weekday = String(weekday);
        const legend = document.createElement('legend');
        legend.className = 'schedule-day';
        legend.textContent = dayName;
        const worksLabel = document.createElement('label');
        worksLabel.className = 'schedule-works-label';
        const works = document.createElement('input');
        works.type = 'checkbox';
        works.className = 'schedule-works';
        worksLabel.append(works, document.createTextNode(' Trabaja'));
        const times = document.createElement('div');
        times.className = 'schedule-times';
        [['start', 'Inicio'], ['end', 'Fin']].forEach(([key, text]) => {
            const label = document.createElement('label');
            const input = document.createElement('input');
            input.type = 'time';
            input.step = 60;
            input.className = `schedule-${key}`;
            input.setAttribute('aria-label', `${text} del ${dayName.toLowerCase()}`);
            label.append(document.createTextNode(`${text} `), input);
            times.appendChild(label);
        });
        row.append(legend, worksLabel, times);
        scheduleRows.appendChild(row);
    });

    scheduleRows.addEventListener('change', (e) => {
        if (!e.target.classList.contains('schedule-works')) return;
        const row = e.target.closest('.schedule-row');
        // Al marcar un día vacío se propone el horario de oficina
        if (e.target.checked) {
            const start = row.querySelector('.schedule-start');
            const end = row.querySelector('.schedule-end');
            if (!start.value) start.value = DEFAULT_START;
            if (!end.value) end.value = DEFAULT_END;
        }
        syncScheduleRow(row);
    });

    function openScheduleDialog(userId, name, days) {
        return new Promise(resolve => {
            scheduleUserId = userId;
            scheduleDone = resolve;
            document.getElementById('scheduleTarget').textContent = name;
            const byDay = Object.fromEntries(days.map(d => [d.weekday, d]));
            scheduleRows.querySelectorAll('.schedule-row').forEach(row => {
                const day = byDay[Number(row.dataset.weekday)];
                row.querySelector('.schedule-works').checked = Boolean(day);
                row.querySelector('.schedule-start').value = day ? day.start : '';
                row.querySelector('.schedule-end').value = day ? day.end : '';
                syncScheduleRow(row);
            });
            scheduleError.hidden = true;
            scheduleDialog.showModal();
            scheduleRows.querySelector('.schedule-works').focus();
        });
    }

    function closeScheduleDialog(done) {
        if (scheduleDialog.open) scheduleDialog.close();
        const resolve = scheduleDone;
        scheduleDone = null;
        if (resolve) resolve(done);
    }

    function showScheduleError(message) {
        scheduleError.textContent = message;
        scheduleError.hidden = false;
    }

    document.getElementById('scheduleCancel').addEventListener('click', () => closeScheduleDialog(false));
    scheduleDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeScheduleDialog(false);
    });
    scheduleForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        scheduleError.hidden = true;
        const days = [];
        for (const row of scheduleRows.querySelectorAll('.schedule-row')) {
            if (!row.querySelector('.schedule-works').checked) continue;
            const start = row.querySelector('.schedule-start').value;
            const end = row.querySelector('.schedule-end').value;
            const dayName = WEEKDAY_NAMES[Number(row.dataset.weekday)];
            if (!start || !end) {
                showScheduleError(`Escriba la hora de inicio y de fin del ${dayName.toLowerCase()}`);
                return;
            }
            // Mismas reglas que app/schedules.py (el servidor también valida)
            if (start >= end) {
                showScheduleError(`${dayName}: la hora de inicio debe ser anterior a la de fin`);
                return;
            }
            days.push({ weekday: Number(row.dataset.weekday), start, end });
        }
        try {
            await apiFetch(`/accounts/${scheduleUserId}/schedule`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ days }),
                silent: true
            });
        } catch (err) {
            if (err.status === undefined) {
                closeScheduleDialog(false);
                return;
            }
            showScheduleError(err.message);
            return;
        }
        closeScheduleDialog(true);
        showSuccess('Horario guardado');
    });

    // Diálogo de vacaciones e incapacidades de otra persona (coordinador sobre técnicos; admin también coordinadores)
    const availabilityDialog = document.getElementById('availabilityDialog');
    const availabilityForm = document.getElementById('availabilityForm');
    const availabilityKind = document.getElementById('availabilityKind');
    const availabilityStart = document.getElementById('availabilityStart');
    const availabilityEnd = document.getElementById('availabilityEnd');
    const availabilityPeriods = document.getElementById('availabilityPeriods');
    const availabilityError = document.getElementById('availabilityError');
    let availabilityUserId = null;
    let availabilityDone = null;
    let availabilityChanged = false;

    async function loadAvailabilityDialog() {
        let account;
        try {
            account = await apiFetch(`/accounts/${availabilityUserId}`, { silent: true });
        } catch (err) {
            availabilityError.textContent = err.message;
            availabilityError.hidden = false;
            return;
        }
        document.getElementById('availabilityNow').textContent = availabilityNowText(account.availability, account.return_date);
        renderPeriodList(availabilityPeriods, account.periods || []);
    }

    function openAvailabilityDialog(userId, name) {
        return new Promise(resolve => {
            availabilityUserId = userId;
            availabilityDone = resolve;
            availabilityChanged = false;
            document.getElementById('availabilityTarget').textContent = name;
            document.getElementById('availabilityNow').textContent = '';
            availabilityPeriods.replaceChildren();
            availabilityForm.reset();
            preparePeriodDates(availabilityStart, availabilityEnd);
            availabilityError.hidden = true;
            availabilityDialog.showModal();
            availabilityKind.focus();
            loadAvailabilityDialog();
        });
    }

    function closeAvailabilityDialog() {
        if (availabilityDialog.open) availabilityDialog.close();
        const resolve = availabilityDone;
        availabilityDone = null;
        if (resolve) resolve(availabilityChanged);
    }

    document.getElementById('availabilityCancel').addEventListener('click', closeAvailabilityDialog);
    availabilityDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeAvailabilityDialog();
    });
    availabilityForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        availabilityError.hidden = true;
        try {
            await apiFetch(`/accounts/${availabilityUserId}/availability-periods`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(periodPayload(availabilityKind, availabilityStart, availabilityEnd)),
                silent: true
            });
        } catch (err) {
            if (err.status === undefined) {
                closeAvailabilityDialog();
                return;
            }
            availabilityError.textContent = err.message;
            availabilityError.hidden = false;
            return;
        }
        availabilityChanged = true;
        availabilityForm.reset();
        showSuccess('Periodo registrado');
        loadUsers();
        loadAvailabilityDialog();
    });
    availabilityPeriods.addEventListener('click', async (e) => {
        const button = e.target.closest('button[data-period-id]');
        if (!button || !confirm('¿Quitar este periodo? Su disponibilidad volverá a depender de su horario.')) return;
        button.disabled = true;
        availabilityError.hidden = true;
        try {
            await apiFetch(`/accounts/${availabilityUserId}/availability-periods/${button.dataset.periodId}`,
                { method: 'DELETE', silent: true });
        } catch (err) {
            button.disabled = false;
            availabilityError.textContent = err.message;
            availabilityError.hidden = false;
            return;
        }
        availabilityChanged = true;
        showSuccess('Periodo quitado');
        loadUsers();
        loadAvailabilityDialog();
    });

    // Diálogo para reportar una cuenta de solicitante
    const reportDialog = document.getElementById('reportDialog');
    const reportForm = document.getElementById('reportForm');
    const reportReason = document.getElementById('reportReason');
    const reportComment = document.getElementById('reportComment');
    const reportError = document.getElementById('reportError');
    const reportExisting = document.getElementById('reportExisting');
    let reportTargetId = null;
    let reportTargetName = '';
    let reportDone = null;

    function syncReportComment() {
        const other = reportReason.value === 'other';
        reportComment.required = other;
        document.getElementById('reportCommentLabel').textContent = other ? 'Describa el motivo' : 'Comentario (opcional)';
    }

    function openReportDialog(userId, name) {
        return new Promise(resolve => {
            reportTargetId = parseInt(userId, 10);
            reportDone = resolve;
            reportForm.reset();
            syncReportComment();
            document.getElementById('reportDialogTarget').textContent = `Cuenta: ${name}`;
            reportError.hidden = true;
            reportExisting.hidden = true;
            reportTargetName = name;
            reportDialog.showModal();
            reportReason.focus();
            showExistingReports(reportTargetId);
        });
    }

    function openReportsText(count) {
        return count === 1 ? '1 reporte abierto' : `${count} reportes abiertos`;
    }

    // Solo coordinador y admin: el técnico reporta sin saber de otros reportes (el servidor tampoco se lo envía)
    async function showExistingReports(targetId) {
        if (!REPORT_REVIEW_ROLES.includes(currentUser.role)) return;
        let data;
        try {
            data = await apiFetch(`/account-reports/open-count?target_user_id=${targetId}`, { silent: true });
        } catch (err) {
            return; // Sin el aviso se puede reportar igual
        }
        // El diálogo pudo cerrarse o abrirse para otra cuenta mientras llegaba la respuesta
        if (!reportDialog.open || reportTargetId !== targetId || data.open === 0) return;
        document.getElementById('reportExistingText').textContent = `Esta cuenta ya tiene ${openReportsText(data.open)}.`;
        reportExisting.hidden = false;
    }

    function closeReportDialog(done) {
        if (reportDialog.open) reportDialog.close();
        const resolve = reportDone;
        reportDone = null;
        if (resolve) resolve(done);
    }

    reportReason.addEventListener('change', syncReportComment);
    document.getElementById('reportExistingLink').addEventListener('click', () => {
        const target = { id: reportTargetId, name: reportTargetName };
        closeReportDialog(false);
        openReportsForAccount(target.id, target.name);
    });
    document.getElementById('reportCancel').addEventListener('click', () => closeReportDialog(false));
    reportDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeReportDialog(false);
    });
    reportForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        reportError.hidden = true;
        const comment = reportComment.value.trim();
        if (reportReason.value === 'other' && !comment) {
            reportError.textContent = 'Describa el motivo del reporte';
            reportError.hidden = false;
            return;
        }
        try {
            await apiFetch('/account-reports', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: reportTargetId, reason: reportReason.value, comment: comment || null }),
                silent: true
            });
        } catch (err) {
            if (err.status === undefined) {
                closeReportDialog(false);
                return;
            }
            reportError.textContent = err.message;
            reportError.hidden = false;
            return;
        }
        closeReportDialog(true);
        showSuccess('Reporte enviado');
        refreshReportsBadge();
    });

    // Vista Reportes (coordinador y admin)
    const reportsList = document.getElementById('reportsList');
    const reportsEmpty = document.getElementById('reportsEmpty');
    const reportsStatus = document.getElementById('reportsStatus');
    const reportsBadge = document.getElementById('reportsBadge');
    const reportsAccountFilter = document.getElementById('reportsAccountFilter');
    // Filtro por cuenta: { id, name } o null
    let reportsAccount = null;

    function setReportsAccount(account) {
        reportsAccount = account;
        reportsAccountFilter.hidden = !account;
        document.getElementById('reportsAccountName').textContent = account ? `Cuenta: ${account.name}` : '';
    }

    function openReportsForAccount(id, name) {
        setReportsAccount({ id: parseInt(id, 10), name });
        reportsStatus.value = 'open';
        showView('reports');
    }

    async function refreshReportsBadge() {
        if (!currentUser || !REPORT_REVIEW_ROLES.includes(currentUser.role)) return;
        try {
            const data = await apiFetch('/account-reports/open-count');
            reportsBadge.textContent = String(data.open);
            reportsBadge.hidden = data.open === 0;
        } catch (err) {
            // apiFetch ya mostró el error
        }
    }

    // Reportes automáticos: intentos sumados y si probó varias cédulas ajenas distintas
    function reportAttemptsText(item) {
        if (!item.attempts || item.attempts < 2) return '';
        return `${item.attempts} intentos · último ${relativeTime(item.last_attempt_at)}`;
    }

    function reportStrongText(item) {
        return item.strong_signal ? `Probó ${item.distinct_id_numbers} cédulas distintas` : '';
    }

    function renderReportItem(item) {
        const li = makeEl('li', 'ui-card report-item');
        const head = makeEl('div', 'report-item-head');
        const titles = makeEl('div', 'report-item-titles');
        titles.appendChild(makeEl('strong', 'report-item-name', `${item.target_name} · ${item.reason_label}`));
        const meta = makeEl('p', 'report-item-meta', `${item.target_email} · reportado por ${item.reporter} · `);
        meta.appendChild(timeTag(item.created_at));
        titles.appendChild(meta);
        head.appendChild(titles);
        const tone = { open: 'is-danger', confirmed: 'is-warn', dismissed: '' }[item.status] || '';
        head.appendChild(makeEl('span', `ui-tag ${tone}`, REPORT_STATUS[item.status] || item.status));
        li.appendChild(head);
        if (needsMyReview(item)) li.appendChild(makeTag('Requiere su revisión', 'tag-review'));
        if (reportStrongText(item)) li.appendChild(makeEl('span', 'ui-tag is-danger', reportStrongText(item)));
        if (reportAttemptsText(item)) li.appendChild(makeEl('p', 'report-item-meta', reportAttemptsText(item)));
        if (item.comment) li.appendChild(makeEl('p', 'report-item-comment', item.comment));
        if (item.status !== 'open') {
            let state = REPORT_STATUS[item.status] || item.status;
            if (item.resolver) state += ` por ${item.resolver} (${formatDateTime(item.resolved_at)})`;
            li.appendChild(requestLine('Decisión', state));
            if (item.resolution_note) li.appendChild(requestLine('Nota', item.resolution_note));
        }
        const actions = makeEl('div', 'report-item-actions');
        if (item.other_open > 0) {
            const others = makeEl('span', 'report-others', item.other_open === 1
                ? 'Esta cuenta tiene otro reporte abierto'
                : `Esta cuenta tiene otros ${item.other_open} reportes abiertos`);
            actions.appendChild(others);
            if (!reportsAccount) {
                const filterBtn = makeEl('button', 'ticket-bar-link', 'Ver solo esta cuenta');
                filterBtn.type = 'button';
                filterBtn.dataset.filterTarget = item.target_user_id;
                filterBtn.dataset.filterName = item.target_name;
                actions.appendChild(filterBtn);
            }
        }
        if (item.status === 'open') {
            // Quien reportó puede retirarlo, pero la confirmación la da otra persona
            const own = item.reporter_id === currentUser.id;
            const review = makeEl('button', `ui-btn ui-btn-sm ${own ? 'ui-btn-secondary' : 'ui-btn-primary'} report-review-btn`,
                own ? 'Retirar reporte' : 'Revisar');
            review.type = 'button';
            review.dataset.reviewId = String(item.id);
            actions.appendChild(review);
            if (own) actions.appendChild(makeEl('span', 'report-waiting', 'Esperando revisión de otro coordinador o un administrador.'));
        }
        if (actions.children.length) li.appendChild(actions);
        return li;
    }

    // ----- Revisión de un reporte (P-Reporte) -----
    let reviewReport = null;
    let reviewAccount = null;
    let reviewDecision = 'confirm';
    let reportItems = [];
    const reportReview = document.getElementById('reportReview');
    const reportsListPane = document.getElementById('reportsListPane');

    function showReportsList() {
        reviewReport = null;
        reviewAccount = null;
        reportReview.hidden = true;
        reportsListPane.hidden = false;
        setDocumentTitle('Reportes de cuentas');
    }

    function setReviewDecision(decision) {
        reviewDecision = decision;
        document.querySelectorAll('#reportDecisionChoices .report-choice').forEach(choice => {
            const checked = choice.dataset.decision === decision;
            choice.setAttribute('aria-checked', checked ? 'true' : 'false');
            choice.tabIndex = checked ? 0 : -1;
        });
        const confirming = decision === 'confirm';
        const submit = document.getElementById('reportDecisionSubmit');
        submit.textContent = confirming ? 'Confirmar reporte' : (reviewReport && reviewReport.reporter_id === currentUser.id ? 'Retirar reporte' : 'Descartar reporte');
        submit.className = `ui-btn ${confirming ? 'ui-btn-danger' : 'ui-btn-primary'}`;
        // Desactivar solo al confirmar y si quien decide puede desactivar esa cuenta (lo dice el servidor)
        const canDeactivate = Boolean(reviewAccount && reviewAccount.is_active && reviewAccount.actions.includes('deactivate'));
        document.getElementById('reportDeactivateRow').hidden = !(confirming && canDeactivate);
        if (!confirming) document.getElementById('reportDeactivate').checked = false;
    }

    function reviewFact(list, label, value, tone) {
        const box = makeEl('div');
        box.append(makeEl('dt', '', label), makeEl('dd', tone || '', value));
        list.appendChild(box);
    }

    async function openReportReview(reportId) {
        const item = reportItems.find(r => r.id === reportId);
        if (!item) return;
        reviewReport = item;
        reviewAccount = null;
        reportsListPane.hidden = true;
        reportReview.hidden = false;
        window.scrollTo(0, 0);
        const own = item.reporter_id === currentUser.id;
        document.getElementById('reportReviewCrumb').textContent = item.target_name;
        document.getElementById('reportReviewKicker').textContent = `REPORTE ABIERTO · ${relativeTime(item.created_at).toUpperCase()}`;
        document.getElementById('reportReviewKicker').title = exactDateTime(item.created_at);
        document.getElementById('reportReviewTitle').textContent = `${item.target_name} · ${item.reason_label}`;
        const by = document.getElementById('reportReviewBy');
        by.replaceChildren(document.createTextNode('Reportado por '), makeEl('b', '', item.reporter),
            document.createTextNode(` (${ROLE_MAP[item.reporter_role] || item.reporter_role})`));
        if (reportAttemptsText(item)) by.appendChild(document.createTextNode(` · ${reportAttemptsText(item)}`));
        if (reportStrongText(item)) by.append(' ', makeEl('span', 'ui-tag is-danger', reportStrongText(item)));
        document.getElementById('reportReviewComment').textContent = item.comment || 'Quien reportó no dejó comentario.';
        setDocumentTitle(`Reporte de ${item.target_name}`);
        // Al reportante solo le queda retirar su reporte
        document.querySelector('#reportDecisionChoices [data-decision="confirm"]').hidden = own;
        document.getElementById('reportDecisionOwn').hidden = !own;
        document.getElementById('reportDecisionNote').value = '';
        document.getElementById('reportDeactivate').checked = false;
        setReviewDecision(own ? 'dismiss' : 'confirm');

        const facts = document.getElementById('reportReviewFacts');
        const history = document.getElementById('reportReviewHistory');
        facts.replaceChildren();
        showSkeleton(history, { rows: 2 });
        let account;
        let reports;
        try {
            [account, reports] = await Promise.all([
                apiFetch(`/accounts/${item.target_user_id}`, { silent: true }),
                apiFetch(`/account-reports?target_user_id=${item.target_user_id}`, { silent: true }),
            ]);
        } catch (err) {
            if (reviewReport !== item) return;
            history.removeAttribute('aria-busy');
            history.replaceChildren(errorState(err, () => openReportReview(reportId)));
            return;
        }
        if (reviewReport !== item) return;
        reviewAccount = account;
        setReviewDecision(reviewDecision);
        const identityTone = { match: 'is-ok', mismatch: 'is-danger', not_found: 'is-danger' }[account.identity] || '';
        reviewFact(facts, 'Identidad', IDENTITY_LABELS[account.identity] || '—', `report-fact ${identityTone}`);
        reviewFact(facts, 'Estado de la cuenta', ACCOUNT_STATE_LABELS[account.state] || account.state);
        const others = reports.filter(r => r.id !== item.id && r.status === 'open').length;
        reviewFact(facts, 'Otros reportes', others ? `${others} ${others === 1 ? 'abierto' : 'abiertos'}` : 'Ninguno abierto',
            others ? 'report-fact is-danger' : '');

        history.removeAttribute('aria-busy');
        history.replaceChildren();
        account.recent_tickets.forEach(ticket => {
            const li = makeEl('li');
            const row = makeEl('button', 'home-row');
            row.type = 'button';
            row.dataset.ticketId = String(ticket.id);
            row.append(makeEl('span', 'home-row-id ui-num', `#${ticket.id}`), makeEl('span', 'home-row-title home-row-text', ticket.title),
                makeEl('span', `state-tag state-${ticket.status}`, STATE_MAP[ticket.status] || ticket.status));
            li.appendChild(row);
            history.appendChild(li);
        });
        reports.filter(r => r.id !== item.id).forEach(other => {
            const li = makeEl('li', 'home-row report-history-row');
            li.append(makeEl('span', 'home-row-id ui-num is-report', 'RPT'),
                makeEl('span', 'home-row-title home-row-text', `Reporte de ${other.reporter} · ${other.reason_label}`),
                makeEl('span', 'home-row-sub', `${REPORT_STATUS[other.status] || other.status} · ${formatPlainDate(other.created_at.slice(0, 10))}`));
            history.appendChild(li);
        });
        if (!history.children.length) {
            const li = makeEl('li');
            li.appendChild(emptyState({ title: 'Sin historial', text: 'La cuenta no tiene tickets ni otros reportes.' }));
            history.appendChild(li);
        }
    }

    document.getElementById('reportReviewBack').addEventListener('click', showReportsList);
    document.getElementById('reportReviewHistory').addEventListener('click', (e) => {
        const row = e.target.closest('[data-ticket-id]');
        if (row) {
            setSideNavActive(null);
            openTicketById(Number(row.dataset.ticketId));
        }
    });
    document.getElementById('reportDecisionChoices').addEventListener('click', (e) => {
        const choice = e.target.closest('.report-choice');
        if (choice) setReviewDecision(choice.dataset.decision);
    });
    document.getElementById('reportDecisionChoices').addEventListener('keydown', (e) => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
        const choices = [...document.querySelectorAll('#reportDecisionChoices .report-choice')].filter(c => !c.hidden);
        if (choices.length < 2) return;
        e.preventDefault();
        const next = choices.find(c => c.dataset.decision !== reviewDecision);
        setReviewDecision(next.dataset.decision);
        next.focus();
    });

    // Confirmar o descartar. Con "Desactivar también la cuenta": primero se confirma y luego se desactiva;
    // si la desactivación falla, se avisa que solo se confirmó el reporte.
    document.getElementById('reportDecisionForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!reviewReport) return;
        const item = reviewReport;
        const note = document.getElementById('reportDecisionNote').value.trim();
        if (!note) {
            showError('Escriba una nota');
            return;
        }
        const confirming = reviewDecision === 'confirm';
        const deactivate = confirming && document.getElementById('reportDeactivate').checked;
        const submit = document.getElementById('reportDecisionSubmit');
        submit.disabled = true;
        try {
            await apiFetch(`/account-reports/${item.id}/${reviewDecision}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note })
            });
        } catch (err) {
            submit.disabled = false;
            return; // apiFetch ya mostró el error; nada cambió
        }
        let deactivateError = null;
        if (deactivate) {
            try {
                await apiFetch(`/accounts/${item.target_user_id}/deactivate`, { method: 'POST', silent: true });
            } catch (err) {
                deactivateError = err.message || 'No se pudo completar la acción';
            }
        }
        submit.disabled = false;
        showReportsList();
        await loadReports();
        if (deactivateError) {
            showError(`El reporte se confirmó, pero la cuenta no se pudo desactivar: ${deactivateError}`);
        } else if (deactivate) {
            showSuccess('Reporte confirmado y cuenta desactivada');
        } else {
            showSuccess(confirming ? 'Reporte confirmado' : 'Reporte descartado');
        }
    });

    // Reporte abierto de un coordinador: solo lo confirma otra persona, así que va primero para ella
    function needsMyReview(item) {
        return item.status === 'open' && item.reporter_role === 'coordinator' && item.reporter_id !== currentUser.id;
    }

    async function loadReports() {
        const params = new URLSearchParams();
        if (reportsStatus.value) params.set('status', reportsStatus.value);
        if (reportsAccount) params.set('target_user_id', String(reportsAccount.id));
        const query = params.toString();
        let items;
        reportsEmpty.hidden = true;
        if (!reportItems.length) showSkeleton(reportsList, { rows: 3 });
        try {
            items = await apiFetch(`/account-reports${query ? `?${query}` : ''}`, { silent: true });
        } catch (err) {
            reportsList.removeAttribute('aria-busy');
            reportsList.replaceChildren();
            reportsEmpty.replaceChildren(errorState(err, loadReports));
            reportsEmpty.hidden = false;
            return;
        }
        // sort es estable: dentro de cada grupo se mantiene el orden del servidor (más recientes primero)
        items.sort((a, b) => Number(needsMyReview(b)) - Number(needsMyReview(a)));
        reportItems = items;
        reportsList.removeAttribute('aria-busy');
        reportsList.replaceChildren(...items.map(renderReportItem));
        reportsEmpty.replaceChildren(emptyState(reportsAccount
            ? { title: 'Esta cuenta no tiene reportes con ese estado', text: 'Elija otro estado o quite el filtro de cuenta.' }
            : { title: 'No hay reportes con ese estado', text: 'Cuando el personal reporte una cuenta, aparecerá aquí.' }));
        reportsEmpty.hidden = items.length > 0;
        refreshReportsBadge();
    }

    reportsStatus.addEventListener('change', loadReports);
    document.getElementById('reportsAccountClear').addEventListener('click', () => {
        setReportsAccount(null);
        loadReports();
    });
    reportsList.addEventListener('click', async (e) => {
        const filterBtn = e.target.closest('button[data-filter-target]');
        if (filterBtn) {
            setReportsAccount({ id: parseInt(filterBtn.dataset.filterTarget, 10), name: filterBtn.dataset.filterName });
            loadReports();
            return;
        }
        const review = e.target.closest('button[data-review-id]');
        if (review) {
            openReportReview(Number(review.dataset.reviewId));
            return;
        }
    });

    // ----- Solicitudes con doble aprobación -----
    const REQUEST_KINDS = { role: 'Cambio de rol', name: 'Corrección de nombre', id_number: 'Corrección de cédula' };
    const REQUEST_STATUS = { pending: 'Pendiente', approved: 'Aprobada', rejected: 'Rechazada' };

    function requestValue(item, value) {
        if (!value) return '—';
        return item.kind === 'role' ? (ROLE_MAP[value] || value) : value;
    }

    function requestLine(label, value) {
        const p = document.createElement('p');
        p.className = 'change-line';
        const span = document.createElement('span');
        span.className = 'change-label';
        span.textContent = `${label}: `;
        p.append(span, document.createTextNode(value));
        return p;
    }

    function renderRequestItem(item, withActions) {
        const li = document.createElement('li');
        li.className = 'change-item';
        const head = document.createElement('div');
        head.className = 'change-head';
        const title = document.createElement('strong');
        title.textContent = `${REQUEST_KINDS[item.kind] || item.kind} · ${item.target_name}`;
        const time = document.createElement('span');
        time.className = 'change-time';
        time.textContent = formatDateTime(item.created_at);
        head.append(title, time);
        li.appendChild(head);
        li.appendChild(requestLine('Cambio', `${requestValue(item, item.old_value)} → ${requestValue(item, item.new_value)}`));
        li.appendChild(requestLine('Pedido por', item.created_by_name));
        li.appendChild(requestLine('Motivo', item.reason));
        let state = REQUEST_STATUS[item.status] || item.status;
        if (item.resolved_by_name) state += ` por ${item.resolved_by_name} (${formatDateTime(item.resolved_at)})`;
        li.appendChild(requestLine('Estado', state));
        if (item.rejection_reason) li.appendChild(requestLine('Motivo del rechazo', item.rejection_reason));
        if (withActions) {
            const actions = document.createElement('div');
            actions.className = 'account-actions';
            [['approve', 'Aprobar'], ['reject', 'Rechazar']].forEach(([decision, label]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = decision === 'approve' ? 'btn-action btn-small' : 'btn-secondary btn-small';
                button.dataset.requestId = item.id;
                button.dataset.decision = decision;
                button.textContent = label;
                actions.appendChild(button);
            });
            li.appendChild(actions);
        }
        return li;
    }

    const pendingRequestsList = document.getElementById('pendingRequestsList');
    const pendingRequestsEmpty = document.getElementById('pendingRequestsEmpty');

    async function loadPendingRequests() {
        let items;
        try {
            items = await apiFetch('/account-requests?status=pending');
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        pendingRequestsList.replaceChildren(...items.map(item => renderRequestItem(item, true)));
        pendingRequestsEmpty.hidden = items.length > 0;
    }

    pendingRequestsList.addEventListener('click', async (e) => {
        const button = e.target.closest('button[data-decision]');
        if (!button) return;
        const approve = button.dataset.decision === 'approve';
        let body;
        if (approve) {
            if (!confirm('¿Aprobar la solicitud y aplicar el cambio?')) return;
        } else {
            const reason = prompt('Motivo del rechazo (obligatorio):');
            if (reason === null) return;
            if (!reason.trim()) {
                showError('Escriba el motivo del rechazo');
                return;
            }
            body = JSON.stringify({ reason: reason.trim() });
        }
        button.disabled = true;
        try {
            await apiFetch(`/account-requests/${button.dataset.requestId}/${approve ? 'approve' : 'reject'}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                ...(body ? { body } : {})
            });
            showSuccess(approve ? 'Solicitud aprobada' : 'Solicitud rechazada');
        } catch (err) {
            // apiFetch ya mostró el error; la solicitud sigue pendiente
        }
        loadPendingRequests();
        loadAccountChanges();
    });

    // ----- Cuentas del coordinador: Equipo y Mis solicitudes (los clientes están en Cuentas) -----
    const teamBody = document.getElementById('teamBody');
    const teamEmpty = document.getElementById('teamEmpty');
    const myRequestsList = document.getElementById('myRequestsList');
    const myRequestsEmpty = document.getElementById('myRequestsEmpty');

    function loadAccountsView() {
        // "Mis solicitudes" (coordinador) se abre desde Equipo › Personas
        if (!document.getElementById('accountsMyRequests').hidden) loadMyRequests();
        else loadTeam();
    }

    // Personal (técnicos, coordinadores y administradores), con acciones en "Horario" y en el menú "⋯".
    // El servidor ya manda el correo enmascarado y nunca teléfono ni cédula.
    async function loadTeam() {
        const grid = document.getElementById('teamWeekGrid');
        if (!teamUsers.size) showSkeleton(grid, { rows: 4 });
        let users;
        try {
            users = await apiFetch('/team', { silent: true });
        } catch (err) {
            grid.removeAttribute('aria-busy');
            grid.replaceChildren(errorState(err, loadTeam));
            return;
        }
        closeRowMenu(false);
        teamUsers.clear();
        teamEmpty.hidden = users.length > 0;
        users.forEach(user => teamUsers.set(user.id, user));
        if (!teamUsers.has(teamSelectedId)) teamSelectedId = null;
        renderTeamWeek();
        renderTeamSelected();
    }

    async function loadMyRequests() {
        let items;
        try {
            items = await apiFetch('/account-requests');
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        myRequestsList.replaceChildren(...items.map(item => renderRequestItem(item, false)));
        myRequestsEmpty.hidden = items.length > 0;
    }

    async function createAccountRequest(button) {
        // Fila de una tabla o panel de Cuentas: los dos traen data-user-id
        const row = button.closest('[data-user-id]');
        const name = row.dataset.userName;
        const kind = button.dataset.action.replace('request-', '');
        let newValue;
        if (kind === 'role') {
            newValue = row.querySelector('.team-role-select').value;
            if (!newValue) {
                showError('Elija el rol nuevo');
                return false;
            }
        } else {
            const value = prompt(kind === 'name' ? `Nombre completo correcto de ${name}:` : `Cédula correcta de ${name}:`);
            if (value === null) return false;
            newValue = value.trim();
            const error = kind === 'name'
                ? (newValue.split(/\s+/).length < 2 ? 'Escriba el nombre completo (nombre y apellidos)' : null)
                : validateIdNumber(newValue);
            if (error) {
                showError(error);
                return false;
            }
        }
        const reason = prompt('Motivo de la solicitud (obligatorio):');
        if (reason === null) return false;
        if (!reason.trim()) {
            showError('Escriba el motivo de la solicitud');
            return false;
        }
        button.disabled = true;
        try {
            await apiFetch('/account-requests', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    kind, target_user_id: parseInt(row.dataset.userId, 10), new_value: newValue, reason: reason.trim()
                })
            });
        } catch (err) {
            button.disabled = false;
            return false; // apiFetch ya mostró el error
        }
        showSuccess('Solicitud enviada al administrador');
        loadMyRequests();
        return true;
    }

    async function handleAccountsClick(e, reload) {
        const button = e.target.closest('.account-action');
        if (!button) return;
        const done = button.dataset.action.startsWith('request-')
            ? await createAccountRequest(button)
            : await runAccountAction(button);
        if (done) reload();
    }

    teamBody.addEventListener('click', (e) => handleAccountsClick(e, loadTeam));

    // ----- Administración -----
    const adminStatus = document.getElementById('adminStatus');
    const adminUsersByRole = document.getElementById('adminUsersByRole');
    const adminTicketsByStatus = document.getElementById('adminTicketsByStatus');
    const adminAiAnalysis = document.getElementById('adminAiAnalysis');
    const adminAiAccuracy = document.getElementById('adminAiAccuracy');
    const downloadBackupBtn = document.getElementById('downloadBackup');
    const refreshLogsBtn = document.getElementById('refreshLogs');
    const adminLogs = document.getElementById('adminLogs');
    const summaryStatus = document.getElementById('summaryStatus');
    const sendSummaryNowBtn = document.getElementById('sendSummaryNow');
    const analyzeMissingBtn = document.getElementById('analyzeMissing');
    const alertsStatus = document.getElementById('alertsStatus');

    function addStatusItem(label, ...children) {
        const dt = document.createElement('dt');
        dt.textContent = label;
        const dd = document.createElement('dd');
        children.forEach(child => dd.appendChild(child));
        adminStatus.appendChild(dt);
        adminStatus.appendChild(dd);
    }

    function statusText(ok, text) {
        const span = document.createElement('span');
        const dot = document.createElement('span');
        dot.className = `status-dot ${ok ? 'ok' : 'bad'}`;
        dot.setAttribute('aria-hidden', 'true');
        span.appendChild(dot);
        span.appendChild(document.createTextNode(text));
        return span;
    }

    function subText(text, className = 'status-sub') {
        const span = document.createElement('span');
        span.className = className;
        span.textContent = text;
        return span;
    }

    function renderCountList(list, counts, labels, order) {
        list.replaceChildren();
        const keys = order.filter(k => k in counts).concat(Object.keys(counts).filter(k => !order.includes(k)));
        if (keys.length === 0) {
            const li = document.createElement('li');
            li.textContent = 'Sin datos';
            list.appendChild(li);
            return;
        }
        keys.forEach(key => {
            const li = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = labels[key] || key;
            const count = document.createElement('span');
            count.className = 'count';
            count.textContent = String(counts[key]);
            li.appendChild(name);
            li.appendChild(count);
            list.appendChild(li);
        });
    }

    async function loadAdminHealth() {
        try {
            const health = await apiFetch('/admin/health');
            adminStatus.replaceChildren();

            const dbOk = health.database === 'ok';
            addStatusItem('Base de datos', statusText(dbOk, dbOk ? 'Funcionando' : health.database));

            const emailParts = [statusText(health.email_enabled, health.email_enabled ? 'Encendido' : 'Apagado')];
            emailParts.push(subText(`Última revisión: ${health.email_last_run ? formatDateTime(health.email_last_run) : 'Nunca'}`));
            emailParts.push(subText(`Correos procesados en la última revisión: ${health.email_last_processed}`));
            if (health.email_last_error) {
                emailParts.push(subText(`Último error: ${health.email_last_error}`, 'status-sub status-error'));
            }
            addStatusItem('Correo', ...emailParts);

            addStatusItem('Chatbot', statusText(health.chatbot_enabled, health.chatbot_enabled ? 'Encendido' : 'Apagado'));

            renderCountList(adminUsersByRole, health.users_by_role, ROLE_MAP,
                ['requester', 'technician', 'coordinator', 'admin', 'agent']);
            renderCountList(adminTicketsByStatus, health.tickets_by_status, STATE_MAP,
                ['new', 'in_progress', 'resolved', 'closed', 'cancelled']);

            const ai = health.ai_analysis || {};
            const aiLabels = {
                pending: 'Pendientes',
                done: 'Analizados',
                error: 'Con error',
                needs_review: 'Para revisar',
                corrected: 'Corregidos por una persona'
            };
            const aiOrder = Object.keys(aiLabels);
            const aiCounts = {};
            aiOrder.forEach(key => { aiCounts[key] = ai[key] || 0; });
            renderCountList(adminAiAnalysis, aiCounts, aiLabels, aiOrder);
            adminAiAccuracy.textContent = ai.accuracy === null || ai.accuracy === undefined
                ? 'Acierto: sin datos'
                : `Acierto: ${ai.accuracy} %`;

            const summaryState = health.daily_summary_enabled ? 'Automático encendido' : 'Automático apagado';
            const summaryLast = health.daily_summary_last ? formatDateTime(health.daily_summary_last) : 'Nunca';
            summaryStatus.textContent = `${summaryState}. Último resumen automático: ${summaryLast}.`;

            const alertsState = health.alerts_enabled ? 'Encendidas' : 'Apagadas';
            const alertsLast = health.alerts_last ? formatDateTime(health.alerts_last) : 'Nunca';
            alertsStatus.textContent = `${alertsState}. Última alerta enviada: ${alertsLast}.`;
        } catch (err) {
            // apiFetch ya mostró el error
        }
    }

    async function loadAdminLogs() {
        try {
            const data = await apiFetch('/admin/logs');
            adminLogs.textContent = data.lines.length ? data.lines.join('\n') : 'Todavía no hay registros.';
            adminLogs.scrollTop = adminLogs.scrollHeight;
        } catch (err) {
            // apiFetch ya mostró el error
        }
    }

    function loadAdminPanel() {
        loadAdminHealth();
        loadAdminLogs();
        loadAccountChanges();
        loadPendingRequests();
    }

    // ----- Registro de cambios por persona (solo administrador, solo lectura) -----
    const CHANGE_ACTIONS = {
        password_changed: 'Contraseña cambiada',
        email_changed: 'Correo cambiado',
        phone_changed: 'Teléfono cambiado',
        name_changed: 'Nombre cambiado',
        id_number_changed: 'Cédula cambiada',
        role_changed: 'Rol cambiado',
        account_activated: 'Cuenta activada',
        account_deactivated: 'Cuenta desactivada',
        invitation_sent: 'Invitación enviada',
        invitation_used: 'Invitación usada',
        microsoft_linked: 'Cuenta Microsoft enlazada',
        microsoft_login: 'Ingreso con Microsoft',
        microsoft_login_rejected: 'Ingreso con Microsoft rechazado',
        emergency_access: 'Acceso de emergencia',
        id_number_conflict: 'Cédula de otra cuenta',
        login: 'Ingreso',
        login_failed: 'Intento de ingreso fallido',
        login_locked: 'Bloqueo por intentos',
        password_reset: 'Contraseña restablecida',
        access_reset_sent: 'Código de acceso enviado',
        access_reset_forced: 'Restablecimiento forzado',
        login_unlocked: 'Desbloqueo de intentos',
        tickets_released: 'Tickets liberados',
        request_created: 'Solicitud creada',
        request_approved: 'Solicitud aprobada',
        request_rejected: 'Solicitud rechazada',
        sessions_closed: 'Sesiones cerradas',
        invitation_cancelled: 'Invitación cancelada',
        availability_changed: 'Disponibilidad cambiada',
        availability_period_added: 'Vacaciones o ausencia registrada',
        availability_period_removed: 'Vacaciones o ausencia quitada',
        schedule_changed: 'Horario cambiado',
        report_created: 'Cuenta reportada',
        report_confirmed: 'Reporte confirmado',
        report_dismissed: 'Reporte descartado',
        server_account_deactivated: 'Cuenta desactivada',
        server_account_activated: 'Cuenta activada',
        server_role_changed: 'Rol cambiado',
        overdue_report_downloaded: 'PDF de reporte de vencimiento descargado',
        attachment_uploaded: 'Adjunto subido',
        attachment_downloaded: 'Adjunto descargado',
        email_attachments_received: 'Adjuntos recibidos por correo',
        email_attachments_sent: 'Adjuntos enviados por correo',
        ticket_merged: 'Tickets fusionados',
        ticket_merge_undone: 'Fusión separada',
        ticket_linked: 'Tickets relacionados',
        ticket_link_undone: 'Relación separada',
        incident_marked: 'Posible incidente',
        conversation_viewed: 'Ver conversación'
    };
    const changesList = document.getElementById('changesList');
    const changesEmpty = document.getElementById('changesEmpty');
    const convAccessList = document.getElementById('convAccessList');
    const convAccessEmpty = document.getElementById('convAccessEmpty');
    const convAccessMore = document.getElementById('convAccessMore');
    let convAccessPage = 1;
    let changesSeq = 0;

    function changeLine(labelText, value) {
        const line = document.createElement('span');
        line.className = 'change-line';
        const label = document.createElement('span');
        label.className = 'change-label';
        label.textContent = labelText;
        line.append(label, document.createTextNode(value));
        return line;
    }

    function renderChange(item) {
        const li = document.createElement('li');
        li.className = 'change-item';
        const head = document.createElement('div');
        head.className = 'change-head';
        const action = document.createElement('strong');
        action.textContent = CHANGE_ACTIONS[item.action] || item.action;
        const when = document.createElement('time');
        when.className = 'change-time';
        when.textContent = formatDateTime(item.created_at);
        head.append(action, when);
        li.append(head, changeLine('Cuenta: ', item.account), changeLine('Por: ', item.actor));
        // "Ver conversación": el ticket va en Cuenta y el motivo, aparte
        if (item.action === 'conversation_viewed') {
            li.appendChild(changeLine('Motivo: ', `«${item.new_value || ''}»`));
        } else if (item.old_value || item.new_value) {
            li.appendChild(changeLine('Cambio: ', `${item.old_value || '—'} → ${item.new_value || '—'}`));
        }
        return li;
    }

    // Pantalla principal: accesos a conversaciones (con "Ver más") y los últimos 20 cambios
    async function loadAccountChanges() {
        const seq = ++changesSeq;
        convAccessPage = 1;
        let recent;
        let access;
        try {
            [recent, access] = await Promise.all([
                apiFetch('/admin/account-changes?page=1&exclude_access=true'),
                apiFetch('/admin/account-changes?page=1&action=conversation_viewed'),
            ]);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        if (seq !== changesSeq) return;
        changesList.replaceChildren(...recent.items.map(renderChange));
        changesEmpty.hidden = recent.items.length > 0;
        convAccessList.replaceChildren(...access.items.map(renderChange));
        convAccessEmpty.hidden = access.items.length > 0;
        convAccessMore.hidden = access.page >= access.pages;
    }

    convAccessMore.addEventListener('click', async () => {
        convAccessMore.disabled = true;
        try {
            const data = await apiFetch(`/admin/account-changes?page=${convAccessPage + 1}&action=conversation_viewed`);
            convAccessPage = data.page;
            convAccessList.append(...data.items.map(renderChange));
            convAccessMore.hidden = data.page >= data.pages;
        } catch (err) {
            // apiFetch ya mostró el error
        } finally {
            convAccessMore.disabled = false;
        }
    });

    // Buscador de personas con sugerencias (combobox)
    const peopleHome = document.getElementById('peopleHome');
    const peopleSearch = document.getElementById('peopleSearch');
    const peopleSuggestions = document.getElementById('peopleSuggestions');
    let peopleTimer = null;
    let peopleSeq = 0;
    let peopleActive = -1;

    function closePeopleSuggestions() {
        peopleSuggestions.hidden = true;
        peopleSuggestions.replaceChildren();
        peopleSearch.setAttribute('aria-expanded', 'false');
        peopleSearch.removeAttribute('aria-activedescendant');
        peopleActive = -1;
    }

    function setPeopleActive(index) {
        const options = [...peopleSuggestions.querySelectorAll('[role="option"]')];
        if (!options.length) return;
        peopleActive = (index + options.length) % options.length;
        options.forEach((option, i) => option.setAttribute('aria-selected', i === peopleActive ? 'true' : 'false'));
        peopleSearch.setAttribute('aria-activedescendant', options[peopleActive].id);
        options[peopleActive].scrollIntoView({ block: 'nearest' });
    }

    async function loadPeopleSuggestions() {
        const query = peopleSearch.value.trim();
        if (query.length < 2) {
            closePeopleSuggestions();
            return;
        }
        const seq = ++peopleSeq;
        let hits;
        try {
            hits = await apiFetch(`/admin/people?query=${encodeURIComponent(query)}`);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        if (seq !== peopleSeq) return;
        peopleActive = -1;
        peopleSearch.removeAttribute('aria-activedescendant');
        if (!hits.length) {
            const empty = makeEl('li', 'people-suggestion-empty', 'Nadie coincide con esa búsqueda.');
            empty.setAttribute('role', 'presentation');
            peopleSuggestions.replaceChildren(empty);
        } else {
            peopleSuggestions.replaceChildren(...hits.map((hit, index) => {
                const li = makeEl('li', 'people-suggestion');
                li.id = `peopleOption-${hit.id}`;
                li.setAttribute('role', 'option');
                li.setAttribute('aria-selected', 'false');
                li.dataset.personId = hit.id;
                li.append(
                    makeEl('span', 'people-suggestion-name', hit.name),
                    makeEl('span', 'people-suggestion-meta', `${ACCOUNT_TYPE_LABELS[hit.role] || hit.role} · ${hit.email}`),
                );
                return li;
            }));
        }
        peopleSuggestions.hidden = false;
        peopleSearch.setAttribute('aria-expanded', 'true');
    }

    function openPersonFromSuggestion(option) {
        if (!option) return;
        const id = option.dataset.personId;
        closePeopleSuggestions();
        peopleSearch.value = '';
        goRoute(`admin/registro/persona/${id}`);
    }

    peopleSearch.addEventListener('input', () => {
        clearTimeout(peopleTimer);
        peopleTimer = setTimeout(loadPeopleSuggestions, 250);
    });

    peopleSearch.addEventListener('keydown', (e) => {
        const open = !peopleSuggestions.hidden;
        if (e.key === 'ArrowDown' && open) {
            e.preventDefault();
            setPeopleActive(peopleActive + 1);
        } else if (e.key === 'ArrowUp' && open) {
            e.preventDefault();
            setPeopleActive(peopleActive - 1);
        } else if (e.key === 'Enter' && open) {
            const options = peopleSuggestions.querySelectorAll('[role="option"]');
            const option = options[peopleActive >= 0 ? peopleActive : 0];
            if (option) {
                e.preventDefault();
                openPersonFromSuggestion(option);
            }
        } else if (e.key === 'Escape' && open) {
            e.preventDefault();
            closePeopleSuggestions();
        }
    });

    // mousedown: elegir antes de que el campo pierda el foco y cierre la lista
    peopleSuggestions.addEventListener('mousedown', (e) => {
        const option = e.target.closest('[role="option"]');
        if (!option) return;
        e.preventDefault();
        openPersonFromSuggestion(option);
    });

    peopleSearch.addEventListener('blur', closePeopleSuggestions);

    // ----- Ficha de una persona -----
    const personView = document.getElementById('personView');
    const personGroups = document.getElementById('personGroups');
    const personRange = document.getElementById('personRange');
    const personDates = document.getElementById('personDates');
    const personFrom = document.getElementById('personFrom');
    const personTo = document.getElementById('personTo');
    const personTicket = document.getElementById('personTicket');
    const personTicketField = document.getElementById('personTicketField');
    const personTimeline = document.getElementById('personTimeline');
    const personEmpty = document.getElementById('personEmpty');
    const personMore = document.getElementById('personMore');
    const PERSON_GROUPS = {
        staff: [['', 'Todo'], ['access', 'Acceso'], ['tickets', 'Tickets'], ['drafts', 'Borradores'], ['shifts', 'Turnos'], ['account', 'Cuenta']],
        client: [['', 'Todo'], ['access', 'Acceso'], ['data', 'Datos'], ['reports', 'Reportes'], ['status', 'Estado']],
    };
    const PERSON_STATES = { active: 'Activa', locked: 'Bloqueada', inactive: 'Desactivada' };
    const PERSON_STATS = [
        ['assigned', 'Asignados'], ['resolved', 'Resueltos'], ['reassigned_received', 'Reasignados recibidos'],
        ['reassigned_given', 'Reasignados dados'], ['overdue', 'Vencidos'], ['drafts_confirmed', 'Borradores confirmados'],
        ['drafts_discarded', 'Borradores descartados'], ['logins', 'Ingresos'],
    ];
    let personId = null;
    let personCrumb = '';
    let personIsClient = false;
    let personGroup = '';
    let personCursor = null;
    let personSeq = 0;
    let personTicketTimer = null;

    function localDateString(date) {
        const pad = n => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    }

    function personParams(cursor) {
        const params = new URLSearchParams();
        if (personGroup) params.set('types', personGroup);
        if (personRange.value === 'custom') {
            if (personFrom.value) params.set('from', personFrom.value);
            if (personTo.value) params.set('to', personTo.value);
        } else {
            const days = Number(personRange.value);
            const from = new Date();
            from.setDate(from.getDate() - days);
            params.set('from', localDateString(from));
            params.set('to', localDateString(new Date()));
        }
        const ticket = personTicket.value.trim();
        if (!personIsClient && /^\d+$/.test(ticket)) params.set('ticket', ticket);
        if (cursor) params.set('cursor', cursor);
        return params;
    }

    function renderPersonGroups() {
        const groups = PERSON_GROUPS[personIsClient ? 'client' : 'staff'];
        personGroups.replaceChildren(...groups.map(([value, label]) => {
            const button = makeEl('button', 'aa-quick-btn', label);
            button.type = 'button';
            button.dataset.group = value;
            button.setAttribute('aria-pressed', value === personGroup ? 'true' : 'false');
            return button;
        }));
    }

    function personFact(term, value) {
        const wrap = makeEl('div', 'person-fact');
        wrap.append(makeEl('dt', '', term), makeEl('dd', '', value));
        return wrap;
    }

    function renderPersonHead(person) {
        document.getElementById('personName').textContent = person.name;
        document.getElementById('personMeta').textContent = `${ACCOUNT_TYPE_LABELS[person.role] || person.role} · ${person.email}`;
        const facts = [personFact('Cuenta', PERSON_STATES[person.state] || person.state)];
        if (person.role === 'requester') {
            facts.push(personFact('Teléfono', person.phone || '—'));
            facts.push(personFact('Verificación con Hacienda', IDENTITY_LABELS[person.identity] || IDENTITY_LABELS.unverified));
            if (person.reported) facts.push(personFact('Etiqueta', 'Reportada'));
        } else {
            facts.push(personFact('Turno hoy', person.schedule_today
                ? `${person.schedule_today}${person.on_shift ? ' · en turno' : ''}` : 'Sin turno hoy'));
            facts.push(personFact('Disponibilidad', AVAILABILITY_LABELS[person.availability] || '—'));
            facts.push(personFact('Último acceso', person.last_access ? formatDateTime(person.last_access) : 'Sin registro'));
            facts.push(personFact('Microsoft', person.microsoft ? 'Entra con Microsoft' : 'No enlazada'));
        }
        document.getElementById('personFacts').replaceChildren(...facts);
    }

    function renderPersonSummary(summary) {
        const box = document.getElementById('personSummary');
        box.hidden = !summary;
        if (!summary) return;
        document.getElementById('personStats').replaceChildren(...PERSON_STATS.map(([key, label]) => {
            const stat = makeEl('div', 'ui-stat person-stat');
            stat.append(makeEl('span', 'ui-stat-label', label), makeEl('span', 'ui-stat-value ui-num', String(summary[key])));
            return stat;
        }));
    }

    function renderPersonItem(item) {
        const li = makeEl('li', `person-event is-${item.group}`);
        const when = makeEl('time', 'person-event-time', formatDateTime(item.at));
        when.dateTime = item.at;
        const group = PERSON_GROUPS[personIsClient ? 'client' : 'staff'].find(([value]) => value === item.group);
        li.append(when, makeEl('span', 'ui-tag person-event-group', group ? group[1] : item.group), makeEl('span', 'person-event-text', item.text));
        return li;
    }

    async function loadPersonActivity(append = false) {
        if (personId === null) return;
        const seq = ++personSeq;
        const params = personParams(append ? personCursor : null);
        let data;
        personMore.disabled = true;
        try {
            data = await apiFetch(`/admin/people/${personId}/activity?${params.toString()}`);
        } catch (err) {
            personMore.disabled = false;
            return; // apiFetch ya mostró el error
        }
        if (seq !== personSeq) return;
        personMore.disabled = false;
        if (!append) {
            // Cliente: otros grupos y sin filtro por ticket (su ficha nunca trae tickets)
            const client = data.person.role === 'requester';
            if (client !== personIsClient) {
                personIsClient = client;
                personTicketField.hidden = client;
                renderPersonGroups();
            }
            renderPersonHead(data.person);
            personCrumb = data.person.name;
            renderPageHead();
            renderPersonSummary(data.summary);
            personTimeline.replaceChildren();
        }
        personTimeline.append(...data.items.map(renderPersonItem));
        personEmpty.hidden = personTimeline.children.length > 0;
        personCursor = data.next_cursor;
        personMore.hidden = !personCursor;
    }

    function openPerson(id) {
        personId = id;
        personCrumb = '';
        personGroup = '';
        personCursor = null;
        personTicket.value = '';
        personRange.value = '30';
        personDates.hidden = true;
        peopleHome.hidden = true;
        personView.hidden = false;
        personTimeline.replaceChildren();
        personEmpty.hidden = true;
        personMore.hidden = true;
        document.getElementById('personName').textContent = '';
        document.getElementById('personMeta').textContent = '';
        document.getElementById('personFacts').replaceChildren();
        document.getElementById('personSummary').hidden = true;
        // El tipo de persona llega con la ficha; mientras tanto, los filtros del personal
        personIsClient = false;
        personTicketField.hidden = false;
        renderPersonGroups();
        loadPersonActivity();
    }

    function closePerson() {
        personId = null;
        personCrumb = '';
        personSeq++;
        personView.hidden = true;
        peopleHome.hidden = false;
    }

    personGroups.addEventListener('click', (e) => {
        const button = e.target.closest('.aa-quick-btn');
        if (!button) return;
        personGroup = button.dataset.group;
        renderPersonGroups();
        loadPersonActivity();
    });

    personRange.addEventListener('change', () => {
        personDates.hidden = personRange.value !== 'custom';
        if (personRange.value !== 'custom') loadPersonActivity();
    });

    [personFrom, personTo].forEach(input => input.addEventListener('change', () => {
        if (personFrom.value && personTo.value && personFrom.value > personTo.value) {
            showError('La fecha "Desde" no puede ser posterior a "Hasta".');
            return;
        }
        loadPersonActivity();
    }));

    personTicket.addEventListener('input', () => {
        clearTimeout(personTicketTimer);
        personTicketTimer = setTimeout(() => loadPersonActivity(), 400);
    });

    personMore.addEventListener('click', () => loadPersonActivity(true));

    document.getElementById('personBack').addEventListener('click', () => goRoute('admin/registro'));

    refreshLogsBtn.addEventListener('click', loadAdminLogs);

    analyzeMissingBtn.addEventListener('click', async () => {
        analyzeMissingBtn.disabled = true;
        try {
            const result = await apiFetch('/admin/ai/analyze-missing', { method: 'POST' });
            showSuccess(result.found
                ? `Se encontraron ${result.found} tickets sin nivel. Se analizarán en segundo plano.`
                : 'No hay tickets abiertos sin nivel.');
        } catch (err) {
            // apiFetch ya mostró el error
        } finally {
            analyzeMissingBtn.disabled = false;
        }
    });

    sendSummaryNowBtn.addEventListener('click', async () => {
        sendSummaryNowBtn.disabled = true;
        try {
            const result = await apiFetch('/admin/daily-summary/send-now', { method: 'POST' });
            showSuccess(`Resumen enviado a ${result.sent} de ${result.recipients} destinatarios`);
        } catch (err) {
            // apiFetch ya mostró el error
        } finally {
            sendSummaryNowBtn.disabled = false;
        }
    });

    downloadBackupBtn.addEventListener('click', async () => {
        downloadBackupBtn.disabled = true;
        try {
            const response = await apiFetch('/admin/backup', { responseType: 'blob' });
            const blob = await response.blob();
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="?([^";]+)"?/);
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = match ? match[1] : 'ticketera-respaldo.db';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        } catch (err) {
            // apiFetch ya mostró el error
        } finally {
            downloadBackupBtn.disabled = false;
        }
    });

    // Solicitante (solo coordinator y admin): crear tickets a nombre de otra persona
    const requesterGroup = document.getElementById('requesterGroup');
    const requesterSearch = document.getElementById('requesterSearch');
    const requesterOptions = document.getElementById('requesterOptions');
    const forMeBtn = document.getElementById('forMeBtn');

    function requesterLabel(r) {
        return `${r.name} — ${r.email}`;
    }

    function fillContactFields(person) {
        document.getElementById('contactName').value = person.name || '';
        document.getElementById('contactEmail').value = person.email || '';
        document.getElementById('contactPhone').value = person.phone || '';
    }

    function clearRequesterSelection() {
        const hadRequester = selectedRequesterId !== null;
        selectedRequesterId = null;
        newTicketClient = null;
        document.getElementById('newTicketAside').hidden = true;
        if (hadRequester && currentUser) {
            fillContactFields(currentUser);
        }
    }

    function resetRequesterField() {
        clearTimeout(requesterSearchTimer);
        requesterSearchSeq++;
        requesterResults = [];
        if (requesterSearch) requesterSearch.value = '';
        if (requesterOptions) requesterOptions.replaceChildren();
        clearRequesterSelection();
    }

    // ----- Nuevo ticket: tickets abiertos del cliente elegido, para no duplicar casos -----
    let newTicketClient = null;
    let newTicketOpenSeq = 0;

    async function loadNewTicketOpen(client) {
        newTicketClient = client;
        const seq = ++newTicketOpenSeq;
        const aside = document.getElementById('newTicketAside');
        const list = document.getElementById('newTicketOpenList');
        document.getElementById('newTicketOpenTitle').textContent = `Tickets abiertos de ${client.name}`;
        aside.hidden = false;
        showSkeleton(list, { rows: 2 });
        let data;
        try {
            data = await apiFetch(`/queue?view=open&requester_id=${client.id}`, { silent: true });
        } catch (err) {
            if (seq !== newTicketOpenSeq) return;
            list.removeAttribute('aria-busy');
            list.replaceChildren(errorState(err, () => loadNewTicketOpen(client)));
            return;
        }
        if (seq !== newTicketOpenSeq) return;
        list.removeAttribute('aria-busy');
        list.replaceChildren();
        if (!data.items.length) {
            const li = makeEl('li');
            li.appendChild(emptyState({ title: 'No tiene tickets abiertos', text: 'Puede crear el ticket sin riesgo de duplicarlo.' }));
            list.appendChild(li);
            return;
        }
        data.items.slice(0, 6).forEach(item => {
            const li = makeEl('li');
            const button = makeEl('button', 'home-row new-ticket-open-row');
            button.type = 'button';
            button.dataset.ticketId = String(item.id);
            button.appendChild(makeEl('span', 'home-row-id ui-num', `#${item.id}`));
            button.appendChild(makeEl('span', 'home-row-title', item.title));
            li.appendChild(button);
            list.appendChild(li);
        });
    }

    document.getElementById('newTicketOpenList').addEventListener('click', (e) => {
        const row = e.target.closest('[data-ticket-id]');
        if (row) openTicketById(Number(row.dataset.ticketId));
    });

    document.getElementById('newTicketClientLink').addEventListener('click', () => {
        if (!newTicketClient) return;
        setSideNavActive('accounts');
        openAccounts('clients', newTicketClient.name);
    });

    // Volver o cancelar: a la cola (personal) o a Mis solicitudes (solicitante)
    function leaveNewTicket() {
        ticketForm.reset();
        resetRequesterField();
        if (isStaffShell()) {
            setSideNavActive(queueView);
            setQueueView(queueView, queueTitle.textContent || defaultQueueLabel());
        } else {
            showView('tickets');
        }
    }

    document.getElementById('newTicketBack').addEventListener('click', leaveNewTicket);
    document.getElementById('newTicketCancel').addEventListener('click', leaveNewTicket);

    async function searchRequesters(q) {
        const seq = ++requesterSearchSeq;
        let results;
        try {
            results = await apiFetch(`/requesters?q=${encodeURIComponent(q)}`);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        if (seq !== requesterSearchSeq) return; // llegó una búsqueda más nueva
        requesterResults = results;
        requesterOptions.replaceChildren();
        results.forEach(r => {
            const option = document.createElement('option');
            option.value = requesterLabel(r);
            requesterOptions.appendChild(option);
        });
    }

    if (requesterSearch) {
        requesterSearch.addEventListener('input', () => {
            const text = requesterSearch.value;
            const match = requesterResults.find(r => requesterLabel(r) === text);
            if (match) {
                clearTimeout(requesterSearchTimer);
                selectedRequesterId = match.id;
                fillContactFields(match);
                loadNewTicketOpen(match);
                return;
            }
            // El texto no coincide con ninguna opción: no se manda requester_id
            clearRequesterSelection();
            clearTimeout(requesterSearchTimer);
            const q = text.trim();
            if (q.length < 2) {
                requesterSearchSeq++;
                requesterResults = [];
                requesterOptions.replaceChildren();
                return;
            }
            requesterSearchTimer = setTimeout(() => searchRequesters(q), 300);
        });
    }

    if (forMeBtn) {
        forMeBtn.addEventListener('click', () => {
            resetRequesterField();
            if (currentUser) {
                fillContactFields(currentUser);
            }
        });
    }

    if (openNewTicket) {
        openNewTicket.addEventListener('click', async () => {
            hideError();
            exitTicketPage();
            setSideNavActive(null);
            // Ruta propia: Atrás vuelve a la sección de donde vino
            if (isStaffShell()) pushRoute(NEW_TICKET_SECTION.route);
            showView('newTicket');
            ticketForm.reset();
            resetRequesterField();
            const canChooseRequester = currentUser && OPERATION_ROLES.includes(currentUser.role);
            requesterGroup.hidden = !canChooseRequester;
            const portal = isRequesterPortal();
            document.getElementById('newTicketTitle').textContent = portal ? 'Nueva solicitud' : 'Nuevo ticket';
            document.getElementById('newTicketCrumb').textContent = portal ? 'Nueva solicitud' : 'Nuevo ticket';
            document.getElementById('newTicketCrumbBase').textContent = portal ? 'Mis solicitudes' : 'Tickets';
            document.getElementById('newTicketBack').textContent = portal ? '← Volver a mis solicitudes' : '← Volver a Tickets';
            document.getElementById('newTicketSubmit').textContent = portal ? 'Enviar solicitud' : 'Crear ticket';
            document.getElementById('newTicketIntro').textContent = portal
                ? 'Cuéntenos qué pasa. Le avisaremos por correo cada vez que haya novedades.'
                : 'Para registrar un caso que llegó por teléfono o en persona.';
            setDocumentTitle(portal ? 'Nueva solicitud' : 'Nuevo ticket');
            if (currentUser) {
                document.getElementById('contactName').value = currentUser.name;
                document.getElementById('contactEmail').value = currentUser.email;
                if (currentUser.phone) {
                    document.getElementById('contactPhone').value = currentUser.phone;
                }
            }
        });
    }

    const usersList = document.getElementById('usersList');

    async function loadUsers() {
        userMap = {};
        userRoleMap = {};
        userAvailabilityMap = {};
        if (usersList) {
            usersList.replaceChildren();
        }
        // El solicitante no recibe la lista de cuentas: los nombres vienen en cada ticket
        if (!currentUser || !['technician', 'coordinator', 'admin'].includes(currentUser.role)) return;
        const users = await apiFetch('/users');
        // El técnico recibe solo nombre y rol del personal: la lista con correos es para quien gestiona cuentas
        const showList = currentUser.role === 'coordinator' || currentUser.role === 'admin';

        users.forEach(user => {
            if (usersList && showList) {
                const li = document.createElement('li');
                li.className = 'users-list-item';
                li.textContent = `${user.name} (${user.email}) — ${ROLE_MAP[user.role] || user.role}`;
                usersList.appendChild(li);
            }
            
            userMap[user.id] = user.name;
            userRoleMap[user.id] = user.role || '';
            userAvailabilityMap[user.id] = user.availability || null;
        });
    }

    // Back to list button
    if (backToListBtn) {
        backToListBtn.addEventListener('click', () => {
            document.querySelector('.split').classList.remove('show-detail');
        });
    }

    // Ticket list click delegation
    ticketList.addEventListener('click', async (e) => {
        const item = e.target.closest('.ticket-item');
        if (!item) return;
        ticketList.querySelectorAll('.ticket-item').forEach(el => el.classList.remove('selected'));
        item.classList.add('selected');
        selectedTicketId = parseInt(item.dataset.id);
        activityFilter = 'all';
        document.querySelector('.split').classList.add('show-detail');
        try {
            const ticket = await apiFetch(`/tickets/${selectedTicketId}`);
            showTicketDetail(ticket);
        } catch (err) {
            selectedTicketId = null;
            renderDetailPlaceholder();
            showError('Error al cargar el detalle del ticket');
        }
    });

    // Detail panel click delegation for actions and comments
    const detailPanel = document.getElementById('detailPanel');
    detailPanel.addEventListener('click', async (e) => {
        if (!selectedTicketId || !currentUser) return;

        const filterBtn = e.target.closest('.activity-filter-btn');
        if (filterBtn) {
            activityFilter = filterBtn.dataset.filter;
            detailPanel.querySelectorAll('.activity-filter-btn').forEach(b => {
                const isMatch = b.dataset.filter === activityFilter;
                b.setAttribute('aria-pressed', isMatch ? 'true' : 'false');
                b.style.background = isMatch ? '#FFFFFF' : 'transparent';
                b.style.fontWeight = isMatch ? '600' : '400';
            });
            if (currentTicket) {
                const evContainer = detailPanel.querySelector('.activity-events-container');
                if (evContainer) {
                    renderEventsList(currentTicket, evContainer);
                }
            }
            return;
        }

        const followBtn = e.target.closest('.follow-btn');
        if (followBtn) {
            const ticketId = selectedTicketId;
            const following = followBtn.getAttribute('aria-pressed') === 'true';
            followBtn.disabled = true;
            try {
                await apiFetch(`/tickets/${ticketId}/follow`, { method: following ? 'DELETE' : 'POST' });
                // Si mientras tanto se abrió otro ticket, no tocar su botón
                if (ticketId === selectedTicketId) {
                    const hint = followBtn.parentElement.querySelector('.follow-hint');
                    renderFollowButton(followBtn, hint, !following);
                    if (currentTicket) currentTicket.following = !following;
                }
                showSuccess(following ? 'Ya no das seguimiento a este ticket' : 'Ahora das seguimiento a este ticket');
            } catch (err) {
                showError(err.message || 'No se pudo actualizar el seguimiento');
            } finally {
                followBtn.disabled = false;
            }
            return;
        }

        const actionBtn = e.target.closest('.btn-action');
        if (actionBtn && !actionBtn.classList.contains('btn-add-comment') && !actionBtn.classList.contains('btn-assign-me')) {
            let newStatus;
            const text = actionBtn.textContent;
            if (text === 'Iniciar trabajo') newStatus = 'in_progress';
            else if (text === 'Marcar como resuelto') newStatus = 'resolved';
            else if (text === 'Cerrar') newStatus = 'closed';
            else if (text === 'Reabrir') newStatus = 'in_progress';
            else return;

            try {
                await apiFetch(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });
                await reloadSelectedTicketAndList();
            } catch (err) {
                showError(err.message || 'Error al aplicar la acción');
            }
        }

        const assignMeBtn = e.target.closest('.btn-assign-me');
        if (assignMeBtn) {
            try {
                if (!(await assignTicket(currentUser.id))) return;
                await reloadSelectedTicketAndList();
                showSuccess('Ticket asignado');
            } catch (err) {
                showError(err.message || 'Error al asignar el ticket');
            }
        }

        const assignBtn = e.target.closest('.btn-assign');
        if (assignBtn) {
            const assignSelect = detailPanel.querySelector('.assign-select');
            const selectedUser = assignSelect ? assignSelect.value : '';
            if (!selectedUser) {
                showError('Seleccione un usuario para asignar');
                return;
            }
            try {
                if (!(await assignTicket(parseInt(selectedUser, 10)))) return;
                await reloadSelectedTicketAndList();
                showSuccess('Ticket asignado');
            } catch (err) {
                showError(err.message || 'Error al asignar el ticket');
            }
        }

        const reportBtn = e.target.closest('.btn-report-account');
        if (reportBtn && currentTicket) {
            const done = await openReportDialog(currentTicket.created_by, userMap[currentTicket.created_by] || 'el solicitante');
            if (done) await reloadSelectedTicketAndList();
            return;
        }

        const addCommentBtn = e.target.closest('.btn-add-comment');
        if (addCommentBtn) {
            const textarea = detailPanel.querySelector('.comment-textarea');
            const content = textarea ? textarea.value.trim() : '';
            if (!content) {
                showError('El comentario no puede estar vacío');
                return;
            }
            try {
                await apiFetch(`/tickets/${selectedTicketId}/comments`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: content })
                });
                await reloadSelectedTicketAndList();
                showSuccess('Comentario agregado');
            } catch (err) {
                showError(err.message || 'Error al agregar el comentario');
            }
        }
    });

    detailPanel.addEventListener('change', async (e) => {
        const select = e.target.closest('.ai-level-select');
        if (!select || !selectedTicketId || select.value === '') return;
        select.disabled = true;
        try {
            await apiFetch(`/tickets/${selectedTicketId}/ai-level`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level: parseInt(select.value, 10) })
            });
            await reloadSelectedTicketAndList();
            showSuccess('Nivel actualizado');
        } catch (err) {
            select.disabled = false;
            showError(err.message || 'No se pudo actualizar el nivel');
        }
    });

    // Initialize session / app on load
    async function initApp() {
        loadMicrosoftLogin();
        if (!authToken) {
            showAuthScreen();
            return;
        }
        try {
            currentUser = await apiFetch('/auth/me');
            showApp();
        } catch (err) {
            sessionStorage.removeItem('ticketeraToken');
            authToken = null;
            currentUser = null;
            showAuthScreen();
        }
    }

    // ----- Invitaciones del personal (admin: técnicos y coordinadores; coordinador: técnicos) -----
    const INVITE_STATUS = { available: 'Disponible', expired: 'Vencida', used: 'Activada' };
    const INVITE_ROLES = { technician: 'Técnico', coordinator: 'Coordinador' };

    function inviteField(labelText, input) {
        const group = document.createElement('div');
        group.className = 'form-group';
        const label = document.createElement('label');
        label.htmlFor = input.id;
        label.textContent = labelText;
        group.append(label, input);
        return group;
    }

    function renderInvitesSection() {
        const old = document.getElementById('invitesSection');
        if (old) old.remove();
        // Invitaciones: solo el administrador
        if (!currentUser || currentUser.role !== 'admin') return;
        const modalBody = document.getElementById('invitesHost');
        if (!modalBody) return;

        const section = document.createElement('section');
        section.id = 'invitesSection';
        section.className = 'invites-section';
        section.classList.add('ui-card');
        const title = document.createElement('h2');
        title.className = 'ui-card-title';
        title.textContent = 'Invitar personal';
        const help = document.createElement('p');
        help.className = 'help-text';
        help.textContent = 'Le enviamos un correo con el código y el enlace para activar su cuenta. Vence en 72 horas.';

        const form = document.createElement('form');
        form.className = 'invite-form';
        const email = document.createElement('input');
        email.type = 'email';
        email.id = 'inviteEmail';
        email.required = true;
        email.maxLength = 150;
        const name = document.createElement('input');
        name.type = 'text';
        name.id = 'inviteName';
        name.required = true;
        name.maxLength = 100;
        const role = document.createElement('select');
        role.id = 'inviteRole';
        const roles = currentUser.role === 'admin' ? ['technician', 'coordinator'] : ['technician'];
        roles.forEach(value => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = INVITE_ROLES[value];
            role.appendChild(option);
        });
        const submit = document.createElement('button');
        submit.type = 'submit';
        submit.className = 'ui-btn ui-btn-primary';
        submit.textContent = 'Enviar invitación';
        form.append(inviteField('Correo', email), inviteField('Nombre completo', name), inviteField('Rol', role), submit);

        // Cada sección es nueva: el listener se registra una sola vez por formulario
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            submit.disabled = true;
            try {
                const data = await apiFetch('/staff-invitations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: email.value.trim(), name: name.value.trim(), role: role.value })
                });
                form.reset();
                if (data.email_sent) {
                    showSuccess('Invitación enviada');
                } else {
                    showError('La invitación se guardó, pero no se pudo enviar el correo. Revise el correo y use Reenviar.');
                }
                loadInvitesTable();
            } catch (err) {
                // apiFetch ya mostró el error
            } finally {
                submit.disabled = false;
            }
        });

        const tableTitle = document.createElement('h3');
        tableTitle.className = 'invites-table-title';
        tableTitle.textContent = 'Invitaciones enviadas';
        const table = document.createElement('table');
        table.className = 'invites-table ui-table';
        const head = document.createElement('thead');
        const headRow = document.createElement('tr');
        ['Correo', 'Nombre', 'Rol', 'Vence', 'Estado', ''].forEach(text => {
            const th = document.createElement('th');
            th.textContent = text;
            headRow.appendChild(th);
        });
        head.appendChild(headRow);
        const body = document.createElement('tbody');
        body.id = 'invitesTableBody';
        body.addEventListener('click', async (e) => {
            const button = e.target.closest('button[data-invite-id]');
            if (!button) return;
            button.disabled = true;
            try {
                const data = await apiFetch(`/staff-invitations/${button.dataset.inviteId}/resend`, { method: 'POST' });
                if (data.email_sent) showSuccess('Invitación reenviada');
                else showError('No se pudo enviar el correo. Intente de nuevo más tarde.');
                loadInvitesTable();
            } catch (err) {
                button.disabled = false;
            }
        });
        table.append(head, body);
        const tableWrap = document.createElement('div');
        tableWrap.className = 'invites-table-wrap';
        tableWrap.appendChild(table);

        section.append(title, help, form, tableTitle, tableWrap);
        modalBody.prepend(section);
        loadInvitesTable();
    }

    async function loadInvitesTable() {
        if (!currentUser || currentUser.role !== 'admin') return;
        let invites;
        try {
            invites = await apiFetch('/staff-invitations');
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        const tableBody = document.getElementById('invitesTableBody');
        if (!tableBody) return;
        tableBody.replaceChildren();
        invites.forEach(invite => {
            const row = document.createElement('tr');
            [invite.email, invite.name, INVITE_ROLES[invite.role] || invite.role,
                formatDate(invite.expires_at), INVITE_STATUS[invite.status] || invite.status].forEach(text => {
                const td = document.createElement('td');
                td.textContent = text;
                row.appendChild(td);
            });
            const actions = document.createElement('td');
            if (invite.status !== 'used') {
                const resend = document.createElement('button');
                resend.type = 'button';
                resend.className = 'ui-btn ui-btn-secondary ui-btn-sm';
                resend.dataset.inviteId = invite.id;
                resend.textContent = 'Reenviar';
                actions.appendChild(resend);
            }
            row.appendChild(actions);
            tableBody.appendChild(row);
        });
        labelTableCells(tableBody.closest('table'));
    }

    // ===== Mesa de servicio: menú lateral, contadores, buscador global y barra de estado =====
    const STAFF_SHELL_ROLES = ['technician', 'coordinator', 'admin'];
    const sideNav = document.getElementById('sideNav');
    const sideNavGroups = document.getElementById('sideNavGroups');
    const sideNavBackdrop = document.getElementById('sideNavBackdrop');
    const navToggle = document.getElementById('navToggle');
    const statusLogo = document.getElementById('statusLogo');
    const directoryRoleSelect = document.getElementById('directoryRole');
    let navPollTimer = null;
    let navCounts = {};
    let activeNavId = null;

    // Íconos de los módulos del menú (trazos de 24 × 24, los mismos del prototipo)
    const NAV_ICONS = {
        home: 'M3 10.5L12 3l9 7.5V21h-6v-6H9v6H3z',
        tickets: 'M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4z',
        mail: 'M4 4h16v16H4zM4 7l8 6 8-6',
        accounts: 'M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M21 20v-1a4 4 0 0 0-3-3.87M16 4.13a3.5 3.5 0 0 1 0 6.75',
        team: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
    };

    function navIcon(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'nav-icon');
        svg.setAttribute('width', '16');
        svg.setAttribute('height', '16');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', NAV_ICONS[name]);
        svg.appendChild(path);
        return svg;
    }

    // ===== Menú por módulos y rutas (#/módulo/sección) =====
    // Cada sección conserva el id de siempre (setSideNavActive), tiene su ruta, su contador de /nav/counts
    // y lo que abre. aliases: vistas de la cola que se ven dentro de la misma sección (sus pestañas).
    function navSection(id, label, route, open, extra = {}) {
        return { id, label, route, open, ...extra };
    }

    function queueSection(id, label, route, extra = {}) {
        return navSection(id, label, route, () => setQueueView(id, extra.title || label), extra);
    }

    function draftsSection(id, label, route, filter, extra = {}) {
        return navSection(id, label, route, () => showDrafts(filter), extra);
    }

    function homeTitleText() {
        const firstName = (currentUser.name || '').split(' ')[0];
        return firstName ? `${homeGreeting()}, ${firstName}` : homeGreeting();
    }

    function homeContextText() {
        const day = new Date().toLocaleDateString('es-CR', { weekday: 'long', day: 'numeric', month: 'long' });
        return `${day.charAt(0).toUpperCase()}${day.slice(1)} · así está la operación ahora`;
    }

    // Módulos de cada rol (los mismos permisos de la Fase 1: el servidor vuelve a revisar todo)
    function navModulesFor(role) {
        if (role === 'technician') {
            return [
                { id: 'work', label: 'Mi trabajo', icon: 'tickets', sections: [
                    queueSection('mine_open', 'Mis tickets', 'mi-trabajo/tickets', {
                        count: 'mine_open', aliases: ['mine_resolved_today', 'following'],
                        tabs: [
                            ['mine_open', 'Abiertos', 'mine_open', ''],
                            ['mine_due_soon', 'Por vencer', 'mine_due_soon', 'warn'],
                            ['mine_resolved_today', 'Resueltos hoy', 'mine_resolved_today', ''],
                        ],
                        context: () => {
                            const open = navCounts.mine_open;
                            const head = typeof open === 'number' ? `${open} ${open === 1 ? 'abierto' : 'abiertos'} a su cargo` : 'Abiertos a su cargo';
                            return `${head} · ordenados por vencimiento`;
                        },
                    }),
                    draftsSection('drafts', 'Mis borradores', 'mi-trabajo/borradores', 'all', {
                        count: 'drafts_pending', context: 'Correos que le asignaron. Al confirmar uno, el ticket queda a su cargo.',
                    }),
                    queueSection('mine_due_soon', 'Por vencer', 'mi-trabajo/por-vencer', {
                        count: 'mine_due_soon', tone: 'warn', context: 'Sus tickets cerca del tiempo de respuesta',
                    }),
                    queueSection('overdue', 'Vencidos', 'mi-trabajo/vencidos', {
                        count: 'overdue', tone: 'alert', context: 'Sus tickets que pasaron el tiempo de respuesta',
                    }),
                ] },
            ];
        }
        if (role === 'admin') {
            return [
                { id: 'admin', label: 'Administración', icon: 'accounts', sections: [
                    navSection('accounts', 'Cuentas', 'admin/cuentas', () => openAccounts(), {
                        context: 'Personal y clientes · datos de contacto enmascarados',
                    }),
                    navSection('invites', 'Invitaciones', 'admin/invitaciones', () => navUsers.click(), {
                        count: 'invitations_pending', context: 'Invite personal nuevo y revise las invitaciones que ya envió',
                    }),
                    navSection('reports', 'Reportes de cuentas', 'admin/reportes-cuentas', () => {
                        setReportsAccount(null);
                        showView('reports');
                    }, { count: 'reports_open', tone: 'alert', context: 'Cuentas de solicitante reportadas por el personal' }),
                    navSection('approvals', 'Aprobaciones', 'admin/aprobaciones', () => showAdminSection('approvals'), {
                        count: 'approvals_pending', context: 'Cambios de rol y de datos pedidos por los coordinadores',
                    }),
                    navSection('team', 'Equipo y turnos', 'admin/turnos', () => showAccountsSection('team'), {
                        context: 'Horarios de la semana, vacaciones y ausencias',
                    }),
                    navSection('changes', 'Registro de cambios', 'admin/registro', () => {
                        closePerson();
                        showAdminSection('changes');
                    }, {
                        context: 'Quién hizo qué y cuándo · los datos personales salen enmascarados',
                    }),
                    navSection('health', 'Estado del sistema', 'admin/estado', () => showAdminSection('health'), {
                        context: 'Servicios, copias de seguridad y registros del servidor',
                    }),
                ] },
            ];
        }
        return [
            { id: 'home', label: 'Inicio', icon: 'home', sections: [
                navSection('home', 'Inicio', 'inicio', () => showView('home'), {
                    title: homeTitleText, context: homeContextText, actions: () => [sendSummaryNowBtn],
                }),
            ] },
            { id: 'tickets', label: 'Tickets', icon: 'tickets', sections: [
                queueSection('open', 'Todos', 'tickets/todos', {
                    title: 'Todos los tickets', count: 'open', aliases: ['due_soon', 'resolved_today', 'cancelled', 'all'],
                    tabs: [
                        ['open', 'Abiertos', 'open', ''],
                        ['due_soon', 'Por vencer', 'due_soon', 'warn'],
                        ['resolved_today', 'Resueltos hoy', 'resolved_today', ''],
                        ['cancelled', 'Cancelados', 'cancelled', ''],
                    ],
                    context: 'Abiertos · ordenados por vencimiento',
                }),
                queueSection('unassigned', 'Sin asignar', 'tickets/sin-asignar', {
                    count: 'unassigned', tone: 'warn', context: 'Tickets abiertos que todavía no tienen responsable',
                }),
                queueSection('overdue', 'Vencidos', 'tickets/vencidos', {
                    count: 'overdue', tone: 'alert', context: 'Tickets que pasaron su tiempo de respuesta',
                }),
                navSection('incidents', 'Incidentes', 'tickets/incidentes', () => showListPage('incidents'), {
                    title: 'Posibles incidentes', context: 'Varios clientes reportan la misma falla: se atienden desde el ticket principal',
                }),
                // Fichas de clientes en solo lectura (también desde "Ver ficha del cliente" en el ticket)
                navSection('clients', 'Clientes', 'tickets/clientes', () => openAccounts('clients'), {
                    aliases: ['accounts'], context: 'Fichas de los clientes, en solo lectura',
                }),
            ] },
            { id: 'drafts', label: 'Borradores', icon: 'mail', sections: [
                draftsSection('drafts', 'Todos', 'borradores/todos', 'all', {
                    title: 'Borradores de correo', count: 'drafts_pending',
                    context: 'Cada borrador tiene un revisor: el técnico en turno con menos carga',
                }),
                draftsSection('drafts_none', 'Sin revisor', 'borradores/sin-revisor', 'none', {
                    title: 'Borradores sin revisor', count: 'drafts_unassigned', tone: 'warn', context: 'Llegaron sin nadie en turno: se asignan solos al entrar alguien, o puede repartirlos',
                }),
            ] },
            { id: 'team', label: 'Equipo', icon: 'team', sections: [
                navSection('team', 'Turnos', 'equipo/turnos', () => showAccountsSection('team'), {
                    title: 'Turnos de la semana', context: 'Horarios, vacaciones y ausencias del equipo',
                }),
                navSection('load', 'Carga', 'equipo/carga', () => showListPage('load'), {
                    title: 'Carga del equipo', context: 'La misma carga que usa la asignación automática para elegir técnico',
                }),
                navSection('availability', 'Disponibilidad', 'equipo/disponibilidad', () => showListPage('availability'), {
                    context: 'Quién está disponible hoy, y vacaciones y ausencias registradas',
                }),
                peopleSection,
            ], extraSections: [requestsSection] },
        ];
    }

    // Equipo › Personas, con "Mis solicitudes" (los cambios de rol que pidió) como subpágina
    const myRequestsButton = makeEl('button', 'ui-btn ui-btn-secondary', 'Mis solicitudes');
    myRequestsButton.type = 'button';
    const peopleSection = navSection('people', 'Personas', 'equipo/personas', () => openAccounts('staff'), {
        context: 'Técnicos: turnos, vacaciones y ausencias, y cambios de rol',
        actions: () => [myRequestsButton],
    });
    const requestsSection = navSection('requests', 'Mis solicitudes', 'equipo/personas/solicitudes',
        () => showAccountsSection('requests'), { parent: peopleSection, context: 'Cambios que pidió al administrador' });
    myRequestsButton.addEventListener('click', () => goRoute(requestsSection.route));

    // Mi cuenta: no está en el menú de módulos (se abre desde el perfil y desde abajo del menú)
    const PROFILE_SECTION = navSection('profile', 'Mi cuenta', 'mi-cuenta', () => {
        profileTab = 'perfil';
        showProfile();
    });

    // Nuevo ticket: solo quien puede crearlos (coordinador); tampoco está en el menú
    const NEW_TICKET_SECTION = navSection('newTicket', 'Nuevo ticket', 'tickets/nuevo', () => openNewTicket.click());

    let navModules = [];
    let navItems = {};
    let activeSection = null;
    // Mientras se aplica una ruta (Atrás, Adelante o un enlace) no se agrega otra entrada al historial
    let routing = false;

    function allSections() {
        const extra = canCreateTicket() ? [PROFILE_SECTION, NEW_TICKET_SECTION] : [PROFILE_SECTION];
        return [...navModules.flatMap(module => [...module.sections, ...(module.extraSections || [])]), ...extra];
    }

    // Una subpágina (parent) pertenece al módulo de su sección
    function moduleOf(section) {
        const own = section && section.parent ? section.parent : section;
        return navModules.find(module => module.sections.includes(own)) || null;
    }

    function sectionById(id) {
        if (!id) return null;
        const sections = allSections();
        return sections.find(s => s.id === id) || sections.find(s => (s.aliases || []).includes(id)) || null;
    }

    function homeRoute() {
        return { technician: 'mi-trabajo/tickets', coordinator: 'inicio', admin: 'admin/cuentas' }[currentUser.role];
    }

    function routePath() {
        return window.location.hash.startsWith('#/') ? decodeURIComponent(window.location.hash.slice(2)) : '';
    }

    function pushRoute(path) {
        if (routing || !isStaffShell()) return;
        const hash = `#/${path}`;
        if (window.location.hash !== hash) history.pushState(null, '', hash);
    }

    // "tickets/vencidos", "tickets/vencidos/176" o "tickets/vencidos/176/reporte". Desconocida: null
    function parseRoute(path) {
        // Ficha de una persona en el Registro de cambios (solo administrador)
        const person = path.match(/^(admin\/registro)\/persona\/(\d+)$/);
        if (person) {
            const section = allSections().find(s => s.route === person[1]);
            return section ? { section, personId: Number(person[2]) } : null;
        }
        const match = path.match(/^(.*?)(?:\/(\d+)(\/reporte)?)?$/);
        const section = allSections().find(s => s.route === match[1]);
        if (!section) return null;
        return { section, ticketId: match[2] ? Number(match[2]) : null, report: Boolean(match[3]) };
    }

    // La ruta de un ticket cuelga de la sección donde se abrió (o de la principal del rol)
    function ticketRoute(ticketId, report = false) {
        const section = activeSection && ![PROFILE_SECTION, NEW_TICKET_SECTION].includes(activeSection)
            ? activeSection : parseRoute(homeRoute()).section;
        return `${section.route}/${ticketId}${report ? '/reporte' : ''}`;
    }

    function applyRoute() {
        if (!currentUser || !isStaffShell() || profileRequired()) return;
        // Enlace de un correo (#ticket/N) con la sesión abierta: pasa a la ruta del ticket
        const linked = window.location.hash.match(/^#ticket\/(\d+)$/);
        if (linked) history.replaceState(null, '', `#/${ticketRoute(Number(linked[1]))}`);
        let route = parseRoute(routePath());
        // Ruta desconocida o de otro rol: al inicio del rol, sin dejar la mala en el historial
        if (!route) {
            history.replaceState(null, '', `#/${homeRoute()}`);
            route = parseRoute(homeRoute());
        }
        routing = true;
        try {
            hideError();
            closeBellPanel();
            closeProfileMenu();
            if (route.personId) {
                route.section.open();
                setSideNavActive(route.section.id);
                openPerson(route.personId);
            } else if (route.ticketId) {
                setSideNavActive(route.section.id);
                if (route.report) openOverdueReport(route.ticketId);
                else openTicketById(route.ticketId);
            } else {
                route.section.open();
                setSideNavActive(route.section.id);
            }
        } finally {
            routing = false;
        }
        closeSideNav();
        checkSingleSection();
    }

    // Ir a una sección: nueva entrada en el historial (o la misma, si ya está ahí) y se abre
    function goRoute(path) {
        const hash = `#/${path}`;
        if (window.location.hash === hash) history.replaceState(null, '', hash);
        else history.pushState(null, '', hash);
        applyRoute();
    }

    window.addEventListener('popstate', applyRoute);

    function renderSideNav() {
        navModules = navModulesFor(currentUser.role);
        navItems = {};
        sideNavGroups.replaceChildren();
        const heading = makeEl('h2', 'nav-modules-title', 'Módulos');
        heading.id = 'navModulesTitle';
        const list = makeEl('ul', 'nav-modules');
        list.setAttribute('aria-labelledby', heading.id);
        navModules.forEach(module => {
            const li = makeEl('li', 'nav-module-item');
            li.dataset.module = module.id;
            const single = module.sections.length === 1;
            const button = makeEl('button', 'nav-module');
            button.type = 'button';
            button.dataset.moduleId = module.id;
            if (single) button.dataset.navId = module.sections[0].id;
            button.appendChild(navIcon(module.icon));
            button.appendChild(makeEl('span', 'nav-item-label', module.label));
            // Nombre visible como tooltip cuando el menú está contraído
            button.dataset.tooltip = module.label;
            li.appendChild(button);
            if (single) {
                navItems[module.sections[0].id] = module.sections[0];
            } else {
                const caret = makeEl('span', 'nav-module-caret', '▾');
                caret.setAttribute('aria-hidden', 'true');
                button.appendChild(caret);
                button.setAttribute('aria-expanded', 'false');
                const sub = makeEl('ul', 'nav-sections');
                sub.id = `navSections-${module.id}`;
                button.setAttribute('aria-controls', sub.id);
                sub.hidden = true;
                module.sections.forEach(section => {
                    navItems[section.id] = section;
                    const sectionLi = document.createElement('li');
                    const item = makeEl('button', 'nav-item');
                    item.type = 'button';
                    item.dataset.navId = section.id;
                    item.appendChild(makeEl('span', 'nav-item-label', section.label));
                    if (section.count) {
                        const count = makeEl('span', 'nav-count');
                        count.dataset.count = section.count;
                        if (section.tone) count.dataset.tone = section.tone;
                        item.appendChild(count);
                    }
                    sectionLi.appendChild(item);
                    sub.appendChild(sectionLi);
                });
                li.appendChild(sub);
            }
            list.appendChild(li);
        });
        sideNavGroups.append(heading, list);
        renderNavUser();
        // Solo marca la sección: la ruta la decide el inicio de sesión o el enlace
        const wasRouting = routing;
        routing = true;
        setSideNavActive(activeNavId);
        routing = wasRouting;
        renderNavCounts();
    }

    function renderNavUser() {
        if (!currentUser) return;
        document.getElementById('topProfileAvatar').textContent = getInitials(currentUser.name);
        document.getElementById('topProfileName').textContent = currentUser.name || currentUser.email;
        document.getElementById('topProfileRole').textContent = ROLE_MAP[currentUser.role] || currentUser.role;
    }

    // Marca la sección (y abre su módulo), actualiza la ruta y la cabecera de la página
    function setSideNavActive(id) {
        activeNavId = id;
        const section = sectionById(id);
        activeSection = section;
        // Una subpágina marca su sección en el menú
        const activeId = section ? (section.parent ? section.parent.id : section.id) : id;
        sideNav.querySelectorAll('[data-nav-id]').forEach(button => {
            const active = button.dataset.navId === activeId;
            button.classList.toggle('active', active);
            if (active) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        });
        const module = section ? moduleOf(section) : null;
        sideNavGroups.querySelectorAll('.nav-module-item').forEach(li => {
            // Con un solo módulo (técnico) queda siempre abierto
            const open = navModules.length === 1 || Boolean(module && li.dataset.module === module.id);
            li.querySelector('.nav-module').classList.toggle('is-active', Boolean(module && li.dataset.module === module.id));
            const sub = li.querySelector('.nav-sections');
            if (sub) {
                sub.hidden = !open;
                li.querySelector('.nav-module').setAttribute('aria-expanded', open ? 'true' : 'false');
            }
        });
        if (section) pushRoute(section.route);
        renderPageHead();
        renderQueueViews();
        renderMyWork();
    }

    function renderNavCounts() {
        sideNavGroups.querySelectorAll('.nav-count').forEach(el => {
            const value = navCounts[el.dataset.count];
            el.textContent = typeof value === 'number' ? String(value) : '';
            el.classList.toggle('is-alert', el.dataset.tone === 'alert' && value > 0);
            el.classList.toggle('is-warn', el.dataset.tone === 'warn' && value > 0);
        });
        renderNavShift();
        renderQueueViews();
        renderPageHead();
        renderMyWork();
        if (!homeView.hidden) renderHome();
    }

    // Abajo del menú: el turno de hoy (técnico y coordinador) o el estado del sistema (administrador)
    let lastSystemStatus = null;

    function renderNavShift() {
        const box = document.getElementById('navShift');
        const text = document.getElementById('navShiftText');
        if (!currentUser) return;
        if (currentUser.role === 'admin') {
            if (!lastSystemStatus) {
                box.hidden = true;
                return;
            }
            const off = [['email', 'correo'], ['ai_analysis', 'IA'], ['auto_assign', 'asignación automática']]
                .filter(([key]) => !lastSystemStatus[key]).map(([, label]) => label);
            box.hidden = false;
            box.className = `nav-shift ${off.length ? 'is-warn' : 'is-on'}`;
            text.textContent = off.length ? `Sistema: ${off.join(', ')} apagado` : 'Sistema: todo funciona';
            return;
        }
        const shift = navCounts.shift;
        box.hidden = !shift;
        if (!shift) return;
        box.className = `nav-shift ${shift.on_shift ? 'is-on' : 'is-off'}`;
        if (shift.on_shift) text.textContent = `En turno hasta ${shift.end}`;
        else text.textContent = shift.start ? `Fuera de turno · hoy ${shift.start}–${shift.end}` : 'Sin turno hoy';
    }

    // ----- Cabecera de la página: ruta (Módulo › Sección), título, contexto y acciones -----
    const pageHead = document.getElementById('pageHead');
    const pageActions = document.getElementById('pageActions');
    let currentViewName = null;

    document.getElementById('pageCrumb').addEventListener('click', (e) => {
        const link = e.target.closest('.page-crumb-link');
        if (!link) return;
        e.preventDefault();
        goRoute(link.dataset.route);
    });

    // Altura de la cabecera fija: los encabezados de tabla se quedan justo debajo
    if (window.ResizeObserver) {
        new ResizeObserver(() => {
            document.body.style.setProperty('--page-head-h', `${pageHead.hidden ? 0 : pageHead.offsetHeight}px`);
        }).observe(pageHead);
        // Igual con la barra del ticket: la columna derecha del detalle se queda debajo de ella
        const ticketBarEl = document.getElementById('ticketBar');
        new ResizeObserver(() => {
            document.body.style.setProperty('--ticket-bar-h', `${ticketBarEl.hidden ? 0 : ticketBarEl.offsetHeight}px`);
        }).observe(ticketBarEl);
    }

    function renderPageHead() {
        if (!isStaffShell()) {
            pageHead.hidden = true;
            return;
        }
        // Cada paso de la ruta lleva a su página: el módulo abre su primera sección
        const crumbs = [];
        let title = '';
        let context = '';
        const section = activeSection;
        if (section) {
            const module = moduleOf(section);
            if (module && module.sections.length > 1) crumbs.push({ label: module.label, route: module.sections[0].route });
            if (section.parent) crumbs.push({ label: section.parent.label, route: section.parent.route });
            crumbs.push({ label: section.label });
            // Ficha de una persona: "Registro de cambios" lleva de vuelta a la búsqueda
            if (section.id === 'changes' && personId !== null) {
                crumbs[crumbs.length - 1].route = section.route;
                crumbs.push({ label: personCrumb || 'Ficha' });
            }
            title = typeof section.title === 'function' ? section.title() : (section.title || section.label);
            context = typeof section.context === 'function' ? section.context() : (section.context || '');
        } else if (currentViewName === 'tickets') {
            // Búsqueda o tickets de un cliente: sin sección del menú
            const ticketsModule = navModules.find(m => m.id === 'tickets');
            crumbs.push({ label: 'Tickets', route: ticketsModule ? ticketsModule.sections[0].route : null }, { label: queueTitle.textContent });
            title = queueTitle.textContent;
        }
        // Nuevo ticket y el reporte de vencimiento tienen su propia barra
        pageHead.hidden = !crumbs.length || ['newTicket', 'overdueReport'].includes(currentViewName);
        const list = document.getElementById('pageCrumb');
        list.replaceChildren(...crumbs.map((crumb, index) => {
            const last = index === crumbs.length - 1;
            const li = makeEl('li', 'page-crumb-item');
            if (last || !crumb.route) {
                li.textContent = crumb.label;
                if (last) li.setAttribute('aria-current', 'page');
            } else {
                const link = makeEl('a', 'page-crumb-link', crumb.label);
                link.href = `#/${crumb.route}`;
                link.dataset.route = crumb.route;
                li.appendChild(link);
            }
            return li;
        }));
        document.getElementById('pageTitle').textContent = title;
        // Acciones de la sección (los botones son los de siempre: se mueven aquí con sus eventos)
        const actions = section && typeof section.actions === 'function' ? section.actions() : [];
        pageActions.replaceChildren(...actions);
        const contextEl = document.getElementById('pageContext');
        contextEl.textContent = context;
        contextEl.hidden = !context;
    }

    // ----- Coordinador: páginas de lista (Incidentes, Carga, Disponibilidad) con datos que ya existen -----
    const listViewBody = document.getElementById('listViewBody');
    const listViewNote = document.getElementById('listViewNote');
    let listKind = null;
    let listSeq = 0;

    function showListPage(kind) {
        listKind = kind;
        showView('list');
        loadListPage();
    }

    async function loadListPage() {
        const seq = ++listSeq;
        const kind = listKind;
        showSkeleton(listViewBody, { rows: 4 });
        listViewNote.textContent = '';
        let data;
        try {
            data = await apiFetch(kind === 'incidents' ? '/incidents' : '/team', { silent: true });
        } catch (err) {
            if (seq !== listSeq) return;
            clearSkeleton(listViewBody);
            listViewBody.replaceChildren(errorState(err, loadListPage));
            return;
        }
        if (seq !== listSeq) return;
        clearSkeleton(listViewBody);
        if (kind === 'incidents') renderIncidentsPage(data);
        else if (kind === 'load') renderLoadPage(data);
        else renderAvailabilityPage(data);
    }

    function listRow(className = '') {
        const li = makeEl('li', `list-row ${className}`.trim());
        return li;
    }

    function renderIncidentsPage(items) {
        listViewBody.replaceChildren();
        listViewNote.textContent = 'Al resolver el ticket principal se resuelven también los relacionados, y cada cliente recibe el aviso.';
        if (!items.length) {
            listViewBody.appendChild(emptyState({
                title: 'No hay posibles incidentes', text: 'Aparecen cuando varios clientes reportan lo mismo en poco tiempo.',
            }));
            return;
        }
        const list = makeEl('ul', 'list-rows');
        items.forEach(item => {
            const li = listRow();
            const button = makeEl('button', 'list-row-button list-incident');
            button.type = 'button';
            button.dataset.ticketId = String(item.principal_id);
            button.appendChild(makeEl('span', 'ui-tag is-danger', 'Incidente'));
            const text = makeEl('span', 'list-row-text');
            text.appendChild(makeEl('span', 'list-row-title', `${item.clients} clientes reportan «${item.title}»`));
            text.appendChild(makeEl('span', 'list-row-sub', `Principal #${item.principal_id}`));
            button.appendChild(text);
            button.appendChild(makeEl('span', 'list-row-go', 'Ver ›'));
            li.appendChild(button);
            list.appendChild(li);
        });
        listViewBody.appendChild(list);
    }

    // Disponible, de vacaciones o con una ausencia, y el turno de hoy
    function memberStateText(member) {
        if (member.availability === 'vacation' || member.availability === 'incapacity') {
            return AVAILABILITY_LABELS[member.availability]
                + (member.return_date ? ` · regresa el ${formatPlainDate(member.return_date)}` : '');
        }
        if (member.on_shift) return member.schedule_today ? `En turno · ${member.schedule_today}` : 'En turno';
        return member.schedule_today ? `Fuera de turno · hoy ${member.schedule_today}` : 'Sin turno hoy';
    }

    function memberHead(member) {
        const head = makeEl('span', 'list-member');
        const avatar = makeEl('span', 'owner-avatar', getInitials(member.name));
        avatar.setAttribute('aria-hidden', 'true');
        const text = makeEl('span', 'list-row-text');
        text.appendChild(makeEl('span', 'list-row-title', member.name));
        text.appendChild(makeEl('span', 'list-row-sub', memberStateText(member)));
        head.append(avatar, text);
        return head;
    }

    function renderLoadPage(team) {
        listViewBody.replaceChildren();
        const technicians = team.filter(member => member.role === 'technician' && member.is_active);
        const byLevels = lastSystemStatus && lastSystemStatus.auto_assign_mode === 'levels';
        listViewNote.textContent = byLevels
            ? 'Carga = suma de los niveles de sus tickets abiertos y borradores pendientes (sin nivel cuenta 2).'
            : 'Carga = tickets abiertos y borradores pendientes a su cargo.';
        if (!technicians.length) {
            listViewBody.appendChild(emptyState({ title: 'Todavía no hay técnicos', text: 'El administrador los invita desde Invitaciones.' }));
            return;
        }
        const loadOf = (member) => (typeof member.load === 'number' ? member.load : member.tickets_assigned_open);
        technicians.sort((a, b) => loadOf(b) - loadOf(a) || a.name.localeCompare(b.name));
        const maxLoad = Math.max(1, ...technicians.map(loadOf));
        const list = makeEl('ul', 'list-rows');
        technicians.forEach(member => {
            const li = listRow('list-load');
            li.appendChild(memberHead(member));
            const load = loadOf(member);
            const bar = makeEl('span', `ui-bar list-load-bar${load / maxLoad >= 0.75 ? ' is-soon' : ''}`);
            bar.setAttribute('aria-hidden', 'true');
            const fill = makeEl('span');
            fill.style.width = `${Math.round((load / maxLoad) * 100)}%`;
            bar.appendChild(fill);
            li.appendChild(bar);
            const numbers = makeEl('span', 'list-load-numbers');
            numbers.appendChild(makeEl('b', 'ui-num', String(load)));
            numbers.appendChild(makeEl('span', 'list-row-sub',
                `${member.tickets_assigned_open} ${member.tickets_assigned_open === 1 ? 'ticket abierto' : 'tickets abiertos'}`));
            li.appendChild(numbers);
            list.appendChild(li);
        });
        listViewBody.appendChild(list);
    }

    function renderAvailabilityPage(team) {
        listViewBody.replaceChildren();
        listViewNote.textContent = 'Es automática: Disponible dentro del horario y Ausente fuera de él. Las vacaciones y las ausencias empiezan solas el primer día.';
        // Los mismos que puede gestionar quien mira (MANAGE_RULES): el coordinador, técnicos
        const members = team.filter(member => member.is_active && ['technician', 'coordinator'].includes(member.role));
        if (!members.length) {
            listViewBody.appendChild(emptyState({ title: 'No hay personal', text: '' }));
            return;
        }
        const list = makeEl('ul', 'list-rows');
        members.forEach(member => {
            const li = listRow('list-availability');
            li.appendChild(memberHead(member));
            const periods = makeEl('span', 'list-periods');
            if (!(member.periods || []).length) {
                periods.appendChild(makeEl('span', 'list-row-sub', 'Sin vacaciones ni ausencias registradas'));
            }
            (member.periods || []).forEach(period => {
                periods.appendChild(makeEl('span', 'ui-tag', `${PERIOD_KINDS[period.kind] || period.kind} · `
                    + `${formatPlainDate(period.start_date)} al ${formatPlainDate(period.end_date)}`));
            });
            li.appendChild(periods);
            if (canManage(member, 'availability')) {
                const edit = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm list-availability-edit', 'Vacaciones y ausencias');
                edit.type = 'button';
                edit.dataset.userId = String(member.id);
                edit.dataset.userName = member.name;
                li.appendChild(edit);
            }
            list.appendChild(li);
        });
        listViewBody.appendChild(list);
    }

    listViewBody.addEventListener('click', async (e) => {
        const incident = e.target.closest('.list-incident');
        if (incident) {
            openTicketById(Number(incident.dataset.ticketId));
            return;
        }
        const edit = e.target.closest('.list-availability-edit');
        if (edit && await openAvailabilityDialog(Number(edit.dataset.userId), edit.dataset.userName)) loadListPage();
    });

    // ----- Técnico › Mis tickets: aviso de vencidos, su turno, su carga y sus borradores -----
    const queueAside = document.getElementById('queueAside');
    const queueOverdueNotice = document.getElementById('queueOverdueNotice');
    const asideDrafts = document.getElementById('asideDrafts');
    let asideSeq = 0;

    function renderMyWork() {
        const mine = isStaffShell() && currentUser.role === 'technician' && currentViewName === 'tickets'
            && Boolean(activeSection && activeSection.id === 'mine_open') && !queueSearch;
        const overdue = navCounts.overdue;
        queueOverdueNotice.hidden = !(mine && overdue > 0);
        if (!queueOverdueNotice.hidden) {
            document.getElementById('queueOverdueText').textContent =
                `Tiene ${overdue} ${overdue === 1 ? 'ticket vencido' : 'tickets vencidos'}. `
                + 'Los vencidos no aparecen en esta lista: se atienden en su propia sección.';
        }
        queueAside.hidden = !mine;
        splitView.classList.toggle('has-aside', mine);
        if (!mine) return;
        const shift = navCounts.shift;
        document.getElementById('asideShiftTime').textContent = shift && shift.start
            ? `${shift.start} – ${shift.end}${shift.on_shift ? '' : ' · fuera de turno'}` : 'Sin turno hoy';
        // La misma carga que usa la asignación automática (tickets abiertos y borradores pendientes)
        const byLevels = lastSystemStatus && lastSystemStatus.auto_assign_mode === 'levels';
        document.getElementById('asideShiftLoad').textContent = shift
            ? `Carga actual: ${shift.load}${byLevels ? ' (suma de niveles)' : ''}` : '';
        loadAsideDrafts();
    }

    async function loadAsideDrafts() {
        const seq = ++asideSeq;
        let list = null;
        try {
            list = await silentGet('/drafts');
        } catch (err) {
            return; // Se reintenta con la siguiente actualización
        }
        if (seq !== asideSeq || !list) return;
        document.getElementById('asideDraftsTitle').textContent = `Mis borradores · ${list.length}`;
        asideDrafts.replaceChildren();
        if (!list.length) {
            asideDrafts.appendChild(makeEl('li', 'queue-aside-empty', 'No tiene borradores pendientes.'));
            return;
        }
        list.slice(0, 4).forEach(draft => {
            const li = document.createElement('li');
            const button = makeEl('button', 'queue-aside-draft');
            button.type = 'button';
            button.dataset.draftId = String(draft.id);
            button.appendChild(makeEl('span', 'queue-aside-draft-title', `«${draft.title || draft.subject || 'Sin asunto'}»`));
            const meta = makeEl('span', 'queue-aside-draft-meta', `Correo de ${draft.requester_name} · `);
            meta.appendChild(timeTag(draft.created_at));
            button.appendChild(meta);
            button.appendChild(makeEl('span', 'queue-aside-draft-go', 'Revisar y confirmar →'));
            li.appendChild(button);
            asideDrafts.appendChild(li);
        });
    }

    asideDrafts.addEventListener('click', (e) => {
        const button = e.target.closest('.queue-aside-draft');
        if (button) openDraftById(Number(button.dataset.draftId));
    });
    document.getElementById('queueOverdueLink').addEventListener('click', () => goRoute('mi-trabajo/vencidos'));

    function closeSideNav() {
        document.body.classList.remove('nav-open');
        navToggle.setAttribute('aria-expanded', 'false');
        sideNavBackdrop.hidden = true;
    }

    sideNavGroups.addEventListener('click', (e) => {
        const button = e.target.closest('.nav-item, .nav-module');
        if (!button) return;
        // Un módulo abre su primera sección; una sección, la suya
        const module = button.classList.contains('nav-module')
            ? navModules.find(m => m.id === button.dataset.moduleId) : null;
        const section = module ? module.sections[0] : navItems[button.dataset.navId];
        if (!section) return;
        goRoute(section.route);
    });

    document.getElementById('navUser').addEventListener('click', () => goRoute(PROFILE_SECTION.route));

    // ----- Menú contraíble a solo íconos; la preferencia queda en este navegador -----
    const NAV_COLLAPSED_KEY = 'ticketera.navCollapsed';
    const navCollapse = document.getElementById('navCollapse');

    function applyNavCollapsed(collapsed) {
        document.body.classList.toggle('nav-collapsed', collapsed);
        navCollapse.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        navCollapse.querySelector('.nav-collapse-label').textContent = collapsed ? 'Expandir menú' : 'Contraer menú';
        navCollapse.dataset.tooltip = 'Expandir menú';
    }

    try {
        applyNavCollapsed(localStorage.getItem(NAV_COLLAPSED_KEY) === '1');
    } catch (err) {
        applyNavCollapsed(false);
    }

    navCollapse.addEventListener('click', () => {
        const collapsed = !document.body.classList.contains('nav-collapsed');
        applyNavCollapsed(collapsed);
        try {
            localStorage.setItem(NAV_COLLAPSED_KEY, collapsed ? '1' : '0');
        } catch (err) {
            // Sin almacenamiento: el menú queda así solo en esta visita
        }
    });

    // ----- Perfil en la barra superior: Mi cuenta y Cerrar sesión -----
    const topProfile = document.getElementById('topProfile');
    const profileMenu = document.getElementById('profileMenu');

    function closeProfileMenu(returnFocus = false) {
        if (profileMenu.hidden) return;
        profileMenu.hidden = true;
        topProfile.setAttribute('aria-expanded', 'false');
        if (returnFocus) topProfile.focus();
    }

    topProfile.addEventListener('click', () => {
        const open = profileMenu.hidden;
        profileMenu.hidden = !open;
        topProfile.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) profileMenu.querySelector('.profile-menu-item').focus();
    });
    document.getElementById('profileMenuAccount').addEventListener('click', () => {
        closeProfileMenu();
        goRoute(PROFILE_SECTION.route);
    });
    document.getElementById('profileMenuLogout').addEventListener('click', () => {
        closeProfileMenu();
        switchAccountBtn.click();
    });
    profileMenu.addEventListener('keydown', (e) => {
        const items = [...profileMenu.querySelectorAll('.profile-menu-item')];
        const index = items.indexOf(document.activeElement);
        if (e.key === 'Escape') {
            e.preventDefault();
            closeProfileMenu(true);
        } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            items[(index + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
        }
    });
    document.addEventListener('click', (e) => {
        if (!profileMenu.hidden && !profileMenu.contains(e.target) && !topProfile.contains(e.target)) closeProfileMenu();
    });

    // El buscador de la barra superior abre la paleta (Ctrl K)
    document.getElementById('topSearch').addEventListener('click', () => openPalette());

    document.getElementById('navLogout').addEventListener('click', () => switchAccountBtn.click());

    navToggle.addEventListener('click', () => {
        const open = !document.body.classList.contains('nav-open');
        document.body.classList.toggle('nav-open', open);
        navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        sideNavBackdrop.hidden = !open;
    });
    sideNavBackdrop.addEventListener('click', closeSideNav);

    // Sin el logo no se muestra nada (ni el texto alternativo)
    statusLogo.addEventListener('error', () => { statusLogo.hidden = true; });

    // ----- Mi turno y disponibilidad (técnico) -----
    // ----- Contadores y barra de estado: cada minuto, solo con la pestaña visible -----
    // No usa apiFetch para no borrar un mensaje de error en pantalla.
    async function silentGet(endpoint) {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: { 'Accept': 'application/json', 'Authorization': `Bearer ${authToken}` }
        });
        if (response.status === 401) {
            const data = await response.json().catch(() => ({}));
            handleUnauthorized(data.detail);
            return null;
        }
        return response.ok ? response.json() : null;
    }

    // Estado de los servicios: abajo del menú del administrador y la carga del técnico (por niveles o cantidad)
    function renderStatusBar(status) {
        lastSystemStatus = status;
        renderNavShift();
    }

    // withQueue: el temporizador también actualiza la cola; tras un cambio solo hacen falta los contadores
    async function pollNavCounts(withQueue = true) {
        if (document.visibilityState !== 'visible' || !authToken || !currentUser) return;
        if (!STAFF_SHELL_ROLES.includes(currentUser.role)) return;
        try {
            const coordinator = OPERATION_ROLES.includes(currentUser.role);
            const [counts, status, notices, unassignedDrafts] = await Promise.all([
                silentGet('/nav/counts'), silentGet('/system/status'), silentGet('/notifications/overdue'),
                // Borradores › Sin revisor: el contador sale de la misma lista que ve el coordinador
                coordinator ? silentGet('/drafts?assigned=none') : Promise.resolve(null),
            ]);
            if (counts) {
                if (unassignedDrafts) counts.drafts_unassigned = unassignedDrafts.length;
                navCounts = counts;
                renderNavCounts();
            }
            if (status) renderStatusBar(status);
            if (notices) renderBell(notices);
            // La cola visible también se actualiza; el detalle abierto no se toca
            // El administrador no tiene cola de tickets
            if (withQueue && currentUser.role !== 'admin' && document.querySelector('main .split').style.display !== 'none') {
                await loadQueue({ quiet: true });
            }
        } catch (err) {
            // Se reintenta en el siguiente minuto
        }
    }

    function stopNavPolling() {
        if (navPollTimer !== null) {
            clearInterval(navPollTimer);
            navPollTimer = null;
        }
    }

    function startNavPolling() {
        stopNavPolling();
        if (!currentUser || !STAFF_SHELL_ROLES.includes(currentUser.role)) return;
        pollNavCounts();
        navPollTimer = setInterval(pollNavCounts, 60000);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && navPollTimer !== null) pollNavCounts();
    });

    // ----- Buscador global: #número abre el ticket; el texto busca tickets y, con permiso, clientes -----
    async function openTicketById(ticketId) {
        // El administrador no abre tickets directamente: primero el motivo de "Ver conversación"
        const admin = currentUser && currentUser.role === 'admin';
        // Abierto desde una ruta (Atrás, Adelante, enlace): no se agrega otra entrada al historial
        const fromRoute = routing;
        const reason = admin ? await askConversationReason(ticketId) : null;
        if (admin && reason === null) return;
        showView('tickets');
        if (!fromRoute) {
            history.pushState(null, '', `#/${ticketRoute(ticketId)}`);
        }
        selectedTicketId = ticketId;
        activityFilter = 'public';
        enterTicketPage(ticketId);
        try {
            const ticket = admin
                ? await apiFetch(`/tickets/${ticketId}/view-conversation`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason })
                })
                : await apiFetch(`/tickets/${ticketId}`);
            showTicketDetail(ticket);
        } catch (err) {
            // apiFetch ya mostró el error (por ejemplo, que no existe)
            exitTicketPage();
        }
    }

    // ----- "Ver conversación" (administrador): motivo corto, de 5 a 150 caracteres -----
    const conversationDialog = document.getElementById('conversationDialog');
    const conversationForm = document.getElementById('conversationForm');
    const conversationReason = document.getElementById('conversationReason');
    const conversationError = document.getElementById('conversationError');
    let conversationDone = null;

    function askConversationReason(ticketId) {
        return new Promise(resolve => {
            conversationDone = resolve;
            conversationForm.reset();
            conversationError.hidden = true;
            document.getElementById('conversationDialogTitle').textContent = `Ver la conversación del ticket #${ticketId}`;
            conversationDialog.showModal();
            conversationReason.focus();
        });
    }

    function closeConversationDialog(reason) {
        if (conversationDialog.open) conversationDialog.close();
        const resolve = conversationDone;
        conversationDone = null;
        if (resolve) resolve(reason);
    }

    document.getElementById('conversationCancel').addEventListener('click', () => closeConversationDialog(null));
    conversationDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeConversationDialog(null);
    });
    conversationForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const reason = conversationReason.value.trim().replace(/\s+/g, ' ');
        if (reason.length < 5) {
            conversationError.textContent = 'Escriba un motivo de al menos 5 caracteres';
            conversationError.hidden = false;
            return;
        }
        closeConversationDialog(reason);
    });

    // Inicio del personal. El administrador no tiene Inicio de tickets (no entra al flujo): va a Cuentas
    function showStaffHome() {
        goRoute(homeRoute());
    }

    // Al iniciar sesión: la ruta que traía la dirección o, sin ella, el inicio del rol
    function startStaffRoute() {
        if (!routePath()) history.replaceState(null, '', `#/${homeRoute()}`);
        applyRoute();
    }

    function setupStaffShell() {
        const staff = Boolean(currentUser && STAFF_SHELL_ROLES.includes(currentUser.role));
        document.body.classList.toggle('staff-shell', staff);
        sideNav.hidden = !staff;
        resetBell();
        document.getElementById('staffFooter').hidden = !staff;
        navToggle.hidden = !staff;
        closeSideNav();
        if (!staff) {
            stopNavPolling();
            return;
        }
        navCounts = {};
        activeNavId = currentUser.role === 'technician' ? 'mine_open' : 'open';
        queueView = activeNavId;
        queueTitle.textContent = currentUser.role === 'technician' ? 'Mis abiertos' : 'Todos abiertos';
        queueSearch = '';
        queuePageNum = 1;
        [queueStatus, queueLevel, queueAssignee, queueSource].forEach(select => { select.value = ''; });
        while (queueAssignee.options.length > 2) queueAssignee.remove(2);
        // El técnico no crea tickets (igual que el botón de la barra superior)
        document.getElementById('queueNewTicket').hidden = currentUser.role === 'technician';
        renderSideNav();
        startNavPolling();
    }

    function teardownStaffShell() {
        stopNavPolling();
        document.body.classList.remove('staff-shell', 'nav-open', 'ticket-open', 'newticket-open');
        cancelPendingAssignSilently();
        cancelPendingCancelSilently();
        ticketBar.hidden = true;
        sideNav.hidden = true;
        resetBell();
        document.getElementById('staffFooter').hidden = true;
        sideNavBackdrop.hidden = true;
        pageHead.hidden = true;
        closeProfileMenu();
        activeSection = null;
        // La ruta del personal no queda para la próxima sesión
        if (routePath()) history.replaceState(null, '', window.location.pathname + window.location.search);
    }

    // ===== Cola de tickets y panel de detalle del personal =====
    const queueBody = document.getElementById('queueBody');
    const queueTitle = document.getElementById('queueTitle');
    const queueTotal = document.getElementById('queueTotal');
    const queueUpdated = document.getElementById('queueUpdated');
    const queueEmpty = document.getElementById('queueEmpty');
    const queueStatus = document.getElementById('queueStatus');
    const queueLevel = document.getElementById('queueLevel');
    const queueAssignee = document.getElementById('queueAssignee');
    const queueSource = document.getElementById('queueSource');
    const queuePrev = document.getElementById('queuePrev');
    const queueNext = document.getElementById('queueNext');
    const queuePageLabel = document.getElementById('queuePage');
    const SOURCE_LABELS = { web: 'Web', email: 'Correo' };
    const ASSIGNEE_NOTES = {
        vacation: 'De vacaciones', incapacity: 'Con ausencia', away: 'Fuera de su horario',
        after_shift: 'Vence después del fin de su turno de hoy'
    };
    // Las mismas transiciones que valida el servidor
    const STATUS_TRANSITIONS = { new: ['in_progress'], in_progress: ['resolved'], resolved: ['closed', 'in_progress'] };
    const ASK_DETAILS_TEXT = 'Para avanzar con su caso necesitamos más información: ';
    let queueView = 'open';
    let queueSearch = '';
    // "Ver sus tickets" desde Cuentas: { id, name } del cliente
    let queueRequester = null;
    let queuePageNum = 1;
    let queueLoadedAt = null;
    let queueSeq = 0;

    function isStaffShell() {
        return Boolean(currentUser && STAFF_SHELL_ROLES.includes(currentUser.role));
    }

    function makeEl(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    // En el celular las tablas se ven como tarjetas: cada celda muestra el nombre de su columna
    function labelTableCells(table) {
        if (!table) return;
        const heads = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
        table.querySelectorAll('tbody tr').forEach(row => {
            [...row.children].forEach((cell, index) => {
                if (heads[index]) cell.dataset.label = heads[index];
            });
        });
    }

    // ----- Errores de validación en línea (personal): el mensaje va debajo del campo, no en la burbuja -----
    function fieldErrorFor(field) {
        const id = field.id ? `${field.id}-error` : '';
        return id ? document.getElementById(id) : null;
    }

    function clearFieldError(field) {
        const error = fieldErrorFor(field);
        if (error) error.hidden = true;
        field.removeAttribute('aria-invalid');
    }

    document.addEventListener('invalid', (e) => {
        const field = e.target;
        if (!isStaffShell() || !field.id || !(field instanceof HTMLElement)) return;
        e.preventDefault();
        let error = fieldErrorFor(field);
        if (!error) {
            error = makeEl('p', 'ui-field-error');
            error.id = `${field.id}-error`;
            error.setAttribute('aria-live', 'polite');
            // Debajo del campo (o de su fila, si comparte línea con un botón)
            const anchor = field.parentElement && field.parentElement.classList.contains('requester-row') ? field.parentElement : field;
            anchor.after(error);
            const described = (field.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
            field.setAttribute('aria-describedby', [...described, error.id].join(' '));
        }
        error.textContent = field.validationMessage;
        error.hidden = false;
        field.setAttribute('aria-invalid', 'true');
        // El primer campo con error recibe el foco
        const form = field.form;
        if (form && !form.querySelector('[aria-invalid="true"]:focus')) {
            const first = form.querySelector(':invalid');
            if (first === field) field.focus();
        }
    }, true);

    ['input', 'change'].forEach(type => document.addEventListener(type, (e) => {
        const field = e.target;
        if (field instanceof HTMLElement && field.getAttribute('aria-invalid') === 'true' && field.checkValidity && field.checkValidity()) {
            clearFieldError(field);
        }
    }, true));

    function formatDuration(seconds) {
        const minutes = Math.max(0, Math.round(seconds / 60));
        const days = Math.floor(minutes / 1440);
        const hours = Math.floor((minutes % 1440) / 60);
        const mins = minutes % 60;
        if (days > 0) return `${days} d ${hours} h`;
        if (hours > 0) return `${hours} h ${mins} min`;
        return `${mins} min`;
    }

    function dueText(state, remainingSeconds) {
        if (!state || remainingSeconds === null || remainingSeconds === undefined) return 'Sin fecha límite';
        if (state === 'overdue') return `Vencido ${formatDuration(-remainingSeconds)}`;
        return formatDuration(remainingSeconds);
    }

    function dueBar(state, percent) {
        const bar = makeEl('div', 'due-bar');
        bar.setAttribute('aria-hidden', 'true');
        const fill = makeEl('span', `due-fill due-${state || 'none'}`);
        fill.style.width = `${percent || 0}%`;
        bar.appendChild(fill);
        return bar;
    }

    function levelText(level) {
        return level === null || level === undefined ? 'Sin nivel' : `Nivel ${level}`;
    }

    function fillAssigneeFilter() {
        const current = queueAssignee.value;
        while (queueAssignee.options.length > 2) queueAssignee.remove(2);
        Object.keys(userMap).forEach(userId => {
            const role = userRoleMap[userId];
            if (role !== 'technician' && role !== 'coordinator') return;
            const opt = document.createElement('option');
            opt.value = userId;
            opt.textContent = userMap[userId];
            queueAssignee.appendChild(opt);
        });
        queueAssignee.value = current;
        if (queueAssignee.value !== current) queueAssignee.value = '';
    }

    // Pestañas de vista de la cola: [vista, título, contador de /nav/counts, tono del contador]
    // Pestañas de la sección del menú (Mis tickets, Todos); las demás secciones no tienen
    function queueViewTabs() {
        return (activeSection && activeSection.tabs) || [];
    }

    const queueViewsBox = document.getElementById('queueViews');

    function renderQueueViews() {
        if (!isStaffShell()) return;
        queueViewsBox.replaceChildren();
        queueViewsBox.hidden = !queueViewTabs().length;
        queueViewTabs().forEach(([view, label, countKey, tone]) => {
            // Con búsqueda o filtro de cliente ninguna pestaña queda marcada
            const selected = view === queueView && !queueSearch && !queueRequester;
            const tab = makeEl('button', 'ui-tab', label);
            tab.type = 'button';
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', selected ? 'true' : 'false');
            tab.dataset.view = view;
            tab.dataset.label = label;
            const value = navCounts[countKey];
            if (typeof value === 'number') {
                const count = makeEl('span', `ui-tab-count${tone && value > 0 ? ` is-${tone}` : ''}`, String(value));
                tab.appendChild(count);
            }
            queueViewsBox.appendChild(tab);
        });
    }

    queueViewsBox.addEventListener('click', (e) => {
        const tab = e.target.closest('[data-view]');
        if (!tab) return;
        // La pestaña cambia la vista dentro de la misma sección del menú
        setQueueView(tab.dataset.view, tab.dataset.label);
    });

    function setQueueView(view, label, search = '', requester = null) {
        exitTicketPage();
        queueView = view;
        queueSearch = search;
        queueRequester = requester;
        queuePageNum = 1;
        queueTitle.textContent = label;
        renderQueueRequester();
        renderQueueViews();
        showView('tickets');
        setDocumentTitle(`${label} · Tickets`);
        loadQueue();
    }

    const queueRequesterBox = document.getElementById('queueRequester');

    function renderQueueRequester() {
        queueRequesterBox.hidden = !queueRequester;
        document.getElementById('queueRequesterName').textContent = queueRequester ? `Cliente: ${queueRequester.name}` : '';
    }

    document.getElementById('queueRequesterClear').addEventListener('click', () => {
        queueRequester = null;
        queuePageNum = 1;
        queueTitle.textContent = 'Todos los tickets';
        renderQueueRequester();
        renderQueueViews();
        loadQueue();
    });

    function queueParams() {
        const params = new URLSearchParams({ view: queueView, page: String(queuePageNum) });
        if (queueStatus.value) params.set('status', queueStatus.value);
        if (queueLevel.value) params.set('level', queueLevel.value);
        if (queueSource.value) params.set('source', queueSource.value);
        // El técnico no filtra por responsable
        if (queueAssignee.value && currentUser.role !== 'technician') params.set('assigned_to', queueAssignee.value);
        if (queueSearch) params.set('search', queueSearch);
        if (queueRequester) params.set('requester_id', String(queueRequester.id));
        return params;
    }

    function queueFiltersSet() {
        return [queueStatus, queueLevel, queueSource].some(select => select.value)
            || (queueAssignee.value && currentUser.role !== 'technician');
    }

    // Filtros como chips: punteados sin valor, azules con valor
    function renderQueueChips() {
        [queueStatus, queueLevel, queueAssignee, queueSource].forEach(select => {
            select.classList.toggle('is-set', Boolean(select.value));
        });
    }

    function clearQueueFilters() {
        [queueStatus, queueLevel, queueAssignee, queueSource].forEach(select => { select.value = ''; });
        renderQueueChips();
        queuePageNum = 1;
        loadQueue();
    }

    const queueNewBanner = document.getElementById('queueNewBanner');

    // quiet: actualización automática, sin tocar los mensajes en pantalla.
    // Si llegaron tickets nuevos, la lista no se mueve: aparece el aviso "Mostrar".
    async function loadQueue({ quiet = false } = {}) {
        if (!isStaffShell()) return;
        queueAssignee.hidden = currentUser.role === 'technician';
        if (queueAssignee.options.length <= 2) fillAssigneeFilter();
        renderQueueChips();
        const seq = ++queueSeq;
        if (!quiet) {
            queueNewBanner.hidden = true;
            queueEmpty.hidden = true;
            showSkeleton(queueBody, { rows: 6, columns: 5 });
        }
        let data;
        try {
            data = quiet ? await silentGet(`/queue?${queueParams()}`) : await apiFetch(`/queue?${queueParams()}`, { silent: true });
        } catch (err) {
            if (quiet || seq !== queueSeq) return;
            clearSkeleton(queueBody);
            queueEmpty.replaceChildren(errorState(err, () => loadQueue()));
            queueEmpty.hidden = false;
            return;
        }
        if (!data || seq !== queueSeq) return;
        if (quiet) {
            const shown = queueIds();
            const arrived = data.items.filter(item => !shown.includes(item.id)).length;
            if (shown.length && arrived > 0) {
                document.getElementById('queueNewText').textContent = arrived === 1
                    ? '1 ticket nuevo llegó desde que abrió esta vista. La lista no se movió.'
                    : `${arrived} tickets nuevos llegaron desde que abrió esta vista. La lista no se movió.`;
                queueNewBanner.hidden = false;
                return;
            }
        }
        queuePageNum = data.page;
        queueTotal.textContent = `${data.total} ${data.total === 1 ? 'ticket' : 'tickets'}`;
        queuePageLabel.textContent = `Página ${data.page} de ${data.pages}`;
        queuePrev.disabled = data.page <= 1;
        queueNext.disabled = data.page >= data.pages;
        queueLoadedAt = Date.now();
        renderQueueUpdated();
        clearSkeleton(queueBody);
        renderQueue(data.items);
        if (document.body.classList.contains('ticket-open')) renderTicketSteps();
    }

    document.getElementById('queueNewShow').addEventListener('click', () => loadQueue());

    function renderQueueUpdated() {
        if (queueLoadedAt === null) {
            queueUpdated.textContent = '';
            return;
        }
        const seconds = Math.max(0, Math.round((Date.now() - queueLoadedAt) / 1000));
        queueUpdated.textContent = seconds < 60
            ? `ACTUALIZADO HACE ${seconds} S`
            : `ACTUALIZADO HACE ${Math.floor(seconds / 60)} MIN`;
    }

    setInterval(() => {
        if (document.visibilityState === 'visible' && isStaffShell()) renderQueueUpdated();
    }, 5000);

    function queueCell(label, className) {
        const td = makeEl('td', className);
        td.dataset.label = label;
        return td;
    }

    // Qué pasó y qué puede hacer la persona cuando una vista no tiene tickets
    function queueEmptyState() {
        if (queueSearch) {
            return emptyState({
                title: `No hay tickets que coincidan con «${queueSearch}»`,
                text: 'Revise lo que escribió o busque por #número.',
                action: 'Quitar búsqueda',
                onAction: () => setQueueView(queueView === 'all' ? defaultQueueView() : queueView, defaultQueueLabel()),
            });
        }
        if (queueFiltersSet()) {
            return emptyState({ title: 'No hay tickets con estos filtros', text: 'Quite algún filtro para ver más tickets.',
                                action: 'Quitar filtros', onAction: clearQueueFilters });
        }
        if (queueRequester) {
            return emptyState({ title: `${queueRequester.name} no tiene tickets en esta vista` });
        }
        const toUnassigned = () => setQueueView('unassigned', 'Sin asignar');
        const messages = {
            open: ['No hay tickets abiertos', 'Cuando llegue uno nuevo aparecerá aquí.'],
            unassigned: ['Todos los tickets tienen responsable', 'No hay nada pendiente de asignar.'],
            due_soon: ['Ningún ticket está por vencer', 'Los tickets abiertos están dentro de su tiempo meta.'],
            overdue: ['No hay tickets vencidos', 'Todos los tickets abiertos están a tiempo.'],
            resolved_today: ['Todavía no se resolvió ningún ticket hoy', 'Los que se resuelvan hoy aparecerán aquí.'],
            mine_open: ['No tiene tickets abiertos asignados', 'Puede tomar uno de los que están sin asignar.'],
            mine_due_soon: ['Ninguno de sus tickets está por vencer', 'Sus tickets abiertos están a tiempo.'],
            following: ['No sigue ningún ticket abierto', 'Use "Seguir" en un ticket para tenerlo a mano aquí.'],
            mine_resolved_today: ['Todavía no resolvió tickets hoy', 'Los que resuelva hoy aparecerán aquí.'],
            cancelled: ['No hay tickets cancelados', 'Los tickets que se cancelen aparecen aquí y se pueden reabrir.'],
        };
        const [title, text] = messages[queueView] || ['No hay tickets en esta vista', ''];
        if (queueView === 'mine_open') return emptyState({ title, text, action: 'Ver sin asignar', onAction: toUnassigned });
        return emptyState({ title, text });
    }

    function defaultQueueView() {
        return currentUser.role === 'technician' ? 'mine_open' : 'open';
    }

    function defaultQueueLabel() {
        return currentUser.role === 'technician' ? 'Mis abiertos' : 'Todos abiertos';
    }

    // Nivel con color: 1 gris, 2 azul, 3 azul marino, 4 rojo
    function levelTag(level) {
        if (level === null || level === undefined) return makeEl('span', 'ui-tag is-mono', '—');
        const tone = { 2: ' is-info', 3: ' is-navy', 4: ' is-danger' }[level] || '';
        const tag = makeEl('span', `ui-tag is-mono${tone}`, `N${level}`);
        tag.title = AI_LEVEL_NAMES[level] ? `Nivel ${level} · ${AI_LEVEL_NAMES[level]}` : `Nivel ${level}`;
        return tag;
    }

    function ownerAvatar(name) {
        const avatar = makeEl('span', name ? 'owner-avatar' : 'owner-avatar is-unassigned', name ? getInitials(name) : '?');
        avatar.setAttribute('aria-hidden', 'true');
        return avatar;
    }

    function dueClass(state) {
        return { overdue: 'is-overdue', due_soon: 'is-soon' }[state] || '';
    }

    // Vencidos del coordinador: columna con la causa registrada (o "Pendiente de causa") y su reporte
    function queueShowsCause() {
        return queueView === 'overdue' && OPERATION_ROLES.includes(currentUser.role) && !queueSearch;
    }

    function renderQueue(items) {
        const withCause = queueShowsCause();
        document.getElementById('queueTable').classList.toggle('with-cause', withCause);
        const causeNotice = document.getElementById('queueCauseNotice');
        const missing = withCause ? items.filter(item => !item.overdue_cause).length : 0;
        causeNotice.hidden = !missing;
        causeNotice.textContent = missing
            ? `${missing} ${missing === 1 ? 'vencido sin causa registrada' : 'vencidos sin causa registrada'}. `
              + 'Registrarla ayuda a ver qué falla más seguido: falta de turnos, carga alta, fuera de horario…'
            : '';
        queueBody.replaceChildren();
        queueEmpty.hidden = items.length > 0;
        if (!items.length) queueEmpty.replaceChildren(queueEmptyState());
        const selectable = queueSelectable();
        // La selección solo guarda tickets de la página que se ve
        const ids = items.map(item => item.id);
        [...queueSelected].forEach(id => { if (!ids.includes(id)) queueSelected.delete(id); });
        items.forEach(item => {
            const tr = makeEl('tr', 'queue-row');
            tr.dataset.id = item.id;
            tr.tabIndex = 0;
            if (item.id === selectedTicketId) tr.classList.add('selected');
            // Cancelado hace un momento: sigue oculto mientras se puede deshacer
            if (pendingCancel && pendingCancel.ids.includes(item.id)) tr.hidden = true;

            const num = queueCell('ID', 'col-num ui-num');
            if (selectable) {
                const check = makeEl('input', 'queue-check');
                check.type = 'checkbox';
                check.checked = queueSelected.has(item.id);
                check.setAttribute('aria-label', `Seleccionar ticket #${item.id}`);
                num.appendChild(check);
            }
            num.appendChild(document.createTextNode(`#${item.id}`));

            const subject = queueCell('Asunto', 'col-subject');
            subject.appendChild(makeEl('div', 'queue-subject', item.title));
            const meta = makeEl('div', 'queue-meta', `${item.requester_name} · ${SOURCE_LABELS[item.source] || item.source}`);
            // El estado solo se marca cuando no es el habitual de la vista (abierto)
            if (item.cancelled) {
                meta.appendChild(makeEl('span', 'state-tag state-cancelled', STATE_MAP.cancelled));
            } else if (item.status === 'resolved' || item.status === 'closed') {
                meta.appendChild(makeEl('span', `state-tag state-${item.status}`, STATE_MAP[item.status]));
            }
            if (item.reported) meta.appendChild(makeEl('span', 'queue-flag-reported', 'Reportada'));
            if (item.auto_assigned) meta.appendChild(makeEl('span', 'queue-flag-auto', 'asignado automáticamente'));
            if (item.incident) meta.appendChild(makeEl('span', 'ui-tag is-danger queue-flag-incident', 'Incidente'));
            subject.appendChild(meta);

            const level = queueCell('Nivel', 'col-level');
            level.appendChild(levelTag(item.level));

            const owner = queueCell('Responsable', 'col-owner');
            const ownerBox = makeEl('div', 'queue-owner-box');
            ownerBox.appendChild(ownerAvatar(item.assigned_name));
            const ownerText = makeEl('div', 'queue-owner-text');
            ownerText.appendChild(item.assigned_name
                ? makeEl('span', 'queue-owner', item.assigned_name)
                : makeEl('span', 'queue-owner is-unassigned', 'Sin asignar'));
            if (item.assignee_note) ownerText.appendChild(makeEl('span', 'queue-note', ASSIGNEE_NOTES[item.assignee_note]));
            ownerBox.appendChild(ownerText);
            owner.appendChild(ownerBox);

            const due = queueCell('Vence', 'col-due');
            const dueLabel = makeEl('div', `ui-due ${dueClass(item.due_state)}`, dueText(item.due_state, item.remaining_seconds));
            if (item.due_at) dueLabel.title = `Vence el ${exactDateTime(item.due_at)}`;
            due.appendChild(dueLabel);
            if (item.due_state) {
                const bar = makeEl('span', `ui-bar ${dueClass(item.due_state)}`);
                bar.setAttribute('aria-hidden', 'true');
                const fill = makeEl('span');
                fill.style.width = `${item.consumed_pct || 0}%`;
                bar.appendChild(fill);
                due.appendChild(bar);
            }

            const cause = queueCell('Causa', 'col-cause');
            if (withCause) {
                cause.appendChild(makeEl('span', `ui-tag ${item.overdue_cause ? '' : 'is-warn'}`,
                    item.overdue_cause || 'Pendiente de causa'));
                const report = makeEl('button', 'ticket-bar-link queue-report-link', 'Ver reporte ›');
                report.type = 'button';
                report.dataset.ticketId = String(item.id);
                cause.appendChild(report);
            }

            tr.append(num, subject, level, owner, due, cause);
            queueBody.appendChild(tr);
        });
        renderQueueBulk();
    }

    async function openQueueTicket(ticketId) {
        if (isStaffShell() && !routing) {
            const hash = `#/${ticketRoute(ticketId)}`;
            if (window.location.hash !== hash) history.pushState(null, '', hash);
        }
        queueBody.querySelectorAll('.queue-row').forEach(row => {
            row.classList.toggle('selected', Number(row.dataset.id) === ticketId);
        });
        selectedTicketId = ticketId;
        activityFilter = 'public';
        enterTicketPage(ticketId);
        try {
            const ticket = await apiFetch(`/tickets/${ticketId}`);
            showTicketDetail(ticket);
        } catch (err) {
            exitTicketPage();
        }
    }

    // ----- Página del ticket (personal): con Volver y Anterior/Siguiente -----
    const ticketBar = document.getElementById('ticketBar');
    const ticketPrev = document.getElementById('ticketPrev');
    const ticketNext = document.getElementById('ticketNext');

    // Los tickets de la página de la cola que está cargada, en el mismo orden
    function queueIds() {
        return [...queueBody.querySelectorAll('.queue-row')].map(row => Number(row.dataset.id));
    }

    function renderTicketSteps() {
        const ids = queueIds();
        const index = ids.indexOf(selectedTicketId);
        ticketPrev.disabled = index <= 0;
        ticketNext.disabled = index === -1 || index >= ids.length - 1;
    }

    function enterTicketPage(ticketId) {
        commitPendingAssign();
        commitPendingCancel();
        document.querySelector('.split').classList.add('show-detail');
        if (!isStaffShell()) return;
        document.body.classList.add('ticket-open');
        ticketBar.hidden = false;
        composerMode = 'reply';
        document.getElementById('ticketCrumb').textContent = `#${ticketId}`;
        // Mi trabajo › Mis tickets › #198 (o la sección desde donde se abrió)
        const section = activeSection;
        const module = section ? moduleOf(section) : null;
        document.getElementById('ticketCrumbPath').textContent = section
            ? (module && module.sections.length > 1 ? `${module.label} › ${section.label}` : section.label) : 'Tickets';
        setDocumentTitle(`Ticket #${ticketId}`);
        renderTicketSteps();
        closeSideNav();
        window.scrollTo(0, 0);
        applyFabPos(false);
    }

    // Vuelve a la lista. Sin ticket abierto no hace nada.
    function exitTicketPage() {
        commitPendingAssign();
        commitPendingCancel();
        if (!document.body.classList.contains('ticket-open')) return;
        const closedId = selectedTicketId;
        document.body.classList.remove('ticket-open');
        ticketBar.hidden = true;
        document.querySelector('.split').classList.remove('show-detail');
        selectedTicketId = null;
        renderDetailPlaceholder();
        queueBody.querySelectorAll('.queue-row.selected').forEach(row => row.classList.remove('selected'));
        setDocumentTitle('Tickets');
        applyFabPos(false);
        return closedId;
    }

    document.getElementById('ticketBack').addEventListener('click', () => {
        const closedId = exitTicketPage();
        // La ruta vuelve a la sección
        if (activeSection) pushRoute(activeSection.route);
        // El foco vuelve a la fila del ticket que estaba abierto
        const row = queueBody.querySelector(`.queue-row[data-id="${closedId}"]`);
        if (row) row.focus();
    });

    document.getElementById('ticketHome').addEventListener('click', () => showStaffHome());

    ticketPrev.addEventListener('click', () => {
        const ids = queueIds();
        const index = ids.indexOf(selectedTicketId);
        if (index > 0) openQueueTicket(ids[index - 1]);
    });

    ticketNext.addEventListener('click', () => {
        const ids = queueIds();
        const index = ids.indexOf(selectedTicketId);
        if (index !== -1 && index < ids.length - 1) openQueueTicket(ids[index + 1]);
    });

    document.getElementById('queueNewTicket').addEventListener('click', () => openNewTicket.click());

    // ----- Inicio: saludo y resumen con los mismos contadores del menú -----
    const homeStats = document.getElementById('homeStats');

    function homeGreeting() {
        const hour = new Date().getHours();
        if (hour < 12) return 'Buenos días';
        if (hour < 19) return 'Buenas tardes';
        return 'Buenas noches';
    }

    // Inicio del coordinador. Se completan al cargar las listas: vencidos sin causa y técnicos en turno
    let homeOverdueNoCause = null;
    let homeOnShift = null;

    // Tarjetas del Inicio: [ruta, texto, valor, detalle, tono]
    function homeCards() {
        const value = (key) => (typeof navCounts[key] === 'number' ? navCounts[key] : null);
        const overdue = value('overdue');
        return [
            ['tickets/todos', 'Abiertos', value('open'), '', ''],
            ['tickets/sin-asignar', 'Sin asignar', value('unassigned'), homeOnShift === 0 ? 'nadie en turno' : '', 'warn'],
            ['tickets/vencidos', 'Vencidos', overdue,
             overdue && homeOverdueNoCause ? `${homeOverdueNoCause} sin causa registrada` : '', 'alert'],
            ['borradores/sin-revisor', 'Borradores sin revisor', value('drafts_unassigned'), '', 'warn'],
        ];
    }

    function renderHome() {
        if (!currentUser) return;
        sendSummaryNowBtn.hidden = !OPERATION_ROLES.includes(currentUser.role);
        homeStats.replaceChildren();
        homeCards().forEach(([route, label, value, detail, tone]) => {
            const card = makeEl('button', 'ui-card home-card');
            card.type = 'button';
            card.dataset.route = route;
            card.appendChild(makeEl('span', 'ui-stat-label', label));
            const valueRow = makeEl('span', 'home-card-value');
            const number = makeEl('b', `ui-stat-value${tone && value ? ` is-${tone}` : ''}`, value === null ? '—' : String(value));
            valueRow.appendChild(number);
            if (detail) valueRow.appendChild(makeEl('span', 'home-card-detail', detail));
            card.appendChild(valueRow);
            homeStats.appendChild(card);
        });
    }

    // ----- Inicio: listas "Requieren su atención" y "Equipo ahora" -----
    const homeAttention = document.getElementById('homeAttention');
    const homeTeam = document.getElementById('homeTeam');
    const homeTeamPanel = document.getElementById('homeTeamPanel');
    const homeIncidents = document.getElementById('homeIncidents');
    let homeSeq = 0;

    function homeRow(className) {
        const li = makeEl('li');
        const button = makeEl('button', `home-row ${className || ''}`);
        button.type = 'button';
        li.appendChild(button);
        return [li, button];
    }

    // Vencidos primero y luego por vencer (los del técnico, si es técnico), más los reportes de cuentas abiertos
    async function loadHomeLists() {
        const seq = ++homeSeq;
        const technician = currentUser.role === 'technician';
        const canManage = OPERATION_ROLES.includes(currentUser.role);
        showSkeleton(homeAttention, { rows: 3 });
        homeTeamPanel.hidden = !canManage;
        if (canManage) showSkeleton(homeTeam, { rows: 3 });
        let overdue;
        let dueSoon;
        let reports = [];
        let team = null;
        let incidents = [];
        let drafts = [];
        try {
            [overdue, dueSoon, reports, team, incidents, drafts] = await Promise.all([
                // El servidor le da al técnico solo sus vencidos
                apiFetch('/queue?view=overdue', { silent: true }),
                apiFetch(`/queue?view=${technician ? 'mine_due_soon' : 'due_soon'}`, { silent: true }),
                REPORT_REVIEW_ROLES.includes(currentUser.role)
                    ? apiFetch('/account-reports?status=open', { silent: true }) : Promise.resolve([]),
                canManage ? apiFetch('/team', { silent: true }) : Promise.resolve(null),
                // Si falla, Inicio sigue sin la franja
                canManage ? apiFetch('/incidents', { silent: true }).catch(() => []) : Promise.resolve([]),
                // Borradores que llegaron sin nadie en turno
                canManage ? apiFetch('/drafts?assigned=none', { silent: true }).catch(() => []) : Promise.resolve([]),
            ]);
        } catch (err) {
            if (seq !== homeSeq) return;
            renderHomeIncidents([]);
            homeAttention.removeAttribute('aria-busy');
            homeAttention.replaceChildren(errorState(err, loadHomeLists));
            if (canManage) {
                homeTeam.removeAttribute('aria-busy');
                homeTeam.replaceChildren();
            }
            return;
        }
        if (seq !== homeSeq) return;
        const seen = new Set();
        const urgent = [...overdue.items, ...dueSoon.items].filter(item => !seen.has(item.id) && seen.add(item.id));
        renderHomeAttention(urgent.slice(0, 6), reports.slice(0, 2), drafts.slice(0, 2));
        renderHomeIncidents(incidents);
        if (canManage) renderHomeTeam(team);
        // Detalle de las tarjetas: vencidos sin causa y si hay técnicos en turno
        homeOverdueNoCause = overdue.items.filter(item => !item.overdue_cause).length;
        homeOnShift = canManage ? (team || []).filter(m => m.role === 'technician' && m.is_active && m.on_shift).length : null;
        renderHome();
    }

    // Posibles incidentes (coordinador y admin): varios clientes con la misma falla
    function renderHomeIncidents(items) {
        homeIncidents.replaceChildren();
        homeIncidents.hidden = !items.length;
        items.forEach(item => {
            const row = makeEl('div', 'ui-notice is-danger home-incident');
            row.appendChild(makeEl('span', '', `Posible incidente: ${item.clients} clientes reportan «${item.title}»`));
            const open = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm home-incident-open', 'Ver');
            open.type = 'button';
            open.dataset.ticketId = String(item.principal_id);
            row.appendChild(open);
            homeIncidents.appendChild(row);
        });
    }

    function renderHomeAttention(items, reports, drafts = []) {
        homeAttention.removeAttribute('aria-busy');
        homeAttention.replaceChildren();
        if (!items.length && !reports.length && !drafts.length) {
            const li = makeEl('li');
            li.appendChild(emptyState({
                title: 'Nada urgente por ahora',
                text: 'No hay tickets vencidos ni por vencer. Revise la cola para ver todo lo abierto.',
            }));
            homeAttention.appendChild(li);
            return;
        }
        items.forEach(item => {
            const [li, button] = homeRow();
            button.dataset.ticketId = String(item.id);
            button.appendChild(makeEl('span', 'home-row-id ui-num', `#${item.id}`));
            const text = makeEl('span', 'home-row-text');
            text.appendChild(makeEl('span', 'home-row-title', item.title));
            const owner = item.assigned_name || 'sin asignar';
            const note = item.assignee_note ? ` · ${ASSIGNEE_NOTES[item.assignee_note].toLowerCase()}` : '';
            text.appendChild(makeEl('span', 'home-row-sub', `${item.requester_name} · ${owner}${note}`));
            button.appendChild(text);
            const tag = makeEl('span', `ui-tag ${item.due_state === 'overdue' ? 'is-danger' : 'is-warn'}`,
                dueText(item.due_state, item.remaining_seconds));
            if (item.due_at) tag.title = `Vence el ${exactDateTime(item.due_at)}`;
            button.appendChild(tag);
            homeAttention.appendChild(li);
        });
        drafts.forEach(draft => {
            const [li, button] = homeRow();
            button.dataset.draftId = String(draft.id);
            button.appendChild(makeEl('span', 'ui-tag is-warn home-row-tag', 'Sin revisor'));
            const text = makeEl('span', 'home-row-text');
            text.appendChild(makeEl('span', 'home-row-title', `Borrador · «${draft.title || draft.subject || 'Sin asunto'}»`));
            const sub = makeEl('span', 'home-row-sub', `${draft.requester_name} · llegó `);
            sub.appendChild(timeTag(draft.created_at));
            text.appendChild(sub);
            button.appendChild(text);
            homeAttention.appendChild(li);
        });
        reports.forEach(report => {
            const [li, button] = homeRow();
            button.dataset.reportAccount = String(report.target_user_id);
            button.dataset.reportName = report.target_name;
            button.appendChild(makeEl('span', 'home-row-id ui-num is-report', 'RPT'));
            const text = makeEl('span', 'home-row-text');
            text.appendChild(makeEl('span', 'home-row-title', `Cuenta reportada · ${report.target_name}`));
            const attempts = reportAttemptsText(report);
            text.appendChild(makeEl('span', 'home-row-sub',
                `${report.reason_label} · reportado por ${report.reporter}${attempts ? ` · ${attempts}` : ''}`));
            button.appendChild(text);
            button.appendChild(makeEl('span', 'ui-tag is-info', 'Revisar'));
            homeAttention.appendChild(li);
        });
    }

    // Técnicos activos: en turno, horario de hoy y una barra con su carga relativa
    function renderHomeTeam(team) {
        homeTeam.removeAttribute('aria-busy');
        homeTeam.replaceChildren();
        const technicians = (team || []).filter(member => member.role === 'technician' && member.is_active);
        const onShift = technicians.filter(member => member.on_shift).length;
        document.getElementById('homeTeamCount').textContent = `${onShift} de ${technicians.length} en turno`;
        if (!technicians.length) {
            const li = makeEl('li');
            li.appendChild(emptyState({ title: 'Todavía no hay técnicos', text: 'Invite técnicos desde Invitaciones.' }));
            homeTeam.appendChild(li);
            return;
        }
        // Carga de la asignación automática (tickets y borradores); sin ese dato, los tickets abiertos
        const loadOf = (member) => (typeof member.load === 'number' ? member.load : member.tickets_assigned_open);
        const maxLoad = Math.max(1, ...technicians.map(loadOf));
        technicians.forEach(member => {
            const li = makeEl('li', 'home-team-row');
            // Vacaciones o incapacidad: atenuado. Fuera de turno (Ausente automático) es lo normal fuera de horario
            const away = member.availability === 'vacation' || member.availability === 'incapacity';
            const avatar = makeEl('span', `owner-avatar home-team-avatar${away ? ' is-away' : ''}`, getInitials(member.name));
            avatar.setAttribute('aria-hidden', 'true');
            const text = makeEl('span', 'home-row-text');
            text.appendChild(makeEl('span', `home-row-title${away ? ' is-muted' : ''}`, member.name));
            let sub;
            if (away) {
                sub = AVAILABILITY_LABELS[member.availability]
                    + (member.return_date ? ` · regresa el ${formatPlainDate(member.return_date)}` : '');
            } else if (member.on_shift) {
                sub = member.schedule_today ? `En turno · ${member.schedule_today}` : 'En turno';
            } else {
                sub = member.schedule_today ? `Fuera de turno · hoy ${member.schedule_today}` : 'Sin turno hoy';
            }
            text.appendChild(makeEl('span', `home-row-sub${away ? ' is-warn' : ''}`, sub));
            li.append(avatar, text);
            if (!away) {
                const load = loadOf(member);
                const bar = makeEl('span', `ui-bar home-load${load / maxLoad >= 0.75 ? ' is-soon' : ''}`);
                bar.title = `Carga ${load}`;
                const fill = makeEl('span');
                fill.style.width = `${Math.round((load / maxLoad) * 100)}%`;
                bar.appendChild(fill);
                li.appendChild(bar);
                li.appendChild(makeEl('span', 'home-team-load ui-num', String(load)));
                li.appendChild(makeEl('span', 'sr-only', ` de carga (${member.tickets_assigned_open} tickets abiertos)`));
            }
            homeTeam.appendChild(li);
        });
    }

    homeIncidents.addEventListener('click', (e) => {
        const open = e.target.closest('.home-incident-open');
        if (open) openTicketById(Number(open.dataset.ticketId));
    });

    homeAttention.addEventListener('click', (e) => {
        const row = e.target.closest('.home-row');
        if (!row) return;
        if (row.dataset.ticketId) {
            openTicketById(Number(row.dataset.ticketId));
            return;
        }
        if (row.dataset.draftId) {
            openDraftById(Number(row.dataset.draftId));
            return;
        }
        if (row.dataset.reportAccount) {
            setSideNavActive('reports');
            openReportsForAccount(Number(row.dataset.reportAccount), row.dataset.reportName);
        }
    });

    document.getElementById('homeAttentionAll').addEventListener('click', () => goRoute('tickets/vencidos'));

    homeStats.addEventListener('click', (e) => {
        const card = e.target.closest('.home-card');
        if (card) goRoute(card.dataset.route);
    });


    queueBody.addEventListener('click', (e) => {
        // La casilla de Vencidos solo selecciona
        if (e.target.closest('.queue-check')) return;
        const report = e.target.closest('.queue-report-link');
        if (report) {
            openOverdueReport(Number(report.dataset.ticketId));
            return;
        }
        const row = e.target.closest('.queue-row');
        if (row) openQueueTicket(Number(row.dataset.id));
    });
    queueBody.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        if (e.target.closest('.queue-check, .queue-report-link')) return;
        const row = e.target.closest('.queue-row');
        if (!row) return;
        e.preventDefault();
        openQueueTicket(Number(row.dataset.id));
    });
    [queueStatus, queueLevel, queueAssignee, queueSource].forEach(select => {
        select.addEventListener('change', () => {
            queuePageNum = 1;
            renderQueueChips();
            loadQueue();
        });
    });
    queuePrev.addEventListener('click', () => {
        if (queuePageNum > 1) {
            queuePageNum -= 1;
            loadQueue();
        }
    });
    queueNext.addEventListener('click', () => {
        queuePageNum += 1;
        loadQueue();
    });

    // ----- Panel de detalle -----
    function staffField(list, label, content) {
        const box = makeEl('div', 'sd-field');
        box.appendChild(makeEl('dt', '', label));
        const dd = makeEl('dd');
        if (typeof content === 'string') dd.textContent = content;
        else dd.appendChild(content);
        box.appendChild(dd);
        list.appendChild(box);
        return dd;
    }

    function staffOption(value, text, selected) {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = text;
        if (selected) opt.selected = true;
        return opt;
    }

    function staffStatusControl(ticket) {
        const next = STATUS_TRANSITIONS[ticket.status] || [];
        if (ticket.status === 'closed' || next.length === 0) {
            return makeEl('span', `state-tag state-${ticket.status}`, STATE_MAP[ticket.status] || ticket.status);
        }
        const select = makeEl('select', 'sd-status-select');
        select.setAttribute('aria-label', 'Estado');
        select.appendChild(staffOption(ticket.status, STATE_MAP[ticket.status], true));
        next.forEach(status => select.appendChild(staffOption(status, STATE_MAP[status], false)));
        return select;
    }

    function staffLevelControl(ticket) {
        const ai = ticket.ai_analysis;
        // Sin análisis no hay nivel que corregir
        if (!ai || ticket.status === 'closed') return levelText(ai ? ai.effective_level : null);
        const select = makeEl('select', 'ai-level-select');
        select.setAttribute('aria-label', 'Nivel');
        if (ai.effective_level === null) select.appendChild(staffOption('', 'Sin nivel', true));
        Object.keys(AI_LEVEL_NAMES).forEach(key => {
            select.appendChild(staffOption(key, `${key} · ${AI_LEVEL_NAMES[key]}`, Number(key) === ai.effective_level));
        });
        return select;
    }

    function staffAssigneeControl(ticket) {
        const name = ticket.assigned_to !== null ? (userMap[ticket.assigned_to] || 'Sin nombre') : null;
        const canAssign = OPERATION_ROLES.includes(currentUser.role) && ticket.status !== 'closed';
        if (!canAssign) {
            // El técnico ve "Usted" en sus tickets
            const own = currentUser.role === 'technician' && ticket.assigned_to === currentUser.id;
            const text = makeEl('span', name ? '' : 'is-unassigned', own ? 'Usted' : (name || 'Sin asignar'));
            if (name && ticket.auto_assigned) text.textContent = `${name} · asignado automáticamente`;
            return text;
        }
        const select = makeEl('select', 'sd-assign-select');
        select.setAttribute('aria-label', 'Responsable');
        if (ticket.assigned_to === null) {
            const empty = staffOption('', 'Sin asignar', true);
            empty.disabled = true;
            select.appendChild(empty);
        }
        Object.keys(userMap).forEach(userId => {
            const role = userRoleMap[userId];
            if (role !== 'technician' && role !== 'coordinator') return;
            const availability = userAvailabilityMap[userId];
            // Se puede asignar a alguien no disponible, con confirmación del servidor
            const text = availability && availability !== 'available'
                ? `${userMap[userId]} — ${AVAILABILITY_LABELS[availability]}`
                : userMap[userId];
            select.appendChild(staffOption(userId, text, Number(userId) === ticket.assigned_to));
        });
        return select;
    }

    function staffDueContent(ticket) {
        const box = makeEl('div', 'sd-due');
        const dueAt = ticket.ai_analysis ? ticket.ai_analysis.due_at : null;
        const remaining = dueAt ? (parseServerDate(dueAt) - Date.now()) / 1000 : null;
        box.appendChild(makeEl('span', `due-text-${ticket.due_state || 'none'}`, dueText(ticket.due_state, remaining)));
        if (dueAt) box.appendChild(makeEl('span', 'sd-due-date', ` · ${formatDateTime(dueAt)}`));
        if (ticket.due_state) box.appendChild(dueBar(ticket.due_state, ticket.consumed_pct));
        return box;
    }

    async function loadSuggestion(ticketId, slot) {
        let found;
        try {
            found = await silentGet(`/tickets/${ticketId}/suggestion`);
        } catch (err) {
            return;
        }
        // Si mientras tanto se abrió otro ticket, no se muestra
        if (!found || ticketId !== selectedTicketId) return;
        const box = makeEl('section', 'ui-card td-card td-suggestion');
        box.appendChild(makeEl('h2', 'ui-kicker', 'Sugerencia'));
        const text = makeEl('p', 'td-suggestion-text', 'Asignar a ');
        text.appendChild(makeEl('strong', '', found.name));
        text.appendChild(document.createTextNode(`, que está en turno con carga ${found.load}.`));
        const apply = makeEl('button', 'ui-btn ui-btn-primary ui-btn-sm sd-apply-suggestion', 'Aplicar');
        apply.type = 'button';
        apply.dataset.userId = String(found.user_id);
        box.append(text, apply);
        slot.replaceChildren(box);
    }

    // Filtro de la línea de tiempo: conversación con el cliente (al abrir), notas internas o historial
    function staffTabs(ticket) {
        const comments = ticket.comments || [];
        const tabs = [
            ['public', 'Conversación', comments.filter(c => !c.internal).length],
            ['internal', 'Notas internas', comments.filter(c => c.internal).length],
            ['history', 'Historial', (ticket.history || []).length],
        ];
        if (!['public', 'internal', 'history'].includes(activityFilter)) activityFilter = 'public';
        const bar = makeEl('div', 'ui-tabs td-filter');
        bar.setAttribute('role', 'tablist');
        bar.setAttribute('aria-label', 'Mostrar en la actividad');
        tabs.forEach(([key, label, count]) => {
            const tab = makeEl('button', 'ui-tab sd-tab', label);
            tab.type = 'button';
            tab.dataset.filter = key;
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', activityFilter === key ? 'true' : 'false');
            tab.appendChild(makeEl('span', 'ui-tab-count', String(count)));
            bar.appendChild(tab);
        });
        return bar;
    }

    // Línea de tiempo en orden de llegada: mensajes como tarjetas (notas internas en ámbar) y cambios como líneas
    function renderStaffTimeline(ticket, container) {
        container.replaceChildren();
        const events = [];
        (ticket.comments || []).forEach(c => events.push({ type: 'comment', date: c.created_at, data: c }));
        (ticket.history || []).forEach(h => events.push({ type: 'history', date: h.changed_at, data: h }));
        events.sort((x, y) => parseServerDate(x.date) - parseServerDate(y.date));
        const shown = events.filter(ev => {
            if (activityFilter === 'history') return ev.type === 'history';
            if (activityFilter === 'public') return ev.type === 'comment' && !ev.data.internal;
            return ev.type === 'comment' && ev.data.internal;
        });
        if (!shown.length) {
            const texts = {
                public: ['Todavía no hay conversación con el cliente', 'Use "Responder al cliente" para escribirle.'],
                internal: ['No hay notas internas', 'Las notas internas solo las ve el equipo de soporte.'],
                history: ['Todavía no hay cambios', 'Aquí aparecen los cambios de estado y de responsable.'],
            };
            const [title, text] = texts[activityFilter];
            container.appendChild(emptyState({ title, text }));
            return;
        }
        const list = makeEl('ol', 'td-timeline');
        shown.forEach(ev => {
            const li = makeEl('li');
            if (ev.type === 'comment') {
                const comment = ev.data;
                const article = makeEl('article', comment.internal ? 'td-message is-internal' : 'td-message');
                const meta = makeEl('p', 'td-message-meta');
                meta.appendChild(makeEl('strong', '', userMap[comment.user_id] || 'Usuario'));
                const role = userRoleMap[comment.user_id];
                const who = comment.internal ? 'nota interna' : (role === 'requester' ? 'cliente' : 'soporte');
                meta.appendChild(makeEl('span', 'td-message-kind', ` · ${who} · `));
                meta.appendChild(timeTag(comment.created_at));
                article.appendChild(meta);
                // Un comentario puede traer solo adjuntos
                if (comment.content) article.appendChild(makeEl('p', 'td-message-body', comment.content));
                const files = attachmentList(comment.attachments);
                if (files) article.appendChild(files);
                li.appendChild(article);
            } else {
                const entry = ev.data;
                const name = entry.field === 'assigned_to' && userRoleMap[entry.changed_by] === 'agent'
                    ? 'Sistema'
                    : userMap[entry.changed_by] || 'Usuario';
                const line = makeEl('p', 'td-event');
                line.appendChild(makeEl('span', 'td-event-arrow', '⟶ '));
                appendHistoryText(line, entry, name);
                line.appendChild(document.createTextNode(' · '));
                line.appendChild(timeTag(entry.changed_at));
                li.appendChild(line);
            }
            list.appendChild(li);
        });
        container.appendChild(list);
    }

    // Compositor: pestañas "Responder al cliente" y "Nota interna" (la nota se ve distinta y no sale al cliente)
    let composerMode = 'reply';

    function staffComposer(ticket) {
        const box = makeEl('div', 'ui-card td-composer sd-composer');
        if (ticket.status === 'closed') {
            box.appendChild(makeEl('p', 'td-composer-closed', 'Este ticket está cerrado y ya no admite comentarios.'));
            return box;
        }
        const internal = composerMode === 'note';
        box.classList.toggle('is-internal', internal);
        const tabs = makeEl('div', 'ui-tabs-line');
        tabs.setAttribute('role', 'tablist');
        tabs.setAttribute('aria-label', 'Tipo de mensaje');
        [['reply', 'Responder al cliente'], ['note', 'Nota interna']].forEach(([mode, label]) => {
            const tab = makeEl('button', `ui-tab-line td-composer-tab${mode === 'note' ? ' is-note' : ''}`, label);
            tab.type = 'button';
            tab.dataset.mode = mode;
            tab.id = `composerTab-${mode}`;
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-controls', 'staffReply');
            tab.setAttribute('aria-selected', composerMode === mode ? 'true' : 'false');
            tabs.appendChild(tab);
        });
        const clientName = userMap[ticket.created_by] || 'el cliente';
        const label = makeEl('label', 'sr-only', internal ? 'Nota interna para el equipo' : `Respuesta para ${clientName}`);
        label.htmlFor = 'staffReply';
        const textarea = makeEl('textarea', 'td-reply sd-reply');
        textarea.id = 'staffReply';
        textarea.rows = 3;
        textarea.maxLength = 5000;
        textarea.placeholder = internal ? 'Escriba una nota para el equipo…' : `Escriba una respuesta para ${clientName}…`;
        textarea.setAttribute('aria-describedby', 'composerHint');
        const actions = makeEl('div', 'td-composer-actions sd-composer-actions');
        if (currentUser.role === 'technician' && !internal) {
            const ask = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm sd-ask-details', 'Pedir más detalles');
            ask.type = 'button';
            actions.appendChild(ask);
        }
        const hint = makeEl('span', 'td-composer-hint', internal
            ? 'Solo la ve el equipo de soporte. No se envía al cliente.'
            : 'Se envía al cliente por correo y queda en la conversación.');
        hint.id = 'composerHint';
        const send = makeEl('button', internal
            ? 'ui-btn ui-btn-sm td-send-note sd-send-internal'
            : 'ui-btn ui-btn-primary ui-btn-sm sd-send-public', internal ? 'Guardar nota' : 'Enviar respuesta');
        send.type = 'button';
        actions.append(hint, send);
        box.append(tabs, label, textarea, attachmentPicker(), actions);
        return box;
    }

    // ===== Adjuntos de los comentarios =====
    // Mismas reglas que app/attachments.py (solo comodidad: el servidor revisa el contenido real)
    const ATTACH_MAX_BYTES = 10 * 1024 * 1024;
    const ATTACH_MAX_FILES = 5;
    const ATTACH_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'zip'];
    const ATTACH_REJECTED = 'Este tipo de archivo no se puede adjuntar. Se permiten imágenes, documentos y .zip.';
    const ATTACH_ICONS = {
        pdf: '📄', doc: '📝', docx: '📝', xls: '📊', xlsx: '📊', csv: '📊', ppt: '📽️', pptx: '📽️',
        txt: '📃', zip: '🗜️', heic: '🖼️'
    };
    // Archivos elegidos para el comentario que se está escribiendo (se olvidan al cambiar de ticket)
    let pendingFiles = [];
    let pendingFilesTicket = null;
    let uploadingFiles = false;
    const attachmentPreviews = new Map();

    function fileExtension(name) {
        const dot = name.lastIndexOf('.');
        return dot === -1 ? '' : name.slice(dot + 1).toLowerCase();
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
    }

    function attachmentPicker() {
        if (pendingFilesTicket !== selectedTicketId) {
            pendingFiles = [];
            pendingFilesTicket = selectedTicketId;
        }
        const picker = makeEl('div', 'att-picker');
        const input = makeEl('input', 'att-input');
        input.type = 'file';
        input.multiple = true;
        input.hidden = true;
        input.accept = ATTACH_EXTS.map(ext => `.${ext}`).join(',');
        const add = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm att-add', '📎 Adjuntar');
        add.type = 'button';
        const limits = makeEl('span', 'att-limits',
            `Hasta ${ATTACH_MAX_FILES} archivos de 10 MB: imágenes, documentos y .zip. También puede arrastrarlos o pegar una captura.`);
        const list = makeEl('ul', 'att-pending');
        list.setAttribute('aria-label', 'Archivos para adjuntar');
        picker.append(input, add, limits, list);
        renderPendingFiles(list);
        return picker;
    }

    function renderPendingFiles(list = detailPanel.querySelector('.att-pending')) {
        if (!list) return;
        list.replaceChildren();
        pendingFiles.forEach((file, index) => {
            const li = makeEl('li', 'att-chip');
            li.dataset.index = String(index);
            li.appendChild(makeEl('span', 'att-chip-name', file.name));
            li.appendChild(makeEl('span', 'att-chip-size', formatFileSize(file.size)));
            const progress = makeEl('progress', 'att-progress');
            progress.max = 1;
            progress.value = 0;
            progress.hidden = !uploadingFiles;
            progress.setAttribute('aria-label', `Subiendo ${file.name}`);
            li.appendChild(progress);
            const remove = makeEl('button', 'att-remove', '✕');
            remove.type = 'button';
            remove.disabled = uploadingFiles;
            remove.setAttribute('aria-label', `Quitar ${file.name}`);
            li.appendChild(remove);
            list.appendChild(li);
        });
    }

    function pad2(n) {
        return String(n).padStart(2, '0');
    }

    function addPendingFiles(fileList, fromPaste = false) {
        if (uploadingFiles) return;
        for (let file of fileList) {
            if (pendingFiles.length >= ATTACH_MAX_FILES) {
                showError(`Puede adjuntar hasta ${ATTACH_MAX_FILES} archivos por comentario.`);
                break;
            }
            if (fromPaste && file.type.startsWith('image/')) {
                // Las capturas llegan como "image.png": se les da un nombre con la fecha
                const d = new Date();
                const stamp = `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
                const ext = fileExtension(file.name) || file.type.split('/')[1] || 'png';
                file = new File([file], `captura-${stamp}.${ext}`, { type: file.type });
            }
            if (!ATTACH_EXTS.includes(fileExtension(file.name))) {
                showError(ATTACH_REJECTED);
                continue;
            }
            if (file.size > ATTACH_MAX_BYTES) {
                showError(`«${file.name}» pesa más de 10 MB.`);
                continue;
            }
            pendingFiles.push(file);
        }
        renderPendingFiles();
    }

    // Reparte el avance total entre los archivos, en el orden en que viajan
    function showUploadProgress(fraction) {
        const total = pendingFiles.reduce((sum, f) => sum + f.size, 0) || 1;
        let start = 0;
        const done = fraction * total;
        detailPanel.querySelectorAll('.att-pending .att-progress').forEach((bar, index) => {
            const size = pendingFiles[index] ? pendingFiles[index].size : 0;
            bar.value = size ? Math.min(1, Math.max(0, (done - start) / size)) : 1;
            start += size;
        });
    }

    function uploadCommentWithFiles(content, internal) {
        return new Promise((resolve, reject) => {
            const form = new FormData();
            form.append('content', content);
            form.append('internal', internal ? 'true' : 'false');
            pendingFiles.forEach(file => form.append('files', file, file.name));
            const xhr = new XMLHttpRequest();
            xhr.open('POST', `${API_BASE}/tickets/${selectedTicketId}/comments/with-attachments`);
            xhr.setRequestHeader('Accept', 'application/json');
            if (authToken) xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);
            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable) showUploadProgress(e.loaded / e.total);
            });
            xhr.addEventListener('load', () => {
                let data = {};
                try { data = JSON.parse(xhr.responseText); } catch (err) { data = {}; }
                if (xhr.status === 401) {
                    handleUnauthorized(data.detail);
                    reject(new Error('Unauthorized'));
                } else if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(data);
                } else {
                    reject(new Error(getErrorMessage(data)));
                }
            });
            xhr.addEventListener('error', () => reject(new Error('No se pudieron subir los archivos. Revise su conexión.')));
            xhr.send(form);
        });
    }

    // Con archivos va por multipart con barra de progreso; sin archivos, igual que siempre. Devuelve true si se guardó.
    async function sendComment(content, internal) {
        if (!pendingFiles.length) {
            try {
                await apiFetch(`/tickets/${selectedTicketId}/comments`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(internal ? { content, internal } : { content })
                });
                return true;
            } catch (err) {
                return false; // apiFetch ya mostró el error
            }
        }
        uploadingFiles = true;
        renderPendingFiles();
        try {
            await uploadCommentWithFiles(content, internal);
            pendingFiles = [];
            return true;
        } catch (err) {
            if (err.message !== 'Unauthorized') showError(err.message);
            return false;
        } finally {
            uploadingFiles = false;
            renderPendingFiles();
        }
    }

    // url: /attachments/{id} (comentarios) o /drafts/attachments/{id} (borradores)
    async function fetchAttachment(url, preview) {
        const response = await fetch(`${API_BASE}${url}${preview ? '?preview=1' : ''}`, {
            headers: authToken ? { 'Authorization': `Bearer ${authToken}` } : {}
        });
        if (response.status === 401) {
            const data = await response.json().catch(() => ({}));
            handleUnauthorized(data.detail);
            throw new Error('Unauthorized');
        }
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(getErrorMessage(data));
        }
        return response.blob();
    }

    // Miniaturas y tarjetas debajo del texto del comentario (o del correo de un borrador)
    function attachmentList(files, urlBase = '/attachments') {
        if (!files || !files.length) return null;
        const list = makeEl('ul', 'att-list');
        files.forEach(file => {
            const li = makeEl('li');
            const isImage = file.inline && file.content_type.startsWith('image/');
            const button = makeEl('button', isImage ? 'att-thumb' : 'att-card');
            button.type = 'button';
            button.dataset.attUrl = `${urlBase}/${file.id}`;
            button.dataset.attName = file.original_name;
            button.dataset.attType = file.content_type;
            if (isImage) {
                button.setAttribute('aria-label', `Ver ${file.original_name} en grande`);
                const img = makeEl('img');
                img.alt = file.original_name;
                img.loading = 'lazy';
                button.appendChild(img);
                loadAttachmentPreview(button.dataset.attUrl, img);
            } else {
                button.setAttribute('aria-label', `Descargar ${file.original_name} (${formatFileSize(file.size)})`);
                button.appendChild(makeEl('span', 'att-card-icon', ATTACH_ICONS[fileExtension(file.original_name)] || '📎'));
                const text = makeEl('span', 'att-card-text');
                text.appendChild(makeEl('span', 'att-card-name', file.original_name));
                text.appendChild(makeEl('span', 'att-card-size', formatFileSize(file.size)));
                button.appendChild(text);
            }
            li.appendChild(button);
            list.appendChild(li);
        });
        return list;
    }

    async function loadAttachmentPreview(url, img) {
        try {
            if (!attachmentPreviews.has(url)) {
                attachmentPreviews.set(url, URL.createObjectURL(await fetchAttachment(url, true)));
            }
            img.src = attachmentPreviews.get(url);
        } catch (err) {
            img.closest('.att-thumb').classList.add('is-broken');
        }
    }

    const attachmentDialog = document.getElementById('attachmentDialog');
    const attachmentDialogImage = document.getElementById('attachmentDialogImage');

    async function openAttachmentImage(button) {
        try {
            // Sin preview: abrirla en grande queda en el Registro de cambios
            const url = URL.createObjectURL(await fetchAttachment(button.dataset.attUrl, false));
            document.getElementById('attachmentDialogTitle').textContent = button.dataset.attName;
            attachmentDialogImage.alt = button.dataset.attName;
            attachmentDialogImage.src = url;
            attachmentDialog.showModal();
        } catch (err) {
            if (err.message !== 'Unauthorized') showError(err.message);
        }
    }

    async function downloadAttachmentFile(button) {
        // El PDF se abre en una pestaña nueva; se abre ya (antes de esperar) para que el navegador no la bloquee
        const isPdf = button.dataset.attType === 'application/pdf';
        const tab = isPdf ? window.open('about:blank', '_blank') : null;
        try {
            const url = URL.createObjectURL(await fetchAttachment(button.dataset.attUrl, false));
            if (tab) {
                tab.location.href = url;
            } else {
                const link = makeEl('a');
                link.href = url;
                link.download = button.dataset.attName;
                document.body.appendChild(link);
                link.click();
                link.remove();
            }
            setTimeout(() => URL.revokeObjectURL(url), 60000);
        } catch (err) {
            if (tab) tab.close();
            if (err.message !== 'Unauthorized') showError(err.message);
        }
    }

    attachmentDialog.addEventListener('close', () => {
        URL.revokeObjectURL(attachmentDialogImage.src);
        attachmentDialogImage.removeAttribute('src');
    });
    document.getElementById('attachmentDialogClose').addEventListener('click', () => attachmentDialog.close());

    // Miniatura: se abre en grande; tarjeta: se descarga (o el PDF en otra pestaña)
    function openAttachmentFromClick(e) {
        const thumb = e.target.closest('.att-thumb');
        if (thumb) {
            openAttachmentImage(thumb);
            return;
        }
        const card = e.target.closest('.att-card');
        if (card) downloadAttachmentFile(card);
    }

    detailPanel.addEventListener('click', (e) => {
        if (e.target.closest('.att-add')) {
            detailPanel.querySelector('.att-input').click();
            return;
        }
        const remove = e.target.closest('.att-remove');
        if (remove) {
            pendingFiles.splice(Number(remove.closest('.att-chip').dataset.index), 1);
            renderPendingFiles();
            return;
        }
        openAttachmentFromClick(e);
    });
    draftContent.addEventListener('click', openAttachmentFromClick);

    detailPanel.addEventListener('change', (e) => {
        const input = e.target.closest('.att-input');
        if (!input) return;
        addPendingFiles(input.files);
        input.value = '';
    });

    detailPanel.addEventListener('dragover', (e) => {
        const composer = e.target.closest('.sd-composer');
        if (!composer || !e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        composer.classList.add('is-dragging');
    });

    detailPanel.addEventListener('dragleave', (e) => {
        const composer = e.target.closest('.sd-composer');
        if (composer && !composer.contains(e.relatedTarget)) composer.classList.remove('is-dragging');
    });

    detailPanel.addEventListener('drop', (e) => {
        const composer = e.target.closest('.sd-composer');
        if (!composer) return;
        e.preventDefault();
        composer.classList.remove('is-dragging');
        addPendingFiles(e.dataTransfer.files);
    });

    // Ctrl+V con una captura: solo se intercepta si el portapapeles trae archivos (el texto se pega normal)
    detailPanel.addEventListener('paste', (e) => {
        if (!e.target.closest('.sd-composer') || !e.clipboardData || !e.clipboardData.files.length) return;
        e.preventDefault();
        addPendingFiles(e.clipboardData.files, true);
    });

    function dueTagText(ticket) {
        const dueAt = ticket.ai_analysis ? ticket.ai_analysis.due_at : null;
        if (!dueAt || !ticket.due_state) return null;
        const remaining = (parseServerDate(dueAt) - Date.now()) / 1000;
        if (ticket.due_state === 'overdue') return `Vencido hace ${formatDuration(-remaining)}`;
        return `Vence en ${formatDuration(remaining)}`;
    }

    function renderStaffDetail(ticket) {
        detailContent.replaceChildren();
        detailPanel.scrollTop = 0;
        const isClosed = ticket.status === 'closed';
        const canManage = OPERATION_ROLES.includes(currentUser.role);
        // Técnico que lo tuvo antes o administrador con "Ver conversación": solo lectura
        const readOnly = Boolean(ticket.read_only);
        const clientName = userMap[ticket.created_by] || 'Solicitante';

        // ----- Encabezado: título, seguir, estado y reasignar -----
        const head = makeEl('header', 'td-head');
        const titleRow = makeEl('div', 'td-title-row');
        titleRow.appendChild(makeEl('h1', 'td-title', ticket.title));
        const followBox = makeEl('div', 'follow-box');
        const followBtn = makeEl('button', 'follow-btn');
        followBtn.type = 'button';
        const followHint = makeEl('p', 'follow-hint');
        followBox.append(followBtn, followHint);
        renderFollowButton(followBtn, followHint, Boolean(ticket.following));
        if (currentUser.role !== 'admin') titleRow.appendChild(followBox);
        const status = staffStatusControl(ticket);
        if (!readOnly && status.tagName === 'SELECT') {
            const statusLabel = makeEl('label', 'td-status-label', 'Cambiar estado');
            statusLabel.appendChild(status);
            titleRow.appendChild(statusLabel);
        }
        if (canManage && !isClosed) {
            const reassign = makeEl('button', 'ui-btn ui-btn-primary td-reassign', 'Reasignar');
            reassign.type = 'button';
            titleRow.appendChild(reassign);
        }
        if (canCancelTickets() && !isClosed) {
            const cancel = makeEl('button', 'ui-btn ui-btn-secondary td-cancel', 'Cancelar ticket');
            cancel.type = 'button';
            titleRow.appendChild(cancel);
        }
        head.appendChild(titleRow);
        // Ticket #198 · a su cargo (técnico) o · solo lectura
        const own = currentUser.role === 'technician' && ticket.assigned_to === currentUser.id;
        head.appendChild(makeEl('p', 'td-subtitle',
            `Ticket #${ticket.id}${own ? ' · a su cargo' : (readOnly ? ' · solo lectura' : '')}`));

        const tags = makeEl('p', 'td-tags');
        const stateKey = ticket.cancellation ? 'cancelled' : ticket.status;
        tags.appendChild(makeEl('span', `state-tag state-${stateKey}`, STATE_MAP[stateKey] || ticket.status));
        const dueTag = dueTagText(ticket);
        if (dueTag) {
            tags.appendChild(makeEl('span', `ui-tag ${ticket.due_state === 'overdue' ? 'is-danger' : (ticket.due_state === 'due_soon' ? 'is-warn' : 'is-info')}`, dueTag));
        }
        // Mismos permisos que el servidor: coordinador, admin o el técnico responsable
        if (ticket.due_state === 'overdue' && (canManage || ticket.assigned_to === currentUser.id)) {
            const report = makeEl('button', 'ticket-bar-link td-overdue-report', 'Ver reporte de vencimiento');
            report.type = 'button';
            tags.appendChild(report);
        }
        const level = ticket.ai_analysis ? ticket.ai_analysis.effective_level : null;
        const meta = makeEl('span', 'td-tags-meta',
            ` · ${levelText(level)} · llegó por ${(SOURCE_LABELS[ticket.source] || ticket.source).toLowerCase()} · `);
        tags.appendChild(meta);
        tags.appendChild(timeTag(ticket.created_at));
        head.appendChild(tags);
        detailContent.appendChild(head);

        const grid = makeEl('div', 'td-grid');
        const main = makeEl('section', 'td-main');
        main.setAttribute('aria-label', 'Actividad');
        const side = makeEl('aside', 'td-side');
        side.setAttribute('aria-label', 'Datos del ticket');
        grid.append(main, side);
        detailContent.appendChild(grid);

        // ----- Columna principal: mensaje original, actividad y compositor -----
        const original = makeEl('article', 'td-message is-original');
        const originalMeta = makeEl('p', 'td-message-meta');
        originalMeta.appendChild(makeEl('strong', '', clientName));
        originalMeta.appendChild(makeEl('span', 'td-message-kind', ' · cliente · '));
        originalMeta.appendChild(timeTag(ticket.created_at));
        original.append(originalMeta, makeEl('p', 'td-message-body', ticket.description));
        main.appendChild(original);
        main.appendChild(staffTabs(ticket));
        const events = makeEl('div', 'td-events sd-events');
        main.appendChild(events);
        renderStaffTimeline(ticket, events);
        if (readOnly) {
            const box = makeEl('div', 'ui-card td-composer sd-composer');
            box.appendChild(makeEl('p', 'td-composer-closed', currentUser.role === 'admin'
                ? 'Está viendo la conversación en solo lectura. Este acceso quedó en el Registro de cambios.'
                : 'Este ticket ya no está asignado a usted: puede verlo, pero no comentarlo ni cambiarlo.'));
            main.appendChild(box);
        } else {
            main.appendChild(staffComposer(ticket));
        }

        // ----- Columna derecha -----

        // Solo el personal recibe cancellation
        if (ticket.cancellation) {
            const info = ticket.cancellation;
            const notice = makeEl('div', 'ui-notice sd-notice td-cancelled');
            const text = makeEl('div', 'td-cancelled-text');
            text.appendChild(makeEl('strong', '', `Cancelado · ${info.reason_label}`));
            text.appendChild(makeEl('span', 'td-cancelled-by',
                `Por ${info.cancelled_by_name || 'Usuario'} el ${exactDateTime(info.cancelled_at)}`));
            if (info.note) text.appendChild(makeEl('span', 'td-cancelled-note', info.note));
            notice.appendChild(text);
            if (canCancelTickets()) {
                const reopen = makeEl('button', 'ui-btn ui-btn-primary ui-btn-sm td-reopen', 'Reabrir');
                reopen.type = 'button';
                notice.appendChild(reopen);
            }
            side.appendChild(notice);
        }

        const groupCard = groupingCard(ticket.grouping);
        if (groupCard) side.appendChild(groupCard);

        const props = makeEl('section', 'ui-card td-card');
        props.appendChild(makeEl('h2', 'ui-kicker', 'Propiedades'));
        const fields = makeEl('dl', 'td-props');
        staffField(fields, 'Responsable', staffAssigneeControl(ticket));
        staffField(fields, 'Nivel', readOnly
            ? levelText(ticket.ai_analysis ? ticket.ai_analysis.effective_level : null) : staffLevelControl(ticket));
        staffField(fields, 'Canal', SOURCE_LABELS[ticket.source] || ticket.source);
        staffField(fields, 'Vence', staffDueContent(ticket));
        staffField(fields, 'Prioridad', priorityMap[ticket.priority_id] || '');
        staffField(fields, 'Categoría', categoryMap[ticket.category_id] || '');
        if (ticket.contact_phone) staffField(fields, 'Teléfono', ticket.contact_phone);
        if (ticket.contact_id_number) staffField(fields, 'Cédula', ticket.contact_id_number);
        props.appendChild(fields);
        // El técnico no reasigna: se lo pide a su coordinador (el servidor tampoco lo permite)
        if (currentUser.role === 'technician' && !readOnly && !isClosed) {
            props.appendChild(makeEl('p', 'td-props-note', 'Para cambiar de responsable, pídaselo a su coordinador.'));
        }
        side.appendChild(props);

        const suggestionSlot = makeEl('div', 'sd-suggestion-slot');
        side.appendChild(suggestionSlot);
        if (canManage && !isClosed) loadSuggestion(ticket.id, suggestionSlot);

        const client = makeEl('section', 'ui-card td-card td-client');
        client.appendChild(makeEl('h2', 'ui-kicker', 'Cliente'));
        const who = makeEl('div', 'td-client-row');
        const avatar = makeEl('span', 'owner-avatar td-client-avatar', getInitials(clientName));
        avatar.setAttribute('aria-hidden', 'true');
        who.append(avatar, makeEl('span', 'td-client-name sd-client-name', clientName));
        client.appendChild(who);
        // Solo llega al personal: el solicitante nunca recibe requester_flags
        if (ticket.requester_flags) {
            const flags = makeEl('div', 'td-client-flags');
            requesterTags(ticket.requester_flags).forEach(tag => flags.appendChild(tag));
            client.appendChild(flags);
        }
        const links = makeEl('div', 'td-client-links');
        if (canManage) {
            const tickets = makeEl('button', 'ticket-bar-link td-client-tickets', 'Ver sus tickets');
            tickets.type = 'button';
            const account = makeEl('button', 'ticket-bar-link td-client-account', 'Ver ficha del cliente →');
            account.type = 'button';
            links.append(tickets, account);
        }
        if (ticket.requester_flags) {
            const report = makeEl('button', 'ticket-bar-link btn-report-account', 'Reportar cuenta');
            report.type = 'button';
            links.appendChild(report);
        }
        if (links.children.length) client.appendChild(links);
        side.appendChild(client);

        if (ticket.ai_analysis) {
            const card = renderAiCard(ticket);
            // El nivel se corrige en Propiedades
            const correct = card.querySelector('.ai-correct');
            if (correct) correct.remove();
            card.classList.add('td-card');
            side.appendChild(card);
        }
    }

    // Fusiones y tickets relacionados. "Separar" solo sale si el servidor lo permite (can_separate)
    function groupingCard(grouping) {
        if (!grouping) return null;
        const rows = [];
        const groupRow = (label, item, kind) => {
            const li = makeEl('li', 'td-group-row');
            li.appendChild(document.createTextNode(label));
            if (item.ticket_id) {
                const open = makeEl('button', 'ticket-bar-link td-group-open', `#${item.ticket_id}`);
                open.type = 'button';
                open.dataset.ticketId = String(item.ticket_id);
                li.appendChild(open);
                if (item.title) li.appendChild(makeEl('span', 'td-group-title', item.title));
            }
            if (item.status) li.appendChild(makeEl('span', `state-tag state-${item.status}`, STATE_MAP[item.status] || item.status));
            if (item.can_separate) {
                const separate = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm td-group-separate', 'Separar');
                separate.type = 'button';
                separate.dataset.kind = kind;
                separate.dataset.id = String(kind === 'merge' ? item.merge_id : item.link_id);
                li.appendChild(separate);
            }
            rows.push(li);
        };
        if (grouping.merged_into) groupRow('Fusionado en ', grouping.merged_into, 'merge');
        grouping.merged_from.forEach(item => groupRow(item.ticket_id ? 'Fusionado desde ' : 'Fusionado desde un correo del cliente', item, 'merge'));
        if (grouping.principal) groupRow('Relacionado con ', grouping.principal, 'link');
        grouping.related.forEach(item => groupRow('', item, 'link'));
        if (!rows.length) return null;
        const card = makeEl('section', `ui-card td-card td-group${grouping.incident ? ' is-incident' : ''}`);
        const count = grouping.related.length;
        const title = grouping.incident
            ? `Incidente · ${count} tickets relacionados`
            : (count ? `${count} ticket${count === 1 ? '' : 's'} relacionado${count === 1 ? '' : 's'}` : 'Agrupación');
        card.appendChild(makeEl('h2', 'ui-kicker', title));
        const list = makeEl('ul', 'td-group-list');
        rows.forEach(li => list.appendChild(li));
        card.appendChild(list);
        return card;
    }

    detailPanel.addEventListener('click', async (e) => {
        if (!selectedTicketId || !currentTicket || !isStaffShell()) return;

        const groupOpen = e.target.closest('.td-group-open');
        if (groupOpen) {
            openTicketById(Number(groupOpen.dataset.ticketId));
            return;
        }
        const separate = e.target.closest('.td-group-separate');
        if (separate) {
            separate.disabled = true;
            const path = separate.dataset.kind === 'merge' ? 'merges' : 'links';
            try {
                await apiFetch(`/${path}/${separate.dataset.id}/undo`, { method: 'POST' });
                toast('Tickets separados.');
                reloadSelectedTicketAndList();
                pollNavCounts(false);
            } catch (err) {
                separate.disabled = false; // apiFetch ya mostró el error
            }
            return;
        }

        if (e.target.closest('.td-cancel')) {
            startCancel([currentTicket.id]);
            return;
        }
        if (e.target.closest('.td-overdue-report')) {
            openOverdueReport(currentTicket.id);
            return;
        }
        const reopen = e.target.closest('.td-reopen');
        if (reopen) {
            reopen.disabled = true;
            try {
                const ticket = await apiFetch(`/tickets/${currentTicket.id}/reopen`, { method: 'POST' });
                toast(`Ticket #${ticket.id} reabierto: quedó sin asignar.`);
                reloadSelectedTicketAndList();
                pollNavCounts(false);
            } catch (err) {
                reopen.disabled = false; // apiFetch ya mostró el error
            }
            return;
        }

        const tab = e.target.closest('.sd-tab');
        if (tab) {
            activityFilter = tab.dataset.filter;
            detailPanel.querySelectorAll('.sd-tab').forEach(t => {
                t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
            });
            renderStaffTimeline(currentTicket, detailPanel.querySelector('.sd-events'));
            return;
        }

        const modeTab = e.target.closest('.td-composer-tab');
        if (modeTab) {
            if (modeTab.dataset.mode === composerMode) return;
            // El texto escrito se conserva al cambiar de pestaña
            const draft = document.getElementById('staffReply').value;
            composerMode = modeTab.dataset.mode;
            const composer = staffComposer(currentTicket);
            detailPanel.querySelector('.td-composer').replaceWith(composer);
            const textarea = document.getElementById('staffReply');
            textarea.value = draft;
            document.getElementById(`composerTab-${composerMode}`).focus();
            return;
        }

        if (e.target.closest('.td-reassign')) {
            const select = detailPanel.querySelector('.sd-assign-select');
            if (select) {
                select.scrollIntoView({ block: 'center' });
                select.focus();
            }
            return;
        }

        if (e.target.closest('.td-client-tickets')) {
            const name = userMap[currentTicket.created_by] || 'Cliente';
            setQueueView('all', `Tickets de ${name}`, '', { id: currentTicket.created_by, name });
            return;
        }

        if (e.target.closest('.td-client-account')) {
            setSideNavActive('accounts');
            openAccounts('clients', userMap[currentTicket.created_by] || '');
            return;
        }

        const ask = e.target.closest('.sd-ask-details');
        if (ask) {
            // Texto base editable: se envía como una respuesta normal al cliente
            const textarea = document.getElementById('staffReply');
            if (!textarea.value.startsWith(ASK_DETAILS_TEXT)) textarea.value = ASK_DETAILS_TEXT + textarea.value;
            textarea.focus();
            textarea.setSelectionRange(textarea.value.length, textarea.value.length);
            return;
        }

        const send = e.target.closest('.sd-send-internal, .sd-send-public');
        if (send) {
            const internal = send.classList.contains('sd-send-internal');
            const textarea = document.getElementById('staffReply');
            const content = textarea.value.trim();
            if (!pendingFiles.length && (!content || content === ASK_DETAILS_TEXT.trim())) {
                showError('Escriba el mensaje antes de enviarlo');
                return;
            }
            const buttons = detailPanel.querySelectorAll('.sd-composer button');
            buttons.forEach(b => { b.disabled = true; });
            if (!(await sendComment(content, internal))) {
                buttons.forEach(b => { b.disabled = false; });
                return;
            }
            // Se ve en su pestaña lo que se acaba de enviar
            activityFilter = internal ? 'internal' : 'public';
            await reloadSelectedTicketAndList();
            showSuccess(internal ? 'Nota interna guardada' : 'Respuesta enviada al cliente');
            return;
        }

        const apply = e.target.closest('.sd-apply-suggestion');
        if (apply) {
            const userId = Number(apply.dataset.userId);
            if (!scheduleReassign(userId)) return;
            // Se ve el cambio de inmediato; el servidor lo recibe al terminar el plazo para deshacer
            const select = detailPanel.querySelector('.sd-assign-select');
            if (select) select.value = String(userId);
            apply.closest('.td-suggestion').remove();
        }
    });

    detailPanel.addEventListener('change', async (e) => {
        if (!selectedTicketId || !currentTicket || !isStaffShell()) return;

        const statusSelect = e.target.closest('.sd-status-select');
        if (statusSelect) {
            if (statusSelect.value === currentTicket.status) return;
            statusSelect.disabled = true;
            try {
                await apiFetch(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: statusSelect.value })
                });
            } catch (err) {
                statusSelect.value = currentTicket.status;
                statusSelect.disabled = false;
                return; // apiFetch ya mostró el error
            }
            await reloadSelectedTicketAndList();
            showSuccess('Estado actualizado');
            return;
        }

        const assignSelect = e.target.closest('.sd-assign-select');
        if (assignSelect && assignSelect.value) {
            if (Number(assignSelect.value) === currentTicket.assigned_to) return;
            if (!scheduleReassign(Number(assignSelect.value))) {
                assignSelect.value = currentTicket.assigned_to === null ? '' : String(currentTicket.assigned_to);
            }
        }
    });

    // ===== Cuentas: una sola lista con filtros rápidos (administrador y coordinador) =====
    // El servidor decide qué cuentas llegan y qué acciones tiene cada una (y las vuelve a revisar al hacerlas)
    const aaBody = document.getElementById('aaBody');
    const aaEmpty = document.getElementById('aaEmpty');
    const aaSearch = document.getElementById('aaSearch');
    const aaQuick = document.getElementById('aaQuick');
    const aaDetail = document.getElementById('aaDetail');
    const accountMenu = document.getElementById('accountMenu');
    const ACCOUNT_STATE_LABELS = {
        active: 'Activa', locked: 'Bloqueada', inactive: 'Desactivada',
        vacation: 'Vacaciones', incapacity: 'Ausencia', away: 'Ausente', invited: 'Invitación pendiente'
    };
    const ACCOUNT_TYPE_LABELS = {
        requester: 'Cliente', technician: 'Técnico', coordinator: 'Coordinador', admin: 'Administrador'
    };
    // Grupos del menú "Acciones ▾", en este orden; la zona delicada va separada y en rojo
    const ACCOUNT_MENU_GROUPS = [
        { title: 'Seguimiento', actions: ['view-tickets', 'report', 'review-reports', 'request-name', 'request-id_number', 'full-data'] },
        { title: 'Rol y turno', actions: ['change-role', 'schedule', 'availability'] },
        { title: 'Acceso', actions: ['unlock', 'close-sessions', 'reset-access'] },
        { title: 'Zona delicada', actions: ['deactivate', 'reactivate'], danger: true },
        { title: 'Invitación', actions: ['resend-invitation'] },
        { title: '', actions: ['cancel-invitation'], danger: true }
    ];
    const ACCOUNT_MENU_LABELS = {
        'view-tickets': 'Ver sus tickets', report: 'Reportar cuenta', 'review-reports': 'Revisar reportes',
        'request-name': 'Corregir nombre', 'request-id_number': 'Corregir cédula', 'full-data': 'Ver datos completos',
        'change-role': 'Cambiar rol…', schedule: 'Editar horario', availability: 'Vacaciones y ausencias',
        unlock: 'Desbloquear', 'close-sessions': 'Cerrar sesiones', 'reset-access': 'Forzar restablecimiento',
        deactivate: 'Desactivar', reactivate: 'Reactivar',
        'resend-invitation': 'Reenviar invitación', 'cancel-invitation': 'Cancelar invitación'
    };
    const aaScheduleOn = document.getElementById('aaScheduleOn');
    const aaScheduleFields = document.getElementById('aaScheduleFields');
    const aaDay = document.getElementById('aaDay');
    const aaStart = document.getElementById('aaStart');
    const aaEnd = document.getElementById('aaEnd');
    const aaMatch = document.getElementById('aaMatch');
    let aaRows = [];
    let aaFilter = 'all';
    let aaSelected = null;
    // Lo que muestra el panel: { kind: 'user', ...detalle } o { kind: 'invitation', ...fila }
    let aaCurrent = null;
    let aaSeq = 0;
    let aaSearchTimer = null;
    let accountMenuOwner = null;

    function aaPlaceholder() {
        aaCurrent = null;
        aaDetail.replaceChildren(emptyState({
            title: 'Elija una cuenta',
            text: 'Aquí verá su estado, sus tickets y las acciones que puede hacer.',
        }));
    }

    // Abre Cuentas con un filtro rápido y una búsqueda (desde el menú o desde la búsqueda global)
    function openAccounts(filter = 'all', search = '') {
        // Coordinador: Tickets › Clientes o Equipo › Personas ya eligen el filtro; invitar es del administrador
        aaQuick.hidden = OPERATION_ROLES.includes(currentUser.role);
        document.getElementById('aaInvite').hidden = currentUser.role !== 'admin';
        aaFilter = filter;
        aaSearch.value = search;
        aaSelected = null;
        aaPlaceholder();
        adminAccountsView.classList.remove('show-detail');
        showView('adminAccounts');
    }

    async function loadAdminAccounts() {
        const params = new URLSearchParams({ filter: aaFilter });
        if (aaSearch.value.trim()) params.set('q', aaSearch.value.trim());
        // Filtro por horario: lo resuelve el servidor (solo técnicos y coordinadores con turno ese día)
        if (aaScheduleOn.checked) {
            if (!aaStart.value || !aaEnd.value) {
                showError('Indique la hora de entrada y la de salida');
                return;
            }
            if (aaStart.value >= aaEnd.value) {
                showError('La hora de entrada debe ser anterior a la de salida');
                return;
            }
            params.set('schedule_day', aaDay.value);
            params.set('schedule_start', aaStart.value.slice(0, 5));
            params.set('schedule_end', aaEnd.value.slice(0, 5));
            params.set('schedule_match', aaMatch.value);
        }
        const seq = ++aaSeq;
        if (!aaRows.length) showSkeleton(aaBody, { rows: 6 });
        let data;
        try {
            data = await apiFetch(`/accounts?${params}`, { silent: true });
        } catch (err) {
            if (seq !== aaSeq) return;
            aaBody.removeAttribute('aria-busy');
            aaBody.replaceChildren();
            aaEmpty.replaceChildren(errorState(err, loadAdminAccounts));
            aaEmpty.hidden = false;
            return;
        }
        if (seq !== aaSeq) return;
        aaRows = data.items;
        renderAccountFilters(data.counts);
        renderAdminAccounts();
    }

    function renderAccountFilters(counts) {
        aaQuick.querySelectorAll('.aa-quick-btn').forEach(button => {
            button.setAttribute('aria-pressed', button.dataset.filter === aaFilter ? 'true' : 'false');
        });
        aaQuick.querySelectorAll('.aa-quick-count').forEach(el => {
            const value = counts[el.dataset.count] || 0;
            el.textContent = String(value);
            el.classList.toggle('is-warn', el.dataset.tone === 'warn' && value > 0);
            el.classList.toggle('is-alert', el.dataset.tone === 'alert' && value > 0);
        });
    }

    function accountKey(row) {
        return `${row.kind}:${row.id}`;
    }

    // Etiqueta corta junto al nombre en la lista: lo más urgente de la cuenta
    function accountFlag(row) {
        if (row.reported) return ['Reportada', 'is-danger'];
        if (row.state === 'locked') return ['Bloqueada', 'is-danger'];
        if (row.state === 'inactive') return ['Desactivada', ''];
        if (row.state === 'vacation' || row.state === 'away') return [ACCOUNT_STATE_LABELS[row.state], 'is-warn'];
        if (row.kind === 'invitation') return [`Invitación · ${row.invitation_hours_left} h`, 'is-info'];
        return null;
    }

    function renderAdminAccounts() {
        aaBody.removeAttribute('aria-busy');
        aaBody.replaceChildren();
        aaEmpty.hidden = aaRows.length > 0;
        if (!aaRows.length) {
            aaEmpty.replaceChildren(aaSearch.value.trim()
                ? emptyState({ title: `No hay cuentas que coincidan con «${aaSearch.value.trim()}»`, text: 'Revise el nombre o el correo.' })
                : emptyState({ title: 'No hay cuentas con este filtro', text: 'Elija otro filtro para ver más cuentas.' }));
        }
        aaRows.forEach(row => {
            const li = makeEl('li');
            const button = makeEl('button', 'queue-row aa-row');
            button.type = 'button';
            button.dataset.key = accountKey(row);
            const selected = button.dataset.key === aaSelected;
            button.classList.toggle('selected', selected);
            if (selected) button.setAttribute('aria-current', 'true');
            const avatar = makeEl('span', `aa-avatar${row.role === 'requester' ? ' is-client' : ''}`, getInitials(row.name));
            avatar.setAttribute('aria-hidden', 'true');
            const text = makeEl('span', 'aa-row-text');
            // El nombre completo siempre se ve (puede ocupar dos líneas); el correo ya viene enmascarado
            text.appendChild(makeEl('span', 'aa-row-name', row.name));
            let sub = `${ACCOUNT_TYPE_LABELS[row.role] || row.role} · ${row.email}`;
            if (row.kind === 'user' && row.open_tickets) sub += ` · ${row.open_tickets} abiertos`;
            if (row.schedule_day) sub += ` · Turno: ${row.schedule_day}`;
            text.appendChild(makeEl('span', 'aa-row-sub', sub));
            button.append(avatar, text);
            const flag = accountFlag(row);
            if (flag) button.appendChild(makeEl('span', `ui-tag ${flag[1]}`, flag[0]));
            li.appendChild(button);
            aaBody.appendChild(li);
        });
    }

    // Encabezado del panel: nombre, tipo · correo, etiqueta de estado y "Acciones ▾" (si hay alguna)
    function accountPanelHead(item, actions) {
        const head = makeEl('div', 'aa-panel-head');
        const avatar = makeEl('span', `aa-avatar aa-avatar-lg${item.role === 'requester' ? ' is-client' : ''}`, getInitials(item.name));
        avatar.setAttribute('aria-hidden', 'true');
        head.appendChild(avatar);
        const titles = makeEl('div', 'aa-panel-titles');
        titles.appendChild(makeEl('h2', 'aa-name', item.name));
        const tags = makeEl('p', 'aa-panel-meta');
        tags.appendChild(makeEl('span', '', `${ACCOUNT_TYPE_LABELS[item.role] || item.role} · ${item.email}`));
        tags.appendChild(makeEl('span', `state-tag acc-${item.state}`, ACCOUNT_STATE_LABELS[item.state] || item.state));
        titles.appendChild(tags);
        head.appendChild(titles);
        if (actions.length) {
            const button = makeEl('button', 'ui-btn ui-btn-primary aa-actions-btn', 'Acciones ▾');
            button.type = 'button';
            button.setAttribute('aria-haspopup', 'menu');
            button.setAttribute('aria-expanded', 'false');
            button.setAttribute('aria-controls', 'accountMenu');
            head.appendChild(button);
        }
        return { head, tags };
    }

    // Tarjetas de datos del panel: solo lo que da el servidor
    function accountStats(d, row) {
        const stats = makeEl('div', 'aa-stats');
        const add = (label, value) => {
            const card = makeEl('div', 'ui-card ui-stat aa-stat');
            card.append(makeEl('span', 'ui-stat-label', label), makeEl('span', 'aa-stat-value', value));
            stats.appendChild(card);
        };
        const open = row && row.kind === 'user' ? String(row.open_tickets) : '—';
        if (d.role === 'requester') {
            add('Abiertos', open);
            add('Identidad', IDENTITY_LABELS[d.identity] || '—');
            add('Reportes abiertos', String(d.alerts.open_reports || 0));
        } else {
            add('Asignados abiertos', open);
            const today = (new Date().getDay() + 6) % 7;
            const shift = (d.schedule || []).find(day => day.weekday === today);
            add('Turno hoy', shift ? `${shift.start}–${shift.end}` : '—');
            const availability = d.availability ? (AVAILABILITY_LABELS[d.availability] || d.availability) : '—';
            add('Disponibilidad', d.return_date && d.availability !== 'available'
                ? `${availability} hasta ${formatPlainDate(d.return_date)}` : availability);
        }
        return stats;
    }

    function accountMenuActions(item) {
        if (item.kind === 'invitation') return ['resend-invitation', 'cancel-invitation'];
        // Nunca sobre la propia cuenta ni sobre otro administrador (el servidor tampoco las permite)
        if (item.id === currentUser.id || item.role === 'admin') return [];
        return item.actions.filter(action => ACCOUNT_MENU_LABELS[action]);
    }

    function renderInvitationPanel(row) {
        closeAccountMenu(false);
        aaDetail.replaceChildren();
        const box = makeEl('div', 'aa-account');
        box.appendChild(accountPanelHead(row, accountMenuActions(row)).head);
        const alerts = makeEl('ul', 'aa-alerts');
        alerts.appendChild(makeEl('li', 'aa-alert', `La invitación vence en ${row.invitation_hours_left} h`));
        box.appendChild(alerts);
        aaDetail.appendChild(box);
    }

    function renderAccountPanel(d) {
        closeAccountMenu(false);
        aaDetail.replaceChildren();
        const box = makeEl('div', 'aa-account');
        const { head, tags } = accountPanelHead(d, accountMenuActions(d));
        if (d.role === 'requester') requesterTags({ identity: d.identity, reported: d.reported }).forEach(t => tags.appendChild(t));
        box.appendChild(head);

        const alerts = makeEl('ul', 'aa-alerts');
        if (d.alerts.open_reports > 0) {
            const li = makeEl('li', 'aa-alert');
            li.appendChild(makeEl('span', '', `${d.alerts.open_reports} ${d.alerts.open_reports === 1 ? 'reporte abierto' : 'reportes abiertos'}. Revíselos antes de atender sus tickets nuevos.`));
            const review = makeEl('button', 'ticket-bar-link aa-review-reports', 'Revisar reportes →');
            review.type = 'button';
            li.appendChild(review);
            alerts.appendChild(li);
        }
        if (d.alerts.locked_until) {
            alerts.appendChild(makeEl('li', 'aa-alert', `Bloqueada por intentos hasta ${formatDateTime(d.alerts.locked_until)}`));
        }
        if (d.alerts.pending_role) {
            const li = makeEl('li', 'aa-alert');
            const by = d.alerts.pending_role_by ? ` (lo pidió ${d.alerts.pending_role_by})` : '';
            li.appendChild(makeEl('span', '', `Cambio de rol pendiente a ${ROLE_MAP[d.alerts.pending_role] || d.alerts.pending_role}${by}`));
            // Solo el administrador la resuelve (nunca la que pidió él mismo)
            if (d.actions.includes('approve-role')) {
                ['approve', 'reject'].forEach(kind => {
                    const button = makeEl('button', `btn-secondary btn-small aa-${kind}`, kind === 'approve' ? 'Aprobar' : 'Rechazar');
                    button.type = 'button';
                    button.dataset.requestId = String(d.alerts.pending_role_request_id);
                    li.appendChild(button);
                });
            }
            alerts.appendChild(li);
        }
        if (alerts.children.length) box.appendChild(alerts);
        box.appendChild(accountStats(d, aaRows.find(r => r.kind === 'user' && r.id === d.id)));

        const section = makeEl('section', 'ui-card aa-section');
        const sectionHead = makeEl('div', 'ui-card-head');
        const admin = currentUser.role === 'admin';
        if (d.role === 'requester') {
            sectionHead.appendChild(makeEl('h3', 'ui-card-title', admin ? 'Tickets de esta cuenta' : 'Tickets del cliente'));
            if (d.actions.includes('view-tickets')) {
                const all = makeEl('button', 'ticket-bar-link aa-view-queue', 'Ver en la cola →');
                all.type = 'button';
                sectionHead.appendChild(all);
            }
            section.appendChild(sectionHead);
            if (admin) {
                section.appendChild(makeEl('p', 'aa-section-note',
                    'Las conversaciones son del equipo de soporte. Para abrir una necesita indicar el motivo.'));
            }
            if (d.recent_tickets.length === 0) {
                section.appendChild(emptyState({ title: 'Todavía no tiene tickets' }));
            } else {
                const list = makeEl('ul', 'home-list');
                d.recent_tickets.forEach(ticket => {
                    const li = makeEl('li');
                    const link = makeEl('button', 'home-row aa-ticket');
                    link.type = 'button';
                    link.dataset.ticketId = String(ticket.id);
                    link.append(makeEl('span', 'home-row-id ui-num', `#${ticket.id}`),
                        makeEl('span', 'home-row-title home-row-text', ticket.title),
                        makeEl('span', `state-tag state-${ticket.status}`, STATE_MAP[ticket.status] || ticket.status));
                    // El administrador abre la conversación con motivo (queda en el Registro de cambios)
                    if (admin) link.appendChild(makeEl('span', 'aa-ticket-go', `Ver conversación del #${ticket.id}…`));
                    li.appendChild(link);
                    list.appendChild(li);
                });
                section.appendChild(list);
            }
        } else {
            sectionHead.appendChild(makeEl('h3', 'ui-card-title', 'Cambios recientes'));
            section.appendChild(sectionHead);
            if (d.recent_changes.length === 0) {
                section.appendChild(emptyState({ title: 'Sin cambios registrados' }));
            } else {
                const list = makeEl('ul', 'aa-changes');
                d.recent_changes.forEach(change => {
                    const li = makeEl('li');
                    li.appendChild(timeTag(change.created_at, 'aa-change-date'));
                    let text = ` · ${CHANGE_ACTIONS[change.action] || change.action} · ${change.actor}`;
                    if (change.old_value || change.new_value) text += ` · ${change.old_value || '—'} → ${change.new_value || '—'}`;
                    li.appendChild(document.createTextNode(text));
                    list.appendChild(li);
                });
                section.appendChild(list);
            }
        }
        box.appendChild(section);
        aaDetail.appendChild(box);
    }

    async function selectAccount(key) {
        aaSelected = key;
        aaBody.querySelectorAll('.queue-row').forEach(row => {
            const selected = row.dataset.key === key;
            row.classList.toggle('selected', selected);
            if (selected) row.setAttribute('aria-current', 'true');
            else row.removeAttribute('aria-current');
        });
        adminAccountsView.classList.add('show-detail');
        const row = aaRows.find(r => accountKey(r) === key);
        if (!row) return;
        if (row.kind === 'invitation') {
            aaCurrent = { ...row };
            renderInvitationPanel(row);
            return;
        }
        let detail;
        try {
            detail = await apiFetch(`/accounts/${row.id}`);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        if (aaSelected !== key) return;
        aaCurrent = { kind: 'user', ...detail };
        renderAccountPanel(detail);
    }

    async function refreshSelectedAccount() {
        await loadAdminAccounts();
        if (aaSelected && aaRows.some(r => accountKey(r) === aaSelected)) await selectAccount(aaSelected);
        else aaPlaceholder();
    }

    // ----- Menú "Acciones ▾": agrupado, con teclado; se cierra con Escape o con un clic fuera -----
    function accountMenuItems() {
        return [...accountMenu.querySelectorAll('.row-menu-item')];
    }

    function openAccountMenu(button) {
        closeAccountMenu(false);
        if (!aaCurrent) return;
        const allowed = new Set(accountMenuActions(aaCurrent));
        accountMenu.replaceChildren();
        // runAccountAction y createAccountRequest leen la cuenta del contenedor con data-user-id: el menú mismo
        accountMenu.dataset.userId = String(aaCurrent.id);
        accountMenu.dataset.userName = aaCurrent.name;
        accountMenu.dataset.userRole = aaCurrent.role;
        ACCOUNT_MENU_GROUPS.forEach(group => {
            const actions = group.actions.filter(action => allowed.has(action));
            if (!actions.length) return;
            if (accountMenu.children.length) {
                const sep = makeEl('div', 'row-menu-sep');
                sep.setAttribute('role', 'separator');
                accountMenu.appendChild(sep);
            }
            if (group.title) {
                const title = makeEl('div', 'account-menu-group', group.title);
                title.setAttribute('role', 'presentation');
                accountMenu.appendChild(title);
            }
            actions.forEach(action => {
                const entry = makeEl('button', `row-menu-item account-action${group.danger ? ' is-danger' : ''}`,
                    ACCOUNT_MENU_LABELS[action]);
                entry.type = 'button';
                entry.tabIndex = -1;
                entry.setAttribute('role', 'menuitem');
                entry.dataset.action = action;
                accountMenu.appendChild(entry);
            });
        });
        if (!accountMenu.children.length) return;
        accountMenuOwner = button;
        button.setAttribute('aria-expanded', 'true');
        accountMenu.hidden = false;
        const rect = button.getBoundingClientRect();
        const width = accountMenu.offsetWidth;
        const height = accountMenu.offsetHeight;
        let top = rect.bottom + 4;
        if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
        accountMenu.style.left = `${clamp(rect.right - width, 8, window.innerWidth - width - 8)}px`;
        accountMenu.style.top = `${top}px`;
        accountMenuItems()[0].focus();
    }

    function closeAccountMenu(returnFocus) {
        if (accountMenu.hidden) return;
        accountMenu.hidden = true;
        if (accountMenuOwner) {
            accountMenuOwner.setAttribute('aria-expanded', 'false');
            if (returnFocus && accountMenuOwner.isConnected) accountMenuOwner.focus();
        }
        accountMenuOwner = null;
    }

    async function runAccountMenuAction(entry) {
        const item = aaCurrent;
        const action = entry.dataset.action;
        closeAccountMenu(true);
        if (!item) return;
        let done = false;
        if (action === 'view-tickets') {
            setSideNavActive(null);
            setQueueView('all', `Tickets de ${item.name}`, '', { id: item.id, name: item.name });
            return;
        }
        if (action === 'review-reports') {
            openReportsForAccount(item.id, item.name);
            return;
        }
        if (action === 'full-data') {
            // Directorio del administrador: teléfono y cédula con "Mostrar"
            directoryRoleSelect.value = 'requester';
            directorySearch.value = item.name;
            setSideNavActive(null);
            showView('directory');
            return;
        }
        if (action === 'report') {
            done = await openReportDialog(item.id, item.name);
        } else if (action === 'change-role') {
            done = await openRoleDialog({ id: item.id, name: item.name, role: item.role });
        } else if (action === 'schedule') {
            done = await openScheduleDialog(item.id, item.name, item.schedule || []);
        } else if (action === 'availability') {
            done = await openAvailabilityDialog(item.id, item.name);
        } else if (action === 'request-name' || action === 'request-id_number') {
            done = await createAccountRequest(entry);
        } else if (action === 'resend-invitation') {
            try {
                await apiFetch(`/staff-invitations/${item.id}/resend`, { method: 'POST' });
                showSuccess('Invitación reenviada');
                done = true;
            } catch (err) {
                return; // apiFetch ya mostró el error
            }
        } else if (action === 'cancel-invitation') {
            if (!confirm(`¿Cancelar la invitación de ${item.name}? El código dejará de servir.`)) return;
            try {
                await apiFetch(`/staff-invitations/${item.id}`, { method: 'DELETE' });
            } catch (err) {
                return; // apiFetch ya mostró el error
            }
            showSuccess('Invitación cancelada');
            aaSelected = null;
            adminAccountsView.classList.remove('show-detail');
            done = true;
        } else {
            if (action === 'close-sessions'
                && !confirm(`Se cerrarán todas las sesiones de ${item.name} en todos sus dispositivos. ¿Continuar?`)) {
                return;
            }
            done = await runAccountAction(entry);
        }
        if (done) await refreshSelectedAccount();
    }

    accountMenu.addEventListener('click', (e) => {
        const entry = e.target.closest('.row-menu-item');
        if (entry) runAccountMenuAction(entry);
    });

    accountMenu.addEventListener('keydown', (e) => {
        const items = accountMenuItems();
        const index = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            const step = e.key === 'ArrowDown' ? 1 : -1;
            items[(index + step + items.length) % items.length].focus();
        } else if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault();
            items[e.key === 'Home' ? 0 : items.length - 1].focus();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            closeAccountMenu(true);
        } else if (e.key === 'Tab') {
            closeAccountMenu(false);
        }
    });

    document.addEventListener('click', (e) => {
        if (!accountMenu.hidden && !accountMenu.contains(e.target)
            && !(accountMenuOwner && accountMenuOwner.contains(e.target))) {
            closeAccountMenu(false);
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !accountMenu.hidden) closeAccountMenu(true);
    });
    window.addEventListener('resize', () => closeAccountMenu(false));
    document.addEventListener('scroll', (e) => {
        if (!accountMenu.contains(e.target)) closeAccountMenu(false);
    }, true);

    aaBody.addEventListener('click', (e) => {
        const row = e.target.closest('.queue-row');
        if (row) selectAccount(row.dataset.key);
    });
    aaQuick.addEventListener('click', (e) => {
        const button = e.target.closest('.aa-quick-btn');
        if (!button || button.dataset.filter === aaFilter) return;
        aaFilter = button.dataset.filter;
        loadAdminAccounts();
    });
    aaScheduleOn.addEventListener('change', () => {
        aaScheduleFields.hidden = !aaScheduleOn.checked;
        // Por defecto, el día de hoy (0 = lunes)
        if (aaScheduleOn.checked) aaDay.value = String((new Date().getDay() + 6) % 7);
        loadAdminAccounts();
    });
    [aaDay, aaStart, aaEnd, aaMatch].forEach(field => field.addEventListener('change', () => {
        if (aaScheduleOn.checked) loadAdminAccounts();
    }));
    aaSearch.addEventListener('input', () => {
        clearTimeout(aaSearchTimer);
        aaSearchTimer = setTimeout(loadAdminAccounts, 300);
    });
    document.getElementById('aaInvite').addEventListener('click', () => navUsers.click());
    document.getElementById('aaBack').addEventListener('click', () => {
        closeAccountMenu(false);
        adminAccountsView.classList.remove('show-detail');
    });

    aaDetail.addEventListener('click', async (e) => {
        const more = e.target.closest('.aa-actions-btn');
        if (more) {
            if (accountMenuOwner === more) closeAccountMenu(true);
            else openAccountMenu(more);
            return;
        }
        if (e.target.closest('.aa-review-reports') && aaCurrent) {
            setSideNavActive('reports');
            openReportsForAccount(aaCurrent.id, aaCurrent.name);
            return;
        }
        if (e.target.closest('.aa-view-queue') && aaCurrent) {
            setQueueView('all', `Tickets de ${aaCurrent.name}`, '', { id: aaCurrent.id, name: aaCurrent.name });
            return;
        }
        const ticket = e.target.closest('.aa-ticket');
        if (ticket) {
            // La sección (Cuentas o Clientes) queda marcada: la ruta del ticket cuelga de ella
            openTicketById(parseInt(ticket.dataset.ticketId, 10));
            return;
        }
        const approve = e.target.closest('.aa-approve');
        const reject = e.target.closest('.aa-reject');
        if (approve || reject) {
            const button = approve || reject;
            let body;
            if (reject) {
                const reason = prompt('Motivo del rechazo:');
                if (reason === null) return;
                if (!reason.trim()) {
                    showError('Escriba el motivo del rechazo');
                    return;
                }
                body = JSON.stringify({ reason: reason.trim() });
            }
            button.disabled = true;
            try {
                await apiFetch(`/account-requests/${button.dataset.requestId}/${approve ? 'approve' : 'reject'}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body
                });
            } catch (err) {
                button.disabled = false;
                return; // apiFetch ya mostró el error
            }
            showSuccess(approve ? 'Solicitud aprobada' : 'Solicitud rechazada');
            await refreshSelectedAccount();
        }
    });

    // ===== Portal del solicitante: Centro de Soporte =====
    const portalList = document.getElementById('portalList');
    const portalEmpty = document.getElementById('portalEmpty');
    const portalTotal = document.getElementById('portalTotal');
    const portalFooter = document.getElementById('portalFooter');
    const portalLogo = document.getElementById('portalLogo');
    const navAssistant = document.getElementById('navAssistant');
    const brandName = document.getElementById('brandName');
    // Pasos derivados de los estados reales (app/portal.py)
    const PORTAL_STEPS = ['Recibida', 'Asignada', 'En atención', 'Resuelta'];

    function isRequesterPortal() {
        return Boolean(currentUser && currentUser.role === 'requester');
    }

    async function loadPortalList() {
        let tickets;
        if (!portalList.children.length) showSkeleton(portalList, { rows: 3 });
        try {
            tickets = await apiFetch('/tickets', { silent: true });
        } catch (err) {
            portalList.removeAttribute('aria-busy');
            portalList.replaceChildren(errorState(err, loadPortalList));
            return;
        }
        portalList.removeAttribute('aria-busy');
        tickets.sort((a, b) => parseServerDate(b.last_activity_at) - parseServerDate(a.last_activity_at));
        portalList.replaceChildren();
        portalEmpty.hidden = tickets.length > 0;
        portalEmpty.replaceChildren(emptyState({
            title: 'Todavía no tiene solicitudes',
            text: 'Cuéntenos qué pasa y el equipo de soporte le responderá por correo.',
            action: 'Nueva solicitud',
            onAction: () => openNewTicket.click(),
        }));
        portalTotal.textContent = `${tickets.length} ${tickets.length === 1 ? 'solicitud' : 'solicitudes'}`;
        tickets.forEach(ticket => {
            const li = makeEl('li');
            const button = makeEl('button', 'portal-item');
            button.type = 'button';
            button.dataset.id = String(ticket.id);
            if (ticket.id === selectedTicketId) button.classList.add('selected');
            button.appendChild(makeEl('span', 'portal-item-title', ticket.title));
            const meta = makeEl('span', 'portal-item-meta');
            meta.appendChild(makeEl('span', 'ui-num', `#${ticket.id}`));
            meta.appendChild(makeEl('span', `state-tag state-${ticket.status}`, STATE_MAP[ticket.status] || ticket.status));
            const updated = makeEl('span', '', 'Actualizada ');
            updated.appendChild(timeTag(ticket.last_activity_at));
            meta.appendChild(updated);
            button.appendChild(meta);
            // Avance en una línea: "Paso 2 de 4 · Asignada"
            const step = ticket.progress_step || 1;
            const progress = makeEl('span', 'portal-item-progress');
            const bar = makeEl('span', 'ui-bar');
            bar.setAttribute('aria-hidden', 'true');
            const fill = makeEl('span');
            fill.style.width = `${(step / PORTAL_STEPS.length) * 100}%`;
            bar.appendChild(fill);
            progress.append(bar, makeEl('span', 'portal-item-step', `Paso ${step} de ${PORTAL_STEPS.length} · ${PORTAL_STEPS[step - 1]}`));
            button.appendChild(progress);
            li.appendChild(button);
            portalList.appendChild(li);
        });
    }

    async function openPortalTicket(ticketId) {
        portalList.querySelectorAll('.portal-item').forEach(b => b.classList.toggle('selected', Number(b.dataset.id) === ticketId));
        selectedTicketId = ticketId;
        document.querySelector('.split').classList.add('show-detail');
        try {
            const ticket = await apiFetch(`/tickets/${ticketId}`);
            showTicketDetail(ticket);
        } catch (err) {
            selectedTicketId = null;
            document.querySelector('.split').classList.remove('show-detail');
            renderDetailPlaceholder();
        }
    }

    portalList.addEventListener('click', (e) => {
        const item = e.target.closest('.portal-item');
        if (item) openPortalTicket(Number(item.dataset.id));
    });

    function renderPortalSteps(step) {
        const list = makeEl('ol', 'portal-steps');
        list.setAttribute('aria-label', 'Avance de la solicitud');
        PORTAL_STEPS.forEach((label, index) => {
            const number = index + 1;
            const state = number < step ? 'done' : number === step ? 'current' : 'todo';
            const li = makeEl('li', `portal-step is-${state}`);
            if (state === 'current') li.setAttribute('aria-current', 'step');
            li.appendChild(makeEl('span', 'portal-step-dot', String(number)));
            li.appendChild(makeEl('span', 'portal-step-label', label));
            list.appendChild(li);
        });
        return list;
    }

    function renderPortalDetail(ticket) {
        detailContent.replaceChildren();
        detailPanel.scrollTop = 0;
        const created = makeEl('p', 'sd-meta portal-detail-meta', `SOLICITUD #${ticket.id} · creada `);
        created.appendChild(timeTag(ticket.created_at));
        detailContent.appendChild(created);
        detailContent.appendChild(makeEl('h1', 'portal-detail-title', ticket.title));
        if (ticket.merged_into) {
            const notice = makeEl('div', 'ui-notice is-ok portal-merged');
            notice.appendChild(makeEl('span', '', `Su mensaje se agregó a su solicitud #${ticket.merged_into}, que ya estamos atendiendo.`));
            const open = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm portal-merged-open', `Ver #${ticket.merged_into}`);
            open.type = 'button';
            open.dataset.ticketId = String(ticket.merged_into);
            notice.appendChild(open);
            detailContent.appendChild(notice);
        }
        detailContent.appendChild(renderPortalSteps(ticket.progress_step || 1));
        if (ticket.assigned_name) {
            detailContent.appendChild(makeEl('p', 'sd-muted portal-owner', `Le atiende: ${ticket.assigned_name}`));
        }
        const original = makeEl('div', 'portal-original');
        original.appendChild(makeEl('p', 'portal-message-meta', 'Su solicitud'));
        original.appendChild(makeEl('p', 'portal-message-text', ticket.description));
        detailContent.appendChild(original);

        detailContent.appendChild(makeEl('h2', 'portal-subtitle', 'Conversación'));
        // El servidor nunca envía notas internas al solicitante; el filtro es solo por si acaso
        const messages = (ticket.comments || []).filter(c => !c.internal);
        if (messages.length === 0) {
            detailContent.appendChild(emptyState({ title: 'Todavía no hay mensajes', text: 'Cuando el equipo le responda, lo verá aquí y le llegará un correo.' }));
        } else {
            const list = makeEl('ol', 'portal-messages');
            messages.forEach(comment => {
                const own = comment.user_id === currentUser.id;
                const li = makeEl('li', `portal-message${own ? ' is-own' : ''}`);
                const author = own ? 'Usted' : (userMap[comment.user_id] || 'Equipo de soporte');
                const meta = makeEl('p', 'portal-message-meta', `${author} · `);
                meta.appendChild(timeTag(comment.created_at));
                li.appendChild(meta);
                if (comment.content) li.appendChild(makeEl('p', 'portal-message-text', comment.content));
                const files = attachmentList(comment.attachments);
                if (files) li.appendChild(files);
                list.appendChild(li);
            });
            detailContent.appendChild(list);
        }

        if (ticket.status === 'closed') {
            detailContent.appendChild(makeEl('p', 'sd-muted', 'Esta solicitud está cerrada y ya no admite respuestas.'));
            return;
        }
        const composer = makeEl('div', 'ui-card sd-composer portal-composer');
        const label = makeEl('label', 'sr-only', 'Su respuesta');
        label.htmlFor = 'portalReply';
        const textarea = makeEl('textarea', 'sd-reply');
        textarea.id = 'portalReply';
        textarea.rows = 3;
        textarea.maxLength = 5000;
        textarea.placeholder = 'Escriba su respuesta';
        const actions = makeEl('div', 'sd-composer-actions');
        const send = makeEl('button', 'ui-btn ui-btn-primary ui-btn-sm portal-send', 'Enviar respuesta');
        send.type = 'button';
        actions.appendChild(send);
        composer.append(label, textarea, attachmentPicker(), actions);
        detailContent.appendChild(composer);
    }

    detailPanel.addEventListener('click', async (e) => {
        const merged = e.target.closest('.portal-merged-open');
        if (merged && isRequesterPortal()) {
            openPortalTicket(Number(merged.dataset.ticketId));
            return;
        }
        const send = e.target.closest('.portal-send');
        if (!send || !selectedTicketId || !isRequesterPortal()) return;
        const textarea = document.getElementById('portalReply');
        const content = textarea.value.trim();
        if (!content && !pendingFiles.length) {
            showError('Escriba su respuesta antes de enviarla');
            return;
        }
        send.disabled = true;
        if (!(await sendComment(content, false))) {
            send.disabled = false;
            return;
        }
        await reloadSelectedTicketAndList();
        showSuccess('Respuesta enviada');
    });

    navAssistant.addEventListener('click', () => {
        if (chatPanel.hidden) openChat();
    });
    portalLogo.addEventListener('error', () => { portalLogo.hidden = true; });

    function setupPortal() {
        const portal = isRequesterPortal();
        document.body.classList.toggle('requester-portal', portal);
        portalFooter.hidden = !portal;
        brandName.textContent = portal ? 'Centro de Soporte' : 'Mesa de Servicio';
        navTickets.textContent = portal ? 'Mis solicitudes' : 'Tickets';
        openNewTicket.textContent = portal ? '+ Nueva solicitud' : '+ Nuevo ticket';
    }

    function teardownPortal() {
        document.body.classList.remove('requester-portal');
        portalFooter.hidden = true;
        brandName.textContent = 'Mesa de Servicio';
    }

    // ===== Una sola sección por opción del menú =====
    const ACCOUNT_SECTION_TITLES = { team: 'Equipo y turnos', requests: 'Mis solicitudes' };
    const ADMIN_SECTION_TITLES = { health: 'Estado del sistema', approvals: 'Aprobaciones', changes: 'Registro de cambios' };

    function showAccountsSection(section) {
        accountsView.querySelectorAll('[data-account-section]').forEach(el => {
            el.hidden = el.dataset.accountSection !== section;
        });
        document.getElementById('accountsTitle').textContent = ACCOUNT_SECTION_TITLES[section];
        showView('accounts');
        setDocumentTitle(ACCOUNT_SECTION_TITLES[section]);
    }

    function showAdminSection(section) {
        adminView.querySelectorAll('[data-admin-section]').forEach(el => {
            el.hidden = el.dataset.adminSection !== section;
        });
        document.getElementById('adminTitle').textContent = ADMIN_SECTION_TITLES[section];
        showView('admin');
        setDocumentTitle(ADMIN_SECTION_TITLES[section]);
    }

    // Verificación en el navegador: después de elegir una opción del menú debe verse una sola vista
    // y, dentro de Cuentas o Administración, una sola sección. Si no, queda el error en la consola.
    function visibleViews() {
        return [...document.querySelectorAll('main > .split, main > .page-view')]
            .filter(el => !el.hidden && el.style.display !== 'none');
    }

    function checkSingleSection() {
        const views = visibleViews();
        const problems = [];
        if (views.length !== 1) problems.push(`${views.length} vistas visibles`);
        if (!accountsView.hidden) {
            const shown = [...accountsView.querySelectorAll('[data-account-section]')].filter(el => !el.hidden);
            if (shown.length !== 1) problems.push(`${shown.length} secciones visibles en Cuentas`);
            if (shown.some(el => el.dataset.accountSection === 'requests') && currentUser.role !== 'coordinator') {
                problems.push('Mis solicitudes visible para quien no las crea');
            }
        }
        if (!adminView.hidden) {
            const kinds = new Set([...adminView.querySelectorAll('[data-admin-section]')]
                .filter(el => !el.hidden).map(el => el.dataset.adminSection));
            if (kinds.size !== 1) problems.push(`${kinds.size} secciones visibles en Administración`);
        }
        if (problems.length) console.error(`Vista del menú con más de una sección: ${problems.join('; ')}`);
        return problems.length === 0;
    }

    // ===== Tabla de personal (Equipo y Horarios) =====
    const teamUsers = new Map();
    const rowMenu = document.getElementById('rowMenu');
    let rowMenuOwner = null;

    function truncated(tag, className, text) {
        const node = makeEl(tag, `${className} team-trunc`, text);
        node.title = text;
        return node;
    }

    function teamStateText(user) {
        let text = !user.is_active ? 'Desactivada' : user.login_locked ? 'Bloqueada' : 'Activa';
        if (user.availability && user.availability !== 'available') {
            text += ` · ${AVAILABILITY_LABELS[user.availability] || user.availability}`;
            if (user.return_date) text += ` (regresa ${formatPlainDate(user.return_date)})`;
        }
        return text;
    }

    function canChangeRole(user) {
        if (!currentUser || user.id === currentUser.id) return false;
        // Admin: directo. Coordinador: solicitud con doble aprobación, solo para técnicos (como hasta ahora)
        return currentUser.role === 'admin' || (currentUser.role === 'coordinator' && user.role === 'technician');
    }

    // Las mismas reglas de hoy (MANAGE_RULES); el servidor también las revisa. En esta tabla nadie ve
    // acciones sobre su propia cuenta (eso va en Mi cuenta), el admin no las ve sobre otro admin y el
    // coordinador solo las ve sobre técnicos.
    function teamMenuItems(user) {
        const items = [];
        if (!currentUser || user.id === currentUser.id) return items;
        if (currentUser.role === 'admin' && user.role === 'admin') return items;
        if (currentUser.role === 'coordinator' && user.role !== 'technician') return items;
        if (canManage(user, 'availability')) {
            items.push({ action: 'availability', label: 'Vacaciones y ausencias' });
        }
        if (canChangeRole(user)) items.push({ action: 'change-role', label: 'Cambiar rol…' });
        if (user.is_active && canManage(user, 'reset')) items.push({ action: 'reset-access', label: 'Forzar restablecimiento' });
        if (user.login_locked && canManage(user, 'unlock')) items.push({ action: 'unlock', label: 'Desbloquear' });
        if (canManage(user, 'sessions')) items.push({ action: 'close-sessions', label: 'Cerrar sesiones' });
        if (canManage(user, 'active')) {
            items.push({ action: user.is_active ? 'deactivate' : 'reactivate',
                label: user.is_active ? 'Desactivar' : 'Reactivar', danger: true });
        }
        return items;
    }

    // ----- Equipo y turnos: cuadrícula semanal (P-Equipo) -----
    // El horario es semanal y se repite: cambiar de semana solo cambia las fechas y las vacaciones
    let teamWeekOffset = 0;
    let teamSelectedId = null;
    const WEEKDAY_SHORT = ['LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB', 'DOM'];

    function teamWeekDays() {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const monday = new Date(today);
        monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) + teamWeekOffset * 7);
        return Array.from({ length: 7 }, (_, i) => {
            const date = new Date(monday);
            date.setDate(monday.getDate() + i);
            return { date, weekday: i, isToday: date.getTime() === today.getTime(), isPast: date < today };
        });
    }

    // Vacaciones o ausencia: de hoy en adelante, hasta el día anterior a la fecha de regreso (si la hay)
    // Vacaciones o incapacidad registradas que cubren ese día (los periodos vienen de /team)
    function teamPeriodOn(user, date) {
        const day = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
        return (user.periods || []).find(period => period.start_date <= day && day <= period.end_date) || null;
    }

    function teamStatus(user) {
        if (!user.is_active) return ['Desactivada', 'is-muted'];
        if (user.login_locked) return ['Bloqueada', 'is-alert'];
        if (user.availability === 'vacation' || user.availability === 'incapacity') {
            const label = AVAILABILITY_LABELS[user.availability];
            return [user.return_date ? `${label} · regresa el ${formatPlainDate(user.return_date)}` : label, 'is-warn'];
        }
        if (user.role === 'admin') return ['Administrador', 'is-muted'];
        if (user.availability === 'away') {
            return [user.schedule_today ? `Ausente · fuera de horario (hoy ${user.schedule_today})` : 'Ausente · sin turno hoy', 'is-muted'];
        }
        if (user.on_shift) return [user.schedule_today ? `En turno · sale ${user.schedule_today.split('–').pop()}` : 'En turno', 'is-ok'];
        return [user.schedule_today ? `Hoy ${user.schedule_today}` : 'Sin turno hoy', 'is-muted'];
    }

    function renderTeamWeek() {
        const grid = document.getElementById('teamWeekGrid');
        grid.removeAttribute('aria-busy');
        grid.replaceChildren();
        const days = teamWeekDays();
        const first = days[0].date;
        const last = days[6].date;
        const fmt = (date, opts) => date.toLocaleDateString('es-CR', opts);
        const todayName = fmt(new Date(), { weekday: 'long' });
        document.getElementById('teamWeekLabel').textContent = teamWeekOffset === 0
            ? `Semana del ${first.getDate()} al ${fmt(last, { day: 'numeric', month: 'long' })} · hoy es ${todayName}`
            : `Semana del ${first.getDate()} al ${fmt(last, { day: 'numeric', month: 'long' })}`;

        const header = makeEl('div', 'team-week-row team-week-header');
        header.setAttribute('role', 'row');
        const personHead = makeEl('span', 'team-week-person-head', 'PERSONA');
        personHead.setAttribute('role', 'columnheader');
        header.appendChild(personHead);
        days.forEach(day => {
            const cell = makeEl('span', `team-week-day${day.isToday ? ' is-today' : ''}`,
                `${WEEKDAY_SHORT[day.weekday]} ${day.date.getDate()}`);
            cell.setAttribute('role', 'columnheader');
            if (day.isToday) cell.setAttribute('aria-current', 'date');
            header.appendChild(cell);
        });
        grid.appendChild(header);

        [...teamUsers.values()].forEach(user => {
            const row = makeEl('div', 'team-week-row');
            row.setAttribute('role', 'row');
            const personCell = makeEl('span', 'team-week-person');
            personCell.setAttribute('role', 'rowheader');
            const person = makeEl('button', 'team-person');
            person.type = 'button';
            person.dataset.teamUser = String(user.id);
            person.setAttribute('aria-pressed', user.id === teamSelectedId ? 'true' : 'false');
            const avatar = makeEl('span', 'aa-avatar', getInitials(user.name || ''));
            avatar.setAttribute('aria-hidden', 'true');
            const text = makeEl('span', 'aa-row-text');
            text.appendChild(makeEl('span', 'aa-row-name', user.name || 'Sin nombre'));
            const [status, tone] = teamStatus(user);
            text.appendChild(makeEl('span', `team-person-status ${tone}`, `${ROLE_MAP[user.role] || user.role} · ${status}`));
            person.append(avatar, text);
            personCell.appendChild(person);
            row.appendChild(personCell);

            days.forEach(day => {
                const cell = makeEl('span', `team-week-cell${day.isToday ? ' is-today' : ''}`);
                cell.setAttribute('role', 'cell');
                const shift = (user.schedule || []).find(item => item.weekday === day.weekday);
                let chip;
                if (user.role === 'admin') {
                    chip = makeEl('span', 'team-shift is-empty', '—');
                } else if (teamPeriodOn(user, day.date)) {
                    chip = makeEl('span', 'team-shift is-away', PERIOD_KINDS[teamPeriodOn(user, day.date).kind]);
                } else if (shift) {
                    chip = makeEl('span', `team-shift${user.role === 'coordinator' ? ' is-coordinator' : ''}`,
                        `${shift.start}–${shift.end}`);
                } else {
                    chip = makeEl('span', 'team-shift is-empty', '—');
                    chip.title = 'No trabaja este día';
                }
                cell.appendChild(chip);
                row.appendChild(cell);
            });
            grid.appendChild(row);
        });
    }

    // Resumen de la persona elegida: datos, "Ver sus tickets" y las acciones que ya existían
    function renderTeamSelected() {
        const box = document.getElementById('teamSelected');
        const user = teamUsers.get(teamSelectedId);
        box.hidden = !user;
        box.replaceChildren();
        if (!user) return;
        const name = user.name || 'Sin nombre';
        // runAccountAction y el menú leen la cuenta del contenedor con data-user-id
        box.dataset.userId = String(user.id);
        box.dataset.userName = name;
        box.dataset.userRole = user.role;
        const info = makeEl('div', 'team-selected-info');
        info.appendChild(makeEl('h2', 'team-selected-name', name));
        info.appendChild(makeEl('p', 'team-selected-detail', `${ROLE_MAP[user.role] || user.role} · ${teamStateText(user)} · ${user.email}`));
        const numbers = makeEl('p', 'team-selected-numbers');
        numbers.appendChild(makeEl('b', '', String(user.tickets_assigned_open)));
        numbers.appendChild(document.createTextNode(' tickets abiertos asignados'));
        info.appendChild(numbers);
        box.appendChild(info);

        const actions = makeEl('div', 'team-selected-actions');
        if (user.role !== 'admin') {
            const tickets = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm team-view-tickets', 'Ver sus tickets');
            tickets.type = 'button';
            actions.appendChild(tickets);
        }
        const menu = teamMenuItems(user);
        const availability = menu.find(item => item.action === 'availability');
        if (availability) {
            const button = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm account-action', 'Vacaciones y ausencias');
            button.type = 'button';
            button.dataset.action = 'availability';
            actions.appendChild(button);
        }
        // Horario: el coordinador edita el de técnicos; el admin, el de técnicos y coordinadores. Nunca el propio
        if (user.role !== 'admin' && user.id !== currentUser.id && canManage(user, 'schedule')) {
            const schedule = makeEl('button', 'ui-btn ui-btn-navy ui-btn-sm account-action', 'Editar horario');
            schedule.type = 'button';
            schedule.dataset.action = 'schedule';
            schedule.dataset.schedule = JSON.stringify(user.schedule || []);
            actions.appendChild(schedule);
        }
        if (menu.some(item => item.action !== 'availability')) {
            const more = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm row-menu-btn', 'Más acciones ▾');
            more.type = 'button';
            more.title = `Más acciones para ${name}`;
            more.setAttribute('aria-haspopup', 'menu');
            more.setAttribute('aria-expanded', 'false');
            more.setAttribute('aria-controls', 'rowMenu');
            actions.appendChild(more);
        }
        box.appendChild(actions);
    }

    document.getElementById('teamWeekGrid').addEventListener('click', (e) => {
        const person = e.target.closest('[data-team-user]');
        if (!person) return;
        const id = Number(person.dataset.teamUser);
        teamSelectedId = teamSelectedId === id ? null : id;
        document.querySelectorAll('#teamWeekGrid [data-team-user]').forEach(button => {
            button.setAttribute('aria-pressed', Number(button.dataset.teamUser) === teamSelectedId ? 'true' : 'false');
        });
        renderTeamSelected();
    });

    document.getElementById('teamSelected').addEventListener('click', (e) => {
        if (!e.target.closest('.team-view-tickets')) return;
        const user = teamUsers.get(teamSelectedId);
        if (!user) return;
        // Sus tickets abiertos: la cola filtrada por responsable
        queueAssignee.value = String(user.id);
        if (queueAssignee.value !== String(user.id)) {
            const option = document.createElement('option');
            option.value = String(user.id);
            option.textContent = user.name;
            queueAssignee.appendChild(option);
            queueAssignee.value = String(user.id);
        }
        setSideNavActive('open');
        setQueueView('open', `Tickets de ${user.name}`);
    });

    document.getElementById('teamPrevWeek').addEventListener('click', () => {
        teamWeekOffset -= 1;
        renderTeamWeek();
    });
    document.getElementById('teamNextWeek').addEventListener('click', () => {
        teamWeekOffset += 1;
        renderTeamWeek();
    });
    document.getElementById('teamInvite').addEventListener('click', () => navUsers.click());

    function menuItems() {
        return [...rowMenu.querySelectorAll('.row-menu-item')];
    }

    function openRowMenu(button) {
        closeRowMenu(false);
        const row = button.closest('[data-user-id]');
        const user = teamUsers.get(Number(row.dataset.userId));
        if (!user) return;
        rowMenu.replaceChildren();
        // runAccountAction lee la cuenta del contenedor más cercano con data-user-id: el menú mismo
        rowMenu.dataset.userId = row.dataset.userId;
        rowMenu.dataset.userName = row.dataset.userName;
        rowMenu.dataset.userRole = row.dataset.userRole;
        teamMenuItems(user).forEach(item => {
            if (item.danger && rowMenu.children.length) {
                const sep = makeEl('div', 'row-menu-sep');
                sep.setAttribute('role', 'separator');
                rowMenu.appendChild(sep);
            }
            const entry = makeEl('button', `row-menu-item account-action${item.danger ? ' is-danger' : ''}`, item.label);
            entry.type = 'button';
            entry.tabIndex = -1;
            entry.setAttribute('role', 'menuitem');
            entry.dataset.action = item.action;
            Object.entries(item.data || {}).forEach(([key, value]) => { entry.dataset[key] = value; });
            rowMenu.appendChild(entry);
        });
        rowMenuOwner = button;
        button.setAttribute('aria-expanded', 'true');
        rowMenu.hidden = false;
        const rect = button.getBoundingClientRect();
        const width = rowMenu.offsetWidth;
        const height = rowMenu.offsetHeight;
        let top = rect.bottom + 4;
        if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
        rowMenu.style.left = `${clamp(rect.right - width, 8, window.innerWidth - width - 8)}px`;
        rowMenu.style.top = `${top}px`;
        menuItems()[0].focus();
    }

    function closeRowMenu(returnFocus) {
        if (rowMenu.hidden) return;
        rowMenu.hidden = true;
        if (rowMenuOwner) {
            rowMenuOwner.setAttribute('aria-expanded', 'false');
            if (returnFocus) rowMenuOwner.focus();
        }
        rowMenuOwner = null;
    }

    async function runTeamMenuAction(entry) {
        const user = teamUsers.get(Number(rowMenu.dataset.userId));
        closeRowMenu(true);
        if (!user) return;
        if (entry.dataset.action === 'change-role') {
            if (await openRoleDialog(user)) loadTeam();
            return;
        }
        if (entry.dataset.action === 'close-sessions'
            && !confirm(`Se cerrarán todas las sesiones de ${user.name || 'esta cuenta'} en todos sus dispositivos. ¿Continuar?`)) {
            return;
        }
        if (await runAccountAction(entry)) loadTeam();
    }

    teamBody.addEventListener('click', (e) => {
        const more = e.target.closest('.row-menu-btn');
        if (!more) return;
        if (rowMenuOwner === more) closeRowMenu(true);
        else openRowMenu(more);
    });

    rowMenu.addEventListener('click', (e) => {
        const entry = e.target.closest('.row-menu-item');
        if (entry) runTeamMenuAction(entry);
    });

    rowMenu.addEventListener('keydown', (e) => {
        const items = menuItems();
        const index = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            const step = e.key === 'ArrowDown' ? 1 : -1;
            items[(index + step + items.length) % items.length].focus();
        } else if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault();
            items[e.key === 'Home' ? 0 : items.length - 1].focus();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closeRowMenu(true);
        } else if (e.key === 'Tab') {
            closeRowMenu(false);
        }
    });

    document.addEventListener('click', (e) => {
        if (!rowMenu.hidden && !rowMenu.contains(e.target) && !(rowMenuOwner && rowMenuOwner.contains(e.target))) {
            closeRowMenu(false);
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !rowMenu.hidden) closeRowMenu(true);
    });
    window.addEventListener('resize', () => closeRowMenu(false));
    document.addEventListener('scroll', () => closeRowMenu(false), true);

    // ----- Cambiar rol… (admin: directo; coordinador: solicitud con doble aprobación) -----
    const roleDialog = document.getElementById('roleDialog');
    const roleSelect = document.getElementById('roleSelect');
    const roleReason = document.getElementById('roleReason');
    const roleError = document.getElementById('roleError');
    let roleUser = null;
    let roleDone = null;

    function openRoleDialog(user) {
        return new Promise(resolve => {
            roleUser = user;
            roleDone = resolve;
            const isCoordinator = currentUser.role === 'coordinator';
            document.getElementById('roleTarget').textContent = `${user.name || 'Sin nombre'} · ${ROLE_MAP[user.role] || user.role}`;
            roleSelect.replaceChildren();
            const options = isCoordinator
                ? [['coordinator', 'Coordinador'], ['requester', 'Solicitante']]
                : ROLE_OPTIONS.filter(([value]) => value !== user.role);
            options.forEach(([value, label]) => roleSelect.appendChild(staffOption(value, label, false)));
            document.getElementById('roleReasonGroup').hidden = !isCoordinator;
            roleReason.value = '';
            document.getElementById('roleSubmit').textContent = isCoordinator ? 'Enviar solicitud' : 'Cambiar rol';
            roleError.hidden = true;
            roleDialog.showModal();
            roleSelect.focus();
        });
    }

    function closeRoleDialog(done) {
        if (roleDialog.open) roleDialog.close();
        const resolve = roleDone;
        roleDone = null;
        roleUser = null;
        if (resolve) resolve(done);
    }

    function showRoleError(message) {
        roleError.textContent = message;
        roleError.hidden = false;
    }

    document.getElementById('roleCancel').addEventListener('click', () => closeRoleDialog(false));
    roleDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeRoleDialog(false);
    });
    document.getElementById('roleForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!roleUser) return;
        roleError.hidden = true;
        const isCoordinator = currentUser.role === 'coordinator';
        const reason = roleReason.value.trim();
        if (isCoordinator && !reason) {
            showRoleError('Escriba el motivo de la solicitud');
            return;
        }
        const submit = document.getElementById('roleSubmit');
        submit.disabled = true;
        try {
            if (isCoordinator) {
                await apiFetch('/account-requests', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: 'role', target_user_id: roleUser.id, new_value: roleSelect.value, reason }),
                    silent: true
                });
            } else {
                await apiFetch(`/admin/users/${roleUser.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ role: roleSelect.value }),
                    silent: true
                });
            }
        } catch (err) {
            showRoleError(err.message || 'No se pudo cambiar el rol');
            return;
        } finally {
            submit.disabled = false;
        }
        showSuccess(isCoordinator ? 'Solicitud enviada al administrador' : 'Rol actualizado');
        closeRoleDialog(true);
    });


    // ===== Fase 6 · Paleta de comandos, atajos de teclado y deshacer la reasignación =====

    // ----- Paleta (Ctrl K): pantallas del rol, #número, búsqueda de tickets y clientes, crear ticket -----
    const paletteDialog = document.getElementById('paletteDialog');
    const paletteInput = document.getElementById('paletteInput');
    const paletteList = document.getElementById('paletteList');
    let paletteItems = [];
    let paletteActive = 0;
    let paletteClients = [];
    let paletteClientSeq = 0;
    let paletteClientTimer = null;
    let paletteReturnFocus = null;

    // Crear a nombre de un cliente y reasignar: el coordinador (el administrador no entra al flujo de tickets)
    function canCreateTicket() {
        return Boolean(currentUser && OPERATION_ROLES.includes(currentUser.role));
    }

    function canAssign() {
        return Boolean(currentUser && OPERATION_ROLES.includes(currentUser.role));
    }

    // Sin tildes ni mayúsculas, para que "creditos" encuentre "Créditos"
    function plain(text) {
        return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    }

    function paletteCommands(query) {
        const q = plain(query.trim());
        const groups = [];
        const number = query.trim().match(/^#?\s*(\d+)$/);
        if (number) {
            groups.push({ title: 'Ticket', items: [{
                label: currentUser.role === 'admin' ? `Ver conversación del ticket #${number[1]}` : `Abrir ticket #${number[1]}`,
                hint: 'Ticket',
                run: () => openTicketById(parseInt(number[1], 10)),
            }] });
        }
        // Las mismas secciones del menú de este rol
        const screens = [];
        navModules.forEach(module => module.sections.forEach(section => {
            const label = module.sections.length > 1 ? `${module.label} › ${section.label}` : section.label;
            screens.push({ label, hint: 'Ir a', run: () => goRoute(section.route) });
        }));
        screens.push({ label: 'Mi cuenta', hint: 'Ir a', run: () => goRoute(PROFILE_SECTION.route) });
        const go = screens.filter(item => !q || plain(item.label).includes(q));
        if (go.length) groups.push({ title: 'Ir a', items: go });

        const actions = [];
        if (canCreateTicket()) actions.push({ label: 'Crear ticket nuevo', key: 'C', run: () => openNewTicket.click() });
        actions.push({ label: 'Ver atajos de teclado', key: '?', run: () => openShortcuts() });
        const shownActions = actions.filter(item => !q || plain(item.label).includes(q));
        if (shownActions.length) groups.push({ title: 'Acciones', items: shownActions });

        // El administrador no busca en los tickets (no tiene cola)
        if (q.length >= 2 && !number && currentUser.role !== 'admin') {
            const text = query.trim();
            const search = [{ label: `Buscar «${text}» en los tickets`, hint: 'Búsqueda', run: () => {
                setSideNavActive(null);
                setQueueView('all', `Búsqueda: ${text}`, text);
            } }];
            // Clientes: solo nombre, sin correo (lo pueden ver coordinador y admin)
            paletteClients.forEach(client => search.push({
                label: client.name || 'Sin nombre', hint: 'Cliente', run: () => {
                    setSideNavActive('accounts');
                    openAccounts('clients', client.name || '');
                },
            }));
            groups.push({ title: 'Buscar', items: search });
        }
        return groups;
    }

    function renderPalette() {
        const groups = paletteCommands(paletteInput.value);
        paletteItems = [];
        paletteList.replaceChildren();
        groups.forEach((group, groupIndex) => {
            const heading = makeEl('div', 'palette-group', group.title.toUpperCase());
            heading.id = `paletteGroup${groupIndex}`;
            heading.setAttribute('role', 'presentation');
            paletteList.appendChild(heading);
            group.items.forEach(item => {
                const index = paletteItems.length;
                const option = makeEl('div', 'palette-item');
                option.id = `paletteOption${index}`;
                option.setAttribute('role', 'option');
                option.dataset.index = String(index);
                option.appendChild(makeEl('span', 'palette-item-label', item.label));
                if (item.key) option.appendChild(makeEl('kbd', 'palette-kbd', item.key));
                else if (item.hint) option.appendChild(makeEl('span', 'palette-item-hint', item.hint));
                paletteList.appendChild(option);
                paletteItems.push(item);
            });
        });
        if (!paletteItems.length) {
            paletteList.appendChild(emptyState({ title: 'Sin resultados', text: 'Pruebe con otra palabra o con #número.' }));
        }
        paletteActive = Math.min(paletteActive, Math.max(0, paletteItems.length - 1));
        markPaletteActive();
    }

    function markPaletteActive() {
        paletteList.querySelectorAll('.palette-item').forEach(option => {
            const active = Number(option.dataset.index) === paletteActive;
            option.setAttribute('aria-selected', active ? 'true' : 'false');
            if (active) {
                paletteInput.setAttribute('aria-activedescendant', option.id);
                option.scrollIntoView({ block: 'nearest' });
            }
        });
        if (!paletteItems.length) paletteInput.removeAttribute('aria-activedescendant');
    }

    async function searchPaletteClients(text) {
        const seq = ++paletteClientSeq;
        let clients = [];
        try {
            clients = await apiFetch(`/requesters?q=${encodeURIComponent(text)}`, { silent: true });
        } catch (err) {
            return;
        }
        if (seq !== paletteClientSeq || !paletteDialog.open) return;
        paletteClients = clients.slice(0, 5);
        renderPalette();
    }

    function openPalette() {
        if (!isStaffShell() || paletteDialog.open) return;
        closeSideNav();
        paletteReturnFocus = document.activeElement;
        paletteInput.value = '';
        paletteClients = [];
        paletteActive = 0;
        renderPalette();
        paletteDialog.showModal();
        paletteInput.focus();
    }

    function closePalette() {
        if (paletteDialog.open) paletteDialog.close();
    }

    function runPaletteItem(index) {
        const item = paletteItems[index];
        if (!item) return;
        paletteReturnFocus = null;
        closePalette();
        hideError();
        item.run();
        checkSingleSection();
    }

    paletteInput.addEventListener('input', () => {
        paletteActive = 0;
        paletteClients = [];
        renderPalette();
        clearTimeout(paletteClientTimer);
        const text = paletteInput.value.trim();
        if (OPERATION_ROLES.includes(currentUser.role) && text.length >= 2 && !/^#?\s*\d+$/.test(text)) {
            paletteClientTimer = setTimeout(() => searchPaletteClients(text), 300);
        }
    });

    paletteInput.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!paletteItems.length) return;
            const step = e.key === 'ArrowDown' ? 1 : -1;
            paletteActive = (paletteActive + step + paletteItems.length) % paletteItems.length;
            markPaletteActive();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            runPaletteItem(paletteActive);
        }
    });

    paletteList.addEventListener('click', (e) => {
        const option = e.target.closest('.palette-item');
        if (option) runPaletteItem(Number(option.dataset.index));
    });

    document.getElementById('paletteClose').addEventListener('click', closePalette);
    // Clic fuera de la caja (en el fondo) también la cierra
    paletteDialog.addEventListener('click', (e) => {
        if (e.target === paletteDialog) closePalette();
    });
    paletteDialog.addEventListener('close', () => {
        clearTimeout(paletteClientTimer);
        paletteClientSeq++;
        if (paletteReturnFocus && document.contains(paletteReturnFocus)) paletteReturnFocus.focus();
        paletteReturnFocus = null;
    });

    // ----- Hoja de ayuda (?) -----
    const shortcutsDialog = document.getElementById('shortcutsDialog');

    function shortcutList() {
        const list = [
            ['Buscar o ir a una pantalla', '/'], ['Paleta de comandos', 'Ctrl K'],
            ['Ticket siguiente', 'J'], ['Ticket anterior', 'K'],
            ['Abrir el ticket elegido', 'Enter'], ['Responder al cliente', 'R'],
            ['Nota interna', 'N'],
        ];
        if (canAssign()) list.push(['Reasignar', 'A']);
        if (canCreateTicket()) list.push(['Crear ticket', 'C']);
        list.push(['Ver esta ayuda', '?']);
        return list;
    }

    function openShortcuts() {
        if (!isStaffShell() || shortcutsDialog.open) return;
        const list = document.getElementById('shortcutsList');
        list.replaceChildren();
        shortcutList().forEach(([label, key]) => {
            const row = makeEl('div', 'shortcuts-row');
            const dd = makeEl('dd');
            dd.appendChild(makeEl('kbd', 'palette-kbd', key));
            row.append(makeEl('dt', '', label), dd);
            list.appendChild(row);
        });
        shortcutsDialog.showModal();
        document.getElementById('shortcutsClose').focus();
    }

    document.getElementById('shortcutsClose').addEventListener('click', () => shortcutsDialog.close());
    shortcutsDialog.addEventListener('click', (e) => {
        if (e.target === shortcutsDialog) shortcutsDialog.close();
    });
    document.getElementById('queueHelp').addEventListener('click', openShortcuts);

    // ----- Atajos: no actúan mientras se escribe ni con un diálogo o menú abierto -----
    function typingIn(target) {
        if (!target || !target.closest) return false;
        return Boolean(target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'));
    }

    function overlayOpen() {
        return Boolean(document.querySelector('dialog[open]')) || !accountMenu.hidden || !rowMenu.hidden
            || document.body.classList.contains('nav-open') || (chatPanel && !chatPanel.hidden && chatPanel.contains(document.activeElement));
    }

    // J / K: en la cola mueven el foco entre filas; en un ticket pasan al siguiente o al anterior
    function moveInQueue(step) {
        if (document.body.classList.contains('ticket-open')) {
            (step > 0 ? ticketNext : ticketPrev).click();
            return;
        }
        if (splitView.style.display === 'none') return;
        const rows = [...queueBody.querySelectorAll('.queue-row')];
        if (!rows.length) return;
        const index = rows.indexOf(document.activeElement);
        const next = index === -1 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, index + step));
        rows[next].focus();
    }

    function focusComposer(mode) {
        if (!document.body.classList.contains('ticket-open')) return;
        const tab = document.getElementById(`composerTab-${mode}`);
        if (!tab) return; // ticket cerrado
        if (composerMode !== mode) tab.click();
        const textarea = document.getElementById('staffReply');
        if (textarea) textarea.focus();
    }

    document.addEventListener('keydown', (e) => {
        if (!isStaffShell() || !currentUser) return;
        // Ctrl K abre la paleta desde cualquier lugar, salvo con otro diálogo abierto
        if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
            if (document.querySelector('dialog[open]') && !paletteDialog.open) return;
            e.preventDefault();
            if (paletteDialog.open) closePalette();
            else openPalette();
            return;
        }
        if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
        if (typingIn(e.target) || overlayOpen()) return;
        const key = e.key;
        if (key === '/') {
            e.preventDefault();
            openPalette();
        } else if (key === '?') {
            e.preventDefault();
            openShortcuts();
        } else if (key === 'j' || key === 'J') {
            e.preventDefault();
            moveInQueue(1);
        } else if (key === 'k' || key === 'K') {
            e.preventDefault();
            moveInQueue(-1);
        } else if (key === 'r' || key === 'R') {
            if (!document.body.classList.contains('ticket-open')) return;
            e.preventDefault();
            focusComposer('reply');
        } else if (key === 'n' || key === 'N') {
            if (!document.body.classList.contains('ticket-open')) return;
            e.preventDefault();
            focusComposer('note');
        } else if ((key === 'a' || key === 'A') && canAssign()) {
            const select = detailPanel.querySelector('.sd-assign-select');
            if (!document.body.classList.contains('ticket-open') || !select) return;
            e.preventDefault();
            select.scrollIntoView({ block: 'center' });
            select.focus();
        } else if ((key === 'c' || key === 'C') && canCreateTicket()) {
            if (!newTicketView.hidden) return;
            e.preventDefault();
            openNewTicket.click();
        }
    });

    // ----- Deshacer al reasignar -----
    // La reasignación se envía al servidor (y sale el correo de aviso) solo cuando terminan los 5 s,
    // o antes si la persona sale del ticket o cambia de pantalla. "Deshacer" cancela el envío.
    const UNDO_SECONDS = 5;
    let pendingAssign = null;

    function assignBody(pending) {
        return JSON.stringify(pending.confirmUnavailable
            ? { assigned_to: pending.userId, confirm_unavailable: true }
            : { assigned_to: pending.userId });
    }

    // Confirmación de "no disponible" primero (con lo que se sabe en el navegador); luego el aviso con Deshacer
    function scheduleReassign(userId) {
        commitPendingAssign();
        const name = userMap[userId] || 'esa persona';
        const availability = userAvailabilityMap[userId];
        let confirmUnavailable = false;
        if (availability && availability !== 'available') {
            const label = { vacation: 'de vacaciones', incapacity: 'con ausencia', away: 'fuera de su horario' }[availability]
                || 'no disponible';
            if (!confirm(`${name} está ${label}. Las alertas de este ticket irán a los coordinadores. ¿Asignarle el ticket de todos modos?`)) return false;
            confirmUnavailable = true;
        }
        const pending = { ticketId: selectedTicketId, userId, confirmUnavailable, done: false };
        pendingAssign = pending;
        pending.toast = toast(`Ticket #${pending.ticketId} reasignado a ${name}. Se le avisará por correo en ${UNDO_SECONDS} s.`, {
            action: 'Deshacer',
            duration: UNDO_SECONDS * 1000,
            onClose: (reason) => {
                if (reason === 'action') undoPendingAssign(pending);
                else commitPendingAssign();
            },
        });
        return true;
    }

    async function commitPendingAssign() {
        const pending = pendingAssign;
        if (!pending || pending.done) return;
        pending.done = true;
        pendingAssign = null;
        pending.toast.close();
        const send = () => apiFetch(`/tickets/${pending.ticketId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: assignBody(pending),
            silent: true,
        });
        try {
            await send();
        } catch (err) {
            // El servidor pide confirmar (por ejemplo, fuera de turno): se pregunta ahora
            if (err.status === 409 && !pending.confirmUnavailable && confirm(err.message)) {
                pending.confirmUnavailable = true;
                try {
                    await send();
                } catch (retryErr) {
                    showError(`No se reasignó el ticket #${pending.ticketId}: ${retryErr.message}`);
                    if (selectedTicketId === pending.ticketId) reloadSelectedTicketAndList();
                    return;
                }
            } else {
                if (err.status !== undefined) showError(`No se reasignó el ticket #${pending.ticketId}: ${err.message}`);
                if (selectedTicketId === pending.ticketId) reloadSelectedTicketAndList();
                return;
            }
        }
        if (selectedTicketId === pending.ticketId) reloadSelectedTicketAndList();
        else loadQueue({ quiet: true });
        pollNavCounts(false);
    }

    function undoPendingAssign(pending) {
        if (pending.done) return;
        pending.done = true;
        if (pendingAssign === pending) pendingAssign = null;
        // El ticket vuelve a mostrar el responsable de antes
        if (selectedTicketId === pending.ticketId && currentTicket) renderStaffDetail(currentTicket);
        toast('Reasignación cancelada. No se envió ningún correo.');
    }

    // Al cerrar sesión no se envía nada pendiente
    function cancelPendingAssignSilently() {
        if (!pendingAssign) return;
        pendingAssign.done = true;
        pendingAssign.toast.close();
        pendingAssign = null;
    }

    // ----- Avisos de vencimiento (campana) -----
    // El servidor decide quién ve qué: 50 %, 80 % y vencido al responsable; al 80 %, si el responsable
    // no está disponible, también a los coordinadores con la sugerencia; el reporte de vencimiento a
    // coordinadores y administradores. Se revisa con la actualización de cada minuto.
    const navBell = document.getElementById('navBell');
    const navBellCount = document.getElementById('navBellCount');
    const bellPanel = document.getElementById('bellPanel');
    const bellList = document.getElementById('bellList');
    let bellItems = [];
    let bellKnown = new Set();  // Avisos ya vistos en esta sesión: solo los nuevos sacan un toast
    let bellLoaded = false;

    // Los de borradores no tienen ticket: se identifican por el borrador
    const noticeKey = (item) => (item.kind === 'draft_assigned' ? `draft:${item.draft_id}` : `${item.ticket_id}:${item.kind}`);

    // "2 h 05 min", "45 min", "3 d 4 h"
    function durationText(seconds) {
        const minutes = Math.max(0, Math.floor(seconds / 60));
        const days = Math.floor(minutes / 1440);
        const hours = Math.floor((minutes % 1440) / 60);
        const mins = minutes % 60;
        if (days) return `${days} d ${hours} h`;
        if (hours) return `${hours} h ${String(mins).padStart(2, '0')}`;
        return `${mins} min`;
    }

    function secondsUntil(dateString) {
        return Math.round((parseServerDate(dateString) - Date.now()) / 1000);
    }

    function noticeText(item) {
        if (item.kind === 'draft_assigned') return `Nuevo borrador asignado · ${item.title}`;
        const ticket = `El ticket #${item.ticket_id} · ${item.title}`;
        if (item.kind === 'report') return `Reporte de vencimiento del ticket #${item.ticket_id} · ${item.title}`;
        if (item.kind === 'overdue') return `${ticket} venció hace ${durationText(-secondsUntil(item.due_at))}`;
        return `${ticket} vence en ${durationText(secondsUntil(item.due_at))}`;
    }

    function noticeToastText(item) {
        if (item.kind === 'draft_assigned') return `Nuevo borrador asignado · ${item.title}`;
        if (item.kind === 'report') return `Reporte de vencimiento del ticket #${item.ticket_id} · pendiente de causa`;
        if (item.kind === 'overdue') return `El ticket #${item.ticket_id} acaba de vencer`;
        return `El ticket #${item.ticket_id} vence en ${durationText(secondsUntil(item.due_at))}`;
    }

    function resetBell() {
        bellItems = [];
        bellKnown = new Set();
        bellLoaded = false;
        bellList.replaceChildren();
        navBellCount.hidden = true;
        closeBellPanel();
    }

    function renderBell(data) {
        bellItems = data.items;
        navBellCount.hidden = data.unread === 0;
        navBellCount.textContent = data.unread > 99 ? '99+' : String(data.unread);
        const label = data.unread ? `Avisos de vencimiento (${data.unread} sin leer)` : 'Avisos de vencimiento';
        navBell.setAttribute('aria-label', label);
        navBell.title = label;
        // Al entrar no se repite lo que ya estaba: solo avisa lo que aparece con la sesión abierta
        if (bellLoaded) {
            data.items.filter(item => !item.read && !bellKnown.has(noticeKey(item))).forEach(item => {
                toast(noticeToastText(item), {
                    action: item.kind === 'draft_assigned' ? 'Ver borrador'
                        : (item.kind === 'report' || item.kind === 'overdue' ? 'Ver reporte' : 'Ver ticket'),
                    duration: 8000,
                    onClose: (reason) => { if (reason === 'action') openNotice(item); },
                });
            });
        }
        data.items.forEach(item => bellKnown.add(noticeKey(item)));
        bellLoaded = true;
        if (!bellPanel.hidden) renderBellList();
    }

    function renderBellList() {
        bellList.replaceChildren();
        document.getElementById('bellReadAll').disabled = !bellItems.some(item => !item.read);
        if (!bellItems.length) {
            const li = makeEl('li', 'bell-empty', 'No hay avisos de vencimiento.');
            bellList.appendChild(li);
            return;
        }
        bellItems.forEach(item => {
            const calm = item.kind === 'due_50' || item.kind === 'draft_assigned';
            const li = makeEl('li', `bell-item${item.read ? '' : ' is-unread'}${calm ? '' : ' is-urgent'}`);
            const open = makeEl('button', 'bell-open');
            open.type = 'button';
            open.dataset.key = noticeKey(item);
            open.appendChild(makeEl('span', 'bell-text', noticeText(item)));
            if (item.needs_review) open.appendChild(makeEl('span', 'ui-tag is-warn bell-review', 'Pendiente de revisar'));
            if (!item.read) open.appendChild(makeEl('span', 'sr-only', ' (sin leer)'));
            li.appendChild(open);
            if (item.suggestion) {
                const reassign = makeEl('button', 'ui-btn ui-btn-secondary ui-btn-sm bell-reassign', `Reasignar a ${item.suggestion.name}`);
                reassign.type = 'button';
                reassign.dataset.ticketId = String(item.ticket_id);
                reassign.dataset.userId = String(item.suggestion.user_id);
                li.appendChild(reassign);
            }
            bellList.appendChild(li);
        });
    }

    function openBellPanel() {
        renderBellList();
        bellPanel.hidden = false;
        navBell.setAttribute('aria-expanded', 'true');
        const first = bellPanel.querySelector('button:not(:disabled)');
        if (first) first.focus();
    }

    function closeBellPanel(returnFocus = false) {
        if (bellPanel.hidden) return;
        bellPanel.hidden = true;
        navBell.setAttribute('aria-expanded', 'false');
        if (returnFocus) navBell.focus();
    }

    async function markNoticesRead(body) {
        try {
            await apiFetch('/notifications/overdue/read', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), silent: true,
            });
        } catch (err) {
            return; // Se vuelve a intentar con el siguiente aviso
        }
        const data = await silentGet('/notifications/overdue');
        if (data) renderBell(data);
    }

    // Vencido o reporte: al Reporte de vencimiento; 50 % y 80 %: al ticket
    function openNotice(item) {
        markNoticesRead(item.kind === 'draft_assigned' ? { draft_id: item.draft_id } : { ticket_id: item.ticket_id });
        closeBellPanel();
        closeSideNav();
        if (item.kind === 'draft_assigned') openDraftById(item.draft_id);
        else if (item.kind === 'overdue' || item.kind === 'report') openOverdueReport(item.ticket_id);
        else openTicketById(item.ticket_id);
    }

    navBell.addEventListener('click', () => {
        if (bellPanel.hidden) openBellPanel();
        else closeBellPanel();
    });
    bellList.addEventListener('click', async (e) => {
        const reassign = e.target.closest('.bell-reassign');
        if (reassign) {
            const ticketId = Number(reassign.dataset.ticketId);
            markNoticesRead({ ticket_id: ticketId });
            closeBellPanel();
            closeSideNav();
            await openTicketById(ticketId);
            // El mismo flujo de reasignar, con Deshacer
            if (selectedTicketId === ticketId && currentTicket) scheduleReassign(Number(reassign.dataset.userId));
            return;
        }
        const open = e.target.closest('.bell-open');
        if (!open) return;
        const item = bellItems.find(i => noticeKey(i) === open.dataset.key);
        if (item) openNotice(item);
    });
    document.getElementById('bellReadAll').addEventListener('click', () => markNoticesRead({ all: true }));
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !bellPanel.hidden) closeBellPanel(true);
    });
    document.addEventListener('click', (e) => {
        if (!bellPanel.hidden && !bellPanel.contains(e.target) && !navBell.contains(e.target)) closeBellPanel();
    });

    // ----- Reporte de vencimiento -----
    const odContent = document.getElementById('odContent');
    const OVERDUE_CAUSES = {
        no_staff: 'Sin técnicos en turno',
        assignee_unavailable: 'Responsable no disponible',
        high_load: 'Carga alta',
        off_hours: 'Fuera de horario',
        waiting_client: 'Esperando al cliente',
        other: 'Otro'
    };
    let odReport = null;
    let odSeq = 0;

    async function openOverdueReport(ticketId) {
        const fromRoute = routing;
        showView('overdueReport');
        if (!fromRoute) {
            // Vencidos queda marcado en el menú y la ruta es la del reporte
            routing = true;
            setSideNavActive('overdue');
            routing = false;
            history.pushState(null, '', `#/${ticketRoute(ticketId, true)}`);
        }
        document.getElementById('odCrumb').textContent = `Ticket #${ticketId}`;
        setDocumentTitle(`Reporte de vencimiento · Ticket #${ticketId}`);
        window.scrollTo(0, 0);
        const seq = ++odSeq;
        odReport = null;
        showSkeleton(odContent, { rows: 5 });
        let report;
        try {
            report = await apiFetch(`/tickets/${ticketId}/overdue-report`, { silent: true });
        } catch (err) {
            if (seq !== odSeq) return;
            clearSkeleton(odContent);
            odContent.replaceChildren(errorState(err, () => openOverdueReport(ticketId)));
            return;
        }
        if (seq !== odSeq) return;
        clearSkeleton(odContent);
        renderOverdueReport(report);
    }

    function odCard(label, value, sub) {
        const card = makeEl('section', 'ui-card od-card');
        card.appendChild(makeEl('h2', 'ui-kicker', label));
        card.appendChild(makeEl('p', 'od-card-value', value));
        if (sub) card.appendChild(makeEl('p', 'od-card-sub', sub));
        return card;
    }

    function renderOverdueReport(report) {
        odReport = report;
        odContent.replaceChildren();
        const canManage = OPERATION_ROLES.includes(currentUser.role);

        const head = makeEl('header', 'od-head');
        const titles = makeEl('div', 'od-titles');
        titles.appendChild(makeEl('p', 'od-kicker ui-num', `#${report.id}`));
        const title = makeEl('h1', 'od-title', report.title);
        title.id = 'odTitle';
        titles.appendChild(title);
        const tags = makeEl('p', 'od-tags');
        tags.appendChild(makeEl('span', 'ui-tag is-danger', `Vencido hace ${durationText(report.overdue_seconds)}`));
        const level = report.level === null ? 'Sin nivel' : `Nivel ${report.level}`;
        tags.appendChild(makeEl('span', 'od-meta',
            ` ${level} · ${SOURCE_LABELS[report.source] || report.source} · Prioridad ${report.priority || '—'} · ${report.category || 'Sin categoría'}`));
        titles.appendChild(tags);
        head.appendChild(titles);
        const actions = makeEl('div', 'od-actions');
        const open = makeEl('button', 'ui-btn ui-btn-secondary od-open', 'Abrir ticket');
        open.type = 'button';
        actions.appendChild(open);
        if (canManage && !['resolved', 'closed'].includes(report.status)) {
            const reassign = makeEl('button', 'ui-btn ui-btn-primary od-reassign', 'Reasignar');
            reassign.type = 'button';
            actions.appendChild(reassign);
        }
        const pdf = makeEl('button', 'ui-btn ui-btn-secondary od-pdf', 'Descargar PDF');
        pdf.type = 'button';
        actions.appendChild(pdf);
        head.appendChild(actions);
        odContent.appendChild(head);

        const cards = makeEl('div', 'od-cards');
        const contact = [report.contact_phone, report.contact_id_number].filter(Boolean).join(' · ');
        cards.appendChild(odCard('Cliente', report.requester_name || '—', contact || null));
        cards.appendChild(odCard('Responsable actual', report.assigned_name || 'Sin asignar'));
        cards.appendChild(odCard('Creación', exactDateTime(report.created_at)));
        cards.appendChild(odCard('Vencimiento', exactDateTime(report.due_at)));
        cards.appendChild(odCard('Sin primera respuesta', durationText(report.without_response_seconds),
            report.first_response_at ? `Respondió el ${exactDateTime(report.first_response_at)}` : 'Todavía sin respuesta'));
        cards.appendChild(odCard('Última actividad', report.last_activity_at ? exactDateTime(report.last_activity_at) : '—'));
        odContent.appendChild(cards);

        const grid = makeEl('div', 'od-grid');
        const timeline = makeEl('section', 'ui-card od-section');
        timeline.setAttribute('aria-labelledby', 'odTimelineTitle');
        const timelineTitle = makeEl('h2', 'ui-kicker', 'Línea de tiempo');
        timelineTitle.id = 'odTimelineTitle';
        timeline.appendChild(timelineTitle);
        const list = makeEl('ol', 'od-timeline');
        report.timeline.forEach(event => {
            const li = makeEl('li', `od-event is-${event.kind}`);
            li.appendChild(makeEl('span', 'od-event-time', exactDateTime(event.at)));
            const text = makeEl('span', 'od-event-text', event.text);
            if (event.actor) text.appendChild(makeEl('span', 'od-event-actor', ` · ${event.actor}`));
            li.appendChild(text);
            list.appendChild(li);
        });
        timeline.appendChild(list);
        grid.appendChild(timeline);
        grid.appendChild(renderOverdueCause(report));
        odContent.appendChild(grid);
    }

    function renderOverdueCause(report) {
        const box = makeEl('section', 'ui-card od-section od-cause');
        box.setAttribute('aria-labelledby', 'odCauseTitle');
        const heading = makeEl('h2', 'ui-kicker', 'Causa del vencimiento');
        heading.id = 'odCauseTitle';
        box.appendChild(heading);
        const cause = report.cause;
        if (cause) {
            box.appendChild(makeEl('p', 'od-cause-current', cause.label));
            if (cause.note) box.appendChild(makeEl('p', 'od-cause-note', cause.note));
            box.appendChild(makeEl('p', 'od-card-sub', `Registrada por ${cause.recorded_by_name || 'Usuario'} el ${exactDateTime(cause.recorded_at)}`));
        } else {
            box.appendChild(makeEl('p', 'od-cause-pending', 'Causa pendiente de registrar'));
        }
        if (!report.can_record_cause) return box;
        const form = makeEl('form', 'od-cause-form');
        const label = makeEl('label', '', cause ? 'Cambiar la causa' : 'Causa');
        label.htmlFor = 'odCauseSelect';
        const select = makeEl('select', 'ui-input');
        select.id = 'odCauseSelect';
        select.required = true;
        select.appendChild(new Option('Elija la causa', ''));
        Object.entries(OVERDUE_CAUSES).forEach(([value, text]) => select.appendChild(new Option(text, value)));
        select.value = cause ? cause.cause : '';
        const noteLabel = makeEl('label', '', 'Nota (obligatoria con "Otro")');
        noteLabel.htmlFor = 'odCauseNote';
        const note = makeEl('textarea', 'ui-input');
        note.id = 'odCauseNote';
        note.rows = 2;
        note.maxLength = 500;
        note.value = cause && cause.note ? cause.note : '';
        const save = makeEl('button', 'ui-btn ui-btn-primary ui-btn-sm', 'Guardar causa');
        save.type = 'submit';
        form.append(label, select, noteLabel, note, save);
        box.appendChild(form);
        return box;
    }

    odContent.addEventListener('submit', async (e) => {
        if (!e.target.classList.contains('od-cause-form') || !odReport) return;
        e.preventDefault();
        const cause = document.getElementById('odCauseSelect').value;
        const note = document.getElementById('odCauseNote').value.trim();
        if (cause === 'other' && !note) {
            showError('Describa la causa en la nota');
            return;
        }
        const button = e.target.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            const report = await apiFetch(`/tickets/${odReport.id}/overdue-cause`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cause, note: note || null }),
            });
            renderOverdueReport(report);
            toast('Causa registrada.');
            pollNavCounts(false);
        } catch (err) {
            button.disabled = false; // apiFetch ya mostró el error
        }
    });

    odContent.addEventListener('click', async (e) => {
        if (!odReport) return;
        const ticketId = odReport.id;
        if (e.target.closest('.od-open')) {
            openTicketById(ticketId);
        } else if (e.target.closest('.od-reassign')) {
            await openTicketById(ticketId);
            // El mismo control de reasignar del ticket
            const select = detailPanel.querySelector('.sd-assign-select');
            if (selectedTicketId === ticketId && select) {
                select.scrollIntoView({ block: 'center' });
                select.focus();
            }
        } else if (e.target.closest('.od-pdf')) {
            downloadOverduePdf(ticketId, e.target.closest('.od-pdf'));
        }
    });

    async function downloadOverduePdf(ticketId, button) {
        button.disabled = true;
        try {
            const response = await fetch(`${API_BASE}/tickets/${ticketId}/overdue-report.pdf`, {
                headers: { 'Authorization': `Bearer ${authToken}` },
            });
            if (response.status === 401) {
                const data = await response.json().catch(() => ({}));
                handleUnauthorized(data.detail);
                return;
            }
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                showError(getErrorMessage(data));
                return;
            }
            const blob = await response.blob();
            const match = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '');
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = match ? match[1] : `vencimiento-ticket-${ticketId}.pdf`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        } catch (err) {
            showError('No se pudo descargar el PDF');
        } finally {
            button.disabled = false;
        }
    }

    document.getElementById('odBack').addEventListener('click', () => {
        setSideNavActive('overdue');
        setQueueView('overdue', 'Vencidos');
    });

    // ----- Cancelar tickets (coordinador y administrador) -----
    // Como al reasignar: se envía al servidor (y sale el correo al cliente) cuando terminan los 5 s,
    // o antes si la persona cambia de pantalla. "Deshacer" no envía nada.
    const CANCEL_ROLES = ['coordinator'];
    const cancelDialog = document.getElementById('cancelDialog');
    const cancelForm = document.getElementById('cancelForm');
    const cancelNote = document.getElementById('cancelNote');
    const cancelNotify = document.getElementById('cancelNotify');
    const queueBulk = document.getElementById('queueBulk');
    const queueSelectAll = document.getElementById('queueSelectAll');
    const queueBulkCancel = document.getElementById('queueBulkCancel');
    const queueSelected = new Set();
    let cancelDone = null;
    let cancelNotifyTouched = false;
    let pendingCancel = null;

    function canCancelTickets() {
        return Boolean(currentUser && CANCEL_ROLES.includes(currentUser.role));
    }

    // Casillas para cancelar varios: solo en Vencidos
    function queueSelectable() {
        return canCancelTickets() && queueView === 'overdue';
    }

    function renderQueueBulk() {
        const show = queueSelectable();
        queueBulk.hidden = !show;
        if (!show) {
            queueSelected.clear();
            return;
        }
        const checks = [...queueBody.querySelectorAll('.queue-row:not([hidden]) .queue-check')];
        const count = queueSelected.size;
        document.getElementById('queueBulkCount').textContent = count ? `${count} ${count === 1 ? 'seleccionado' : 'seleccionados'}` : '';
        queueBulkCancel.disabled = count === 0;
        queueBulkCancel.textContent = count ? `Cancelar seleccionados (${count})` : 'Cancelar seleccionados';
        queueSelectAll.disabled = checks.length === 0;
        queueSelectAll.checked = checks.length > 0 && checks.every(check => check.checked);
        queueSelectAll.indeterminate = count > 0 && !queueSelectAll.checked;
    }

    queueBody.addEventListener('change', (e) => {
        const check = e.target.closest('.queue-check');
        if (!check) return;
        const id = Number(check.closest('.queue-row').dataset.id);
        if (check.checked) queueSelected.add(id);
        else queueSelected.delete(id);
        renderQueueBulk();
    });

    queueSelectAll.addEventListener('change', () => {
        queueBody.querySelectorAll('.queue-row:not([hidden]) .queue-check').forEach(check => {
            check.checked = queueSelectAll.checked;
            const id = Number(check.closest('.queue-row').dataset.id);
            if (check.checked) queueSelected.add(id);
            else queueSelected.delete(id);
        });
        renderQueueBulk();
    });

    queueBulkCancel.addEventListener('click', () => {
        if (queueSelected.size) startCancel([...queueSelected]);
    });

    // Devuelve { reason, note, notify } o null si la persona vuelve atrás
    function openCancelDialog(ids) {
        return new Promise(resolve => {
            cancelDone = resolve;
            cancelForm.reset();
            cancelNotify.checked = true;
            cancelNotifyTouched = false;
            const title = ids.length > 1 ? `Cancelar ${ids.length} tickets` : `Cancelar ticket #${ids[0]}`;
            document.getElementById('cancelDialogTitle').textContent = title;
            document.getElementById('cancelDialogSubmit').textContent = ids.length > 1 ? title : 'Cancelar ticket';
            cancelDialog.showModal();
            cancelForm.querySelector('input[name="cancelReason"]').focus();
        });
    }

    function closeCancelDialog(result) {
        if (cancelDialog.open) cancelDialog.close();
        const resolve = cancelDone;
        cancelDone = null;
        if (resolve) resolve(result);
    }

    // "Prueba o spam" no avisa al cliente, salvo que la persona marque la casilla a mano
    cancelForm.addEventListener('change', (e) => {
        if (e.target === cancelNotify) {
            cancelNotifyTouched = true;
        } else if (e.target.name === 'cancelReason' && !cancelNotifyTouched) {
            cancelNotify.checked = e.target.value !== 'test_spam';
        }
    });
    cancelForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const reason = cancelForm.querySelector('input[name="cancelReason"]:checked');
        if (!reason) return;
        closeCancelDialog({ reason: reason.value, note: cancelNote.value.trim(), notify: cancelNotify.checked });
    });
    document.getElementById('cancelDialogClose').addEventListener('click', () => closeCancelDialog(null));
    cancelDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeCancelDialog(null);
    });

    function cancelBody(pending) {
        return JSON.stringify({
            ticket_ids: pending.ids, reason: pending.reason, note: pending.note || null, notify_requester: pending.notify,
        });
    }

    function setQueueRowsHidden(ids, hidden) {
        queueBody.querySelectorAll('.queue-row').forEach(row => {
            if (ids.includes(Number(row.dataset.id))) row.hidden = hidden;
        });
    }

    async function startCancel(ids) {
        const choice = await openCancelDialog(ids);
        if (!choice) return;
        commitPendingCancel();
        // Desde el detalle se vuelve a la cola
        if (document.body.classList.contains('ticket-open') && ids.includes(selectedTicketId)) exitTicketPage();
        const pending = { ids, ...choice, done: false };
        pendingCancel = pending;
        ids.forEach(id => queueSelected.delete(id));
        setQueueRowsHidden(ids, true);
        renderQueueBulk();
        const text = ids.length === 1 ? `Ticket #${ids[0]} cancelado.` : `${ids.length} tickets cancelados.`;
        pending.toast = toast(text, {
            action: 'Deshacer',
            duration: UNDO_SECONDS * 1000,
            onClose: (reason) => {
                if (reason === 'action') undoPendingCancel(pending);
                else commitPendingCancel();
            },
        });
    }

    async function commitPendingCancel() {
        const pending = pendingCancel;
        if (!pending || pending.done) return;
        pending.done = true;
        pendingCancel = null;
        pending.toast.close();
        try {
            await apiFetch('/tickets/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: cancelBody(pending),
                silent: true,
            });
        } catch (err) {
            showError(`No se cancelaron los tickets: ${err.message}`);
            setQueueRowsHidden(pending.ids, false);
            renderQueueBulk();
            return;
        }
        if (!isStaffShell()) return;
        loadQueue({ quiet: true });
        pollNavCounts(false);
    }

    function undoPendingCancel(pending) {
        if (pending.done) return;
        pending.done = true;
        if (pendingCancel === pending) pendingCancel = null;
        setQueueRowsHidden(pending.ids, false);
        renderQueueBulk();
        toast('Cancelación deshecha. No se envió ningún correo.');
    }

    // La sesión venció: no se envía nada pendiente
    function cancelPendingCancelSilently() {
        if (!pendingCancel) return;
        pendingCancel.done = true;
        pendingCancel.toast.close();
        pendingCancel = null;
    }

    // Al cerrar o recargar la pestaña: se envía con keepalive para que no se pierda
    window.addEventListener('pagehide', () => {
        const pending = pendingAssign;
        if (!pending || pending.done || !authToken) return;
        pending.done = true;
        pendingAssign = null;
        fetch(`${API_BASE}/tickets/${pending.ticketId}`, {
            method: 'PATCH',
            keepalive: true,
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
            body: assignBody(pending),
        }).catch(() => {});
    });
    window.addEventListener('pagehide', () => {
        const pending = pendingCancel;
        if (!pending || pending.done || !authToken) return;
        pending.done = true;
        pendingCancel = null;
        fetch(`${API_BASE}/tickets/cancel`, {
            method: 'POST',
            keepalive: true,
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
            body: cancelBody(pending),
        }).catch(() => {});
    });

    initApp();
});
