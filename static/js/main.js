const BASE_PATH = window.location.pathname.startsWith('/openvpn') ? '/openvpn' : '';

let allClients = [];
let clientToRevoke = null;

// Вспомогательная функция для проверки авторизации (401)
function checkAuthResponse(response) {
    if (response.status === 401) {
        window.location.href = `${BASE_PATH}/login`;
        return false;
    }
    return true;
}

// Загрузка логов аудита действий
async function loadAuditLogs() {
    try {
        const response = await fetch(`${BASE_PATH}/api/audit?_=${new Date().getTime()}`);
        if (!checkAuthResponse(response)) return;
        
        const logs = await response.json();
        renderAuditLogs(logs);
    } catch (error) {
        console.error("Ошибка загрузки журнала аудита:", error);
    }
}

// Отрисовка таблицы логов аудита
function renderAuditLogs(logs) {
    const tbody = document.getElementById('auditTableBody');
    tbody.innerHTML = '';

    if (logs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:16px; color:#888;">Журнал пуст</td></tr>`;
        return;
    }

    logs.forEach(log => {
        let actionBadge = `<span class="log-action">${log.action}</span>`;
        if (log.action.includes('FAIL')) {
            actionBadge = `<span class="log-action fail" style="background-color:#ef4444; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">${log.action}</span>`;
        } else if (log.action.includes('CREATE') || log.action.includes('REBUILD')) {
            actionBadge = `<span class="log-action success" style="background-color:#3b82f6; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">${log.action}</span>`;
        } else if (log.action.includes('REVOKE')) {
            actionBadge = `<span class="log-action revoke" style="background-color:#d97706; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">${log.action}</span>`;
        } else {
            actionBadge = `<span class="log-action info" style="background-color:#4b5563; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">${log.action}</span>`;
        }

        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="color:#888; white-space:nowrap; padding:10px 16px;">${log.timestamp}</td>
            <td style="font-weight:600; padding:10px 16px;">${log.username}</td>
            <td style="padding:10px 16px;">${actionBadge}</td>
            <td style="color:#ccc; padding:10px 16px;">${log.details || ''}</td>
        `;
        tbody.appendChild(row);
    });
}

// Проверка статуса службы OpenVPN
async function checkServiceStatus() {
    const indicator = document.getElementById('serviceIndicator');
    try {
        const response = await fetch(`${BASE_PATH}/api/service/status?_=${new Date().getTime()}`);
        if (!checkAuthResponse(response)) return;
        
        const data = await response.json();
        if (data.status === 'active') {
            indicator.className = 'service-status-badge status-active';
            indicator.innerText = 'VPN: RUNNING';
        } else {
            indicator.className = 'service-status-badge status-failed';
            indicator.innerText = 'VPN: STOPPED';
        }
    } catch {
        indicator.className = 'service-status-badge status-failed';
        indicator.innerText = 'VPN: ERROR';
    }
}

// Перезапуск службы OpenVPN
async function restartService() {
    if (!confirm('Вы уверены, что хотите перезапустить службу OpenVPN? Это временно прервет связь у всех клиентов!')) return;
    
    const btn = document.getElementById('serviceRestartBtn');
    const indicator = document.getElementById('serviceIndicator');
    btn.classList.add('spinning');
    indicator.className = 'service-status-badge status-loading';
    indicator.innerText = 'VPN: RESTARTING...';

    try {
        const response = await fetch(`${BASE_PATH}/api/service/restart`, { method: 'POST' });
        if (!checkAuthResponse(response)) return;
        
        if (response.ok) {
            setTimeout(async () => {
                await checkServiceStatus();
                btn.classList.remove('spinning');
                loadAuditLogs();
            }, 2000);
        } else {
            alert('Не удалось перезапустить службу OpenVPN');
            btn.classList.remove('spinning');
            checkServiceStatus();
        }
    } catch {
        alert('Ошибка связи с сервером');
        btn.classList.remove('spinning');
        checkServiceStatus();
    }
}

// Загрузка списка клиентов
async function loadClients() {
    try {
        const response = await fetch(`${BASE_PATH}/api/clients?_=${new Date().getTime()}`);
        if (!checkAuthResponse(response)) return;
        
        allClients = await response.json();
        filterClients();
    } catch (error) {
        console.error("Ошибка загрузки данных:", error);
    }
}

// Форматирование времени последнего подключения
function formatLastSeen(val) {
    if (!val) return `<span style="color:#666; font-style:italic;">Не подключался</span>`;
    if (val === 'online') {
        return `<span class="status-badge active" style="background-color: #059669; color: white; animation: pulse 2s infinite;">Онлайн</span>`;
    }
    // Форматируем дату для более красивого вывода
    try {
        const parts = val.split(' ');
        const dateParts = parts[0].split('-');
        const timeParts = parts[1].split(':');
        return `${dateParts[2]}.${dateParts[1]}.${dateParts[0]} в ${timeParts[0]}:${timeParts[1]}`;
    } catch {
        return val;
    }
}

// Отрисовка таблицы клиентов
function renderClients(clients) {
    const tbody = document.getElementById('clientsTableBody');
    tbody.innerHTML = '';

    if (clients.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:32px; color:#888;">Клиенты не найдены</td></tr>`;
        return;
    }

    clients.forEach(client => {
        let statusBadge = '';
        
        if (client.status === 'Active') {
            statusBadge = `<span class="status-badge active">Активен</span>`;
        } else {
            statusBadge = `<span class="status-badge revoked">Отозван</span>`;
        }

        const safeName = client.name.replace(/['"]/g, '');
        const isActive = client.status === 'Active';

        const actionButtons = isActive 
            ? `<div class="actions-group">
                <button onclick="rebuildClient('${safeName}')" class="btn-action-rebuild" title="Обновить .ovpn на базе нового common-файла">Пересобрать</button>
                <a href="${BASE_PATH}/api/clients/download/${encodeURIComponent(safeName)}" class="btn-action-download">Скачать</a>
                <button onclick="openRevokeModal('${safeName}')" class="btn-action-revoke">Отозвать</button>
               </div>`
            : `<div class="text-muted-italic">Действий нет</div>`;

        const row = document.createElement('tr');
        
        if (client.online) {
            row.style.backgroundColor = 'rgba(16, 185, 129, 0.04)';
        }

        row.innerHTML = `
            <td style="font-family:monospace; font-weight:600; padding:14px 24px;">${safeName}</td>
            <td style="padding:14px 24px;">${statusBadge}</td>
            <td style="padding:14px 24px;">${formatLastSeen(client.last_seen)}</td>
            <td class="text-right" style="padding:14px 24px;">${actionButtons}</td>
        `;
        tbody.appendChild(row);
    });
}

// Поиск и фильтрация
function filterClients() {
    const query = document.getElementById('searchInput').value.toLowerCase();
    const hideRevoked = document.getElementById('hideRevokedCheck').checked;

    const filtered = allClients.filter(client => {
        const matchesQuery = client.name.toLowerCase().includes(query);
        const matchesStatus = hideRevoked ? (client.status === 'Active') : true;
        return matchesQuery && matchesStatus;
    });

    renderClients(filtered);
}

// Создание нового клиента
async function createClient() {
    const input = document.getElementById('clientNameInput');
    const name = input.value.trim();
    const msg = document.getElementById('actionMessage');
    
    if (!name) return;

    msg.style.color = "#2563eb";
    msg.style.display = "block";
    msg.innerText = "Генерация сертификата на сервере...";

    try {
        const response = await fetch(`${BASE_PATH}/api/clients`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        
        if (!checkAuthResponse(response)) return;
        const result = await response.json();
        
        if (response.ok) {
            msg.style.color = "#10b981";
            msg.innerText = `Конфигурация ${result.client} успешно выпущена.`;
            input.value = '';
            loadClients();
            loadAuditLogs();
        } else {
            msg.style.color = "#ef4444";
            msg.innerText = `Ошибка: ${result.error}`;
        }
    } catch (err) {
        msg.style.color = "#ef4444";
        msg.innerText = "Ошибка соединения с сервером.";
    }
}

// Пересборка конфига .ovpn
async function rebuildClient(name) {
    if (!confirm(`Пересобрать .ovpn файл для ${name}?`)) return;
    try {
        const response = await fetch(`${BASE_PATH}/api/clients/rebuild`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        
        if (!checkAuthResponse(response)) return;
        
        if (response.ok) {
            alert(`Конфиг для ${name} успешно обновлен.`);
            loadAuditLogs();
        } else {
            const res = await response.json();
            alert(`Ошибка: ${res.error}`);
        }
    } catch {
        alert('Ошибка отправки запроса на сервер.');
    }
}

// Модальное окно отзыва
function openRevokeModal(name) {
    if (!name || name === 'undefined') return;
    clientToRevoke = name;
    document.getElementById('modalClientName').innerText = name;
    document.getElementById('confirmModal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('confirmModal').classList.add('hidden');
    clientToRevoke = null;
}

// Подтверждение отзыва
document.getElementById('modalConfirmBtn').onclick = async function() {
    if (!clientToRevoke) return;
    const nameToSend = clientToRevoke;
    closeModal();
    
    try {
        const response = await fetch(`${BASE_PATH}/api/clients/revoke`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ name: nameToSend })
        });
        
        if (!checkAuthResponse(response)) return;
        
        if (response.ok) {
            loadClients();
            loadAuditLogs();
        } else {
            const res = await response.json();
            alert("Ошибка при отзыве: " + (res.error || 'Неизвестный сбой'));
        }
    } catch (err) {
        alert("Ошибка связи с сервером.");
    }
};

// Выход из системы
async function handleLogout() {
    try {
        const response = await fetch(`${BASE_PATH}/logout`, {
            method: 'POST'
        });
        if (response.ok) {
            window.location.href = `${BASE_PATH}/login`;
        }
    } catch {
        alert('Ошибка связи при выходе');
    }
}

// Переключение вкладок (Табы)
let currentTab = 'certificates';

function switchTab(tabName) {
    currentTab = tabName;
    
    const certTabBtn = document.getElementById('tabCertificatesBtn');
    const auditTabBtn = document.getElementById('tabAuditBtn');
    const certSection = document.getElementById('certificatesTabSection');
    const auditSection = document.getElementById('auditTabSection');
    
    if (tabName === 'certificates') {
        certTabBtn.classList.add('active');
        auditTabBtn.classList.remove('active');
        certSection.classList.remove('hidden');
        auditSection.classList.add('hidden');
        loadClients();
    } else {
        certTabBtn.classList.remove('active');
        auditTabBtn.classList.add('active');
        certSection.classList.add('hidden');
        auditSection.classList.remove('hidden');
        loadAuditLogs();
    }
}



// Управление темой оформления
function initThemeToggle() {
    const themeToggleBtn = document.getElementById('theme-toggle');
    if (!themeToggleBtn) return;
    
    const sunIcon = themeToggleBtn.querySelector('.sun-icon');
    const moonIcon = themeToggleBtn.querySelector('.moon-icon');

    if (document.documentElement.classList.contains('light-theme')) {
        sunIcon.classList.add('hidden');
        moonIcon.classList.remove('hidden');
    }

    themeToggleBtn.addEventListener('click', () => {
        document.documentElement.classList.toggle('light-theme');
        const isLight = document.documentElement.classList.contains('light-theme');
        localStorage.setItem('theme', isLight ? 'light' : 'dark');
        
        if (isLight) {
            sunIcon.classList.add('hidden');
            moonIcon.classList.remove('hidden');
        } else {
            sunIcon.classList.remove('hidden');
            moonIcon.classList.add('hidden');
        }
    });
}

// Запуск процесса опроса
window.onload = function() {

    loadClients();
    checkServiceStatus();
    loadAuditLogs();
    initThemeToggle();
    
    // Периодическое обновление данных (раз в 10 секунд)
    setInterval(function() {
        checkServiceStatus();
        if (currentTab === 'certificates') {
            loadClients();
        } else {
            loadAuditLogs();
        }
    }, 10000);
};
