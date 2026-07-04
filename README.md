# 🛡️ OpenVPN Web Management Panel

[![Stack](https://img.shields.io/badge/python-3.10%2B-blue.svg?style=for-the-badge&logo=python)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Flask-3.0%2B-lightgrey.svg?style=for-the-badge&logo=flask)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/SQLite-3-blue.svg?style=for-the-badge&logo=sqlite)](https://www.sqlite.org/)

Легковесная веб-панель управления клиентами OpenVPN (нода), предназначенная для развертывания непосредственно на VPN-серверах. Не хранит локальных пользователей; авторизация администраторов делегирована Центральному серверу авторизации (`centralized-auth`) по схеме защищенной SSO-проверки.

---

## ⚡ Основные возможности

* **Управление клиентами OpenVPN:** Выпуск, скачивание готовых конфигураций `.ovpn`, отзыв сертификатов через Easy-RSA.
* **Делегированная авторизация:** SSO-авторизация администраторов и сотрудников через API-запросы к `centralized-auth`.
* **Локальный аудит действий:** Хранение последних 1000 записей действий администраторов в локальной БД `node.db`.
* **Мониторинг статуса в реальном времени:** Отслеживание статуса клиентов ("Онлайн" / "Был в сети") на основе парсинга логов OpenVPN.
* **Изолированный запуск:** Возможность работы в Docker-контейнере с управлением системным OpenVPN на хосте через D-Bus и привилегированный режим.

---

## 📂 Структура проекта

```text
/opt/openvpn-web/
├── app.py                  # Главный исполняемый файл приложения Flask
├── config.json             # Конфигурационный файл ноды
├── node.db                 # Локальная база данных аудита действий
├── openvpn.sh              # Скрипт автоматической установки ядра OpenVPN и Easy-RSA
├── openvpn-web.service     # Файл службы systemd для автозапуска
├── .env.example            # Пример файла конфигурации окружения
├── templates/              # HTML-шаблоны веб-интерфейса
└── static/                 # Статические файлы стилей и скриптов
    └── css/style.css       # Таблица стилей в красно-черном стиле
```

---

## ⚙️ Конфигурация окружения (`.env`)

Создайте файл `.env` на основе шаблона `.env.example`:

| Переменная | Описание | Значение по умолчанию |
| :--- | :--- | :---: |
| `PORT` | Порт для запуска веб-панели | `5000` |
| `BIND_HOST` | Интерфейс прослушивания | `0.0.0.0` |
| `SECRET_KEY` | Секретный ключ сессий Flask этой ноды (`openssl rand -hex 16`) | *Автогенерация* |
| `CENTRAL_AUTH_URL` | Базовый URL-адрес сервера авторизации `centralized-auth` | `http://host.docker.internal:5001` |
| `NODE_API_TOKEN` | Токен авторизации (должен совпадать с указанным на Auth-сервере) | `default-token` |
| `NODE_NAME` | Название этой ноды для отображения в браузере | *Имя хоста* |

---

## 🚀 Варианты развертывания

### Шаг 1. Установка OpenVPN на сервере
Для подготовки ядра OpenVPN и Easy-RSA воспользуйтесь скриптом:
```bash
wget -O /tmp/openvpn.sh https://raw.githubusercontent.com/Ttolyanich/openvpn-web/main/openvpn.sh
chmod +x /tmp/openvpn.sh
sudo /tmp/openvpn.sh
```

### Шаг 2. Развертывание панели

#### Вариант 1. Запуск через Docker (Рекомендуемый)
Панель работает в Docker-контейнере, но управляет OpenVPN на хосте через монтирование директорий и шину D-Bus.

1. **Клонирование проекта:**
   ```bash
   git clone https://github.com/Ttolyanich/openvpn-web.git /opt/openvpn-web
   cd /opt/openvpn-web
   ```
2. **Настройка окружения:**
   ```bash
   cp .env.example .env
   nano .env # Пропишите CENTRAL_AUTH_URL и секретный NODE_API_TOKEN
   ```
3. **Запуск:**
   ```bash
   docker compose up -d
   ```

#### Вариант 2. Нативный запуск (через Systemd)
1. Установите зависимости:
   ```bash
   sudo apt install python3-pip python3-flask python3-requests -y
   pip3 install -r requirements.txt
   ```
2. Настройте службу:
   ```bash
   sudo cp openvpn-web.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now openvpn-web
   ```
