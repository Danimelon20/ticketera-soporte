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

    const STATE_MAP = {
        new: 'Nuevo',
        in_progress: 'En proceso',
        resolved: 'Resuelto',
        closed: 'Cerrado'
    };

    let categoryMap = {};
    let priorityMap = {};

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

    function renderTickets(tickets) {
        ticketsBody.innerHTML = '';
        
        tickets.forEach(ticket => {
            const tr = document.createElement('tr');
            
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
            
            const tdDate = document.createElement('td');
            tdDate.textContent = formatDate(ticket.created_at);
            
            tr.appendChild(tdId);
            tr.appendChild(tdTitle);
            tr.appendChild(tdCategory);
            tr.appendChild(tdPriority);
            tr.appendChild(tdStatus);
            tr.appendChild(tdDate);
            
            ticketsBody.appendChild(tr);
        });
    }

    function formatDate(dateString) {
        if (!dateString) return '';
        const date = new Date(dateString);
        return date.toLocaleDateString('es-ES');
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

    // Initial load
    loadCategories();
    loadPriorities();
    loadTickets();
});