const BASE_PATH = window.location.pathname.startsWith('/openvpn') ? '/openvpn' : '';

let allUsers = [];

// Проверка ответа на 401
function checkAuthResponse(response) {
    if (response.status === 401) {
        window.location.href = `${BASE_PATH}/login`;
        return false;
    }
    return true;
}

// Загрузка текущего имени
function loadCurrentUserName() {
    // Возьмем имя из сессии (обычно хранится в cookies, получим через простую логику)
    // Либо просто сделаем fetch, но для простоты получим из логов / страницы.
    document.getElementById('currentUserSpan').innerText = "Панель Auth";
}

// Загрузка списка пользователей
async function loadUsers() {
    try {
        const response = await fetch(`${BASE_PATH}/api/users?_=${new Date().getTime()}`);
        if (!checkAuthResponse(response)) return;
        
        allUsers = await response.json();
        renderUsers(allUsers);
    } catch (error) {
        console.error("Ошибка загрузки пользователей:", error);
    }
}

// Отрисовка таблицы пользователей
function renderUsers(users) {
    const tbody = document.getElementById('usersTableBody');
    tbody.innerHTML = '';

    if (users.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:32px; color:#888;">Пользователи не найдены</td></tr>`;
        return;
    }

    users.forEach(user => {
        const safeName = user.username.replace(/['"]/g, '');
        const deleteButton = `<button onclick="deleteUser(${user.id}, '${safeName}')" class="btn-action-revoke">Удалить</button>`;

        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="font-family:monospace; font-weight:600; padding:14px 24px;">${safeName}</td>
            <td style="padding:14px 24px;"><span class="status-badge active">${user.role}</span></td>
            <td style="padding:14px 24px; color: #888;">${user.created_at}</td>
            <td class="text-right" style="padding:14px 24px;">${deleteButton}</td>
        `;
        tbody.appendChild(row);
    });
}

// Создание пользователя
async function createUser() {
    const userInp = document.getElementById('newUsername');
    const passInp = document.getElementById('newPassword');
    const username = userInp.value.trim();
    const password = passInp.value.trim();
    const msg = document.getElementById('actionMessage');

    if (!username || !password) {
        msg.style.color = "#ef4444";
        msg.style.display = "block";
        msg.innerText = "Заполните все поля";
        return;
    }

    msg.style.color = "#2563eb";
    msg.style.display = "block";
    msg.innerText = "Создание пользователя...";

    try {
        const response = await fetch(`${BASE_PATH}/api/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ username: username, password: password, role: 'admin' })
        });
        
        if (!checkAuthResponse(response)) return;
        const result = await response.json();
        
        if (response.ok) {
            msg.style.color = "#10b981";
            msg.innerText = `Пользователь ${username} успешно добавлен.`;
            userInp.value = '';
            passInp.value = '';
            loadUsers();
        } else {
            msg.style.color = "#ef4444";
            msg.innerText = `Ошибка: ${result.error}`;
        }
    } catch (err) {
        msg.style.color = "#ef4444";
        msg.innerText = "Ошибка соединения с сервером.";
    }
}

// Удаление пользователя
async function deleteUser(userId, name) {
    if (!confirm(`Вы действительно хотите удалить пользователя ${name}?`)) return;
    
    try {
        const response = await fetch(`${BASE_PATH}/api/users/delete/${userId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!checkAuthResponse(response)) return;
        
        if (response.ok) {
            loadUsers();
        } else {
            const res = await response.json();
            alert("Ошибка при удалении: " + res.error);
        }
    } catch (err) {
        alert("Ошибка связи с сервером.");
    }
}

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

window.onload = function() {
    loadCurrentUserName();
    loadUsers();
};
