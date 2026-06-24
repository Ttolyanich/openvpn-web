let allClients = [];
let clientToRevoke = null;

async function checkServiceStatus() {
    const indicator = document.getElementById('serviceIndicator');
    try {
        const response = await fetch('/api/service/status');
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

async function restartService() {
    const btn = document.getElementById('serviceRestartBtn');
    const indicator = document.getElementById('serviceIndicator');
    btn.classList.add('spinning');
    indicator.className = 'service-status-badge status-loading';
    indicator.innerText = 'VPN: RESTARTING...';

    try {
        const response = await fetch('/api/service/restart', { method: 'POST' });
        if (response.ok) {
            setTimeout(async () => {
                await checkServiceStatus();
                btn.classList.remove('spinning');
            }, 1500);
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

async function loadClients() {
    try {
        const response = await fetch('/api/clients');
        allClients = await response.json();
        filterClients();
    } catch (error) {
        console.error("Ошибка загрузки данных:", error);
    }
}

function renderClients(clients) {
    const tbody = document.getElementById('clientsTableBody');
    tbody.innerHTML = '';

    if (clients.length === 0) {
        tbody.innerHTML = `<tr><td colspan="3" style="text-align:center; padding:32px; color:#888;">Клиенты не найдены</td></tr>`;
        return;
    }

    clients.forEach(client => {
        const isActive = client.status === 'Active';
        const statusBadge = isActive 
            ? `<span class="status-badge active">Активен</span>`
            : `<span class="status-badge revoked">Отозван</span>`;

        const safeName = client.name.replace(/['"]/g, '');

        const actionButtons = isActive 
            ? `<div class="actions-group">
                <button onclick="rebuildClient('${safeName}')" class="btn-action-rebuild" title="Перегенерировать .ovpn на базе нового common-файла">Пересобрать</button>
                <a href="/api/clients/download/${encodeURIComponent(safeName)}" class="btn-action-download">Скачать</a>
                <button onclick="openRevokeModal('${safeName}')" class="btn-action-revoke">Отозвать</button>
               </div>`
            : `<div class="text-muted-italic">Действий нет</div>`;

        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="font-family:monospace; font-weight:600; padding:14px 24px;">${safeName}</td>
            <td style="padding:14px 24px;">${statusBadge}</td>
            <td class="text-right" style="padding:14px 24px;">${actionButtons}</td>
        `;
        tbody.appendChild(row);
    });
}

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

async function createClient() {
    const input = document.getElementById('clientNameInput');
    const name = input.value.trim();
    const msg = document.getElementById('actionMessage');
    
    if (!name) return;

    msg.style.color = "#2563eb";
    msg.style.display = "block";
    msg.innerText = "Генерация сертификата на сервере...";

    try {
        const response = await fetch('/api/clients', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        const result = await response.json();
        
        if (response.ok) {
            msg.style.color = "#10b981";
            msg.innerText = `Конфигурация ${result.client} успешно выпущена.`;
            input.value = '';
            loadClients();
        } else {
            msg.style.color = "#ef4444";
            msg.innerText = `Ошибка: ${result.error}`;
        }
    } catch (err) {
        msg.style.color = "#ef4444";
        msg.innerText = "Ошибка соединения с сервером.";
    }
}

async function rebuildClient(name) {
    if (!confirm(`Пересобрать .ovpn файл для ${name}? Текущий файл в /root/openvpn будет перезаписан с учетом новых переменных client-common.`)) return;
    try {
        const response = await fetch('/api/clients/rebuild', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        if (response.ok) {
            alert(`Конфиг для ${name} успешно обновлен.`);
        } else {
            const res = await response.json();
            alert(`Ошибка: ${res.error}`);
        }
    } catch {
        alert('Ошибка отправки запроса на сервер.');
    }
}

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

document.getElementById('modalConfirmBtn').onclick = async function() {
    if (!clientToRevoke) return;
    const nameToSend = clientToRevoke;
    closeModal();
    
    try {
        const response = await fetch('/api/clients/revoke', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ name: nameToSend })
        });
        
        if (response.ok) {
            loadClients();
        } else {
            const res = await response.json();
            alert("Ошибка при отзыве: " + (res.error || 'Неизвестный сбой'));
        }
    } catch (err) {
        alert("Ошибка связи с сервером.");
    }
};

window.onload = function() {
    loadClients();
    checkServiceStatus();
    setInterval(checkServiceStatus, 10000); // Опрос статуса службы каждые 10 секунд
};
