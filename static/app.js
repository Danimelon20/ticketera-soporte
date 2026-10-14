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
    const tabLogin = document.getElementById('tabLogin');
    const tabRegister = document.getElementById('tabRegister');
    const loginFormContainer = document.getElementById('loginFormContainer');
    const registerFormContainer = document.getElementById('registerFormContainer');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
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
    let currentStatus = '';
    let selectedTicketId = null;
    let activityFilter = 'all';
    let currentTicket = null;

    let selectedRequesterId = null;
    let requesterResults = [];
    let requesterSearchTimer = null;
    let requesterSearchSeq = 0;

    let authToken = sessionStorage.getItem('ticketeraToken');
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

    async function apiFetch(endpoint, options = {}) {
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
            ...options,
            headers
        });

        if (response.status === 401) {
            sessionStorage.removeItem('ticketeraToken');
            authToken = null;
            currentUser = null;
            showAuthScreen();
            showError('Tu sesión terminó, inicia sesión de nuevo');
            throw new Error('Unauthorized');
        }

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            let message = errorData.detail;
            if (response.status === 422 && endpoint.startsWith('/auth/')) {
                message = 'La contraseña debe tener al menos 8 caracteres';
            } else if (endpoint === '/tickets' && options.method === 'POST') {
                message = getErrorMessage(errorData);
            } else if (response.status === 401 && endpoint.includes('/auth/login')) {
                message = errorData.detail || 'Correo o contraseña incorrectos';
            } else if (response.status === 409) {
                message = errorData.detail || 'El correo ya está registrado';
            } else if (Array.isArray(message)) {
                message = message.map(m => (m && m.msg) ? m.msg : String(m)).join('. ');
            }
            showError(message || `Error ${response.status}: ${response.statusText}`);
            throw new Error(message || `Error ${response.status}`);
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
            if (userNameEl) userNameEl.textContent = currentUser.name;
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
        showView('tickets');
        refreshDraftsBadge();
        setupChat();
    }

    function showAuthScreen() {
        resetChat();
        if (authScreen) authScreen.style.display = 'grid';
        if (topbar) topbar.style.display = 'none';
        if (mainContent) mainContent.style.display = 'none';

        ticketList.replaceChildren();
        renderDetailPlaceholder();
        selectedTicketId = null;

        if (tabLogin && tabRegister && loginFormContainer && registerFormContainer) {
            tabLogin.classList.add('active');
            tabLogin.setAttribute('aria-pressed', 'true');
            tabRegister.classList.remove('active');
            tabRegister.setAttribute('aria-pressed', 'false');
            loginFormContainer.style.display = 'block';
            registerFormContainer.style.display = 'none';
        }
    }

    // Auth tab switching
    if (tabLogin && tabRegister && loginFormContainer && registerFormContainer) {
        tabLogin.addEventListener('click', () => {
            tabLogin.classList.add('active');
            tabLogin.setAttribute('aria-pressed', 'true');
            tabRegister.classList.remove('active');
            tabRegister.setAttribute('aria-pressed', 'false');
            loginFormContainer.style.display = 'block';
            registerFormContainer.style.display = 'none';
            hideError();
        });

        tabRegister.addEventListener('click', () => {
            tabRegister.classList.add('active');
            tabRegister.setAttribute('aria-pressed', 'true');
            tabLogin.classList.remove('active');
            tabLogin.setAttribute('aria-pressed', 'false');
            registerFormContainer.style.display = 'block';
            loginFormContainer.style.display = 'none';
            hideError();
        });
    }

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
                    body: JSON.stringify({ email, password })
                });
                loginForm.reset();
                if (data.requires_code) {
                    showVerifyScreen(data);
                    return;
                }
                completeLogin(data);
            } catch (err) {
                // handled by apiFetch
            }
        });
    }

    // Register submit
    if (registerForm) {
        registerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            hideError();
            const name = document.getElementById('regName').value.trim();
            const email = document.getElementById('regEmail').value.trim();
            const password = document.getElementById('regPassword').value;
            const inviteCodeInput = document.getElementById('regInviteCode');
            const inviteCode = inviteCodeInput.value.trim().toUpperCase().replace(/\s+/g, '');

            try {
                const body = { name, email, password };
                if (inviteCode) {
                    body.invite_code = inviteCode;
                }
                const data = await apiFetch('/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                registerForm.reset();
                if (data.requires_code) {
                    showVerifyScreen(data);
                    return;
                }
                completeLogin(data);
            } catch (err) {
                // handled by apiFetch
            }
        });
    }

    function completeLogin(data) {
        sessionStorage.setItem('ticketeraToken', data.token);
        authToken = data.token;
        currentUser = data.user;
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

    const CODE_SECONDS = 10 * 60;
    const RESEND_WAIT_SECONDS = 60;

    let challengeId = null;
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
        const deadline = Date.now() + CODE_SECONDS * 1000;
        const tick = () => {
            const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
            if (left === 0) {
                clearInterval(expiryTimer);
                verifyTimer.textContent = 'El código venció. Inicia sesión de nuevo.';
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
        resetChat();
        challengeId = data.challenge_id;
        verifyHint.textContent = 'Enviamos un código de 6 dígitos a ' + data.email_hint;
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
        verifyBusy = false;
        clearDigits();
        hideVerifyError();
        verifyScreen.hidden = true;
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
        if (verifyBusy || !challengeId) return;
        if (!/^\d{6}$/.test(code)) {
            showVerifyError('Escribe los 6 dígitos del código');
            return;
        }
        verifyBusy = true;
        verifySubmit.disabled = true;
        hideVerifyError();

        let result;
        try {
            result = await postAuth('/auth/verify-code', { challenge_id: challengeId, code });
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
                verifyBusy = false;
                completeLogin(result.data);
            }, 1500);
            return;
        }

        const message = getErrorMessage(result.data);
        showVerifyError(message);
        if (result.status === 429 || message.includes('Inicia sesión de nuevo')) {
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
        if (!challengeId) return;
        resendCodeBtn.disabled = true;
        resendNotice.textContent = '';
        hideVerifyError();

        let result;
        try {
            result = await postAuth('/auth/resend-code', { challenge_id: challengeId });
        } catch (err) {
            resendCodeBtn.disabled = false;
            showVerifyError('No se pudo conectar con el servidor');
            return;
        }

        if (result.ok) {
            challengeId = result.data.challenge_id;
            verifySubmit.disabled = false;
            clearDigits();
            startExpiryCountdown();
            startResendCountdown();
            resendNotice.textContent = 'Te enviamos un código nuevo';
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
                sessionStorage.removeItem('ticketeraToken');
                authToken = null;
                currentUser = null;
                showAuthScreen();
                showError('Tu sesión terminó, inicia sesión de nuevo');
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
                assignedSpan.textContent = userMap[ticket.assigned_to] || ticket.assigned_to;
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
            { title: 'Asignado a', text: assignedTo },
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
                    opt.textContent = userMap[userId];
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
                rightDiv.appendChild(metaP);

                const contentDiv = document.createElement('div');
                contentDiv.style.color = 'var(--ink)';
                contentDiv.style.fontSize = '14px';
                contentDiv.textContent = comment.content;
                rightDiv.appendChild(contentDiv);

                li.appendChild(rightDiv);

            } else {
                const entry = ev.data;
                const changedByName = userMap[entry.changed_by] || 'Usuario';

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

            const contactPhone = document.getElementById('contactPhone').value.trim();
            const contactIdNumber = document.getElementById('contactIdNumber').value.trim();

            const contactError = validatePhone(contactPhone) || validateIdNumber(contactIdNumber);
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
                    contact_id_number: contactIdNumber,
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
    const adminView = document.getElementById('adminView');
    const navDrafts = document.getElementById('navDrafts');
    const draftsView = document.getElementById('draftsView');

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
        navDrafts.hidden = !DRAFT_ROLES.includes(role);
    }

    function showView(name) {
        if (splitView) splitView.style.display = name === 'tickets' ? '' : 'none';
        directoryView.hidden = name !== 'directory';
        adminView.hidden = name !== 'admin';
        draftsView.hidden = name !== 'drafts';
        const tabs = { tickets: navTickets, directory: navDirectory, admin: navAdmin, drafts: navDrafts };
        setActiveTab(tabs[name]);
        if (name === 'directory') loadDirectory();
        if (name === 'admin') loadAdminPanel();
        if (name === 'drafts') loadDrafts();
    }

    navDirectory.addEventListener('click', () => showView('directory'));
    navAdmin.addEventListener('click', () => showView('admin'));
    navDrafts.addEventListener('click', () => showView('drafts'));

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
    let drafts = [];
    let selectedDraftId = null;

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

            const subject = document.createElement('span');
            subject.className = 'draft-item-subject';
            subject.textContent = draft.subject || '(Sin asunto)';
            const meta = document.createElement('span');
            meta.className = 'draft-item-meta';
            meta.textContent = `${draft.requester_name} · ${formatDateTime(draft.created_at)}`;
            const state = document.createElement('span');
            state.className = 'draft-item-meta';
            state.textContent = DRAFT_STATUS_LABELS[draft.status] || draft.status;
            btn.append(subject, meta, state);
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

        document.getElementById('draftSubject').textContent = draft.subject || '(Sin asunto)';
        document.getElementById('draftMeta').textContent =
            `${draft.requester_name} · ${formatDateTime(draft.created_at)} · ${DRAFT_STATUS_LABELS[draft.status] || draft.status}`;
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
    }

    function setDraftBusy(busy) {
        draftConfirmBtn.disabled = busy;
        draftDiscardBtn.disabled = busy;
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

    // ----- Directorio -----
    const directorySearch = document.getElementById('directorySearch');
    const directoryRole = document.getElementById('directoryRole');
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
            row.dataset.userName = user.name;

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
            row.appendChild(stateTd);

            if (isAdmin) {
                const actionsTd = document.createElement('td');
                if (!isSelf) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'btn-secondary directory-toggle-active';
                    btn.dataset.activate = user.is_active ? 'false' : 'true';
                    btn.textContent = user.is_active ? 'Desactivar' : 'Activar';
                    actionsTd.appendChild(btn);
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

        const activeBtn = e.target.closest('.directory-toggle-active');
        if (activeBtn) {
            const row = activeBtn.closest('tr');
            const activate = activeBtn.dataset.activate === 'true';
            if (!activate && !confirm(`¿Desactivar la cuenta de ${row.dataset.userName}? No podrá entrar hasta que la actives de nuevo.`)) {
                return;
            }
            activeBtn.disabled = true;
            updateDirectoryUser(row.dataset.userId, { is_active: activate });
        }
    });

    directoryBody.addEventListener('change', (e) => {
        const select = e.target.closest('.directory-role-select');
        if (select) {
            select.disabled = true;
            updateDirectoryUser(select.closest('tr').dataset.userId, { role: select.value });
        }
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
    }

    refreshLogsBtn.addEventListener('click', loadAdminLogs);

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
        document.getElementById('contactIdNumber').value = person.id_number || '';
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
                if (currentUser.id_number) {
                    document.getElementById('contactIdNumber').value = currentUser.id_number;
                }
            }
        });
    }

    const usersList = document.getElementById('usersList');

    async function loadUsers() {
        const users = await apiFetch('/users');
        if (usersList) {
            usersList.replaceChildren();
        }
        
        userMap = {};
        userRoleMap = {};
        
        users.forEach(user => {
            if (usersList) {
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
                await apiFetch(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ assigned_to: currentUser.id })
                });
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
                await apiFetch(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ assigned_to: parseInt(selectedUser) })
                });
                await reloadSelectedTicketAndList();
                showSuccess('Ticket asignado');
            } catch (err) {
                showError(err.message || 'Error al asignar el ticket');
            }
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

    function renderInvitesSection() {
        if (!currentUser || (currentUser.role !== 'coordinator' && currentUser.role !== 'admin')) {
            const section = document.getElementById('invitesSection');
            if (section) section.style.display = 'none';
            return;
        }

        let section = document.getElementById('invitesSection');
        const modalBody = document.querySelector('#usersModal .modal-body');
        if (!modalBody) return;

        if (section) {
            section.remove();
        }
        section = document.createElement('div');
        section.id = 'invitesSection';
        section.style.marginTop = '12px';
        section.style.padding = '12px';
        section.style.background = '#FFF8F0';
        section.style.borderRadius = '8px';
        section.style.border = '1px solid #8A4B00';
        section.style.display = 'block';

        const isAdmin = currentUser.role === 'admin';
        let buttonsHtml = `<button id="generateTechnicianCodeBtn" class="btn-action">Generar código de técnico</button>`;
            if (isAdmin) {
                buttonsHtml += `<button id="generateCoordinatorCodeBtn" class="btn-action" style="margin-left: 8px;">Generar código de coordinador</button>`;
            }
            section.innerHTML += `<div style="margin-bottom: 12px;">${buttonsHtml}</div>`;

            section.innerHTML += `<div id="codeDisplay" style="margin: 12px 0; padding: 12px; background: var(--bg); font-family: var(--mono); font-size: 20px; min-height: 44px; border: 1px solid var(--line); border-radius: 4px; display: none;">
                <span id="codeText">---</span>
            </div>`;

            section.innerHTML += `<button id="copyCodeBtn" class="btn-action" style="display: none;">Copiar</button>`;

            section.innerHTML += `<div id="codeWarning" style="margin-top: 8px; color: #8A4B00; font-size: 12px; display: none;">
                Guárdalo ahora: no se volverá a mostrar. Vence en 7 días y sirve una sola vez.
            </div>`;

            const isAdmin2 = currentUser.role === 'admin';
            section.innerHTML += `<div style="margin-top: 12px; font-size: 14px;">
                <strong>Códigos de invitación</strong>
                <table style="width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px;">
                    <thead>
                        <tr style="background: #F0F2F5;">
                            <th style="padding: 8px; text-align: left;">Rol</th>
                            <th style="padding: 8px; text-align: left;">Creado</th>
                            <th style="padding: 8px; text-align: left;">Vence</th>
                            <th style="padding: 8px; text-align: left;">Estado</th>
                        </tr>
                    </thead>
                    <tbody id="invitesTableBody"></tbody>
                </table>
            </div>`;

            modalBody.prepend(section);

            // Add event listeners
            document.getElementById('generateTechnicianCodeBtn').addEventListener('click', async () => {
                try {
                    const data = await apiFetch('/invites', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ role: 'technician' })
                    });
                    const codeText = document.getElementById('codeText');
                    const codeDisplay = document.getElementById('codeDisplay');
                    const copyBtn = document.getElementById('copyCodeBtn');
                    const codeWarning = document.getElementById('codeWarning');

                    codeText.textContent = data.code;
                    codeDisplay.style.display = 'block';
                    copyBtn.style.display = 'inline-block';
                    codeWarning.style.display = 'block';

                    loadInvitesTable();

                    // Set up copy button
                    copyBtn.onclick = async () => {
                        try {
                            await navigator.clipboard.writeText(data.code);
                            copyBtn.textContent = 'Copiado';
                            copyBtn.style.color = 'var(--exito)';
                            setTimeout(() => {
                                copyBtn.textContent = 'Copiar';
                                copyBtn.style.color = '';
                            }, 2000);
                        } catch (err) {
                            showError('No se pudo copiar al portapapeles');
                        }
                    };
                } catch (err) {
                    showError(err.message || 'Error al generar código');
                }
            });

            if (isAdmin2) {
                document.getElementById('generateCoordinatorCodeBtn').addEventListener('click', async () => {
                    try {
                        const data = await apiFetch('/invites', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ role: 'coordinator' })
                        });
                        const codeText = document.getElementById('codeText');
                        const codeDisplay = document.getElementById('codeDisplay');
                        const copyBtn = document.getElementById('copyCodeBtn');
                        const codeWarning = document.getElementById('codeWarning');

codeText.textContent = data.code;
                    codeDisplay.style.display = 'block';
                    copyBtn.style.display = 'inline-block';
                    codeWarning.style.display = 'block';

                    loadInvitesTable();

                    // Set up copy button
                        copyBtn.onclick = async () => {
                            try {
                                await navigator.clipboard.writeText(data.code);
                                copyBtn.textContent = 'Copiado';
                                copyBtn.style.color = 'var(--exito)';
                                setTimeout(() => {
                                    copyBtn.textContent = 'Copiar';
                                    copyBtn.style.color = '';
                                }, 2000);
                            } catch (err) {
                                showError('No se pudo copiar al portapapeles');
                            }
                        };
                    } catch (err) {
                        showError(err.message || 'Error al generar código');
                    }
                });
            }

        // Load invites table
        loadInvitesTable();
    }

async function loadInvitesTable() {
        if (!currentUser || (currentUser.role !== 'coordinator' && currentUser.role !== 'admin')) return;

        try {
            const invites = await apiFetch('/invites');
            const tableBody = document.getElementById('invitesTableBody');
            if (!tableBody) return;

            tableBody.innerHTML = '';

            invites.forEach(invite => {
                const roleLabel = { technician: 'Técnico', coordinator: 'Coordinador', admin: 'Administrador' }[invite.role] || invite.role;

                let statusText;
                if (invite.used) {
                    statusText = `Usado por ${invite.used_by_name}`;
                } else if (new Date(invite.expires_at + 'Z') < new Date()) {
                    statusText = 'Vencido';
                } else {
                    statusText = 'Disponible';
                }

                const row = document.createElement('tr');

                const td1 = document.createElement('td');
                td1.style.padding = '8px';
                td1.style.textAlign = 'left';
                td1.textContent = roleLabel;
                row.appendChild(td1);

                const td2 = document.createElement('td');
                td2.style.padding = '8px';
                td2.style.textAlign = 'left';
                td2.textContent = formatDate(invite.created_at);
                row.appendChild(td2);

                const td3 = document.createElement('td');
                td3.style.padding = '8px';
                td3.style.textAlign = 'left';
                td3.textContent = invite.expires_at ? formatDate(invite.expires_at) : '—';
                row.appendChild(td3);

                const td4 = document.createElement('td');
                td4.style.padding = '8px';
                td4.style.textAlign = 'left';
                td4.textContent = statusText;
                row.appendChild(td4);

                tableBody.appendChild(row);
            });
        } catch (err) {
            showError(err.message || 'Error al cargar códigos');
        }
    }

    initApp();
});
