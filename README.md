# 🛡️ OpenVPN Web Management Gateway (All-in-One Container)

[![Stack](https://img.shields.io/badge/python-3.11%2B-blue.svg?style=for-the-badge&logo=python)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-all--in--one-2496ED.svg?style=for-the-badge&logo=docker)](https://www.docker.com/)
[![Framework](https://img.shields.io/badge/Flask-3.0%2B-lightgrey.svg?style=for-the-badge&logo=flask)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/SQLite-3%20WAL-blue.svg?style=for-the-badge&logo=sqlite)](https://www.sqlite.org/)
[![Network](https://img.shields.io/badge/Wintun-Multi--Tunnel-0078d4.svg?style=for-the-badge&logo=windows)](https://openvpn.net)

Современный изолированный шлюз управления OpenVPN, полностью упакованный в **All-in-One Docker-контейнер**. Демон OpenVPN, Easy-RSA PKI, сокет управления и веб-панель работают в едином изолированном окружении под управлением Supervisor. 

Полная стерилизация хоста: контейнер **не требует** `privileged: true`, `pid: host`, `nsenter` или доступа к системным каталогам хостовой ОС.

---

## ⚡ Основные возможности

* **Левое боковое меню (Sidebar "шторка"):**
  * Современная навигация в стиле корпоративных дашбордов (srv-control / ZWR) с быстрым доступом к реестру, аудиту, настройкам и пользователям.
* **Полное веб-администрирование конфигурации OpenVPN:**
  * Управление `server.conf` и `client-common.txt` прямо из веб-панели: внешний хост, порт, протокол (UDP/TCP), виртуальная подсеть, Push-маршруты в корпоративные сети, Push DNS серверы, `redirect-gateway` и `client-to-client`.
  * Безопасное применение с модальным окном подтверждения, сводкой различий (diff) и предупреждением о разрыве текущих сессий онлайн-клиентов.
* **Динамическая сборка клиентских конфигураций (On-The-Fly):**
  * Файлы `.ovpn` собираются на лету при скачивании из актуального `server.conf`, `client-common.txt` и сертификатов Easy-RSA. Больше никакой рассинхронизации старых файлов на диске.
* **Тумблер изоляции Windows Wintun (`dev-node`):**
  * Возможность включить или отключить директиву `dev-node` для Windows-клиентов. При отключении директива исключается из конфигураций, а поле имени адаптера блокируется в интерфейсе.
* **Разграничение прав доступа (RBAC): Администраторы и Операторы:**
  * **Операторы:** выпуск новых сертификатов, назначение статических IP (CCD), скачивание `.ovpn`, отзыв доступа и просмотр логов.
  * **Администраторы:** полный доступ, включая редактирование конфигурации сервера OpenVPN и назначение ролей пользователям.
  * Назначение админов через `.env` (`ADMIN_USERS`, `ADMIN_GROUP`) + кеширование вошедших пользователей в SQLite (`users` table) с возможностью смены ролей в веб-интерфейсе.
* **Стерилизация и безопасность хоста:**
  * Никаких `privileged: true` и `pid: host`.
  * Веб-панель управляет демоном OpenVPN через локальный Unix Management Socket (`/run/openvpn.sock`).
  * Отзыв сертификата перечитывает CRL на лету (`signal SIGHUP`) без разрыва туннелей других клиентов, и точечно завершает сессию отозванного пользователя (`kill <client>`).
* **Умный пул статических IP (CCD):**
  * Автоматический парсинг подсети сервера (`server.conf`).
  * Бесконфликтное назначение минимального свободного IP из пула хостов без фрагментации.
  * Валидация формата IPv4 и предотвращение выдачи дублирующихся IP в реальном времени.
* **Двойной режим авторизации (`AUTH_MODE`):**
  * `central`: централизованная аутентификация через `centralized-auth` (SSO).
  * `standalone`: автономный запуск со своей локальной учетной записью администратора.
* **6 цветовых палитр:**
  * Classic Zinc (по умолчанию), Cyber Lime, Electric Blue, Crimson Red, Rust Orange, Graphite Pink с поддержкой темного и светлого режимов и сохранением в браузере оператора.

---

## 📂 Структура данных (`./data`)

Все постоянные файлы изолированы внутри локального каталога `./data`:

```text
openvpn-web/
├── data/
│   ├── server/             # Конфигурация OpenVPN
│   │   ├── server.conf     # Конфиг сервера OpenVPN
│   │   ├── ca.crt          # Корневой сертификат CA
│   │   ├── server.crt      # Сертификат сервера
│   │   ├── server.key      # Приватный ключ сервера
│   │   ├── dh.pem          # DH параметры (RFC 7919)
│   │   ├── tc.key          # Ключ tls-crypt
│   │   ├── crl.pem         # Список отозванных сертификатов
│   │   ├── client-common.txt # Шаблон конфигурации клиента
│   │   └── ccd/            # Статические IP клиентов (client-config-dir)
│   ├── easy-rsa/           # PKI хранилище Easy-RSA (ключи и сертификаты клиентов)
│   ├── clients/            # Готовые клиентские файлы .ovpn для скачивания
│   ├── node.db             # База данных аудита и метаданных клиентов (SQLite WAL)
│   └── config.json         # Локальные параметры ноды
├── docker-compose.yml      # Файл развертывания стека
├── Dockerfile              # All-in-One образ (OpenVPN + EasyRSA + Web)
├── entrypoint.sh           # Скрипт инициализации и авто-бутстрапа
├── supervisord.conf        # Конфигурация супервизора процессов
└── .env.example            # Пример файла конфигурации
```

---

## 🚀 Быстрый запуск (1 команда)

### 1. Настройка маршрутизации на хосте (однократно)
Так как контейнер работает в высокопроизводительном режиме `network_mode: host`, включите пересылку пакетов и правило NAT (замените `eth0` на имя вашего внешнего сетевого интерфейса):

```bash
# Включить форвардинг пакетов IPv4
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Разрешить NAT для подсети OpenVPN (10.8.0.0/24)
sudo iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE
```
*(Для сохранения правил после перезагрузки установите `iptables-persistent` или используйте UFW: `ufw route allow in on tun0`).*

### 2. Запуск контейнера OpenVPN Hub
```bash
git clone https://github.com/Ttolyanich/openvpn-web.git /opt/openvpn-web
cd /opt/openvpn-web

# Создайте файл окружения
cp .env.example .env
nano .env

# Запустите шлюз
docker compose up -d --build
```

Панель управления доступна по адресу: `http://<IP_сервера>:5000`

---

## ⚙️ Конфигурация окружения (`.env`)

| Переменная | Описание | Значение по умолчанию |
| :--- | :--- | :---: |
| `BIND_HOST` | Хост для прослушивания веб-панели | `0.0.0.0` |
| `BIND_PORT` | Порт веб-панели | `5000` |
| `OPENVPN_PORT` | Входящий порт сервера OpenVPN | `1194` |
| `OPENVPN_PROTO` | Протокол OpenVPN (`udp` или `tcp`) | `udp` |
| `PUBLIC_IP` | Внешний IP/домен для клиентов (если пусто — автоопределение) | *Авто* |
| `AUTH_MODE` | Режим авторизации (`central` или `standalone`) | `central` |
| `STANDALONE_ADMIN_USER` | Логин администратора для автономного режима | `admin` |
| `STANDALONE_ADMIN_PASSWORD` | Пароль администратора для автономного режима | `admin123` |
| `ADMIN_USERS` | Список логинов/email админов через запятую (RBAC) | `admin` |
| `ADMIN_GROUP` | Имя группы администраторов в Central Auth / LDAP | `VPN_Admins` |
| `SECRET_KEY` | Секретный ключ сессий Flask | *Сгенерируйте свой* |
| `CENTRAL_AUTH_URL` | URL центрального сервиса `centralized-auth` | `http://host.docker.internal:5001` |
| `NODE_API_TOKEN` | Секретный токен для связи с Central Auth | *Обязательный* |
| `DEV_NODE_ENABLED` | Добавлять изоляцию Wintun `dev-node` для Windows | `true` |
| `DEV_NODE_NAME` | Имя виртуального адаптера Wintun | `VPN-ADAPTER` |
| `DEV_NODE_MODE` | Режим именования адаптера (`common` или `none`) | `common` |
| `NODE_NAME` | Отображаемое имя ноды в заголовке вкладки | `VPN-Gateway-1` |
| `VERIFY_SSL` | Проверка SSL-сертификата центра авторизации | `false` |

---

## 🔄 Миграция существующих сертификатов со старого сервера

Если у вас уже был установлен OpenVPN на хосте (`/etc/openvpn/server`), просто скопируйте файлы в папку `./data/` перед запуском контейнера:

```bash
mkdir -p ./data/server ./data/easy-rsa
# Скопируйте ключи и конфиги сервера
sudo cp -r /etc/openvpn/server/* ./data/server/
# Скопируйте PKI базу Easy-RSA
sudo cp -r /etc/openvpn/server/easy-rsa/* ./data/easy-rsa/
# Скопируйте готовые ovpn-файлы (если есть)
sudo cp -r /root/openvpn/* ./data/clients/

# Запустите контейнер
docker compose up -d
```
Контейнер автоматически обнаружит существующий `server.conf` и PKI и запустится на ваших ключах без необходимости перевыпускать сертификаты пользователям!
