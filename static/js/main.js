const BASE_PATH = window.location.pathname.startsWith('/openvpn') ? '/openvpn' : '';

let allClients = [];
let clientToRevoke = null;
let selectedPlatform = 'windows';
let currentPage = 1;
let pageSize = 25;
let filteredClientsList = [];

// Вспомогательная функция для проверки авторизации (401)
function checkAuthResponse(response) {
    if (response.status === 401) {
        window.location.href = `${BASE_PATH}/login`;
        return false;
    }
    return true;
}

// Загрузка настроек ноды для KPI
async function loadNodeSettings() {
    try {
        const response = await fetch(`${BASE_PATH}/api/settings?_=${new Date().getTime()}`);
        if (!checkAuthResponse(response)) return;
        const settings = await response.json();
        
        const nextIpEl = document.getElementById('kpiNextIp');
        if (nextIpEl) nextIpEl.innerText = settings.next_free_ip || 'Пул заполнен';

        const customIpInput = document.getElementById('customIpInput');
        if (customIpInput && !customIpInput.value && settings.next_free_ip) {
            customIpInput.placeholder = `По умолчанию: ${settings.next_free_ip}`;
        }
    } catch (e) {
        console.error("Ошибка загрузки настроек ноды:", e);
    }
}

let selectedDevNodeMode = 'common';

// Переключение платформы при выпуске конфига
function setPlatform(platform) {
    selectedPlatform = platform;
    const winBtn = document.getElementById('platformWinBtn');
    const linuxBtn = document.getElementById('platformLinuxBtn');
    
    if (platform === 'windows') {
        winBtn.classList.add('active');
        linuxBtn.classList.remove('active');
    } else {
        linuxBtn.classList.add('active');
        winBtn.classList.remove('active');
    }
    updateDevNodeHint();
}

// Переключение режима изоляции адаптера Wintun (Тумблер)
function setDevNodeMode(mode) {
    selectedDevNodeMode = mode;
    document.querySelectorAll('#devNodeModeSwitch .segment-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    const customWrapper = document.getElementById('customDevNodeWrapper');
    if (customWrapper) {
        customWrapper.classList.toggle('hidden', mode !== 'custom');
    }
    updateDevNodeHint();
}

function updateDevNodeHint() {
    const hintEl = document.getElementById('devNodeHint');
    if (!hintEl) return;
    const devName = window.DEV_NODE_NAME || 'ITSG-OVPN';
    if (selectedPlatform === 'linux') {
        hintEl.innerText = 'Для Linux / macOS директива dev-node и драйвер Wintun не используются (нативный tun).';
        return;
    }
    if (selectedDevNodeMode === 'common') {
        hintEl.innerText = `Всем клиентам Windows назначается общий адаптер "${devName}" (изоляция без конфликтов).`;
    } else if (selectedDevNodeMode === 'custom') {
        hintEl.innerText = 'Будет использовано указанное вами пользовательское имя dev-node.';
    } else {
        hintEl.innerText = 'Директива dev-node не будет добавлена в .ovpn файл (стандартный TAP/TUN).';
    }
}

// Переключение расширенных сетевых опций
function toggleAdvancedOptions() {
    const sec = document.getElementById('advancedOptionsSection');
    const btn = document.getElementById('advOptionsBtn');
    if (sec.classList.contains('hidden')) {
        sec.classList.remove('hidden');
        btn.innerText = '− Скрыть параметры сети';
    } else {
        sec.classList.add('hidden');
        btn.innerText = '+ Дополнительные параметры сети';
    }
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
    if (!tbody) return;
    tbody.innerHTML = '';

    if (logs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:16px; color:#888;">Журнал пуст</td></tr>`;
        return;
    }

    logs.forEach(log => {
        let actionBadge = `<span class="log-action">${log.action}</span>`;
        if (log.action.includes('FAIL')) {
            actionBadge = `<span class="log-action fail" style="background-color:rgba(239, 68, 68, 0.2); color:#ef4444; border:1px solid rgba(239, 68, 68, 0.3); padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">${log.action}</span>`;
        } else if (log.action.includes('CREATE') || log.action.includes('REBUILD')) {
            actionBadge = `<span class="log-action success" style="background-color:rgba(16, 185, 129, 0.15); color:#10b981; border:1px solid rgba(16, 185, 129, 0.3); padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">${log.action}</span>`;
        } else if (log.action.includes('REVOKE')) {
            actionBadge = `<span class="log-action revoke" style="background-color:rgba(245, 158, 11, 0.2); color:#f59e0b; border:1px solid rgba(245, 158, 11, 0.3); padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">${log.action}</span>`;
        } else {
            actionBadge = `<span class="log-action info" style="background-color:rgba(148, 163, 184, 0.15); color:#94a3b8; border:1px solid var(--border-color); padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">${log.action}</span>`;
        }

        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="color:var(--text-muted); white-space:nowrap; padding:10px 16px; font-size:12px;">${log.timestamp}</td>
            <td style="font-weight:600; padding:10px 16px;">${log.username}</td>
            <td style="padding:10px 16px;">${actionBadge}</td>
            <td style="color:var(--text-main); padding:10px 16px; font-size:13px;">${log.details || ''}</td>
        `;
        tbody.appendChild(row);
    });
}

// Проверка статуса службы OpenVPN
async function checkServiceStatus() {
    const indicator = document.getElementById('serviceIndicator');
    if (!indicator) return;
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
        updateKpiMetrics();
        filterClients();
    } catch (error) {
        console.error("Ошибка загрузки данных:", error);
    }
}

// Обновление верхних метрик
function updateKpiMetrics() {
    const activeClients = allClients.filter(c => c.status === 'Active');
    const onlineClients = allClients.filter(c => c.online);

    const onlineEl = document.getElementById('kpiOnlineClients');
    if (onlineEl) {
        onlineEl.innerText = `${onlineClients.length} / ${activeClients.length}`;
    }

    const counterBadge = document.getElementById('clientCounterBadge');
    if (counterBadge) {
        counterBadge.innerText = `${activeClients.length} активных`;
    }
}

// Форматирование времени активности
function formatLastSeen(val) {
    if (!val) return `<span style="color:var(--text-muted); font-style:italic; font-size:12px;">Не подключался</span>`;
    if (val === 'online') {
        return `<span class="status-badge active" style="background-color: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.4); font-weight: 600; display: inline-flex; align-items: center; gap: 4px;">
            <span style="width: 6px; height: 6px; border-radius: 50%; background-color: #10b981; display: inline-block;"></span> Онлайн
        </span>`;
    }
    return `<span style="font-size:12px; color:var(--text-muted);">${val}</span>`;
}

// Отрисовка таблицы клиентов с учетом пагинации
function renderClients(clients) {
    const tbody = document.getElementById('clientsTableBody');
    tbody.innerHTML = '';

    filteredClientsList = clients;

    // Расчет пагинации
    const totalRecords = clients.length;
    let totalPages = pageSize === 'all' ? 1 : Math.ceil(totalRecords / pageSize);
    if (totalPages === 0) totalPages = 1;

    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    let displayList = [];
    if (pageSize === 'all') {
        displayList = clients;
    } else {
        const startIdx = (currentPage - 1) * pageSize;
        const endIdx = startIdx + pageSize;
        displayList = clients.slice(startIdx, endIdx);
    }

    // Обновление индикаторов пагинации
    const pageInfo = document.getElementById('paginationInfo');
    if (pageInfo) {
        const start = totalRecords === 0 ? 0 : (pageSize === 'all' ? 1 : (currentPage - 1) * pageSize + 1);
        const end = pageSize === 'all' ? totalRecords : Math.min(currentPage * pageSize, totalRecords);
        pageInfo.innerText = `Показано ${start}–${end} из ${totalRecords} записей`;
    }

    const pageIndicator = document.getElementById('currentPageIndicator');
    if (pageIndicator) pageIndicator.innerText = `${currentPage} / ${totalPages}`;

    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');
    if (prevBtn) prevBtn.disabled = (currentPage <= 1);
    if (nextBtn) nextBtn.disabled = (currentPage >= totalPages);

    if (displayList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:32px; color:var(--text-muted);">Клиенты не найдены</td></tr>`;
        return;
    }

    displayList.forEach(client => {
        let statusBadge = '';
        if (client.status === 'Active') {
            statusBadge = client.online 
                ? formatLastSeen('online')
                : `<span class="status-badge" style="background: rgba(148, 163, 184, 0.15); color: var(--text-muted); border: 1px solid var(--border-color);">Офлайн</span>`;
        } else {
            statusBadge = `<span class="status-badge revoked">Отозван</span>`;
        }

        const safeName = client.name.replace(/['"]/g, '');
        const isActive = client.status === 'Active';
        const isWin = client.platform === 'windows';

        const platformBadge = isWin
            ? `<span style="display:inline-flex; align-items:center; gap:4px; font-size:12px; background:rgba(59, 130, 246, 0.12); color:#60a5fa; border:1px solid rgba(59, 130, 246, 0.25); padding:2px 8px; border-radius:4px;">🪟 Windows</span>`
            : `<span style="display:inline-flex; align-items:center; gap:4px; font-size:12px; background:rgba(16, 185, 129, 0.12); color:#34d399; border:1px solid rgba(16, 185, 129, 0.25); padding:2px 8px; border-radius:4px;">🐧 Linux</span>`;

        const ipBadge = client.ip && client.ip !== 'Dynamic'
            ? `<span style="font-family:'JetBrains Mono',monospace; font-weight:600; color:var(--accent);">${client.ip}</span>`
            : `<span style="color:var(--text-muted); font-style:italic;">Динамический</span>`;

        const actionButtons = isActive 
            ? `<div class="actions-group">
                <a href="${BASE_PATH}/api/clients/download/${encodeURIComponent(safeName)}" class="btn-action-download" title="Скачать готовый .ovpn файл">Скачать</a>
                <button onclick="openRevokeModal('${safeName}')" class="btn-action-revoke" title="Отозвать сертификат">Отозвать</button>
               </div>`
            : `<div class="text-muted-italic">Отозван</div>`;

        const row = document.createElement('tr');
        row.className = 'client-row-interactive';
        row.style.cursor = 'pointer';
        row.title = 'Нажмите, чтобы открыть подробную карточку конфигурации';
        row.onclick = (e) => {
            if (e.target.closest('a, button')) return;
            openDetailModal(safeName);
        };

        if (client.online) {
            row.style.backgroundColor = 'rgba(16, 185, 129, 0.06)';
        }

        row.innerHTML = `
            <td style="font-family:'JetBrains Mono',monospace; font-weight:600; padding:12px 20px;">
                <span style="color:var(--text-main); font-weight: 700;">${safeName}</span>
            </td>
            <td style="padding:12px 20px;">${platformBadge}</td>
            <td style="padding:12px 20px;">${ipBadge}</td>
            <td style="padding:12px 20px; font-size:13px; color:var(--text-muted);">${client.created_by || 'system'}</td>
            <td style="padding:12px 20px; font-size:12px; color:var(--text-muted); white-space:nowrap;">${client.created_at || '—'}</td>
            <td style="padding:12px 20px;">${statusBadge}</td>
            <td class="text-right" style="padding:12px 20px;">${actionButtons}</td>
        `;
        tbody.appendChild(row);
    });
}

// Изменение размера страницы пагинации
function changePageSize(val) {
    pageSize = (val === 'all') ? 'all' : parseInt(val);
    currentPage = 1;
    filterClients();
}

function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderClients(filteredClientsList);
    }
}

function nextPage() {
    const totalPages = pageSize === 'all' ? 1 : Math.ceil(filteredClientsList.length / pageSize);
    if (currentPage < totalPages) {
        currentPage++;
        renderClients(filteredClientsList);
    }
}

// Поиск и фильтрация
function filterClients() {
    const query = document.getElementById('searchInput').value.toLowerCase().trim();
    const hideRevoked = document.getElementById('hideRevokedCheck').checked;

    const filtered = allClients.filter(client => {
        const matchesQuery = client.name.toLowerCase().includes(query) ||
                             (client.ip && client.ip.toLowerCase().includes(query)) ||
                             (client.created_by && client.created_by.toLowerCase().includes(query));
        const matchesStatus = hideRevoked ? (client.status === 'Active') : true;
        return matchesQuery && matchesStatus;
    });

    renderClients(filtered);
}

let currentDetailClient = null;

// Открытие подробной модальной карточки клиента
function openDetailModal(name) {
    const client = allClients.find(c => c.name === name);
    if (!client) return;
    currentDetailClient = client;

    const devDefault = window.DEV_NODE_NAME || 'ITSG-OVPN';

    document.getElementById('detailClientName').innerText = client.name;
    document.getElementById('detailPlatformIcon').innerText = client.platform === 'windows' ? '🪟' : '🐧';
    
    // Заполняем поля редактирования
    const platformSelect = document.getElementById('detailPlatformSelect');
    if (platformSelect) {
        platformSelect.value = client.platform || 'windows';
    }
    
    const devNodeInput = document.getElementById('detailDevNodeInput');
    if (devNodeInput) {
        devNodeInput.value = (client.dev_node && client.dev_node !== '—') ? client.dev_node : devDefault;
    }
    
    const ipInput = document.getElementById('detailIpInput');
    if (ipInput) {
        ipInput.value = (client.ip && client.ip !== 'Dynamic') ? client.ip : '';
    }

    document.getElementById('detailCreatedBy').innerText = client.created_by || 'system';
    document.getElementById('detailCreatedAt').innerText = client.created_at || '—';
    document.getElementById('detailLastSeen').innerHTML = formatLastSeen(client.last_seen);
    
    const badgeContainer = document.getElementById('detailStatusBadge');
    badgeContainer.innerHTML = client.status === 'Active'
        ? `<span class="service-status-badge status-active">Активен</span>`
        : `<span class="service-status-badge status-failed">Отозван</span>`;

    const downloadBtn = document.getElementById('detailDownloadBtn');
    downloadBtn.href = `${BASE_PATH}/api/clients/download/${encodeURIComponent(client.name)}`;

    const msgEl = document.getElementById('detailActionMsg');
    if (msgEl) {
        msgEl.style.display = 'none';
        msgEl.innerText = '';
    }

    document.getElementById('clientDetailModal').classList.remove('hidden');
}

function closeDetailModal() {
    document.getElementById('clientDetailModal').classList.add('hidden');
    currentDetailClient = null;
}

// Сохранение отредактированных параметров и пересборка .ovpn
async function saveAndRebuildClient() {
    if (!currentDetailClient) return;
    const name = currentDetailClient.name;
    const saveBtn = document.getElementById('detailSaveBtn');
    const msgEl = document.getElementById('detailActionMsg');
    
    const platform = document.getElementById('detailPlatformSelect').value;
    const devNode = document.getElementById('detailDevNodeInput').value.trim();
    const ip = document.getElementById('detailIpInput').value.trim();

    saveBtn.disabled = true;
    msgEl.style.display = 'block';
    msgEl.style.color = 'var(--text-muted)';
    msgEl.innerText = 'Обновление параметров и пересборка .ovpn...';

    try {
        const response = await fetch(`${BASE_PATH}/api/clients/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                platform: platform,
                dev_node: devNode,
                ip: ip
            })
        });

        if (!checkAuthResponse(response)) return;
        const res = await response.json();

        if (response.ok) {
            msgEl.style.color = '#10b981';
            msgEl.innerText = 'Конфигурация успешно обновлена и готова к скачиванию!';
            currentDetailClient.platform = res.platform;
            currentDetailClient.dev_node = res.dev_node;
            currentDetailClient.ip = res.ip;
            document.getElementById('detailPlatformIcon').innerText = res.platform === 'windows' ? '🪟' : '🐧';
            loadClients();
            loadAuditLogs();
        } else {
            msgEl.style.color = '#ef4444';
            msgEl.innerText = `Ошибка: ${res.error}`;
        }
    } catch {
        msgEl.style.color = '#ef4444';
        msgEl.innerText = 'Ошибка соединения с сервером.';
    } finally {
        saveBtn.disabled = false;
    }
}

// Массовое обновление всех старых конфигураций до Wintun dev-node
async function rebuildAllClients() {
    const devName = window.DEV_NODE_NAME || 'ITSG-OVPN';
    if (!confirm(`Обновить все клиентские .ovpn файлы на сервере?\nКаждый Windows-клиент получит директивы "windows-driver wintun" и dev-node "${devName}".`)) {
        return;
    }

    try {
        const response = await fetch(`${BASE_PATH}/api/clients/rebuild-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        if (!checkAuthResponse(response)) return;
        const res = await response.json();

        if (response.ok) {
            alert(`Успешно обновлено ${res.count} конфигураций клиентов. Теперь все они содержат адаптер ${res.dev_node}.`);
            loadClients();
            loadAuditLogs();
        } else {
            alert(`Ошибка обновления: ${res.error}`);
        }
    } catch {
        alert('Ошибка связи с сервером при массовом обновлении.');
    }
}

// Создание нового клиента
async function createClient() {
    const input = document.getElementById('clientNameInput');
    const name = input.value.trim();
    const msg = document.getElementById('actionMessage');
    const submitBtn = document.getElementById('createClientBtn');
    
    if (!name) {
        msg.style.color = "#ef4444";
        msg.innerText = "Укажите имя клиента (CN)";
        return;
    }

    const customIpInput = document.getElementById('customIpInput');
    const customIp = customIpInput ? customIpInput.value.trim() : '';

    msg.style.color = "var(--text-muted)";
    msg.style.display = "block";
    msg.innerText = "Генерация сертификата и сборка конфигурации на сервере...";
    submitBtn.disabled = true;

    try {
        const response = await fetch(`${BASE_PATH}/api/clients`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ 
                name: name,
                platform: selectedPlatform,
                ip: customIp
            })
        });
        
        if (!checkAuthResponse(response)) return;
        const result = await response.json();
        
        if (response.ok) {
            msg.style.color = "#10b981";
            msg.innerText = `Конфигурация ${result.client} (${result.platform}, Wintun: ${result.dev_node || 'ITSG-OVPN'}) успешно выпущена.`;
            input.value = '';
            if (customIpInput) customIpInput.value = '';
            loadClients();
            loadNodeSettings();
            loadAuditLogs();
        } else {
            msg.style.color = "#ef4444";
            msg.innerText = `Ошибка: ${result.error}`;
        }
    } catch (err) {
        msg.style.color = "#ef4444";
        msg.innerText = "Ошибка соединения с сервером.";
    } finally {
        submitBtn.disabled = false;
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
    closeDetailModal();
    
    try {
        const response = await fetch(`${BASE_PATH}/api/clients/revoke`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ name: nameToSend })
        });
        
        if (!checkAuthResponse(response)) return;
        
        if (response.ok) {
            loadClients();
            loadNodeSettings();
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
        const response = await fetch(`${BASE_PATH}/logout`, { method: 'POST' });
        if (response.ok) {
            window.location.href = `${BASE_PATH}/login`;
        }
    } catch {
        alert('Ошибка связи при выходе');
    }
}

// Переключение вкладок
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

// Инициализация при загрузке страницы
window.onload = function() {
    loadClients();
    checkServiceStatus();
    loadNodeSettings();
    loadAuditLogs();
    initThemeToggle();
    
    // Периодическое обновление данных раз в 10 секунд
    setInterval(function() {
        checkServiceStatus();
        if (currentTab === 'certificates') {
            loadClients();
        } else {
            loadAuditLogs();
        }
    }, 10000);
};
