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
    const actingAsSelect = document.getElementById('actingAs');
    const createUserBtn = document.getElementById('createUserBtn');
    const userNameInput = document.getElementById('userName');
    const userEmailInput = document.getElementById('userEmail');
    const toggleMoreFiltersBtn = document.getElementById('toggleMoreFilters');
    const moreFiltersBlock = document.getElementById('moreFiltersBlock');
    const backToListBtn = document.getElementById('backToList');

    const STATE_MAP = {
        new: 'Nuevo',
        in_progress: 'En proceso',
        resolved: 'Resuelto',
        closed: 'Cerrado'
    };

    const ROLE_MAP = {
        requester: 'Solicitante',
        technician: 'Técnico',
        coordinator: 'Coordinador'
    };

    let categoryMap = {};
    let priorityMap = {};
    let priorityLevelMap = {};
    let userMap = {};
    let userRoleMap = {};
    let actingAsId = null;
    let actingAsRole = null;
    let currentStatus = '';
    let selectedTicketId = null;

    function getFilters() {
        return {
            status: currentStatus || undefined,
            category_id: categoryFilter.value || undefined,
            priority_id: priorityFilter.value || undefined,
            search: searchInput.value || undefined
        };
    }

    async function fetchAPI(endpoint, options = {}) {
        hideError();
        const url = `${API_BASE}${endpoint}`;
        const response = await fetch(url, {
            headers: {
                'Accept': 'application/json'
            },
            ...options
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            showError(errorData.detail || `Error ${response.status}: ${response.statusText}`);
            throw new Error(errorData.detail || `Error ${response.status}`);
        }

        return response.json();
    }

    async function loadCategories() {
        const categories = await fetchAPI('/categories');
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
        const priorities = await fetchAPI('/priorities');
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
        if (actingAsId === null) {
            ticketList.replaceChildren();
            const li = document.createElement('li');
            li.style.padding = '32px 16px';
            li.style.textAlign = 'center';
            li.style.color = 'var(--muted)';
            li.textContent = 'Selecciona un usuario en "Actuando como" para ver los tickets';
            ticketList.appendChild(li);
            renderDetailPlaceholder();
            return;
        }
        
        const params = new URLSearchParams();
        if (filters.status) params.append('status', filters.status);
        if (filters.category_id) params.append('category_id', filters.category_id);
        if (filters.priority_id) params.append('priority_id', filters.priority_id);
        if (filters.search) params.append('search', filters.search);
        params.append('actor_id', actingAsId);

        const tickets = await fetchAPI(`/tickets?${params.toString()}`);
        renderTickets(tickets);

        if (selectedTicketId !== null) {
            const exists = tickets.some(t => t.id === selectedTicketId);
            if (!exists) {
                selectedTicketId = null;
                renderDetailPlaceholder();
            } else {
                try {
                    const ticket = await fetchAPI(`/tickets/${selectedTicketId}?actor_id=${actingAsId}`);
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
        if (!selectedTicketId || !actingAsId) return;
        try {
            const ticket = await fetchAPI(`/tickets/${selectedTicketId}?actor_id=${actingAsId}`);
            showTicketDetail(ticket);
            await loadTickets(getFilters());
        } catch (err) {
            selectedTicketId = null;
            renderDetailPlaceholder();
            await loadTickets(getFilters());
        }
    }

    function showTicketDetail(ticket) {
        detailContent.replaceChildren();
        const detailPanelEl = document.getElementById('detailPanel');
        if (detailPanelEl) detailPanelEl.scrollTop = 0;

        const assignedTo = ticket.assigned_to !== null ? userMap[ticket.assigned_to] || ticket.assigned_to : 'Sin asignar';
        const categoryName = categoryMap[ticket.category_id] || '';
        const priorityName = priorityMap[ticket.priority_id] || '';
        const createdByName = userMap[ticket.created_by] || '';

        // a. Línea superior en var(--mono), 13px, gris: "#ID · Categoría · Creado por Nombre · fecha y hora"
        const topMeta = document.createElement('div');
        topMeta.style.fontFamily = 'var(--mono)';
        topMeta.style.fontSize = '13px';
        topMeta.style.color = 'var(--muted)';
        topMeta.style.marginBottom = '12px';
        topMeta.textContent = `#${ticket.id} · ${categoryName} · Creado por ${createdByName} · ${formatDateTime(ticket.created_at)}`;
        detailContent.appendChild(topMeta);

        // b. Título <h1> de 26px y debajo la descripción (max-width 64ch, line-height 1.55)
        const h1 = document.createElement('h1');
        h1.style.fontSize = '26px';
        h1.style.fontWeight = '600';
        h1.style.color = 'var(--ink)';
        h1.style.margin = '0 0 12px 0';
        h1.textContent = ticket.title;
        detailContent.appendChild(h1);

        const descP = document.createElement('p');
        descP.style.maxWidth = '64ch';
        descP.style.lineHeight = '1.55';
        descP.style.color = 'var(--ink)';
        descP.style.margin = '0 0 28px 0';
        descP.textContent = ticket.description;
        detailContent.appendChild(descP);

        // c. Tres casillas en fila (grid con repeat(auto-fit, minmax(140px, 1fr))): Estado, Prioridad, Asignado a.
        const dl = document.createElement('dl');
        dl.style.display = 'grid';
        dl.style.gridTemplateColumns = 'repeat(auto-fit, minmax(140px, 1fr))';
        dl.style.gap = '16px';
        dl.style.margin = '0 0 28px 0';

        // Box 1: Estado
        const boxStatus = document.createElement('div');
        boxStatus.style.background = 'var(--surface)';
        boxStatus.style.border = '1px solid var(--line)';
        boxStatus.style.borderRadius = 'var(--radius)';
        boxStatus.style.padding = '12px 16px';
        
        const dtStatus = document.createElement('dt');
        dtStatus.style.fontSize = '12px';
        dtStatus.style.color = 'var(--muted)';
        dtStatus.style.marginBottom = '4px';
        dtStatus.textContent = 'Estado';
        
        const ddStatus = document.createElement('dd');
        ddStatus.style.margin = '0';
        ddStatus.style.fontWeight = '500';
        const statusBadge = document.createElement('span');
        statusBadge.className = `badge status-${ticket.status}`;
        statusBadge.textContent = STATE_MAP[ticket.status] || ticket.status;
        ddStatus.appendChild(statusBadge);

        boxStatus.appendChild(dtStatus);
        boxStatus.appendChild(ddStatus);
        dl.appendChild(boxStatus);

        // Box 2: Prioridad
        const boxPriority = document.createElement('div');
        boxPriority.style.background = 'var(--surface)';
        boxPriority.style.border = '1px solid var(--line)';
        boxPriority.style.borderRadius = 'var(--radius)';
        boxPriority.style.padding = '12px 16px';

        const dtPriority = document.createElement('dt');
        dtPriority.style.fontSize = '12px';
        dtPriority.style.color = 'var(--muted)';
        dtPriority.style.marginBottom = '4px';
        dtPriority.textContent = 'Prioridad';

        const ddPriority = document.createElement('dd');
        ddPriority.style.margin = '0';
        ddPriority.style.fontWeight = '500';
        const priorityBadge = document.createElement('span');
        priorityBadge.className = `priority-${priorityLevelMap[ticket.priority_id] || 1}`;
        priorityBadge.textContent = priorityName;
        ddPriority.appendChild(priorityBadge);

        boxPriority.appendChild(dtPriority);
        boxPriority.appendChild(ddPriority);
        dl.appendChild(boxPriority);

        // Box 3: Asignado a
        const boxAssigned = document.createElement('div');
        boxAssigned.style.background = 'var(--surface)';
        boxAssigned.style.border = '1px solid var(--line)';
        boxAssigned.style.borderRadius = 'var(--radius)';
        boxAssigned.style.padding = '12px 16px';

        const dtAssigned = document.createElement('dt');
        dtAssigned.style.fontSize = '12px';
        dtAssigned.style.color = 'var(--muted)';
        dtAssigned.style.marginBottom = '4px';
        dtAssigned.textContent = 'Asignado a';

        const ddAssigned = document.createElement('dd');
        ddAssigned.style.margin = '0';
        ddAssigned.style.fontWeight = '500';
        ddAssigned.style.color = 'var(--ink)';
        ddAssigned.textContent = assignedTo;

        boxAssigned.appendChild(dtAssigned);
        boxAssigned.appendChild(ddAssigned);
        dl.appendChild(boxAssigned);

        detailContent.appendChild(dl);

        // d. Acciones
        const actionsSection = document.createElement('div');
        actionsSection.style.marginBottom = '28px';
        actionsSection.style.paddingTop = '20px';
        actionsSection.style.borderTop = '1px solid var(--line)';

        const state = ticket.status;
        const isClosed = state === 'closed';

        if (actingAsRole === 'requester') {
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
        } else if (actingAsRole === 'technician') {
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
        } else if (actingAsRole === 'coordinator') {
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

            if (!isClosed) {
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

        // e. Dos columnas (Comentarios e Historial)
        const twoCols = document.createElement('div');
        twoCols.style.display = 'grid';
        twoCols.style.gridTemplateColumns = 'repeat(auto-fit, minmax(280px, 1fr))';
        twoCols.style.gap = '28px';

        // Left col: Comentarios
        const commentsCol = document.createElement('div');
        const commentsH3 = document.createElement('h3');
        commentsH3.textContent = 'Comentarios';
        commentsH3.style.fontSize = '17px';
        commentsH3.style.margin = '0 0 12px 0';
        commentsCol.appendChild(commentsH3);

        if (!ticket.comments || ticket.comments.length === 0) {
            const noComments = document.createElement('p');
            noComments.textContent = 'Sin comentarios todavía';
            noComments.style.color = 'var(--muted)';
            commentsCol.appendChild(noComments);
        } else {
            const ulComments = document.createElement('ul');
            ulComments.style.listStyle = 'none';
            ulComments.style.padding = '0';
            ulComments.style.margin = '0 0 16px 0';
            ulComments.style.display = 'flex';
            ulComments.style.flexDirection = 'column';
            ulComments.style.gap = '12px';

            ticket.comments.forEach(comment => {
                const li = document.createElement('li');
                li.style.background = 'var(--surface)';
                li.style.border = '1px solid var(--line)';
                li.style.borderRadius = 'var(--radius)';
                li.style.padding = '10px 14px';

                const metaP = document.createElement('div');
                metaP.style.fontSize = '12px';
                metaP.style.color = 'var(--muted)';
                metaP.style.marginBottom = '4px';
                const authorName = userMap[comment.user_id] || comment.user_id;
                metaP.textContent = `${formatDateTime(comment.created_at)} - ${authorName}`;

                const contentP = document.createElement('div');
                contentP.style.color = 'var(--ink)';
                contentP.textContent = comment.content;

                li.appendChild(metaP);
                li.appendChild(contentP);
                ulComments.appendChild(li);
            });
            commentsCol.appendChild(ulComments);
        }

        if (ticket.status === 'closed') {
            const closedMsg = document.createElement('p');
            closedMsg.textContent = 'No se pueden agregar comentarios a un ticket cerrado';
            closedMsg.style.color = 'var(--muted)';
            commentsCol.appendChild(closedMsg);
        } else {
            const commentForm = document.createElement('div');
            commentForm.style.display = 'flex';
            commentForm.style.flexDirection = 'column';
            commentForm.style.gap = '10px';

            const commentTextarea = document.createElement('textarea');
            commentTextarea.placeholder = 'Escribe un comentario';
            commentTextarea.rows = 3;
            commentTextarea.className = 'comment-textarea';

            const addCommentBtn = document.createElement('button');
            addCommentBtn.textContent = 'Agregar comentario';
            addCommentBtn.className = 'btn-action btn-add-comment';

            commentForm.appendChild(commentTextarea);
            commentForm.appendChild(addCommentBtn);
            commentsCol.appendChild(commentForm);
        }

        twoCols.appendChild(commentsCol);

        // Right col: Historial
        const historyCol = document.createElement('div');
        const historyH3 = document.createElement('h3');
        historyH3.textContent = 'Historial';
        historyH3.style.fontSize = '17px';
        historyH3.style.margin = '0 0 12px 0';
        historyCol.appendChild(historyH3);

        const fieldTranslations = {
            status: 'Estado',
            assigned_to: 'Asignado a',
            priority_id: 'Prioridad',
            category_id: 'Categoría'
        };

        if (!ticket.history || ticket.history.length === 0) {
            const pNoChanges = document.createElement('p');
            pNoChanges.textContent = 'Sin cambios todavía';
            pNoChanges.style.color = 'var(--muted)';
            historyCol.appendChild(pNoChanges);
        } else {
            const ulHistory = document.createElement('ul');
            ulHistory.style.listStyle = 'none';
            ulHistory.style.padding = '0 0 0 16px';
            ulHistory.style.margin = '0';
            ulHistory.style.borderLeft = '2px solid var(--line)';
            ulHistory.style.display = 'flex';
            ulHistory.style.flexDirection = 'column';
            ulHistory.style.gap = '16px';

            ticket.history.forEach(entry => {
                const li = document.createElement('li');

                const fieldLabel = fieldTranslations[entry.field] || entry.field;
                
                let oldValue = entry.old_value;
                if (oldValue === null || oldValue === undefined) {
                    oldValue = 'Sin asignar';
                } else if (entry.field === 'status') {
                    oldValue = STATE_MAP[oldValue] || oldValue;
                } else if (entry.field === 'assigned_to') {
                    oldValue = userMap[oldValue] || oldValue;
                } else if (entry.field === 'priority_id') {
                    oldValue = priorityMap[oldValue] || oldValue;
                } else if (entry.field === 'category_id') {
                    oldValue = categoryMap[oldValue] || oldValue;
                }
                
                let newValue = entry.new_value;
                if (newValue === null || newValue === undefined) {
                    newValue = 'Sin asignar';
                } else if (entry.field === 'status') {
                    newValue = STATE_MAP[newValue] || newValue;
                } else if (entry.field === 'assigned_to') {
                    newValue = userMap[newValue] || newValue;
                } else if (entry.field === 'priority_id') {
                    newValue = priorityMap[newValue] || newValue;
                } else if (entry.field === 'category_id') {
                    newValue = categoryMap[newValue] || newValue;
                }

                const changedByName = userMap[entry.changed_by] || entry.changed_by;

                const metaDiv = document.createElement('div');
                metaDiv.style.fontSize = '12px';
                metaDiv.style.color = 'var(--muted)';
                metaDiv.style.marginBottom = '2px';
                metaDiv.textContent = `${formatDateTime(entry.changed_at)} - ${changedByName}`;

                const changeDiv = document.createElement('div');
                changeDiv.style.color = 'var(--ink)';
                changeDiv.textContent = `${fieldLabel}: ${oldValue} → ${newValue}`;

                li.appendChild(metaDiv);
                li.appendChild(changeDiv);
                ulHistory.appendChild(li);
            });

            historyCol.appendChild(ulHistory);
        }

        twoCols.appendChild(historyCol);
        detailContent.appendChild(twoCols);
    }

    function updateActingAsDisplay() {
        // no-op
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
            closeBtn.addEventListener('click', () => modal.style.display = 'none');
        }
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal').forEach(modal => {
                if (modal.style.display === 'flex') {
                    modal.style.display = 'none';
                }
            });
        }
    });

    document.getElementById('ticketForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();

        if (!actingAsId) {
            showError('Selecciona un usuario en "Actuando como" antes de crear un ticket');
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

        await fetchAPI('/tickets', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                title,
                description,
                category_id: parseInt(categoryId),
                priority_id: parseInt(priorityId),
                actor_id: actingAsId
            })
        });

        document.getElementById('newTicketModal').style.display = 'none';
        document.getElementById('ticketForm').reset();
        await loadTickets(getFilters());

        showSuccess('Ticket creado');
    });

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

    createUserBtn.addEventListener('click', async () => {
        await createUser();
    });

    actingAsSelect.addEventListener('change', async (e) => {
        actingAsId = parseInt(e.target.value) || null;
        if (userMap[actingAsId]) {
            actingAsRole = userRoleMap[actingAsId];
        } else {
            actingAsRole = null;
        }
        
        document.querySelectorAll('.modal').forEach(modal => modal.style.display = 'none');
        selectedTicketId = null;
        renderDetailPlaceholder();
        document.querySelector('.split').classList.remove('show-detail');
        await loadTickets(getFilters());
    });

    const navTickets = document.getElementById('navTickets');
    const navUsers = document.getElementById('navUsers');
    const openNewTicket = document.getElementById('openNewTicket');

    if (navTickets) {
        navTickets.addEventListener('click', () => {
            navTickets.style.background = 'var(--accent-soft)';
            navTickets.style.color = 'var(--accent-dark)';
            if (navUsers) {
                navUsers.style.background = '';
                navUsers.style.color = '';
            }
        });
    }

    if (navUsers) {
        navUsers.addEventListener('click', () => {
            document.getElementById('usersModal').style.display = 'flex';
            navUsers.style.background = 'var(--accent-soft)';
            navUsers.style.color = 'var(--accent-dark)';
            if (navTickets) {
                navTickets.style.background = '';
                navTickets.style.color = '';
            }
        });
    }

    if (openNewTicket) {
        openNewTicket.addEventListener('click', () => {
            document.getElementById('newTicketModal').style.display = 'flex';
        });
    }

    const usersList = document.getElementById('usersList');

    async function loadUsers() {
        const users = await fetchAPI('/users');
        actingAsSelect.innerHTML = '<option value="">Seleccionar</option>';
        if (usersList) {
            usersList.replaceChildren();
        }
        
        userMap = {};
        userRoleMap = {};
        
        users.forEach(user => {
            const opt = document.createElement('option');
            opt.value = user.id;
            opt.textContent = `${user.name} — ${ROLE_MAP[user.role] || ''}`;
            actingAsSelect.appendChild(opt);

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

    async function createUser() {
        const name = userNameInput.value.trim();
        const email = userEmailInput.value.trim();
        const userRoleSelect = document.getElementById('userRole');
        const selectedRole = userRoleSelect.value;

        if (!name || !email) {
            showError('Nombre y email son requeridos');
            return;
        }

        if (!selectedRole || selectedRole === '') {
            showError('Selecciona un rol');
            return;
        }

        await fetchAPI('/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name, email, role: selectedRole })
        });

        userNameInput.value = '';
        userEmailInput.value = '';
        userRoleSelect.value = '';

        await loadUsers();
        updateActingAsDisplay();
        await loadTickets(getFilters());

        showSuccess('Usuario creado');
    }

    // Registrar listener de #backToList una sola vez
    if (backToListBtn) {
        backToListBtn.addEventListener('click', () => {
            document.querySelector('.split').classList.remove('show-detail');
        });
    }

    // Delegación de eventos en #ticketList (registrado una sola vez)
    ticketList.addEventListener('click', async (e) => {
        const item = e.target.closest('.ticket-item');
        if (!item) return;
        ticketList.querySelectorAll('.ticket-item').forEach(el => el.classList.remove('selected'));
        item.classList.add('selected');
        selectedTicketId = parseInt(item.dataset.id);
        document.querySelector('.split').classList.add('show-detail');
        try {
            const ticket = await fetchAPI(`/tickets/${selectedTicketId}?actor_id=${actingAsId}`);
            showTicketDetail(ticket);
        } catch (err) {
            selectedTicketId = null;
            renderDetailPlaceholder();
            showError('Error al cargar el detalle del ticket');
        }
    });

    // Delegación de eventos en #detailPanel para acciones y comentarios (registrado una sola vez)
    const detailPanel = document.getElementById('detailPanel');
    detailPanel.addEventListener('click', async (e) => {
        if (!selectedTicketId || !actingAsId) return;

        const actionBtn = e.target.closest('.btn-action');
        if (actionBtn && !actionBtn.classList.contains('btn-add-comment')) {
            let newStatus;
            const text = actionBtn.textContent;
            if (text === 'Iniciar trabajo') newStatus = 'in_progress';
            else if (text === 'Marcar como resuelto') newStatus = 'resolved';
            else if (text === 'Cerrar') newStatus = 'closed';
            else if (text === 'Reabrir') newStatus = 'in_progress';
            else return;

            try {
                await fetchAPI(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus, actor_id: actingAsId })
                });
                await reloadSelectedTicketAndList();
            } catch (err) {
                showError(err.message || 'Error al aplicar la acción');
            }
        }

        const assignMeBtn = e.target.closest('.btn-assign-me');
        if (assignMeBtn) {
            try {
                await fetchAPI(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ assigned_to: actingAsId, actor_id: actingAsId })
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
                await fetchAPI(`/tickets/${selectedTicketId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ assigned_to: parseInt(selectedUser), actor_id: actingAsId })
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
                await fetchAPI(`/tickets/${selectedTicketId}/comments`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ actor_id: actingAsId, content: content })
                });
                await reloadSelectedTicketAndList();
                showSuccess('Comentario agregado');
            } catch (err) {
                showError(err.message || 'Error al agregar el comentario');
            }
        }
    });
    
    Promise.all([loadCategories(), loadPriorities(), loadUsers()]).then(() => {
        updateActingAsDisplay();
        loadTickets();
    });
});
