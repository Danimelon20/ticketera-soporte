document.addEventListener('DOMContentLoaded', () => {
    const API_BASE = '/api';

    const categorySelect = document.getElementById('category');
    const prioritySelect = document.getElementById('priority');
    const categoryFilter = document.getElementById('categoryFilter');
    const priorityFilter = document.getElementById('priorityFilter');
    const searchInput = document.getElementById('search');
    const ticketList = document.getElementById('ticketList');
    const errorMessage = document.getElementById('errorMessage');
    const successMessage = document.getElementById('successMessage');
    const actingAsSelect = document.getElementById('actingAs');
    const createUserBtn = document.getElementById('createUserBtn');
    const userNameInput = document.getElementById('userName');
    const userEmailInput = document.getElementById('userEmail');
    const toggleMoreFiltersBtn = document.getElementById('toggleMoreFilters');
    const moreFiltersBlock = document.getElementById('moreFiltersBlock');

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
            
            // Línea 1: "#ID · Categoría" (izq) y fecha corta (der)
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

            // Línea 2: Título (font-weight 500)
            const line2 = document.createElement('div');
            line2.style.fontWeight = '500';
            line2.style.fontSize = '15px';
            line2.style.color = 'var(--ink)';
            line2.style.marginBottom = '6px';
            line2.style.textAlign = 'left';
            line2.textContent = ticket.title;

            // Línea 3: Badge de estado, texto de prioridad y asignado (si tiene)
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

    function showTicketDetail(ticket) {
        const detailPanel = document.createElement('div');
        detailPanel.className = 'detail-panel';
        
        const assignedTo = ticket.assigned_to !== null ? userMap[ticket.assigned_to] || ticket.assigned_to : 'Sin asignar';
        const categoryName = categoryMap[ticket.category_id] || '';
        const priorityName = priorityMap[ticket.priority_id] || '';
        
        const h2 = document.createElement('h2');
        h2.textContent = `Detalle del ticket #${ticket.id}`;
        detailPanel.appendChild(h2);
        
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
        const priorityBadge = document.createElement('span');
        priorityBadge.className = `badge priority-${priorityLevelMap[ticket.priority_id] || 1}`;
        priorityBadge.textContent = priorityName || '';
        tdPriority.textContent = 'Prioridad: ';
        tdPriority.appendChild(priorityBadge);
        detailPanel.appendChild(tdPriority);
        
        const tdStatus = document.createElement('p');
        const statusBadge = document.createElement('span');
        statusBadge.className = `badge status-${ticket.status}`;
        statusBadge.textContent = STATE_MAP[ticket.status] || ticket.status;
        tdStatus.textContent = 'Estado: ';
        tdStatus.appendChild(statusBadge);
        detailPanel.appendChild(tdStatus);
        
        const tdAssigned = document.createElement('p');
        tdAssigned.textContent = `Asignado a: ${assignedTo}`;
        detailPanel.appendChild(tdAssigned);
        
        const tdCreated = document.createElement('p');
        tdCreated.textContent = `Creado: ${formatDateTime(ticket.created_at)}`;
        detailPanel.appendChild(tdCreated);
        
        const tdCreatedBy = document.createElement('p');
        tdCreatedBy.textContent = `Creado por: ${userMap[ticket.created_by] || ''}`;
        detailPanel.appendChild(tdCreatedBy);
        
        const tdUpdated = document.createElement('p');
        tdUpdated.textContent = `Última actualización: ${formatDateTime(ticket.updated_at)}`;
        detailPanel.appendChild(tdUpdated);
        
        const actionsSection = document.createElement('div');
        actionsSection.className = 'actions-section';
        
        const state = ticket.status;
        const isClosed = state === 'closed';
        
        if (actingAsRole === 'requester') {
            if (!isClosed) {
                const msg = document.createElement('p');
                msg.textContent = 'Solo el equipo de soporte puede cambiar el estado o la asignación';
                msg.className = 'requester-msg';
                actionsSection.appendChild(msg);
            } else {
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionsSection.appendChild(closedMsg);
            }
        } else if (actingAsRole === 'technician') {
            const actionDiv = document.createElement('div');
            actionDiv.className = 'action-buttons';
            
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
                    assignMeBtn.className = 'btn-action';
                    assignMeBtn.textContent = 'Asignarme a mí';
                    actionDiv.appendChild(assignMeBtn);
                    
                    assignMeBtn.addEventListener('click', async () => {
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
                                    assigned_to: actingAsId,
                                    actor_id: actingAsId
                                })
                            });

                            const updatedTicket = await fetchAPI(`/tickets/${ticket.id}?actor_id=${actingAsId}`);
                            showTicketDetail(updatedTicket);
                            await loadTickets(getFilters());

                            showSuccess('Ticket asignado');
                        } catch (err) {
                            showError(err.message || 'Error al asignar el ticket');
                        }
                    });
                }
            } else {
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionDiv.appendChild(closedMsg);
            }
            
            actionDiv.querySelectorAll('.btn-action').forEach(btn => {
                if (btn.textContent === 'Asignarme a mí') return;
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
                        
                        const updatedTicket = await fetchAPI(`/tickets/${ticketId}?actor_id=${actingAsId}`);
                        showTicketDetail(updatedTicket);
                        await loadTickets(getFilters());
                    } catch (err) {
                        showError(err.message || 'Error al aplicar la acción');
                    }
                });
            });
            
            actionsSection.appendChild(actionDiv);
        } else if (actingAsRole === 'coordinator') {
            const assignmentDiv = document.createElement('div');
            assignmentDiv.className = 'assignment-section';
            
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
            
            const actionDiv = document.createElement('div');
            actionDiv.className = 'action-buttons';
            
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
                        
                        const updatedTicket = await fetchAPI(`/tickets/${ticketId}?actor_id=${actingAsId}`);
                        showTicketDetail(updatedTicket);
                        await loadTickets(getFilters());
                    } catch (err) {
                        showError(err.message || 'Error al aplicar la acción');
                    }
                });
            });
            
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

                    const updatedTicket = await fetchAPI(`/tickets/${ticket.id}?actor_id=${actingAsId}`);
                    showTicketDetail(updatedTicket);
                    await loadTickets(getFilters());

                    showSuccess('Ticket asignado');
                } catch (err) {
                    showError(err.message || 'Error al asignar el ticket');
                }
            });
            
            if (!isClosed) {
                actionsSection.appendChild(assignmentDiv);
            }
            actionsSection.appendChild(actionDiv);
        } else {
            if (isClosed) {
                const actionDiv = document.createElement('div');
                actionDiv.className = 'action-buttons';
                const closedMsg = document.createElement('p');
                closedMsg.textContent = 'Ticket cerrado: no se puede modificar';
                closedMsg.className = 'closed-msg';
                actionDiv.appendChild(closedMsg);
                actionsSection.appendChild(actionDiv);
            }
        }
        
        detailPanel.appendChild(actionsSection);
        
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
                li.textContent = `${formatDateTime(entry.changed_at)} - ${changedByName}: ${fieldLabel}: ${oldValue} → ${newValue}`;
                ul.appendChild(li);
            });
            
            historySection.appendChild(ul);
        }
        
        detailPanel.appendChild(historySection);
        
        const commentsSection = document.createElement('div');
        commentsSection.className = 'comments-section';

        const commentsH3 = document.createElement('h3');
        commentsH3.textContent = 'Comentarios';
        commentsSection.appendChild(commentsH3);

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

                    const updatedTicket = await fetchAPI(`/tickets/${ticket.id}?actor_id=${actingAsId}`);
                    showTicketDetail(updatedTicket);

                    showSuccess('Comentario agregado');
                } catch (err) {
                    showError(err.message || 'Error al agregar el comentario');
                }
            });
        }
        
        detailPanel.appendChild(commentsSection);
        
        getModal().querySelector('.modal-body').replaceChildren(detailPanel);
        openModal('ticketModal');
    }

    function getModal() {
        return document.getElementById('ticketModal');
    }

    function getModalError() {
        return document.getElementById('modalError');
    }

    function isModalOpen(id = 'ticketModal') {
        const modal = document.getElementById(id);
        return modal && modal.style.display === 'flex';
    }

    function openModal(id) {
        const modal = document.getElementById(id);
        if (modal) modal.style.display = 'flex';
    }

    function closeModal(id) {
        const modal = document.getElementById(id);
        if (modal) modal.style.display = 'none';
        if (id === 'ticketModal') {
            const modalError = document.getElementById('modalError');
            if (modalError) modalError.style.display = 'none';
            ticketList.querySelectorAll('.ticket-item.selected').forEach(r => r.classList.remove('selected'));
            selectedTicketId = null;
        }
    }

    function updateActingAsDisplay() {
        // no-op or placeholder if needed
    }

    function showError(message) {
        let text;
        if (Array.isArray(message)) {
            text = message.map(m => (m && m.msg) ? m.msg : String(m)).join('. ');
        } else {
            text = String(message);
        }
        text = text.replace(/Value error, /g, '');
        const target = isModalOpen('ticketModal') ? getModalError() : errorMessage;
        target.textContent = text;
        target.style.display = 'block';
        successMessage.style.display = 'none';
        if (target === errorMessage) {
            clearTimeout(showError.timer);
            showError.timer = setTimeout(() => {
                errorMessage.style.display = 'none';
            }, 3000);
        }
    }

    function hideError() {
        errorMessage.style.display = 'none';
        successMessage.style.display = 'none';
        getModalError().style.display = 'none';
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
            closeBtn.addEventListener('click', () => closeModal(modal.id));
        }
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                closeModal(modal.id);
            }
        });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal').forEach(modal => {
                if (modal.style.display === 'flex') {
                    closeModal(modal.id);
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

        closeModal('newTicketModal');
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

    actingAsSelect.addEventListener('change', (e) => {
        actingAsId = parseInt(e.target.value) || null;
        if (userMap[actingAsId]) {
            actingAsRole = userRoleMap[actingAsId];
        } else {
            actingAsRole = null;
        }
        
        updateActingAsDisplay();
        
        document.querySelectorAll('.modal').forEach(modal => closeModal(modal.id));
        loadTickets(getFilters());
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
            openModal('usersModal');
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
            openModal('newTicketModal');
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

    // Delegación de eventos en #ticketList (registrado una sola vez)
    ticketList.addEventListener('click', async (e) => {
        const item = e.target.closest('.ticket-item');
        if (!item) return;
        ticketList.querySelectorAll('.ticket-item').forEach(el => el.classList.remove('selected'));
        item.classList.add('selected');
        selectedTicketId = parseInt(item.dataset.id);
        try {
            const ticket = await fetchAPI(`/tickets/${selectedTicketId}?actor_id=${actingAsId}`);
            showTicketDetail(ticket);
        } catch (err) {
            showError('Error al cargar el detalle del ticket');
        }
    });
    
    Promise.all([loadCategories(), loadPriorities(), loadUsers()]).then(() => {
        updateActingAsDisplay();
        loadTickets();
    });
});
