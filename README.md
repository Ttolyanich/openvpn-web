# 🛡️ OpenVPN Web Management Gateway

[![Stack](https://img.shields.io/badge/python-3.11%2B-blue.svg?style=for-the-badge&logo=python)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Flask-3.0%2B-lightgrey.svg?style=for-the-badge&logo=flask)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/SQLite-3%20WAL-blue.svg?style=for-the-badge&logo=sqlite)](https://www.sqlite.org/)
[![Network](https://img.shields.io/badge/Wintun-Multi--Tunnel-0078d4.svg?style=for-the-badge&logo=windows)](https://openvpn.net)

Современная веб-панель управления клиентами и туннелями OpenVPN. Поддерживает работу как в режиме SSO через `centralized-auth`, так и полностью автономно (`standalone`). Включает нативную изоляцию сетевых интерфейсов Windows (Wintun dev-node), умное распределение статических IP адресов из пула через CCD и подробные паспорта конфигураций.

---

## ⚡ Основные возможности

* **Мультитуннели и изоляция адаптеров на Windows (Wintun):**
  * Включение директивы `windows-driver wintun` для максимальной производительности.
  * Автоматическое тегирование виртуального интерфейса `dev-node "ITSG-OVPN_<client>"`: решает проблему конфликтов, когда на рабочей станции пользователя одновременно работают другие корпоративные VPN или Radmin.
* **Селектор платформ (Windows vs Linux):**
  * 🪟 **Windows:** конфиг включает оптимизации Wintun и dev-node.
  * 🐧 **Linux / macOS:** специфичные для Windows директивы исключаются, исключая синтаксические ошибки клиента OpenVPN.
* **Умный пул статических IP (CCD):**
  * Автоматический парсинг подсети сервера (`server.conf`).
  * Бесконфликтный выбор минимального свободного IP из пула хостов без дыр и фрагментации.
  * Автоматическая генерация файлов `ccd/<client_name>` с директивой `ifconfig-push <ip> <netmask>`.
* **Паспорт клиента и расширенные метаданные:**
  * Интерактивная модальная карточка клиента.
  * Фиксация: **кто выпустил** (`created_by`), **дата и время выпуска** (`created_at`), **платформа**, **IP** и **dev-node**.
  * Хранение готовых `.ovpn` файлов в рабочем каталоге `./data/configs/` с возможностью мгновенного скачивания или пересборки.
* **Пагинация и фильтрация:**
  * Выбор количества отображаемых записей (10, 25, 50, 100, Все).
  * Мгновенный поиск по имени, IP-адресу или автору выпуска.
* **Двойной режим авторизации (`AUTH_MODE`):**
  * `central`: централизованная аутентификация через `centralized-auth` (SSO).
  * `standalone`: автономный запуск со своей локальной учетной записью администратора.
* **Фирменный дизайн:** Modern Enterprise SaaS Dark (Slate `#0b1120`, Indigo/Sapphire акценты, карточки KPI телеметрии).

---

## 📂 Структура проекта

```text
/opt/openvpn-web/
├── app.py                  # Главный исполняемый файл приложения Flask
├── config.json             # Конфигурационный файл параметров ноды
├── node.db                 # База данных аудита и метаданных клиентов (SQLite WAL)
├── openvpn.sh              # Скрипт автоматической установки OpenVPN и Easy-RSA
├── openvpn-web.service     # Файл службы systemd для автозапуска
├── .env.example            # Пример файла конфигурации окружения
├── templates/              # HTML-шаблоны веб-интерфейса
│   ├── login.html          # Шаблон входа
│   └── index.html          # Главная панель с KPI, таблицей, карточками и пагинацией
└── static/                 # Статические файлы стилей и скриптов
    ├── css/style.css       # Таблица стилей Modern Enterprise SaaS
    └── js/main.js          # Логика пагинации, карточек, фильтрации и выпуска
```

---

## ⚙️ Конфигурация окружения (`.env`)

Создайте файл `.env` на основе шаблона `.env.example`:

| Переменная | Описание | Значение по умолчанию |
| :--- | :--- | :---: |
| `BIND_HOST` | Интерфейс прослушивания | `0.0.0.0` |
| `BIND_PORT` | Порт для запуска веб-панели | `5000` |
| `AUTH_MODE` | Режим авторизации (`central` или `standalone`) | `central` |
| `STANDALONE_ADMIN_USER` | Логин локального админа для standalone-режима | `admin` |
| `STANDALONE_ADMIN_PASSWORD` | Пароль локального админа для standalone-режима | `admin123` |
| `SECRET_KEY` | Секретный ключ сессий Flask этой ноды | *Автогенерация* |
| `CENTRAL_AUTH_URL` | URL-адрес сервера `centralized-auth` | `http://host.docker.internal:5001` |
| `NODE_API_TOKEN` | Секретный токен связи с Central Auth | *Обязательный* |
| `DEV_NODE_ENABLED` | Включить тегирование `dev-node` для Windows | `true` |
| `DEV_NODE_PREFIX` | Префикс интерфейса (например: `ITSG-OVPN`) | `ITSG-OVPN` |
| `DEV_NODE_MODE` | Режим именования: `individual` (`<PREFIX>_<CN>`) или `shared` | `individual` |
| `CCD_DIR` | Каталог хранения статических IP (CCD) | `/etc/openvpn/server/ccd` |
| `CONFIGS_DIR` | Каталог сохранения готовых `.ovpn` файлов | `./data/configs` |
| `VERIFY_SSL` | Проверка SSL-сертификата центра авторизации | `false` |
| `NODE_NAME` | Название этой ноды для отображения в браузере | `VPN-Server-1` |

---

## 🚀 Развертывание

### Шаг 1. Подготовка OpenVPN на сервере
Для первоначальной установки ядра OpenVPN и Easy-RSA используйте скрипт:
```bash
wget -O /tmp/openvpn.sh https://raw.githubusercontent.com/Ttolyanich/openvpn-web/main/openvpn.sh
chmod +x /tmp/openvpn.sh
sudo /tmp/openvpn.sh
```

### Шаг 2. Запуск веб-панели

#### Вариант 1. Через Docker (Рекомендуемый)
```bash
git clone https://github.com/Ttolyanich/openvpn-web.git /opt/openvpn-web
cd /opt/openvpn-web
cp .env.example .env
nano .env
docker compose up -d
```

#### Вариант 2. Нативный запуск через Systemd
```bash
pip3 install -r requirements.txt
python3 app.py
sudo cp openvpn-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openvpn-web
```
