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
    const portalChooser = document.getElementById('portalChooser');
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
        closed: 'Cerrado'
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
                || endpoint.startsWith('/account-reports'))
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
                openNewTicketBtn.style.display = currentUser.role === 'technician' ? 'none' : 'block';
            }
        }

        Promise.all([loadCategories(), loadPriorities(), loadUsers()]).then(() => {
            loadTickets(getFilters());
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
        startDraftsPolling();
        setupChat();
    }

    function showAuthScreen() {
        document.body.classList.remove('profile-required');
        sessionNotice.hidden = true;
        resetEmailChange();
        loginPortal = null;
        forgotMode = false;
        stopDraftsPolling();
        resetChat();
        if (authScreen) authScreen.style.display = 'grid';
        if (topbar) topbar.style.display = 'none';
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

    // Cada opción de inicio de sesión solo deja entrar a sus roles (lo verifica el servidor)
    const PORTALS = {
        admin: { title: 'Administrador', link: null },
        staff: { title: 'Coordinador / Técnico', link: { href: '#activar-cuenta', text: 'Activar cuenta (invitación)' } },
        requester: { title: 'Solicitante', link: { href: '#crear-cuenta', text: 'Crear cuenta de cliente' } }
    };
    const loginTitle = document.getElementById('loginTitle');
    const loginExtraLink = document.getElementById('loginExtraLink');
    let loginPortal = null;
    // "Olvidé mi contraseña" de la opción elegida (loginPortal)
    let forgotMode = false;

    function showAuthPanel() {
        if (window.location.hash.startsWith(RESET_ROUTE)) {
            openResetLink();
            return;
        }
        const route = AUTH_ROUTES[window.location.hash];
        const panel = route || (forgotMode && loginPortal ? 'forgot' : (loginPortal ? 'login' : 'chooser'));
        portalChooser.hidden = panel !== 'chooser';
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

    function openPortal(portal) {
        const info = PORTALS[portal];
        if (!info) return;
        loginPortal = portal;
        loginForm.reset();
        loginTitle.textContent = `Iniciar sesión — ${info.title}`;
        loginExtraLink.hidden = !info.link;
        if (info.link) {
            loginExtraLink.href = info.link.href;
            loginExtraLink.textContent = info.link.text;
        }
        clearHash();
        showAuthPanel();
        document.getElementById('loginEmail').focus();
    }

    function showPortalChooser() {
        loginPortal = null;
        loginForm.reset();
        clearHash();
        showAuthPanel();
    }

    portalChooser.addEventListener('click', (e) => {
        const button = e.target.closest('[data-portal]');
        if (button) openPortal(button.dataset.portal);
    });

    document.getElementById('backToPortals').addEventListener('click', showPortalChooser);

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
            result = await postAuth('/auth/activate', { email, code });
        } catch (err) {
            showError('No se pudo conectar con el servidor');
            return;
        }
        if (!result.ok) {
            showError(getErrorMessage(result.data));
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

        try {
            // fetch propio: los 429/503 se muestran dentro del chat, no en el aviso rojo
            const response = await fetch(`${API_BASE}/chat`, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
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
            const data = await response.json().catch(() => ({}));
            if (session !== chatSession) return;
            pending.remove();
            if (!response.ok) {
                addChatMessage(getErrorMessage(data), 'notice');
                return;
            }
            addChatMessage(data.reply, 'model');
            chatHistory.push({ role: 'user', text: message.slice(0, CHAT_TURN_CHARS) });
            chatHistory.push({ role: 'model', text: String(data.reply).slice(0, CHAT_TURN_CHARS) });
            chatHistory = chatHistory.slice(-CHAT_HISTORY_LIMIT);
        } catch (err) {
            if (session !== chatSession) return;
            pending.remove();
            addChatMessage('No se pudo conectar con el servidor', 'notice');
        } finally {
            if (session === chatSession) setChatBusy(false);
        }
    }

    chatToggle.addEventListener('click', () => {
        if (chatPanel.hidden) openChat();
        else closeChat();
    });

    chatClose.addEventListener('click', closeChat);

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

                const strongWho = document.createElement('strong');
                strongWho.textContent = changedByName;
                rightP.appendChild(strongWho);

                if (entry.field === 'status') {
                    rightP.appendChild(document.createTextNode(' cambió el estado de '));
                    const sOld = document.createElement('strong');
                    sOld.textContent = STATE_MAP[entry.old_value] || entry.old_value || 'Nuevo';
                    rightP.appendChild(sOld);
                    rightP.appendChild(document.createTextNode(' a '));
                    const sNew = document.createElement('strong');
                    sNew.textContent = STATE_MAP[entry.new_value] || entry.new_value || '';
                    rightP.appendChild(sNew);
                } else if (entry.field === 'assigned_to') {
                    if (entry.new_value && parseInt(entry.new_value) === entry.changed_by) {
                        rightP.appendChild(document.createTextNode(' se asignó el ticket'));
                    } else if (!entry.new_value) {
                        rightP.appendChild(document.createTextNode(' desasignó el ticket'));
                    } else {
                        rightP.appendChild(document.createTextNode(' asignó el ticket a '));
                        const sAssign = document.createElement('strong');
                        sAssign.textContent = userMap[parseInt(entry.new_value)] || entry.new_value;
                        rightP.appendChild(sAssign);
                    }
                } else if (entry.field === 'priority_id') {
                    rightP.appendChild(document.createTextNode(' cambió la prioridad de '));
                    const pOld = document.createElement('strong');
                    pOld.textContent = priorityMap[entry.old_value] || entry.old_value || '';
                    rightP.appendChild(pOld);
                    rightP.appendChild(document.createTextNode(' a '));
                    const pNew = document.createElement('strong');
                    pNew.textContent = priorityMap[entry.new_value] || entry.new_value || '';
                    rightP.appendChild(pNew);
                } else if (entry.field === 'category_id') {
                    rightP.appendChild(document.createTextNode(' cambió la categoría de '));
                    const cOld = document.createElement('strong');
                    cOld.textContent = categoryMap[entry.old_value] || entry.old_value || '';
                    rightP.appendChild(cOld);
                    rightP.appendChild(document.createTextNode(' a '));
                    const cNew = document.createElement('strong');
                    cNew.textContent = categoryMap[entry.new_value] || entry.new_value || '';
                    rightP.appendChild(cNew);
                }

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

    function showSuccess(message) {
        successMessage.textContent = message;
        successMessage.style.display = 'block';
        clearTimeout(showSuccess.timer);
        showSuccess.timer = setTimeout(() => {
            successMessage.style.display = 'none';
        }, 3000);
    }

    document.querySelectorAll('.modal').forEach(modal => {
        const closeBtn = modal.querySelector('.modal-close-btn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                modal.style.display = 'none';
                if (modal.id === 'usersModal') {
                    closeUsersModal();
                }
                if (modal.id === 'newTicketModal') {
                    resetRequesterField();
                }
            });
        }
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
                if (modal.id === 'usersModal') {
                    closeUsersModal();
                }
                if (modal.id === 'newTicketModal') {
                    resetRequesterField();
                }
            }
        });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal').forEach(modal => {
                if (modal.style.display === 'flex') {
                    modal.style.display = 'none';
                    if (modal.id === 'usersModal') {
                        closeUsersModal();
                    }
                    if (modal.id === 'newTicketModal') {
                        resetRequesterField();
                    }
                }
            });
        }
    });

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

            await apiFetch('/tickets', {
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

            document.getElementById('newTicketModal').style.display = 'none';
            ticketForm.reset();
            resetRequesterField();
            await loadTickets(getFilters());

            showSuccess('Ticket creado');
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
        navUsers.addEventListener('click', () => {
            const usersModal = document.getElementById('usersModal');
            if (usersModal) usersModal.style.display = 'flex';
            setActiveTab(navUsers);
            renderInvitesSection();
        });
    }

    // ===== Vistas: Tickets, Directorio y Administración =====
    const navDirectory = document.getElementById('navDirectory');
    const navAdmin = document.getElementById('navAdmin');
    const splitView = document.querySelector('main .split');
    const directoryView = document.getElementById('directoryView');
    const navAccounts = document.getElementById('navAccounts');
    const accountsView = document.getElementById('accountsView');
    const adminView = document.getElementById('adminView');
    const navDrafts = document.getElementById('navDrafts');
    const draftsView = document.getElementById('draftsView');
    const navProfile = document.getElementById('navProfile');
    const profileView = document.getElementById('profileView');
    const navReports = document.getElementById('navReports');
    const reportsView = document.getElementById('reportsView');

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
        // La lista de cuentas con correos solo es para quien gestiona cuentas
        navUsers.hidden = !(role === 'coordinator' || role === 'admin');
        navDrafts.hidden = !DRAFT_ROLES.includes(role);
        // Mi cuenta: todos los roles que inician sesión
        navProfile.hidden = !role || role === 'agent';
    }

    function showView(name) {
        if (splitView) splitView.style.display = name === 'tickets' ? '' : 'none';
        directoryView.hidden = name !== 'directory';
        accountsView.hidden = name !== 'accounts';
        adminView.hidden = name !== 'admin';
        draftsView.hidden = name !== 'drafts';
        profileView.hidden = name !== 'profile';
        reportsView.hidden = name !== 'reports';
        const tabs = {
            tickets: navTickets, directory: navDirectory, accounts: navAccounts,
            admin: navAdmin, drafts: navDrafts, profile: navProfile, reports: navReports
        };
        setActiveTab(tabs[name]);
        if (name === 'directory') loadDirectory();
        if (name === 'accounts') loadAccountsView();
        if (name === 'admin') loadAdminPanel();
        if (name === 'drafts') loadDrafts();
        if (name === 'reports') loadReports();
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
        // El solicitante cambia el teléfono en sus datos personales; el personal, en su propio formulario
        document.getElementById('accountPersonal').hidden = !isRequester;
        accountPhoneSection.hidden = isRequester || required;
        accountAvailabilitySection.hidden = currentUser.role !== 'technician';
        accountScheduleSection.hidden = currentUser.role !== 'technician';
        accountEmailSection.hidden = required;
        accountPasswordSection.hidden = required;
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
        if (isRequester) (locked ? profilePhone : profileName).focus();
    }

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
    const ownAvailabilityStatus = document.getElementById('ownAvailabilityStatus');
    const ownAvailabilityReturn = document.getElementById('ownAvailabilityReturn');
    const ownAvailabilityReturnGroup = document.getElementById('ownAvailabilityReturnGroup');
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
        if (currentUser.role === 'technician') {
            renderOwnSchedule(currentUser.schedule || [], currentUser.schedule_summary);
            fillAvailabilityFields(ownAvailabilityStatus, ownAvailabilityReturn, ownAvailabilityReturnGroup,
                currentUser.availability, currentUser.return_date);
        }
    }

    function refreshTopbarUser() {
        document.querySelector('.topbar-user-name').textContent = currentUser.name || currentUser.email;
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

    ownAvailabilityStatus.addEventListener('change', () => {
        syncReturnField(ownAvailabilityStatus, ownAvailabilityReturn, ownAvailabilityReturnGroup);
    });

    document.getElementById('accountAvailabilityForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        try {
            currentUser = await apiFetch('/account/availability', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(availabilityPayload(ownAvailabilityStatus, ownAvailabilityReturn))
            });
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        fillAvailabilityFields(ownAvailabilityStatus, ownAvailabilityReturn, ownAvailabilityReturnGroup,
            currentUser.availability, currentUser.return_date);
        showSuccess('Disponibilidad guardada');
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
    const DRAFT_ROLES = ['technician', 'coordinator', 'admin'];
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

    async function loadDrafts() {
        try {
            drafts = await apiFetch('/drafts');
        } catch (err) {
            return;
        }
        updateDraftsBadge(drafts.length);
        renderDraftList();
        const current = drafts.find(d => d.id === selectedDraftId);
        if (current) {
            showDraft(current);
        } else {
            selectedDraftId = null;
            draftContent.hidden = true;
            draftPlaceholder.hidden = false;
            draftPlaceholder.textContent = drafts.length ? 'Seleccione un borrador.' : 'No hay borradores pendientes.';
        }
    }

    function renderDraftList() {
        draftsList.replaceChildren();
        drafts.forEach(draft => {
            const li = document.createElement('li');
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'draft-item';
            btn.dataset.draftId = String(draft.id);
            if (draft.id === selectedDraftId) btn.setAttribute('aria-current', 'true');

            // Mismo formato que el resumen diario
            const number = document.createElement('span');
            number.className = 'draft-item-meta';
            number.textContent = `Borrador #${draft.id}`;
            const subject = document.createElement('span');
            subject.className = 'draft-item-subject';
            subject.textContent = draft.subject || '(Sin asunto)';
            const meta = document.createElement('span');
            meta.className = 'draft-item-meta';
            meta.textContent = `${draft.requester_name} · ${formatDateTime(draft.created_at)}`;
            const state = document.createElement('span');
            state.className = 'draft-item-meta';
            state.textContent = DRAFT_STATUS_LABELS[draft.status] || draft.status;
            btn.append(number, subject, meta, state);
            if (draft.needs_review) {
                const review = document.createElement('span');
                review.className = 'ai-review';
                review.textContent = 'Revisar';
                btn.appendChild(review);
            }
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

        document.getElementById('draftNumber').textContent = `Borrador #${draft.id}`;
        draftDetailsBtn.hidden = !['pending_review', 'waiting_contact'].includes(draft.status);
        document.getElementById('draftSubject').textContent = draft.subject || '(Sin asunto)';
        document.getElementById('draftMeta').textContent =
            `${draft.requester_name} · ${formatDateTime(draft.created_at)} · ${DRAFT_STATUS_LABELS[draft.status] || draft.status}`;
        shownDraft = draft;
        renderDraftFlags(draft);
        const draftReason = document.getElementById('draftReason');
        draftReason.textContent = draft.pending_reason ? `Pendiente: ${draft.pending_reason}` : '';
        draftReason.hidden = !draft.pending_reason;
        document.getElementById('draftBody').textContent = draft.body;

        const ai = document.getElementById('draftAi');
        ai.replaceChildren();
        const head = document.createElement('div');
        head.className = 'ai-head';
        const badge = document.createElement('span');
        const level = draft.ai_level;
        badge.className = `ai-level ai-level-${level === null ? 'none' : level}`;
        badge.textContent = level === null ? 'Sin nivel' : `Nivel ${level} · ${AI_LEVEL_NAMES[level]}`;
        head.appendChild(badge);
        if (draft.needs_review) {
            const review = document.createElement('span');
            review.className = 'ai-review';
            review.textContent = 'Revisar';
            head.appendChild(review);
        }
        ai.appendChild(head);
        const summary = document.createElement('p');
        summary.textContent = draft.summary || 'La IA no dejó un resumen.';
        if (!draft.summary) summary.className = 'ai-muted';
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
        unlock: 'Desbloquear'
    };
    // Mismas reglas que app/accounts.py (solo comodidad: el servidor también las revisa)
    const MANAGE_RULES = {
        admin: {
            active: ['coordinator', 'technician', 'requester'],
            reset: ['coordinator', 'technician'],
            unlock: ['admin', 'coordinator', 'technician', 'requester'],
            availability: ['technician'],
            schedule: ['technician']
        },
        coordinator: {
            active: ['technician', 'requester'],
            reset: ['technician'],
            unlock: ['technician', 'requester'],
            availability: ['technician'],
            schedule: ['technician']
        }
    };

    function canManage(user, kind) {
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
            const button = makeActionButton('availability', 'Cambiar disponibilidad');
            button.dataset.availability = user.availability || 'available';
            button.dataset.returnDate = user.return_date || '';
            buttons.push(button);
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
        const row = button.closest('tr');
        const name = row.dataset.userName;
        const action = button.dataset.action;
        if (action === 'availability') return openAvailabilityDialog(row.dataset.userId, name, button.dataset);
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
            unlock: 'Correo desbloqueado'
        };
        showSuccess(messages[action]);
        return true;
    }

    // ----- Disponibilidad, identidad y reportes (solo personal) -----
    const AVAILABILITY_LABELS = { available: 'Disponible', vacation: 'Vacaciones', away: 'Ausente' };
    const IDENTITY_LABELS = {
        match: 'Verificado', mismatch: 'No coincide', not_found: 'No encontrado', unverified: 'Sin verificar'
    };
    const REPORT_STATUS = { open: 'Abierto', confirmed: 'Confirmado', dismissed: 'Descartado' };
    const REPORT_REVIEW_ROLES = ['coordinator', 'admin'];

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

    function syncReturnField(statusSelect, dateInput, group) {
        const away = statusSelect.value !== 'available';
        group.hidden = !away;
        if (!away) dateInput.value = '';
        dateInput.min = localToday();
    }

    function fillAvailabilityFields(statusSelect, dateInput, group, status, returnDate) {
        statusSelect.value = status || 'available';
        dateInput.value = returnDate || '';
        syncReturnField(statusSelect, dateInput, group);
    }

    function availabilityPayload(statusSelect, dateInput) {
        const status = statusSelect.value;
        return { status, return_date: status !== 'available' && dateInput.value ? dateInput.value : null };
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

    // Diálogo de disponibilidad (coordinador y admin sobre técnicos)
    const availabilityDialog = document.getElementById('availabilityDialog');
    const availabilityForm = document.getElementById('availabilityForm');
    const availabilityStatus = document.getElementById('availabilityStatus');
    const availabilityReturn = document.getElementById('availabilityReturn');
    const availabilityReturnGroup = document.getElementById('availabilityReturnGroup');
    const availabilityError = document.getElementById('availabilityError');
    let availabilityUserId = null;
    let availabilityDone = null;

    function openAvailabilityDialog(userId, name, current) {
        return new Promise(resolve => {
            availabilityUserId = userId;
            availabilityDone = resolve;
            document.getElementById('availabilityTarget').textContent = name;
            fillAvailabilityFields(availabilityStatus, availabilityReturn, availabilityReturnGroup,
                current.availability, current.returnDate);
            availabilityError.hidden = true;
            availabilityDialog.showModal();
            availabilityStatus.focus();
        });
    }

    function closeAvailabilityDialog(done) {
        if (availabilityDialog.open) availabilityDialog.close();
        const resolve = availabilityDone;
        availabilityDone = null;
        if (resolve) resolve(done);
    }

    availabilityStatus.addEventListener('change', () => {
        syncReturnField(availabilityStatus, availabilityReturn, availabilityReturnGroup);
    });
    document.getElementById('availabilityCancel').addEventListener('click', () => closeAvailabilityDialog(false));
    availabilityDialog.addEventListener('cancel', (e) => {
        e.preventDefault();
        closeAvailabilityDialog(false);
    });
    availabilityForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        availabilityError.hidden = true;
        try {
            await apiFetch(`/accounts/${availabilityUserId}/availability`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(availabilityPayload(availabilityStatus, availabilityReturn)),
                silent: true
            });
        } catch (err) {
            if (err.status === undefined) {
                closeAvailabilityDialog(false);
                return;
            }
            availabilityError.textContent = err.message;
            availabilityError.hidden = false;
            return;
        }
        closeAvailabilityDialog(true);
        showSuccess('Disponibilidad guardada');
        loadUsers();
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

    function renderReportItem(item) {
        const li = document.createElement('li');
        li.className = 'change-item';
        const head = document.createElement('div');
        head.className = 'change-head';
        const title = document.createElement('strong');
        title.textContent = `${item.target_name} (${item.target_email})`;
        const time = document.createElement('span');
        time.className = 'change-time';
        time.textContent = formatDateTime(item.created_at);
        head.append(title, time);
        li.appendChild(head);
        if (needsMyReview(item)) li.appendChild(makeTag('Requiere su revisión', 'tag-review'));
        li.appendChild(requestLine('Motivo', item.reason_label));
        if (item.comment) li.appendChild(requestLine('Comentario', item.comment));
        li.appendChild(requestLine('Reportado por', item.reporter));
        let state = REPORT_STATUS[item.status] || item.status;
        if (item.resolver) state += ` por ${item.resolver} (${formatDateTime(item.resolved_at)})`;
        li.appendChild(requestLine('Estado', state));
        if (item.resolution_note) li.appendChild(requestLine('Nota', item.resolution_note));
        if (item.other_open > 0) {
            const others = document.createElement('p');
            others.className = 'report-others';
            others.textContent = item.other_open === 1
                ? 'Esta cuenta tiene otro reporte abierto'
                : `Esta cuenta tiene otros ${item.other_open} reportes abiertos`;
            if (!reportsAccount) {
                const filterBtn = document.createElement('button');
                filterBtn.type = 'button';
                filterBtn.className = 'link-btn';
                filterBtn.dataset.filterTarget = item.target_user_id;
                filterBtn.dataset.filterName = item.target_name;
                filterBtn.textContent = 'Ver solo esta cuenta';
                others.append(' · ', filterBtn);
            }
            li.appendChild(others);
        }
        if (item.status === 'open') {
            const actions = document.createElement('div');
            actions.className = 'account-actions';
            // Quien reportó puede retirarlo, pero la confirmación la da otra persona
            const own = item.reporter_id === currentUser.id;
            const options = own
                ? [['dismiss', 'Retirar reporte']]
                : [['confirm', 'Confirmar'], ['dismiss', 'Descartar']];
            options.forEach(([decision, label]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = decision === 'confirm' ? 'btn-action btn-small' : 'btn-secondary btn-small';
                button.dataset.reportId = item.id;
                button.dataset.decision = decision;
                button.textContent = label;
                actions.appendChild(button);
            });
            li.appendChild(actions);
            if (own) {
                const waiting = document.createElement('p');
                waiting.className = 'report-waiting';
                waiting.textContent = 'Esperando revisión de otro coordinador o un administrador.';
                li.appendChild(waiting);
            }
        }
        return li;
    }

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
        try {
            items = await apiFetch(`/account-reports${query ? `?${query}` : ''}`);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        // sort es estable: dentro de cada grupo se mantiene el orden del servidor (más recientes primero)
        items.sort((a, b) => Number(needsMyReview(b)) - Number(needsMyReview(a)));
        reportsList.replaceChildren(...items.map(renderReportItem));
        reportsEmpty.textContent = reportsAccount
            ? 'Esta cuenta no tiene reportes con ese estado.'
            : 'No hay reportes con ese estado.';
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
        const button = e.target.closest('button[data-decision]');
        if (!button) return;
        const confirming = button.dataset.decision === 'confirm';
        const note = prompt(confirming ? 'Nota para confirmar el reporte (obligatoria):' : 'Nota para descartar el reporte (obligatoria):');
        if (note === null) return;
        if (!note.trim()) {
            showError('Escriba una nota');
            return;
        }
        button.disabled = true;
        try {
            await apiFetch(`/account-reports/${button.dataset.reportId}/${button.dataset.decision}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note: note.trim() })
            });
            showSuccess(confirming ? 'Reporte confirmado' : 'Reporte descartado');
        } catch (err) {
            // apiFetch ya mostró el error
        }
        loadReports();
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
        changesPage = 1;
        loadAccountChanges();
    });

    // ----- Cuentas del coordinador: Equipo, Clientes y Mis solicitudes -----
    const teamBody = document.getElementById('teamBody');
    const teamEmpty = document.getElementById('teamEmpty');
    const clientsSearch = document.getElementById('clientsSearch');
    const clientsBody = document.getElementById('clientsBody');
    const clientsEmpty = document.getElementById('clientsEmpty');
    const myRequestsList = document.getElementById('myRequestsList');
    const myRequestsEmpty = document.getElementById('myRequestsEmpty');
    let clientsTimer = null;
    let clientsSeq = 0;

    function loadAccountsView() {
        loadTeam();
        loadClients();
        loadMyRequests();
    }

    function accountRow(user) {
        const row = document.createElement('tr');
        row.dataset.userId = user.id;
        row.dataset.userName = user.name || user.email;
        row.dataset.userRole = user.role;
        row.appendChild(makeCell(user.name || '—'));
        row.appendChild(makeCell(user.email));
        return row;
    }

    function stateCell(user) {
        const td = document.createElement('td');
        const state = document.createElement('span');
        state.className = user.is_active ? 'state-active' : 'state-inactive';
        state.textContent = user.is_active ? 'Activa' : 'Desactivada';
        td.appendChild(state);
        if (user.login_locked) {
            const locked = document.createElement('span');
            locked.className = 'state-locked';
            locked.textContent = 'Bloqueada por intentos';
            td.appendChild(locked);
        }
        accountTags(user).forEach(tag => td.appendChild(tag));
        return td;
    }

    async function loadTeam() {
        let users;
        try {
            users = await apiFetch('/directory?role=technician');
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        teamBody.replaceChildren();
        teamEmpty.hidden = users.length > 0;
        users.forEach(user => {
            const row = accountRow(user);
            row.appendChild(makeCell(String(user.tickets_assigned_open), 'num'));
            row.appendChild(stateCell(user));
            const actionsTd = document.createElement('td');
            const buttons = accountActionButtons(user);
            const select = document.createElement('select');
            select.className = 'team-role-select';
            select.setAttribute('aria-label', `Rol nuevo para ${row.dataset.userName}`);
            [['', 'Cambiar rol a…'], ['coordinator', 'Coordinador'], ['requester', 'Solicitante']].forEach(([value, label]) => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = label;
                select.appendChild(option);
            });
            buttons.push(select, makeActionButton('request-role', 'Solicitar cambio de rol'));
            actionsTd.appendChild(accountActionsWrap(buttons));
            row.appendChild(actionsTd);
            teamBody.appendChild(row);
        });
    }

    async function loadClients() {
        const q = clientsSearch.value.trim();
        const seq = ++clientsSeq;
        if (!q) {
            clientsBody.replaceChildren();
            clientsEmpty.textContent = 'Escriba un nombre o correo para buscar.';
            clientsEmpty.hidden = false;
            return;
        }
        let users;
        try {
            users = await apiFetch(`/directory?${new URLSearchParams({ role: 'requester', q }).toString()}`);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        // Solo la última búsqueda pinta la tabla
        if (seq !== clientsSeq) return;
        clientsBody.replaceChildren();
        clientsEmpty.textContent = 'No hay solicitantes con esa búsqueda.';
        clientsEmpty.hidden = users.length > 0;
        users.forEach(user => {
            const row = accountRow(user);
            row.appendChild(stateCell(user));
            const actionsTd = document.createElement('td');
            const buttons = accountActionButtons(user);
            // Nombre y cédula fijos: solo se corrigen con aprobación del administrador
            if (user.identity_locked) {
                buttons.push(makeActionButton('request-name', 'Corregir nombre'));
                buttons.push(makeActionButton('request-id_number', 'Corregir cédula'));
            }
            actionsTd.appendChild(accountActionsWrap(buttons));
            row.appendChild(actionsTd);
            clientsBody.appendChild(row);
        });
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
        const row = button.closest('tr');
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
    clientsBody.addEventListener('click', (e) => handleAccountsClick(e, loadClients));
    clientsSearch.addEventListener('input', () => {
        clearTimeout(clientsTimer);
        clientsTimer = setTimeout(loadClients, 300);
    });

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
                ['new', 'in_progress', 'resolved', 'closed']);

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
        changesPage = 1;
        loadAccountChanges();
        loadPendingRequests();
    }

    // ----- Registro de cambios de cuentas (solo administrador) -----
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
        login_locked: 'Bloqueo por intentos',
        password_reset: 'Contraseña restablecida',
        access_reset_sent: 'Código de acceso enviado',
        access_reset_forced: 'Restablecimiento forzado',
        login_unlocked: 'Desbloqueo de intentos',
        tickets_released: 'Tickets liberados',
        request_created: 'Solicitud creada',
        request_approved: 'Solicitud aprobada',
        request_rejected: 'Solicitud rechazada'
    };
    const changesAccount = document.getElementById('changesAccount');
    const changesAction = document.getElementById('changesAction');
    const changesList = document.getElementById('changesList');
    const changesEmpty = document.getElementById('changesEmpty');
    const changesPrev = document.getElementById('changesPrev');
    const changesNext = document.getElementById('changesNext');
    const changesPageLabel = document.getElementById('changesPage');
    let changesPage = 1;
    let changesPages = 1;
    let changesSearchTimer = null;
    let changesSeq = 0;

    Object.entries(CHANGE_ACTIONS).forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        changesAction.appendChild(option);
    });

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
        if (item.old_value || item.new_value) {
            li.appendChild(changeLine('Cambio: ', `${item.old_value || '—'} → ${item.new_value || '—'}`));
        }
        return li;
    }

    async function loadAccountChanges() {
        const seq = ++changesSeq;
        const params = new URLSearchParams({ page: String(changesPage) });
        if (changesAccount.value.trim()) params.set('account', changesAccount.value.trim());
        if (changesAction.value) params.set('action', changesAction.value);
        let data;
        try {
            data = await apiFetch(`/admin/account-changes?${params.toString()}`);
        } catch (err) {
            return; // apiFetch ya mostró el error
        }
        if (seq !== changesSeq) return;
        changesPage = data.page;
        changesPages = data.pages;
        changesList.replaceChildren(...data.items.map(renderChange));
        changesEmpty.hidden = data.items.length > 0;
        changesPageLabel.textContent = `Página ${data.page} de ${data.pages}`;
        changesPrev.disabled = data.page <= 1;
        changesNext.disabled = data.page >= data.pages;
    }

    changesAccount.addEventListener('input', () => {
        clearTimeout(changesSearchTimer);
        changesSearchTimer = setTimeout(() => {
            changesPage = 1;
            loadAccountChanges();
        }, 300);
    });

    changesAction.addEventListener('change', () => {
        changesPage = 1;
        loadAccountChanges();
    });

    changesPrev.addEventListener('click', () => {
        if (changesPage > 1) {
            changesPage -= 1;
            loadAccountChanges();
        }
    });

    changesNext.addEventListener('click', () => {
        if (changesPage < changesPages) {
            changesPage += 1;
            loadAccountChanges();
        }
    });

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
            document.getElementById('newTicketModal').style.display = 'flex';
            resetRequesterField();
            const canChooseRequester = currentUser && ['coordinator', 'admin'].includes(currentUser.role);
            requesterGroup.style.display = canChooseRequester ? 'block' : 'none';
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
                li.style.padding = '8px 12px';
                li.style.background = '#f8f9fa';
                li.style.borderRadius = '6px';
                li.style.border = '1px solid var(--line)';
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
        if (!currentUser || (currentUser.role !== 'coordinator' && currentUser.role !== 'admin')) return;
        const modalBody = document.querySelector('#usersModal .modal-body');
        if (!modalBody) return;

        const section = document.createElement('section');
        section.id = 'invitesSection';
        section.className = 'invites-section';
        const title = document.createElement('h3');
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
        submit.className = 'btn-action';
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

        const tableTitle = document.createElement('h4');
        tableTitle.textContent = 'Invitaciones';
        const table = document.createElement('table');
        table.className = 'invites-table';
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
        if (!currentUser || (currentUser.role !== 'coordinator' && currentUser.role !== 'admin')) return;
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
                resend.className = 'btn-secondary btn-small';
                resend.dataset.inviteId = invite.id;
                resend.textContent = 'Reenviar';
                actions.appendChild(resend);
            }
            row.appendChild(actions);
            tableBody.appendChild(row);
        });
    }

    initApp();
});
