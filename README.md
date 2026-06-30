# OpenVPN Web Management Panel (Панель управления нодой VPN)

<img width="1198" height="569" alt="image" src="https://github.com/user-attachments/assets/95b505c2-ef22-4cdb-89c7-027b7b67cd63" />

Данный проект представляет собой легковесную веб-панель на Python/Flask для управления клиентскими конфигурациями OpenVPN, предназначенную для развертывания на VPN-серверах (нодах). Авторизация пользователей полностью делегирована центральному серверу авторизации `centralized-auth` по схеме SSO-подобной проверки по API.

---

## Архитектура ноды

* **Управление OpenVPN и Easy-RSA:** Выпуск и отзыв сертификатов, автоматическая генерация файлов `.ovpn`.
* **Локальный аудит действий (`node.db`):** Все действия администраторов на этой ноде логируются локально (хранятся последние 1000 записей).
* **Мониторинг активности:** Отслеживание статуса клиентов в реальном времени ("Last Seen" / "Онлайн").
* **Взаимодействие с Auth Server:** Локальных учетных записей на ноде нет. При входе нода пересылает учетные данные администратора на сервер авторизации.

---

## Сетевое взаимодействие

```text
                                  +---------------------------------------+
                                  | Central Auth Server: centralized-auth |
                                  |             (Порт 5001)               |
                                  +---------------------------------------+
                                                      ^
                                                      | 
                                           /api/auth/verify (X-Node-Token)
                                                      |
+-----------------------------------------------------+---------------------+
| VPN Web Node: openvpn-web (Порт 5000 / Nginx 80)                           |
+---------------------------------------------------------------------------+
```

---

## Пошаговое развертывание всей связки

Для корректной работы ноды вам понадобятся:
1. Развернутый и доступный **Центральный сервер авторизации** (`centralized-auth`).
2. Установленный **OpenVPN** и настроенный **Easy-RSA** на текущем сервере.
3. Веб-панель `openvpn-web`.

---

### Шаг 1. Настройка Центрального сервера авторизации
Если у вас еще не развернут сервер авторизации:
1. Разверните репозиторий [centralized-auth](https://github.com/Ttolyanich/centralized-auth) в папку `/opt/centralized-auth` на выделенном сервере (или на одном из VPN-серверов):
   ```bash
   sudo git clone https://github.com/Ttolyanich/centralized-auth.git /opt/centralized-auth
   ```
2. Запустите службу `centralized-auth`. Настройте порт `5001` и доступность по сети.
3. Зайдите в `/opt/centralized-auth/config.json` и скопируйте значения:
   * **`node_api_token`** (секретный токен для проверки запросов нод).
4. Запомните IP-адрес и порт сервера авторизации (например, `http://<IP_АДРЕС_CENTRAL_AUTH>:5001`).

---

### Шаг 2. Подготовка сети и маршрутизации на VPN-ноде
Чтобы VPN-клиенты имели доступ в интернет через ваш сервер, включите форвардинг трафика и настройте NAT:

1. **Включите пересылку IPv4 (IP Forwarding):**
   ```bash
   echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/forward.conf
   sudo sysctl -p /etc/sysctl.d/forward.conf
   ```

2. **Настройте NFTables для трансляции адресов (Masquerade):**
   Убедитесь, что установлен `nftables`:
   ```bash
   sudo apt-get update && sudo apt-get install nftables -y
   ```
   Отредактируйте конфигурационный файл `/etc/nftables.conf`, чтобы настроить NAT для туннельного интерфейса `tun0`:
   ```nftables
   #!/usr/sbin/nft -f

   flush ruleset

   table ip nat {
       chain POSTROUTING {
           type nat hook postrouting priority srcnat; policy accept;
           iifname "tun0" masquerade
       }
   }
   ```
   Включите автозапуск и примените правила:
   ```bash
   sudo systemctl enable --now nftables
   ```

---

### Шаг 3. Установка ядра OpenVPN
Для установки и базовой настройки сервера OpenVPN выполните скрипт:
```bash
wget -O /tmp/openvpn.sh https://raw.githubusercontent.com/Ttolyanich/openvpn-web/main/openvpn.sh
chmod +x /tmp/openvpn.sh
sudo /tmp/openvpn.sh
```
*(Скрипт установит пакеты openvpn, настроит интерфейс `tun0`, создаст корневой сертификат CA и первоначального клиента).*

---

### Шаг 4. Установка и запуск веб-панели ноды

#### Вариант 1. Запуск через Docker (Рекомендуемый)

При запуске через Docker панель изолирована в контейнере, но монтирует папки с конфигурациями OpenVPN и ключами Easy-RSA с хоста, а также использует системную шину D-Bus для управления службой на хосте.

1. **Клонирование репозитория веб-панели:**
   ```bash
   sudo git clone https://github.com/Ttolyanich/openvpn-web.git /opt/openvpn-web
   cd /opt/openvpn-web
   ```
2. **Настройка окружения:**
   Создайте файл `.env` из шаблона:
   ```bash
   cp .env.example .env
   ```
   Отредактируйте `.env` и укажите параметры (секретный ключ, адрес центра авторизации, токен ноды):
   ```bash
   nano .env
   ```
3. **Запуск контейнера:**
   ```bash
   docker compose up -d --build
   ```
   *Все данные (база активности `node.db`, файлы OpenVPN и ключи Easy-RSA) монтируются напрямую с хоста и будут в полной сохранности.*

---

#### Вариант 2. Нативный запуск (через systemd и Nginx)

1. **Клонирование репозитория веб-панели:**
   ```bash
   sudo git clone https://github.com/Ttolyanich/openvpn-web.git /opt/openvpn-web
   ```
2. **Установка необходимых зависимостей Python и Nginx:**
   ```bash
   sudo apt-get update
   sudo apt-get install python3-pip python3-flask python3-requests nginx -y
   ```
3. **Выдача прав владельца:**
   ```bash
   sudo chown -R root:root /opt/openvpn-web
   ```
4. **Настройка взаимосвязи с сервером авторизации:**
   Создайте или отредактируйте файл `/opt/openvpn-web/config.json`:
   ```json
   {
     "secret_key": "сгенерируйте_случайный_ключ_сессии_этой_ноды",
     "central_auth_url": "http://<IP_АДРЕС_CENTRALIZED_AUTH>:5001",
     "node_api_token": "<ТОКЕН_ИЗ_КОНФИГУРАЦИИ_СЕРВЕРА_АВТОРИЗАЦИИ>",
     "bind_host": "0.0.0.0"
   }
   ```
   > [!IMPORTANT]
   > Значение `"node_api_token"` на ноде должно **символ в символ** совпадать со значением `"node_api_token"` в конфигурационном файле Центрального сервера авторизации. Без этого верификация логинов будет отклонена с ошибкой `403 Forbidden`.

5. **Настройка автозапуска службы веб-панели:**
   ```bash
   sudo cp /opt/openvpn-web/openvpn-web.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now openvpn-web
   ```
   Проверьте статус (веб-интерфейс запустится на порту `5000`):
   ```bash
   sudo systemctl status openvpn-web
   ```
6. **Настройка Nginx в качестве Reverse Proxy:**
   Настройте проксирование внешнего порта `80` на локальный порт `5000`. Запишите в `/etc/nginx/sites-enabled/openvpn-web.conf`:
   ```nginx
   server {
       listen 80;
       server_name _;

       location / {
           proxy_pass http://127.0.0.1:5000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       }
   }
   ```
   Удалите стандартный шаблон Nginx и перезапустите веб-сервер:
   ```bash
   sudo rm -f /etc/nginx/sites-enabled/default
   sudo systemctl restart nginx
   ```

---

### Шаг 7. Проверка работоспособности
1. Перейдите по адресу `http://<IP_АДРЕС_VPN_НОДЫ>/` в браузере.
2. Введите учетные данные администратора, созданного на Центральном сервере авторизации.
3. Нода отправит запрос на `/api/auth/verify` к Центральному серверу авторизации. В случае успеха вы попадете на главную страницу панели управления VPN.
4. Выпустите тестовую конфигурацию клиента и проверьте её скачивание.
5. Проверьте вкладку "Журнал действий" — там должна отобразиться запись о входе.
