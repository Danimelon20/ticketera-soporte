document.addEventListener('DOMContentLoaded', () => {
    const API_BASE = '/api';

    const categorySelect = document.getElementById('category');
    const prioritySelect = document.getElementById('priority');
    const statusFilter = document.getElementById('statusFilter');
    const categoryFilter = document.getElementById('categoryFilter');
    const priorityFilter = document.getElementById('priorityFilter');
    const searchInput = document.getElementById('search');
    const ticketsBody = document.getElementById('ticketsBody');
    const errorMessage = document.getElementById('errorMessage');
    const actingAsSelect = document.getElementById('actingAs');
    const createUserBtn = document.getElementById('createUserBtn');
    const userNameInput = document.getElementById('userName');
    const userEmailInput = document.getElementById('userEmail');

    const STATE_MAP = {
        new: 'Nuevo',
        in_progress: 'En proceso',
        resolved: 'Resuelto',
        closed: 'Cerrado'
    };

    let categoryMap = {};
    let priorityMap = {};
    let userMap = {};
    let actingAsId = null;

    function getFilters() {
        return {
            status: statusFilter.value || undefined,
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
        });
    }

    async function loadTickets(filters = {}) {
        const params = new URLSearchParams();
        if (filters.status) params.append('status', filters.status);
        if (filters.category_id) params.append('category_id', filters.category_id);
        if (filters.priority_id) params.append('priority_id', filters.priority_id);
        if (filters.search) params.append('search', filters.search);

        const tickets = await fetchAPI(`/tickets?${params.toString()}`);
        renderTickets(tickets);
    }

    function getAssignedTo(ticket) {
        if (ticket.assigned_to === null || ticket.assigned_to === undefined) {
            return 'Sin asignar';
        }
        return userMap[ticket.assigned_to] || ticket.assigned_to;
    }

    function renderTickets(tickets) {
        ticketsBody.innerHTML = '';
        
        tickets.forEach(ticket => {
            const tr = document.createElement('tr');
            tr.dataset.ticketId = ticket.id;
            
            const tdId = document.createElement('td');
            tdId.textContent = ticket.id;
            
            const tdTitle = document.createElement('td');
            tdTitle.textContent = ticket.title;
            
            const tdCategory = document.createElement('td');
            tdCategory.textContent = categoryMap[ticket.category_id] || '';
            
            const tdPriority = document.createElement('td');
            tdPriority.textContent = priorityMap[ticket.priority_id] || '';
            
            const tdStatus = document.createElement('td');
            tdStatus.textContent = STATE_MAP[ticket.status] || ticket.status;
            
            const tdAssigned = document.createElement('td');
            tdAssigned.textContent = getAssignedTo(ticket);
            
            const tdDate = document.createElement('td');
            tdDate.textContent = formatDate(ticket.created_at);
            
            tr.appendChild(tdId);
            tr.appendChild(tdTitle);
            tr.appendChild(tdCategory);
            tr.appendChild(tdPriority);
            tr.appendChild(tdAssigned);
            tr.appendChild(tdStatus);
            tr.appendChild(tdDate);
            
            ticketsBody.appendChild(tr);
        });
    }

    function parseServerDate(dateString) {
        // El servidor envía la hora en UTC sin la "Z" final; se la agregamos
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

    function showTicketDetail(ticket) {
        const detailPanel = document.createElement('div');
        detailPanel.className = 'detail-panel';
        
        // Build header info
        const assignedTo = ticket.assigned_to !== null ? userMap[ticket.assigned_to] || ticket.assigned_to : 'Sin asignar';
        
        const categoryName = categoryMap[ticket.category_id] || '';
        const priorityName = priorityMap[ticket.priority_id] || '';
        
        // Title
        const h2 = document.createElement('h2');
        h2.textContent = `Detalle del ticket #${ticket.id}`;
        detailPanel.appendChild(h2);
        
        // Ticket details
        const tdTitle = document.createElement('p');
        tdTitle.textContent = `Título: ${ticket.title}`;
        detailPanel.appendChild(tdTitle);
        
        const tdDescription = document.createElement('p');
        tdDescription.textContent = `Descripción: ${ticket.description}`;
        detailPanel.appendChild(tdDescription);
        
        const tdCategory = document.createElement('p');
        tdCategory.textContent = `Categoría: ${categoryName || ''}`;
        detailPanel.appendChild(tdCategory);
        
        const tdPriority = document.createElement('p');
        tdPriority.textContent = `Prioridad: ${priorityName || ''}`;
        detailPanel.appendChild(tdPriority);
        
        const tdStatus = document.createElement('p');
        tdStatus.textContent = `Estado: ${STATE_MAP[ticket.status] || ticket.status}`;
        detailPanel.appendChild(tdStatus);
        
        const tdAssigned = document.createElement('p');
        tdAssigned.textContent = `Asignado a: ${assignedTo}`;
        detailPanel.appendChild(tdAssigned);
        
        const tdCreated = document.createElement('p');
        tdCreated.textContent = `Creado: ${formatDateTime(ticket.created_at)}`;
        detailPanel.appendChild(tdCreated);
        
        const tdUpdated = document.createElement('p');
        tdUpdated.textContent = `Última actualización: ${formatDateTime(ticket.updated_at)}`;
        detailPanel.appendChild(tdUpdated);
        
        // Acciones section
        const actionsSection = document.createElement('div');
        actionsSection.className = 'actions-section';
        
        // Create assignment selector
        const assignmentDiv = document.createElement('div');
        assignmentDiv.className = 'assignment-section';
        
        const assignLabel = document.createElement('label');
        assignLabel.textContent = 'Asignar a:';
        assignmentDiv.appendChild(assignLabel);
        
        const assignSelect = document.createElement('select');
        assignSelect.className = 'assign-select';
        
        // Add default option
        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        defaultOpt.textContent = '-- Seleccionar --';
        assignSelect.appendChild(defaultOpt);
        
        // Add users from userMap
        Object.keys(userMap).forEach(userId => {
            const opt = document.createElement('option');
            opt.value = userId;
            opt.textContent = userMap[userId];
            assignSelect.appendChild(opt);
        });
        
        const assignBtn = document.createElement('button');
        assignBtn.className = 'btn-assign';
        assignBtn.textContent = 'Asignar';
        assignmentDiv.appendChild(assignSelect);
        assignmentDiv.appendChild(assignBtn);
        
        // State-based buttons
        const state = ticket.status;
        
        const actionDiv = document.createElement('div');
        actionDiv.className = 'action-buttons';
        
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
        } else if (state === 'closed') {
            const closedMsg = document.createElement('p');
            closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
            closedMsg.className = 'closed-msg';
            actionDiv.appendChild(closedMsg);
        }
        
        // Button click handlers
        actionDiv.querySelectorAll('.btn-action').forEach(btn => {
            btn.addEventListener('click', async () => {
                const ticketId = ticket.id;
                let newStatus;
                
                if (btn.textContent === 'Iniciar trabajo') {
                    newStatus = 'in_progress';
                } else if (btn.textContent === 'Marcar como resuelto') {
                    newStatus = 'resolved';
                } else if (btn.textContent === 'Cerrar') {
                    newStatus = 'closed';
                } else if (btn.textContent === 'Reabrir') {
                    newStatus = 'in_progress';
                }
                
                if (!actingAsId) {
                    showError('Selecciona un usuario en \'Actuando como\' antes de modificar');
                    return;
                }
                
                try {
                    await fetchAPI(`/tickets/${ticketId}`, {
                        method: 'PATCH',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            status: newStatus,
                            actor_id: actingAsId
                        })
                    });
                    
                    // Re-fetch ticket and redetail
                    const updatedTicket = await fetchAPI(`/tickets/${ticketId}`);
                    showTicketDetail(updatedTicket);
                    
                    // Reload tickets with current filters
                    await loadTickets(getFilters());
                } catch (err) {
                    showError(err.message || 'Error al aplicar la acción');
                }
            });
        });
        
        // Assign button handler
        assignBtn.addEventListener('click', async () => {
            const selectedUser = assignSelect.value;
            
            if (!selectedUser) {
                showError('Seleccione un usuario para asignar');
                return;
            }
            
            if (!actingAsId) {
                showError('Selecciona un usuario en \'Actuando como\' antes de modificar');
                return;
            }
            
            try {
                await fetchAPI(`/tickets/${ticket.id}`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        assigned_to: parseInt(selectedUser),
                        actor_id: actingAsId
                    })
                });
                
                // Re-fetch ticket and redetail
                const updatedTicket = await fetchAPI(`/tickets/${ticket.id}`);
                showTicketDetail(updatedTicket);
                
                // Reload tickets with current filters
                await loadTickets(getFilters());
            } catch (err) {
                showError(err.message || 'Error al asignar el ticket');
            }
        });
        
        // Append actions section before history
        if (state !== 'closed') {
            actionsSection.appendChild(assignmentDiv);
        }
        actionsSection.appendChild(actionDiv);
        detailPanel.appendChild(actionsSection);
        
        // Build history list - Historial title always visible
        const historySection = document.createElement('div');
        const historyH3 = document.createElement('h3');
        historyH3.textContent = 'Historial';
        historySection.appendChild(historyH3);
        
        const pNoChanges = document.createElement('p');
        pNoChanges.textContent = 'Sin cambios todavía';
        
        if (!ticket.history || ticket.history.length === 0) {
            historySection.appendChild(pNoChanges);
        } else {
            const ul = document.createElement('ul');
            
            const fieldTranslations = {
                status: 'Estado',
                assigned_to: 'Asignado a',
                priority_id: 'Prioridad',
                category_id: 'Categoría'
            };
            
            ticket.history.forEach(entry => {
                const li = document.createElement('li');
                
                // Translate field
                const fieldLabel = fieldTranslations[entry.field] || entry.field;
                
                // Translate old value
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
                
                // Translate new value
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
                
                // Translate changed_by (user ID to name)
                const changedByName = userMap[entry.changed_by] || entry.changed_by;
                
                li.textContent = `${formatDateTime(entry.changed_at)} - ${changedByName}: ${fieldLabel}: ${oldValue} → ${newValue}`;
                ul.appendChild(li);
            });
            
            historySection.appendChild(ul);
        }
        
        detailPanel.appendChild(historySection);
        
                // Comments section
                const commentsSection = document.createElement('div');
                commentsSection.className = 'comments-section';
        
                const commentsH3 = document.createElement('h3');
                commentsH3.textContent = 'Comentarios';
                commentsSection.appendChild(commentsH3);
        
                // La lista se muestra siempre, también en tickets cerrados
                if (!ticket.comments || ticket.comments.length === 0) {
                    const noComments = document.createElement('p');
                    noComments.textContent = 'Sin comentarios todavía';
                    commentsSection.appendChild(noComments);
                } else {
                    const ul = document.createElement('ul');
                    ticket.comments.forEach(comment => {
                        const li = document.createElement('li');
                        const authorName = userMap[comment.user_id] || comment.user_id;
                        const commentDate = formatDateTime(comment.created_at);
                        li.textContent = `${commentDate} - ${authorName}: ${comment.content}`;
                        ul.appendChild(li);
                    });
                    commentsSection.appendChild(ul);
                }
        
                // El formulario solo se muestra si el ticket no está cerrado
                if (ticket.status === 'closed') {
                    const closedMsg = document.createElement('p');
                    closedMsg.textContent = 'No se pueden agregar comentarios a un ticket cerrado';
                    commentsSection.appendChild(closedMsg);
                } else {
            
            const commentForm = document.createElement('div');
            const commentTextarea = document.createElement('textarea');
            commentTextarea.placeholder = 'Escribe un comentario';
            commentTextarea.rows = 3;
            
            const addCommentBtn = document.createElement('button');
            addCommentBtn.textContent = 'Agregar comentario';
            addCommentBtn.className = 'btn-action';
            
            commentForm.appendChild(commentTextarea);
            commentForm.appendChild(addCommentBtn);
            commentsSection.appendChild(commentForm);
            
            addCommentBtn.addEventListener('click', async () => {
                const content = commentTextarea.value.trim();
                
                if (!actingAsId) {
                    showError('Selecciona un usuario en \'Actuando como\' antes de comentar');
                    return;
                }
                
                if (!content) {
                    showError('El comentario no puede estar vacío');
                    return;
                }
                
                try {
                    await fetchAPI(`/tickets/${ticket.id}/comments`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            actor_id: actingAsId,
                            content: content
                        })
                    });
                    
                    const updatedTicket = await fetchAPI(`/tickets/${ticket.id}`);
                    showTicketDetail(updatedTicket);
                } catch (err) {
                    showError(err.message || 'Error al agregar el comentario');
                }
            });
        }
        
        detailPanel.appendChild(commentsSection);
        
        // Remove existing panel if any
        const existingPanel = document.querySelector('.detail-panel');
        if (existingPanel) {
            existingPanel.remove();
        }
        
        document.body.appendChild(detailPanel);
    }

    function showError(message) {
        if (Array.isArray(message)) {
            errorMessage.textContent = message.join('. ');
        } else {
            errorMessage.textContent = message;
        }
        errorMessage.style.display = 'block';
    }

    function hideError() {
        errorMessage.style.display = 'none';
    }

    // Form submit handler
    document.getElementById('ticketForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();

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
                priority_id: parseInt(priorityId)
            })
        });

        // Reset form - only clear fields, don't empty selectors
        document.getElementById('ticketForm').reset();

        // Reload tickets respecting current filters
        await loadTickets(getFilters());
    });

    // Filter change handlers
    statusFilter.addEventListener('change', () => {
        loadTickets(getFilters());
    });

    categoryFilter.addEventListener('change', () => {
        loadTickets(getFilters());
    });

    priorityFilter.addEventListener('change', () => {
        loadTickets(getFilters());
    });

    searchInput.addEventListener('input', (e) => {
        loadTickets(getFilters());
    });

    // Create user button
    createUserBtn.addEventListener('click', async () => {
        await createUser();
    });

    // Actuando como selector change
    actingAsSelect.addEventListener('change', (e) => {
        actingAsId = parseInt(e.target.value) || null;
    });

    async function loadUsers() {
        const users = await fetchAPI('/users');
        actingAsSelect.innerHTML = '<option value="">Seleccionar</option>';
        
        userMap = {};
        
        users.forEach(user => {
            const opt = document.createElement('option');
            opt.value = user.id;
            opt.textContent = user.name;
            actingAsSelect.appendChild(opt);
            
            userMap[user.id] = user.name;
        });
    }

    async function createUser() {
        const name = userNameInput.value.trim();
        const email = userEmailInput.value.trim();

        if (!name || !email) {
            showError('Nombre y email son requeridos');
            return;
        }

        await fetchAPI('/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name, email })
        });

        // Reset form
        userNameInput.value = '';
        userEmailInput.value = '';

        // Reload users and selector
        await loadUsers();
        // Reload tickets to show newly created user in assignments
        await loadTickets(getFilters());
    }

    // Add click handler for row selection - registered once
    ticketsBody.addEventListener('click', async (e) => {
        const tr = e.target.closest('tr');
        if (tr) {
            const ticketId = parseInt(tr.dataset.ticketId);
            try {
                const ticket = await fetchAPI(`/tickets/${ticketId}`);
                showTicketDetail(ticket);
            } catch (err) {
                showError('Error al cargar el detalle del ticket');
            }
        }
    });
    
    // Initial load - wait for categories, priorities, and users first
    Promise.all([loadCategories(), loadPriorities(), loadUsers()]).then(() => {
        loadTickets();
    });
});