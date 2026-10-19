import os
import re
import ipaddress
import subprocess
import sqlite3
import json
import time
import socket
import shutil
import requests
import urllib3
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, abort, redirect, url_for, session
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from vpn_manager import vpn_manager

# Отключение предупреждений об отсутствии SSL-сертификатов
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Загрузка переменных окружения из .env
load_dotenv()

# Загрузка конфигурации
CONFIG_PATH = os.getenv("OPENVPN_WEB_CONFIG", "/app/data/config.json")
config = {
    "auth_mode": "central", # "central" или "standalone"
    "standalone_admin_user": "admin",
    "standalone_admin_password": "admin123",
    "secret_key": "default-secret-key-32-chars-long-please-change",
    "central_auth_url": "http://127.0.0.1:5001",
    "node_api_token": "default-token",
    "node_name": socket.gethostname(),
    "bind_host": "0.0.0.0",
    "bind_port": 5000,
    "verify_ssl": True,
    # Настройки Wintun и dev-node
    "dev_node_enabled": True,
    "dev_node_name": os.getenv("DEV_NODE_NAME", os.getenv("DEV_NODE_PREFIX", "VPN-ADAPTER")),
    "dev_node_prefix": os.getenv("DEV_NODE_NAME", os.getenv("DEV_NODE_PREFIX", "VPN-ADAPTER")),
    "dev_node_mode": "common", # "common" (общий по умолчанию), "none", "custom"
    "configs_dir": os.getenv("CONFIGS_DIR", "./data/configs"),
    # Настройки ролей администраторов ноды
    "admin_users": [],
    "admin_group": ""
}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            file_config = json.load(f)
            if "bind_port" in file_config:
                file_config["bind_port"] = int(file_config["bind_port"])
            if "verify_ssl" in file_config:
                if isinstance(file_config["verify_ssl"], str):
                    file_config["verify_ssl"] = file_config["verify_ssl"].lower() == "true"
            config.update(file_config)
    except Exception as e:
        print(f"Error loading config: {e}")

# Поддержка переменных окружения (Docker/Systemd/.env)
if os.getenv("AUTH_MODE"):
    config["auth_mode"] = os.getenv("AUTH_MODE").lower().strip()
if os.getenv("STANDALONE_ADMIN_USER"):
    config["standalone_admin_user"] = os.getenv("STANDALONE_ADMIN_USER").strip()
if os.getenv("STANDALONE_ADMIN_PASSWORD"):
    config["standalone_admin_password"] = os.getenv("STANDALONE_ADMIN_PASSWORD")
if os.getenv("SECRET_KEY"):
    config["secret_key"] = os.getenv("SECRET_KEY")
if os.getenv("CENTRAL_AUTH_URL"):
    config["central_auth_url"] = os.getenv("CENTRAL_AUTH_URL")
if os.getenv("NODE_API_TOKEN"):
    config["node_api_token"] = os.getenv("NODE_API_TOKEN")
if os.getenv("NODE_NAME"):
    config["node_name"] = os.getenv("NODE_NAME")
if os.getenv("BIND_HOST"):
    config["bind_host"] = os.getenv("BIND_HOST")
if os.getenv("BIND_PORT"):
    config["bind_port"] = int(os.getenv("BIND_PORT"))
if os.getenv("VERIFY_SSL"):
    config["verify_ssl"] = os.getenv("VERIFY_SSL").lower() == "true"
if "dev_node_enabled" not in config and os.getenv("DEV_NODE_ENABLED"):
    config["dev_node_enabled"] = os.getenv("DEV_NODE_ENABLED").lower() in ["true", "1", "yes"]
if "dev_node_name" not in config:
    if os.getenv("DEV_NODE_NAME"):
        config["dev_node_name"] = os.getenv("DEV_NODE_NAME").strip()
        config["dev_node_prefix"] = os.getenv("DEV_NODE_NAME").strip()
    elif os.getenv("DEV_NODE_PREFIX"):
        config["dev_node_name"] = os.getenv("DEV_NODE_PREFIX").strip()
        config["dev_node_prefix"] = os.getenv("DEV_NODE_PREFIX").strip()
if os.getenv("DEV_NODE_MODE"):
    config["dev_node_mode"] = os.getenv("DEV_NODE_MODE").lower().strip()
if os.getenv("CONFIGS_DIR"):
    config["configs_dir"] = os.getenv("CONFIGS_DIR")

# Список администраторов ноды из .env
admin_users_raw = os.getenv("ADMIN_USERS") or os.getenv("OPENVPN_ADMIN_USERS") or ""
if admin_users_raw:
    config["admin_users"] = [u.strip().lower() for u in admin_users_raw.split(",") if u.strip()]
elif isinstance(config.get("admin_users"), str):
    config["admin_users"] = [u.strip().lower() for u in config["admin_users"].split(",") if u.strip()]
elif isinstance(config.get("admin_users"), list):
    config["admin_users"] = [str(u).strip().lower() for u in config["admin_users"] if str(u).strip()]

if os.getenv("ADMIN_GROUP"):
    config["admin_group"] = os.getenv("ADMIN_GROUP").strip()
elif os.getenv("OPENVPN_ADMIN_GROUP"):
    config["admin_group"] = os.getenv("OPENVPN_ADMIN_GROUP").strip()

# Автогенерация secret_key при первом старте
if config.get("secret_key") == "default-secret-key-32-chars-long-please-change" or not config.get("secret_key"):
    import secrets
    config["secret_key"] = secrets.token_hex(16)
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        print(f"AUTO-CONFIG: Generated secure random secret_key and saved to {CONFIG_PATH}")
    except Exception as e:
        print(f"Error saving auto-generated secret key: {e}")

if config.get("node_api_token") == "default-token" and config.get("auth_mode") == "central":
    print("*" * 60)
    print("WARNING: Node is using default 'node_api_token' with Central Auth!")
    print("Please set a secure shared token in config.json that matches the Auth Server.")
    print("*" * 60)

NODE_NAME = config["node_name"]

app = Flask(__name__)
app.secret_key = config["secret_key"]
app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME", "https")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
app.config.update(
    SESSION_COOKIE_NAME=os.getenv("SESSION_COOKIE_NAME", "vpn_session"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=str(os.getenv("SESSION_COOKIE_SECURE", "false")).lower() == "true",
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024)),
)

@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# Универсальный middleware для поддержки подпутей (subpath)
class SubpathMiddleware(object):
    def __init__(self, app, prefix=''):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', self.prefix)
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]
        return self.app(environ, start_response)

app.wsgi_app = SubpathMiddleware(app.wsgi_app, prefix='')

EASY_RSA_DIR = os.getenv("EASY_RSA_DIR", "/app/data/easy-rsa")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/data/clients")
CONFIGS_DIR = os.getenv("CONFIGS_DIR", config.get("configs_dir", "/app/data/clients"))
CLIENT_COMMON = os.getenv("CLIENT_COMMON", "/app/data/server/client-common.txt")
INDEX_FILE = os.getenv("INDEX_FILE", f"{EASY_RSA_DIR}/pki/index.txt")
OPENVPN_SERVICE = os.getenv("OPENVPN_SERVICE", "openvpn")

NODE_DB = os.getenv("DATABASE_PATH", "/app/data/node.db")

CCD_DIR = os.getenv("CCD_DIR", "/app/data/server/ccd")
SERVER_CONF = os.getenv("SERVER_CONF", "/app/data/server/server.conf")
IPP_FILE = os.getenv("IPP_FILE", "/app/data/server/ipp.txt")
STATUS_LOG = os.getenv("STATUS_LOG", "/app/data/openvpn-status.log")

def get_db_connection():
    conn = sqlite3.connect(NODE_DB, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn

# Инициализация базы данных
def init_db():
    db_dir = os.path.dirname(NODE_DB)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if CCD_DIR:
        os.makedirs(CCD_DIR, exist_ok=True)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_activity (
            common_name TEXT PRIMARY KEY,
            last_seen TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            rate_key TEXT NOT NULL,
            attempt_time REAL NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vpn_rate ON rate_limits (rate_key, attempt_time)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vpn_clients_meta (
            client_name TEXT PRIMARY KEY,
            ip_address TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            platform TEXT DEFAULT 'windows',
            dev_node TEXT,
            status TEXT DEFAULT 'Active'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            display_name TEXT,
            auth_source TEXT NOT NULL DEFAULT 'central',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Миграция схемы для таблицы users при обновлении
    try:
        cursor.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        if "display_name" not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        if "auth_source" not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'central'")
        if "last_login" not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.commit()
    except Exception as e:
        print(f"User schema migration warning: {e}")

    conn.close()

init_db()

# Синхронизация и кеширование пользователей ноды
def sync_node_user(username, central_role="operator", display_name=None, auth_source="central"):
    """
    Синхронизирует и кеширует пользователя в таблице users в node.db.
    Определяет роль: 'admin' или 'operator'.
    Если пользователь указан в ADMIN_USERS (включая вариант без @domain),
    он гарантированно получает роль 'admin'.
    Если уже существует в БД, сохраняет измененную в интерфейсе роль (кроме явного повышения в admin по ADMIN_USERS).
    """
    if not username:
        return "operator"
    clean_user = username.strip()
    short_login = clean_user.split('@')[0].split('\\')[-1].strip().lower()

    # Собираем список администраторов из .env
    admin_users_all = set()
    for u in config.get("admin_users", []):
        u_clean = u.strip().lower()
        if u_clean:
            admin_users_all.add(u_clean)
            if "@" in u_clean:
                admin_users_all.add(u_clean.split("@")[0])
            elif "\\" in u_clean:
                admin_users_all.add(u_clean.split("\\")[-1])

    is_explicit_admin = (clean_user.lower() in admin_users_all or short_login in admin_users_all)
    disp_name = display_name or clean_user

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, role, display_name FROM users WHERE LOWER(username) IN (?, ?)",
            (clean_user.lower(), short_login)
        )
        existing = cursor.fetchone()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not existing:
            initial_role = "admin" if (is_explicit_admin or central_role == "admin") else "operator"
            cursor.execute(
                "INSERT INTO users (username, role, display_name, auth_source, last_login) VALUES (?, ?, ?, ?, ?)",
                (clean_user, initial_role, disp_name, auth_source, now_str)
            )
            conn.commit()
            effective_role = initial_role
        else:
            effective_role = existing['role']
            if is_explicit_admin:
                effective_role = "admin"
            cursor.execute(
                "UPDATE users SET role = ?, display_name = ?, last_login = ? WHERE id = ?",
                (effective_role, disp_name or existing['display_name'], now_str, existing['id'])
            )
            conn.commit()
        conn.close()
        return effective_role
    except Exception as e:
        print(f"Error syncing node user: {e}")
        return "admin" if (is_explicit_admin or central_role == "admin") else "operator"

# Логирование действий пользователей (Audit Log)
def log_action(username, action, details=""):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (username, action, details) VALUES (?, ?, ?)",
            (username or "unknown", action, details)
        )
        cursor.execute("""
            DELETE FROM audit_logs 
            WHERE id NOT IN (
                SELECT id FROM audit_logs 
                ORDER BY id DESC 
                LIMIT 1000
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging action: {e}")

# Ограничитель попыток входа
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300

def login_rate_limited(key):
    now = time.time()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rate_limits WHERE attempt_time < ?", (now - LOGIN_WINDOW_SECONDS,))
        cursor.execute("SELECT COUNT(*) FROM rate_limits WHERE rate_key = ? AND attempt_time >= ?", (key, now - LOGIN_WINDOW_SECONDS))
        count = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return count >= LOGIN_MAX_ATTEMPTS
    except Exception as e:
        print(f"Rate limit check error: {e}")
        return False

def register_failed_login(key):
    now = time.time()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO rate_limits (rate_key, attempt_time) VALUES (?, ?)", (key, now))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Register failed login error: {e}")

# Декоратор авторизации
AUTH_CACHE_TTL = 180

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Декоратор прав Администратора
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        if session.get("role") != "admin":
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Forbidden: Требуются права Администратора"}), 403
            abort(403)
        return f(*args, **kwargs)
    return decorated_function



def parse_openvpn_server_subnet():
    if os.path.exists(SERVER_CONF):
        try:
            with open(SERVER_CONF, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("server ") and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) >= 3:
                            return parts[1], parts[2]
        except Exception as e:
            print(f"Error parsing server.conf subnet: {e}")
    return "10.8.0.0", "255.255.255.0"

def get_subnet_hosts():
    subnet_ip, netmask = parse_openvpn_server_subnet()
    try:
        network = ipaddress.IPv4Network(f"{subnet_ip}/{netmask}", strict=False)
        hosts = list(network.hosts())
        if len(hosts) > 1:
            # Исключаем адрес сервера (шлюз)
            return [str(ip) for ip in hosts[1:]]
        return [str(ip) for ip in hosts]
    except Exception as e:
        print(f"Error generating IP list from subnet: {e}")
    return [f"10.8.0.{i}" for i in range(2, 255)]

def get_static_ip_map():
    ip_map = {}
    # Сначала считываем динамические из ipp.txt
    if os.path.exists(IPP_FILE):
        try:
            with open(IPP_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        parts = line.split(",")
                        if len(parts) >= 2:
                            ip_map[parts[0].lower()] = parts[1]
        except Exception as e:
            print(f"Error reading ipp.txt: {e}")

    # Статические IP из каталога CCD (высший приоритет)
    if CCD_DIR and os.path.exists(CCD_DIR):
        try:
            for name in os.listdir(CCD_DIR):
                file_path = os.path.join(CCD_DIR, name)
                if os.path.isfile(file_path):
                    with open(file_path, "r") as f:
                        content = f.read()
                    match = re.search(r'ifconfig-push\s+([\d\.]+)\s+', content)
                    if match:
                        ip_map[name.lower()] = match.group(1)
        except Exception as e:
            print(f"Error reading CCD directory: {e}")

    return ip_map

def sync_existing_clients_meta():
    """Синхронизирует метаданные всех существующих клиентов из index.txt:
       - Устанавливает dev-node (VPN-ADAPTER) для всех Windows клиентов, у кого оно не задано
       - Автоматически регистрирует всех существующих клиентов Easy-RSA в базе vpn_clients_meta
    """
    try:
        if not os.path.exists(INDEX_FILE):
            return
        dev_node_default = config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER"))
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Обновляем dev_node для записей, где оно пусто
        cursor.execute("""
            UPDATE vpn_clients_meta 
            SET dev_node = ?
            WHERE (dev_node IS NULL OR dev_node = '' OR dev_node = '—')
              AND (platform IS NULL OR platform = 'windows')
        """, (dev_node_default,))
        
        # Считываем всех клиентов из index.txt
        with open(INDEX_FILE, "r") as f:
            lines = f.readlines()
        ip_map = get_static_ip_map()
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            status = parts[0]
            cn_part = None
            for p in parts:
                if "CN=" in p:
                    cn_part = p
                    break
            if not cn_part:
                continue
            name = cn_part.split("CN=")[-1].strip()
            if name == "server":
                continue
            
            client_status = "Active" if status == "V" else "Revoked"
            client_ip = ip_map.get(name.lower()) or "Dynamic"
            created_at = parts[1] if len(parts) > 1 else ""
            
            cursor.execute("""
                INSERT OR IGNORE INTO vpn_clients_meta (client_name, ip_address, created_by, created_at, platform, dev_node, status)
                VALUES (?, ?, 'system', ?, 'windows', ?, ?)
            """, (name, client_ip, created_at, dev_node_default, client_status))
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error syncing existing clients meta: {e}")

try:
    sync_existing_clients_meta()
except Exception as e:
    print(f"Sync meta warning: {e}")

def find_free_ip():
    assigned_ips = set(get_static_ip_map().values())
    for ip in get_subnet_hosts():
        if ip not in assigned_ips:
            return ip
    return None

def validate_client_ip(ip_str, exclude_client_name=None):
    """
    Валидация статического IP-адреса:
    - Корректный синтаксис IPv4
    - Вхождение в диапазон подсети VPN
    - Исключение адреса сети, широковещательного (broadcast) и шлюза сервера
    - Проверка на отсутствие конфликта с уже выданными активными IP (CCD и БД)
    """
    if not ip_str:
        return True, None
        
    ip_str = ip_str.strip()
    if ip_str.lower() in ['dynamic', '—', '-', '']:
        return True, None

    try:
        ip_obj = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return False, f"«{ip_str}» не является корректным IPv4-адресом"

    subnet_ip, netmask = parse_openvpn_server_subnet()
    try:
        network = ipaddress.IPv4Network(f"{subnet_ip}/{netmask}", strict=False)
    except Exception as e:
        return False, f"Ошибка определения подсети OpenVPN: {e}"

    if ip_obj not in network:
        return False, f"IP-адрес {ip_str} выходит за пределы подсети VPN ({subnet_ip}/{netmask})"

    if ip_obj == network.network_address:
        return False, f"IP-адрес {ip_str} является адресом сети (network address)"

    if ip_obj == network.broadcast_address:
        return False, f"IP-адрес {ip_str} является широковещательным адресом сети (broadcast)"

    # Адрес сервера OpenVPN (шлюз)
    server_ip = network.network_address + 1
    if ip_obj == server_ip:
        return False, f"IP-адрес {ip_str} зарезервирован за сервером (шлюзом OpenVPN)"

    # Проверка конфликта со статическими адресами в CCD
    static_map = get_static_ip_map()
    for cn, assigned_ip in static_map.items():
        if exclude_client_name and cn.lower() == exclude_client_name.lower():
            continue
        if assigned_ip == str(ip_obj):
            return False, f"IP-адрес {ip_str} уже закреплен в CCD за клиентом «{cn}»"

    # Проверка конфликта в базе метаданных клиентов
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT client_name, ip_address FROM vpn_clients_meta WHERE status = 'Active'")
        for row in cursor.fetchall():
            c_name = row['client_name']
            if exclude_client_name and c_name.lower() == exclude_client_name.lower():
                continue
            if row['ip_address'] == str(ip_obj):
                conn.close()
                return False, f"IP-адрес {ip_str} уже выдан активному клиенту «{c_name}»"
        conn.close()
    except Exception as e:
        print(f"Error checking ip conflict in db: {e}")

    return True, None

def get_online_clients():
    # 1. Сначала пробуем получить живой статус напрямую из сокета управления OpenVPN
    try:
        mgmt_clients = vpn_manager.get_online_clients_from_mgmt()
        if mgmt_clients is not None:
            return mgmt_clients
    except Exception as e:
        print(f"Management socket query error: {e}")

    # 2. Фолбек на парсинг лог-файла статуса
    online_users = set()
    paths = [
        STATUS_LOG,
        "/app/data/openvpn-status.log",
        "/run/openvpn-server/status-server.log",
        "/var/log/openvpn/openvpn-status.log"
    ]
    status_log_path = None
    
    for p in paths:
        if p and os.path.exists(p):
            status_log_path = p
            break
            
    if not status_log_path:
        return online_users

    try:
        with open(status_log_path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CLIENT_LIST,"):
                    parts = line.split(",")
                    if len(parts) > 2:
                        cn = parts[1].strip()
                        if cn and cn != "Common Name" and cn != "UNDEF":
                            online_users.add(cn.lower())
    except Exception as e:
        print(f"Error parsing status log: {e}")
    return online_users

def sanitize_name(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)

# Динамическая сборка клиентского .ovpn на лету (On-The-Fly)
def build_client_ovpn_content(client_name):
    """
    Динамически собирает полный .ovpn файл конфигурации клиента на лету
    с актуальными настройками сервера, ключами и директивами адаптера.
    """
    client_name = sanitize_name(client_name)
    if not client_name:
        return None

    # 1. Шаблон клиента
    common = ""
    if os.path.exists(CLIENT_COMMON):
        try:
            with open(CLIENT_COMMON, "r", encoding="utf-8") as f:
                common = f.read().strip()
        except Exception as e:
            print(f"Error reading CLIENT_COMMON: {e}")

    if not common:
        cfg = get_server_config()
        common = (
            "client\n"
            "dev tun\n"
            f"proto {cfg.get('proto', 'udp')}\n"
            f"remote {cfg.get('server_host', '127.0.0.1')} {cfg.get('port', 1194)}\n"
            "resolv-retry infinite\n"
            "nobind\n"
            "persist-key\n"
            "persist-tun\n"
            "remote-cert-tls server\n"
            "auth SHA512\n"
            "verb 3"
        )

    # 2. Сертификат CA
    ca = ""
    ca_candidates = [
        f"{EASY_RSA_DIR}/pki/ca.crt",
        os.path.join(os.path.dirname(SERVER_CONF), "ca.crt"),
        "/app/data/server/ca.crt",
        "/etc/openvpn/server/ca.crt"
    ]
    for c in ca_candidates:
        if os.path.exists(c):
            with open(c, "r", encoding="utf-8") as f:
                ca = f.read().strip()
            break

    # 3. Сертификат клиента
    cert = ""
    cert_path = f"{EASY_RSA_DIR}/pki/issued/{client_name}.crt"
    if os.path.exists(cert_path):
        with open(cert_path, "r", encoding="utf-8") as f:
            cert_content = f.read()
            idx = cert_content.find("-----BEGIN CERTIFICATE-----")
            if idx != -1:
                cert = cert_content[idx:].strip()

    # 4. Приватный ключ клиента
    key = ""
    key_path = f"{EASY_RSA_DIR}/pki/private/{client_name}.key"
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            key = f.read().strip()

    # 5. Ключ tls-crypt / ta.key
    tc = ""
    tc_candidates = [
        os.path.join(os.path.dirname(SERVER_CONF), "tc.key"),
        os.path.join(os.path.dirname(SERVER_CONF), "ta.key"),
        f"{EASY_RSA_DIR}/tc.key",
        "/app/data/server/tc.key",
        "/app/data/tc.key",
        "/etc/openvpn/server/tc.key"
    ]
    for tc_cand in tc_candidates:
        if os.path.exists(tc_cand):
            with open(tc_cand, "r", encoding="utf-8") as f:
                tc_content = f.read()
                idx = tc_content.find("-----BEGIN OpenVPN Static key")
                if idx != -1:
                    tc = tc_content[idx:].strip()
                else:
                    tc = tc_content.strip()
            break

    if not ca or not cert or not key:
        return None

    # 6. Читаем метаданные клиента (платформа и dev-node)
    platform = "windows"
    dev_node = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT platform, dev_node FROM vpn_clients_meta WHERE client_name = ?", (client_name,))
        row = cursor.fetchone()
        if row:
            platform = row['platform'] or "windows"
            dev_node = row['dev_node']
        conn.close()
    except Exception as e:
        print(f"Error fetching client meta: {e}")

    # 7. Директивы платформы (Windows Wintun и dev-node)
    platform_directives = []
    dev_node_enabled = config.get("dev_node_enabled", True)
    default_dev_node = config.get("dev_node_name", "VPN-ADAPTER")

    if platform == "windows":
        platform_directives.append("# Windows Wintun driver and tunnel interface isolation")
        platform_directives.append("windows-driver wintun")
        if dev_node_enabled:
            target_dev = dev_node if dev_node and dev_node != "—" else default_dev_node
            if target_dev and target_dev != "none":
                platform_directives.append(f'dev-node "{target_dev}"')
    elif platform == "linux":
        platform_directives.append("# Linux / macOS native client configuration")

    directives_str = "\n".join(platform_directives)
    if directives_str:
        directives_str += "\n"

    ovpn_content = (
        f"{common}\n"
        f"{directives_str}"
        f"<ca>\n{ca}\n</ca>\n"
        f"<cert>\n{cert}\n</cert>\n"
        f"<key>\n{key}\n</key>\n"
    )
    if tc:
        ovpn_content += f"<tls-crypt>\n{tc}\n</tls-crypt>\n"

    return ovpn_content

def generate_ovpn(client_name, platform='windows', dev_node=None):
    """Совместимость: возвращает True если конфиг и ключи клиента успешно сформированы."""
    return bool(build_client_ovpn_content(client_name))

# ----------------- Управление конфигурацией OpenVPN сервера -----------------

def get_server_config():
    """
    Парсит server.conf и client-common.txt, возвращая структурированный словарь параметров.
    """
    subnet_def, mask_def = parse_openvpn_server_subnet()
    cfg = {
        "server_host": "",
        "port": 1194,
        "proto": "udp",
        "subnet": subnet_def,
        "netmask": mask_def,
        "push_routes": [],
        "push_dns": [],
        "redirect_gateway": False,
        "client_to_client": False,
        "route_nopull": False,
        "dev_node_enabled": config.get("dev_node_enabled", True),
        "dev_node_name": config.get("dev_node_name", "VPN-ADAPTER")
    }

    # 1. Из client-common.txt считываем remote (host и port)
    if os.path.exists(CLIENT_COMMON):
        try:
            with open(CLIENT_COMMON, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("remote ") and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) >= 2:
                            cfg["server_host"] = parts[1]
                        if len(parts) >= 3 and parts[2].isdigit():
                            cfg["port"] = int(parts[2])
                    elif line.startswith("proto ") and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) >= 2:
                            cfg["proto"] = parts[1].lower()
        except Exception as e:
            print(f"Error reading client-common.txt: {e}")

    # Fallback server_host если пустой
    if not cfg["server_host"]:
        cfg["server_host"] = os.getenv("PUBLIC_IP") or socket.gethostname()

    # 2. Из server.conf считываем параметры OpenVPN
    if os.path.exists(SERVER_CONF):
        try:
            with open(SERVER_CONF, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith(";"):
                        continue
                    if line.startswith("port ") and line.split()[1].isdigit():
                        cfg["port"] = int(line.split()[1])
                    elif line.startswith("proto "):
                        cfg["proto"] = line.split()[1].lower()
                    elif line.startswith("server "):
                        parts = line.split()
                        if len(parts) >= 3:
                            cfg["subnet"] = parts[1]
                            cfg["netmask"] = parts[2]
                    elif "push" in line:
                        if "route-nopull" in line:
                            cfg["route_nopull"] = True
                        elif "route " in line:
                            m = re.search(r'route\s+([\d\.]+)\s+([\d\.]+)', line)
                            if m:
                                net = m.group(1)
                                mask = m.group(2)
                                try:
                                    cidr = ipaddress.IPv4Network(f"{net}/{mask}", strict=False)
                                    cfg["push_routes"].append(str(cidr))
                                except Exception:
                                    cfg["push_routes"].append(f"{net}/{mask}")
                        elif "dhcp-option DNS" in line:
                            m = re.search(r'dhcp-option\s+DNS\s+([\d\.]+)', line)
                            if m:
                                cfg["push_dns"].append(m.group(1))
                        elif "redirect-gateway" in line:
                            cfg["redirect_gateway"] = True
                    elif line == "client-to-client":
                        cfg["client_to_client"] = True
        except Exception as e:
            print(f"Error reading server.conf: {e}")

    return cfg

def save_server_config(new_cfg):
    """
    Валидирует, сохраняет server.conf и client-common.txt,
    обновляет dev-node в config.json и перезапускает службу OpenVPN.
    """
    host = str(new_cfg.get("server_host", "")).strip()
    if not host:
        return False, "Адрес сервера (IP или домен) не может быть пустым"

    try:
        port = int(new_cfg.get("port", 1194))
    except (ValueError, TypeError):
        return False, "Порт должен быть числом"
    if port < 1 or port > 65535:
        return False, "Порт должен быть в диапазоне 1-65535"

    proto = str(new_cfg.get("proto", "udp")).lower().strip()
    if proto not in ["udp", "tcp"]:
        return False, "Протокол должен быть UDP или TCP"

    subnet = str(new_cfg.get("subnet", "10.8.0.0")).strip()
    netmask = str(new_cfg.get("netmask", "255.255.255.0")).strip()
    try:
        ipaddress.IPv4Network(f"{subnet}/{netmask}", strict=False)
    except Exception as e:
        return False, f"Некорректная подсеть или маска сети: {e}"

    # Валидация push-маршрутов
    valid_routes = []
    raw_routes = new_cfg.get("push_routes", [])
    if isinstance(raw_routes, str):
        raw_routes = [r.strip() for r in raw_routes.split(",") if r.strip()]

    for r in raw_routes:
        r = str(r).strip()
        if not r:
            continue
        try:
            if "/" in r:
                net_obj = ipaddress.IPv4Network(r, strict=False)
                valid_routes.append(f"{net_obj.network_address} {net_obj.netmask}")
            else:
                parts = r.split()
                if len(parts) == 2:
                    ipaddress.IPv4Network(f"{parts[0]}/{parts[1]}", strict=False)
                    valid_routes.append(f"{parts[0]} {parts[1]}")
                else:
                    return False, f"Недопустимый формат маршрута: «{r}». Используйте CIDR (например: 192.168.1.0/24)"
        except Exception:
            return False, f"Недопустимый маршрут сети: «{r}». Используйте формат 192.168.1.0/24"

    # Валидация DNS
    valid_dns = []
    raw_dns = new_cfg.get("push_dns", [])
    if isinstance(raw_dns, str):
        raw_dns = [d.strip() for d in raw_dns.split(",") if d.strip()]

    for d in raw_dns:
        d = str(d).strip()
        if not d:
            continue
        try:
            ipaddress.IPv4Address(d)
            valid_dns.append(d)
        except Exception:
            return False, f"Недопустимый IP-адрес DNS: «{d}»"

    redirect_gw = bool(new_cfg.get("redirect_gateway", False))
    c2c = bool(new_cfg.get("client_to_client", False))
    route_nopull = bool(new_cfg.get("route_nopull", False))
    dev_node_en = bool(new_cfg.get("dev_node_enabled", True))
    dev_node_val = str(new_cfg.get("dev_node_name", "VPN-ADAPTER")).strip() or "VPN-ADAPTER"

    # 1. Резервная копия server.conf
    server_dir = os.path.dirname(SERVER_CONF)
    if server_dir:
        os.makedirs(server_dir, exist_ok=True)
    if os.path.exists(SERVER_CONF):
        try:
            shutil.copy(SERVER_CONF, f"{SERVER_CONF}.bak")
        except Exception as e:
            print(f"Warning: could not backup server.conf: {e}")

    # 2. Формируем server.conf
    server_lines = [
        f"port {port}",
        f"proto {proto}",
        "dev tun",
        "ca ca.crt",
        "cert server.crt",
        "key server.key",
        "dh dh.pem",
        "auth SHA512",
        "tls-crypt tc.key",
        "topology subnet",
        f"server {subnet} {netmask}",
        "client-config-dir ccd",
        "ifconfig-pool-persist ipp.txt",
        "keepalive 10 120",
        "persist-key",
        "persist-tun",
        "verb 3",
        "crl-verify crl.pem",
        "status /app/data/openvpn-status.log 5",
        "status-version 2",
        "management /run/openvpn.sock unix"
    ]
    if proto == "udp":
        server_lines.append("explicit-exit-notify")

    if c2c:
        server_lines.append("client-to-client")

    if route_nopull:
        server_lines.append('push "route-nopull"')

    if redirect_gw:
        server_lines.append('push "redirect-gateway def1"')

    for r in valid_routes:
        server_lines.append(f'push "route {r}"')

    for d in valid_dns:
        server_lines.append(f'push "dhcp-option DNS {d}"')

    with open(SERVER_CONF, "w", encoding="utf-8") as f:
        f.write("\n".join(server_lines) + "\n")

    # 3. Формируем client-common.txt
    client_lines = [
        "client",
        "dev tun",
        f"proto {proto}",
        f"remote {host} {port}",
        "resolv-retry infinite",
        "nobind",
        "persist-key",
        "persist-tun",
        "remote-cert-tls server",
        "auth SHA512",
        "verb 3"
    ]
    with open(CLIENT_COMMON, "w", encoding="utf-8") as f:
        f.write("\n".join(client_lines) + "\n")

    # 4. Обновляем config.json
    config["dev_node_enabled"] = dev_node_en
    config["dev_node_name"] = dev_node_val
    config["dev_node_prefix"] = dev_node_val
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config.json: {e}")

    # 5. Перезапуск OpenVPN службы
    vpn_manager.restart_service()

    return True, None

def get_clients():
    clients = []
    if not os.path.exists(INDEX_FILE):
        return clients
    
    online_set = get_online_clients()

    # Обновляем активность и читаем историю
    last_seen_map = {}
    meta_map = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if online_set:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for cn in online_set:
                cursor.execute("""
                    INSERT INTO client_activity (common_name, last_seen)
                    VALUES (?, ?)
                    ON CONFLICT(common_name) DO UPDATE SET last_seen=excluded.last_seen
                """, (cn, now_str))
            conn.commit()
            
        cursor.execute("SELECT common_name, last_seen FROM client_activity")
        for row in cursor.fetchall():
            last_seen_map[row['common_name'].lower()] = row['last_seen']

        cursor.execute("SELECT client_name, ip_address, created_by, created_at, platform, dev_node, status FROM vpn_clients_meta")
        for row in cursor.fetchall():
            meta_map[row['client_name'].lower()] = dict(row)
        conn.close()
    except Exception as e:
        print(f"Error updating client activity: {e}")
            
    ip_map = get_static_ip_map()

    with open(INDEX_FILE, "r") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        parts = line.split("\t")
        if len(parts) < 3:
            continue
            
        status = parts[0]
        cn_part = None
        for part in parts:
            if "CN=" in part:
                cn_part = part
                break
                
        if not cn_part:
            continue
            
        name = cn_part.split("CN=")[-1].strip()
        
        if name != "server":
            cn_lower = name.lower()
            is_online = cn_lower in online_set if status == "V" else False
            
            if is_online:
                last_seen_val = "online"
            else:
                last_seen_val = last_seen_map.get(cn_lower, None)
                
            ip_address = ip_map.get(cn_lower)
            meta = meta_map.get(cn_lower, {})
            client_platform = meta.get("platform") or "windows"
            dev_node_val = meta.get("dev_node")
            if not dev_node_val or dev_node_val == "—":
                if client_platform == "windows":
                    dev_node_val = config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER"))
                else:
                    dev_node_val = "—"

            clients.append({
                "name": name,
                "status": "Active" if status == "V" else "Revoked",
                "online": is_online,
                "last_seen": last_seen_val,
                "ip": ip_address or meta.get("ip_address") or "Dynamic",
                "created_by": meta.get("created_by") or "system",
                "created_at": meta.get("created_at") or (parts[1] if len(parts) > 1 else "—"),
                "platform": client_platform,
                "dev_node": dev_node_val
            })
    return clients

# Главный маршрут
@app.route('/')
@login_required
def index():
    # Авто-синхронизация пользователя активной сессии
    if session.get("username"):
        try:
            eff_role = sync_node_user(
                session.get("username"),
                session.get("role", "operator"),
                session.get("display_name") or session.get("username"),
                session.get("auth_mode", "central")
            )
            session["role"] = eff_role
        except Exception as e:
            print(f"Error syncing session on index: {e}")

    subnet_ip, netmask = parse_openvpn_server_subnet()
    dev_node_name = config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER"))
    return render_template(
        'index.html', 
        node_name=NODE_NAME,
        auth_mode=config.get("auth_mode", "central"),
        subnet=f"{subnet_ip}/{netmask}",
        dev_node_prefix=dev_node_name,
        dev_node_name=dev_node_name,
        dev_node_enabled=config.get("dev_node_enabled", True),
        user_role=session.get("role", "operator"),
        current_username=session.get("username", "")
    )

# Вход в систему
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({"error": "Заполните все поля"}), 400

        if login_rate_limited(request.remote_addr):
            return jsonify({"error": "Слишком много неудачных попыток входа. Попробуйте через 5 минут."}), 429
            
        auth_mode = config.get("auth_mode", "central")

        # 1. Автономный режим (standalone)
        if auth_mode == "standalone":
            admin_user = config.get("standalone_admin_user", "admin")
            admin_pass = config.get("standalone_admin_password", "admin123")
            if username == admin_user and password == admin_pass:
                effective_role = sync_node_user(username, "admin", "Локальный Администратор", "standalone")
                session["logged_in"] = True
                session["username"] = username
                session["role"] = effective_role
                session["display_name"] = "Локальный Администратор"
                session["auth_mode"] = "standalone"
                log_action(username, "LOGIN", f"Успешный автономный вход ({effective_role})")
                return jsonify({"success": True})
            else:
                register_failed_login(request.remote_addr)
                log_action(username, "LOGIN_FAILED", "Неверный логин или пароль (Standalone)")
                return jsonify({"error": "Неверный логин или пароль"}), 401

        # 2. Централизованный режим (centralized-auth)
        central_url = f"{config['central_auth_url'].rstrip('/')}/api/auth/verify"
        try:
            resp = requests.post(
                central_url,
                json={"username": username, "password": password},
                headers={"X-Node-Token": config["node_api_token"]},
                timeout=5,
                verify=config.get("verify_ssl", True)
            )
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("success"):
                    c_role = res_data.get("role", "operator")
                    disp_name = res_data.get("display_name") or res_data.get("full_name") or username
                    effective_role = sync_node_user(res_data.get("username", username), c_role, disp_name, "central")
                    session["logged_in"] = True
                    session["username"] = res_data.get("username", username)
                    session["role"] = effective_role
                    session["display_name"] = disp_name
                    session["auth_mode"] = "central"
                    session["last_verified"] = time.time()
                    log_action(username, "LOGIN", f"Успешный вход через Central Auth ({effective_role})")
                    return jsonify({"success": True})
            
            error_msg = "Неверный логин или пароль"
            if resp.status_code != 200:
                try:
                    error_msg = resp.json().get("error", error_msg)
                except Exception:
                    pass
            register_failed_login(request.remote_addr)
            log_action(username or "unknown", "LOGIN_FAILED", f"Неудачная попытка входа: {error_msg}")
            return jsonify({"error": error_msg}), resp.status_code or 401
        except requests.exceptions.RequestException as e:
            print(f"Central auth connection error: {e}")
            return jsonify({"error": "Центральный сервер авторизации недоступен"}), 503
            
    return render_template('login.html', node_name=NODE_NAME, auth_mode=config.get("auth_mode", "central"))

# Выход из системы
@app.route('/logout', methods=['POST'])
def logout():
    username = session.get("username", "unknown")
    log_action(username, "LOGOUT", "Выход из системы")
    session.pop("logged_in", None)
    session.pop("username", None)
    session.pop("role", None)
    return jsonify({"success": True})

# API VPN Ноды
@app.route('/api/clients', methods=['GET'])
@login_required
def api_get_clients():
    return jsonify(get_clients())

@app.route('/api/clients', methods=['POST'])
@login_required
def api_create_client():
    data = request.json or {}
    raw_name = data.get('name', '') or ''
    raw_name = raw_name.strip()
    platform = data.get('platform', 'windows').lower().strip()
    if platform not in ['windows', 'linux']:
        platform = 'windows'
        
    custom_dev_node = data.get('dev_node', '').strip()
    custom_ip = data.get('ip', '').strip()

    if not raw_name:
        return jsonify({"error": "Имя не может быть пустым"}), 400
        
    client_name = sanitize_name(raw_name)
    existing_clients = [c['name'] for c in get_clients() if c['status'] == 'Active']
    if client_name in existing_clients:
        log_action(session.get("username"), "CREATE_CLIENT_FAILED", f"Клиент {client_name} уже существует")
        return jsonify({"error": f"Активный клиент {client_name} уже существует"}), 400

    # Определение dev-node для Windows (общий вариант как константа / переменная)
    assigned_dev_node = None
    dev_node_mode = data.get('dev_node_mode', 'common')
    if platform == 'windows':
        if dev_node_mode == 'none':
            assigned_dev_node = None
        elif dev_node_mode == 'custom' and custom_dev_node:
            assigned_dev_node = sanitize_name(custom_dev_node)
        elif dev_node_mode == 'individual':
            prefix = config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER"))
            assigned_dev_node = f"{prefix}_{client_name}"
        else: # common (общий вариант по умолчанию)
            assigned_dev_node = config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER"))

    # Определение статического IP для CCD
    subnet_ip, netmask = parse_openvpn_server_subnet()
    ip_to_assign = None
    if custom_ip:
        is_valid, err_msg = validate_client_ip(custom_ip)
        if not is_valid:
            log_action(session.get("username"), "CREATE_CLIENT_FAILED", f"Ошибка IP: {err_msg}")
            return jsonify({"error": err_msg}), 400
        ip_to_assign = custom_ip
    else:
        ip_to_assign = find_free_ip()

    try:
        subprocess.run(
            ["./easyrsa", "--batch", "--days=3650", "build-client-full", client_name, "nopass"],
            cwd=EASY_RSA_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Создание файла CCD со статическим IP
        if CCD_DIR and ip_to_assign:
            try:
                os.makedirs(CCD_DIR, exist_ok=True)
                ccd_file = os.path.join(CCD_DIR, client_name)
                with open(ccd_file, "w", encoding="utf-8") as f:
                    f.write(f"ifconfig-push {ip_to_assign} {netmask}\n")
            except Exception as e:
                print(f"CCD ERROR: Failed to write config for {client_name}: {e}")

        # Сборка .ovpn конфигурации
        if generate_ovpn(client_name, platform=platform, dev_node=assigned_dev_node):
            # Сохранение метаданных в базу данных
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO vpn_clients_meta 
                    (client_name, ip_address, created_by, created_at, platform, dev_node, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'Active')
                """, (
                    client_name, 
                    ip_to_assign or "Dynamic", 
                    session.get("username", "admin"), 
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    platform,
                    assigned_dev_node or "—"
                ))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"DB Error saving client meta: {e}")

            log_action(session.get("username"), "CREATE_CLIENT", f"Выпущен клиент: {client_name} ({platform}, IP: {ip_to_assign or 'Dynamic'})")
            return jsonify({
                "success": True, 
                "client": client_name, 
                "ip": ip_to_assign,
                "platform": platform,
                "dev_node": assigned_dev_node
            })
        else:
            log_action(session.get("username"), "CREATE_CLIENT_FAILED", f"Ошибка сборки .ovpn для {client_name}")
            return jsonify({"error": "Ошибка при сборке .ovpn файла"}), 500
    except subprocess.CalledProcessError:
        log_action(session.get("username"), "CREATE_CLIENT_FAILED", f"Ошибка Easy-RSA для {client_name}")
        return jsonify({"error": "Ошибка выполнения Easy-RSA"}), 500

@app.route('/api/clients/update', methods=['POST'])
@app.route('/api/clients/rebuild', methods=['POST'])
@login_required
def api_rebuild_client():
    data = request.json or {}
    client_name = sanitize_name(data.get('name', '').strip())
    if not client_name:
        return jsonify({"error": "Имя клиента не указано"}), 400
        
    dev_node_default = config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER"))
    
    # Считываем сохраненную платформу и dev-node из метаданных
    platform = "windows"
    dev_node = dev_node_default
    current_ip = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT platform, dev_node, ip_address FROM vpn_clients_meta WHERE client_name = ?", (client_name,))
        row = cursor.fetchone()
        if row:
            platform = row['platform'] or "windows"
            dev_node = row['dev_node'] if row['dev_node'] and row['dev_node'] != "—" else dev_node_default
            current_ip = row['ip_address']
        conn.close()
    except Exception as e:
        print(f"Error fetching client meta for rebuild: {e}")

    # Если переданы параметры для изменения
    if 'platform' in data:
        platform = data.get('platform', 'windows').lower().strip()
        if platform not in ['windows', 'linux']:
            platform = 'windows'

    if 'dev_node' in data:
        req_dev_node = data.get('dev_node', '').strip()
        if req_dev_node in ['none', 'откл', 'disable']:
            dev_node = None
        elif req_dev_node:
            dev_node = sanitize_name(req_dev_node)
        else:
            dev_node = dev_node_default if platform == 'windows' else None

    if 'ip' in data:
        new_ip = data.get('ip', '').strip()
        if new_ip and new_ip.lower() != 'dynamic':
            is_valid, err_msg = validate_client_ip(new_ip, exclude_client_name=client_name)
            if not is_valid:
                log_action(session.get("username"), "REBUILD_CLIENT_FAILED", f"Ошибка IP для {client_name}: {err_msg}")
                return jsonify({"error": err_msg}), 400
            current_ip = new_ip
            if CCD_DIR:
                try:
                    os.makedirs(CCD_DIR, exist_ok=True)
                    ccd_file = os.path.join(CCD_DIR, client_name)
                    subnet_ip, netmask = parse_openvpn_server_subnet()
                    with open(ccd_file, "w") as f:
                        f.write(f"ifconfig-push {current_ip} {netmask}\n")
                except Exception as e:
                    print(f"CCD save error: {e}")
        elif new_ip.lower() == 'dynamic' or new_ip == '':
            current_ip = "Dynamic"
            if CCD_DIR:
                ccd_file = os.path.join(CCD_DIR, client_name)
                if os.path.exists(ccd_file):
                    try:
                        os.remove(ccd_file)
                    except Exception as e:
                        print(f"CCD delete error: {e}")

    # Обновляем метаданные в БД
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vpn_clients_meta (client_name, ip_address, platform, dev_node, status)
            VALUES (?, ?, ?, ?, 'Active')
            ON CONFLICT(client_name) DO UPDATE SET 
                ip_address = coalesce(?, ip_address),
                platform = excluded.platform,
                dev_node = excluded.dev_node
        """, (client_name, current_ip, platform, dev_node or "—", current_ip))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB update error: {e}")

    log_action(session.get("username"), "UPDATE_CLIENT", f"Обновлены параметры клиента {client_name} (Wintun: {dev_node or 'none'}, IP: {current_ip or 'Dynamic'})")
    return jsonify({
        "success": True,
        "client": client_name,
        "platform": platform,
        "dev_node": dev_node or "—",
        "ip": current_ip or "Dynamic"
    })

@app.route('/api/clients/revoke', methods=['POST'])
@login_required
def api_revoke_client():
    data = request.get_json(silent=True) or request.json or {}
    client_name = sanitize_name(data.get('name', '').strip())
    
    if not client_name:
        return jsonify({"error": "Имя клиента не указано"}), 400

    try:
        subprocess.run(["./easyrsa", "--batch", "revoke", client_name], cwd=EASY_RSA_DIR, check=True)
        subprocess.run(["./easyrsa", "--batch", "--days=3650", "gen-crl"], cwd=EASY_RSA_DIR, check=True)
        
        crl_target = os.path.join(os.path.dirname(SERVER_CONF), "crl.pem")
        if os.path.exists(f"{EASY_RSA_DIR}/pki/crl.pem"):
            shutil.copy(f"{EASY_RSA_DIR}/pki/crl.pem", crl_target)
            try:
                shutil.chown(crl_target, user="nobody", group="nogroup")
            except Exception:
                pass
        
        # Перечитываем CRL на лету без разрыва других туннелей
        vpn_manager.reload_crl()
        # Мгновенно выбиваем активную сессию отозванного клиента
        vpn_manager.kill_client(client_name)

        for folder, ext in [("reqs", "req"), ("private", "key")]:
            file_path = f"{EASY_RSA_DIR}/pki/{folder}/{client_name}.{ext}"
            if os.path.exists(file_path):
                os.remove(file_path)
                
        if CCD_DIR:
            ccd_file = os.path.join(CCD_DIR, client_name)
            if os.path.exists(ccd_file):
                try:
                    os.remove(ccd_file)
                except Exception as e:
                    print(f"CCD ERROR: Failed to delete config for revoked client {client_name}: {e}")
                    
        # Обновляем статус в БД метаданных
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE vpn_clients_meta SET status = 'Revoked' WHERE client_name = ?", (client_name,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Error updating revoked status: {e}")
                
        log_action(session.get("username"), "REVOKE_CLIENT", f"Отозван клиент: {client_name}")
        return jsonify({"success": True})
    except subprocess.CalledProcessError as e:
        print(f"Subprocess error during revoke: {e}")
        log_action(session.get("username"), "REVOKE_CLIENT_FAILED", f"Ошибка отзыва клиента: {client_name}")
        return jsonify({"error": "Не удалось отозвать сертификат"}), 500

@app.route('/api/clients/download/<string:client_name>', methods=['GET'])
@login_required
def download_config(client_name):
    client_name = sanitize_name(client_name)
    ovpn_content = build_client_ovpn_content(client_name)
    if not ovpn_content:
        abort(404)

    log_action(session.get("username"), "DOWNLOAD_CONFIG", f"Скачан динамический .ovpn клиента: {client_name}")
    from io import BytesIO
    bio = BytesIO(ovpn_content.encode('utf-8'))
    return send_file(bio, as_attachment=True, download_name=f"{client_name}.ovpn", mimetype="application/x-openvpn-profile")

# ----------------- API Управления конфигурацией сервера (Admin only) -----------------

@app.route('/api/server/config', methods=['GET'])
@admin_required
def api_get_server_config():
    try:
        cfg = get_server_config()
        online_clients = list(get_online_clients())
        cfg["online_count"] = len(online_clients)
        return jsonify(cfg)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/server/config', methods=['POST'])
@admin_required
def api_save_server_config():
    data = request.json or {}
    ok, err = save_server_config(data)
    if not ok:
        return jsonify({"error": err or "Ошибка сохранения настроек"}), 400

    log_action(session.get("username"), "UPDATE_SERVER_CONFIG", "Обновлена конфигурация сервера OpenVPN")
    return jsonify({"success": True, "message": "Конфигурация сервера успешно сохранена и OpenVPN перезапущен"})

# ----------------- API Управления пользователями ноды (Admin only) -----------------

@app.route('/api/users', methods=['GET'])
@admin_required
def api_get_users():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role, display_name, auth_source, created_at, last_login FROM users ORDER BY id ASC")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(users)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/update-role/<int:user_id>', methods=['POST'])
@admin_required
def api_update_user_role(user_id):
    data = request.json or {}
    new_role = data.get('role', '').strip()
    if new_role not in ['admin', 'operator']:
        return jsonify({"error": "Неверная роль (доступны: admin, operator)"}), 400

    current_user = session.get("username", "")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return jsonify({"error": "Пользователь не найден"}), 404
        if user['username'].lower() == current_user.lower():
            conn.close()
            return jsonify({"error": "Вы не можете изменить роль самому себе"}), 400

        cursor.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
        conn.close()
        log_action(current_user, "UPDATE_USER_ROLE", f"Изменена роль для {user['username']}: {new_role}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/service/status', methods=['GET'])
@login_required
def service_status():
    is_active = vpn_manager.is_active()
    return jsonify({"status": "active" if is_active else "failed"})

@app.route('/api/service/restart', methods=['POST'])
@admin_required
def service_restart():
    try:
        success = vpn_manager.restart_service()
        if success:
            log_action(session.get("username"), "RESTART_SERVICE", "Перезапуск службы OpenVPN (Supervisor)")
            return jsonify({"success": True})
        else:
            log_action(session.get("username"), "RESTART_SERVICE_FAILED", "Ошибка перезапуска службы OpenVPN")
            return jsonify({"error": "Не удалось перезапустить службу"}), 500
    except Exception as e:
        log_action(session.get("username"), "RESTART_SERVICE_FAILED", f"Ошибка: {e}")
        return jsonify({"error": "Не удалось перезапустить службу"}), 500

# Эндпоинт получения настроек ноды для UI
@app.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    subnet_ip, netmask = parse_openvpn_server_subnet()
    free_ip = find_free_ip()
    return jsonify({
        "node_name": NODE_NAME,
        "auth_mode": config.get("auth_mode", "central"),
        "subnet": f"{subnet_ip}/{netmask}",
        "next_free_ip": free_ip,
        "dev_node_enabled": config.get("dev_node_enabled", True),
        "dev_node_name": config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER")),
        "dev_node_prefix": config.get("dev_node_name", config.get("dev_node_prefix", "VPN-ADAPTER")),
        "dev_node_mode": config.get("dev_node_mode", "common")
    })

# Эндпоинт получения логов аудита
@app.route('/api/audit', methods=['GET'])
@login_required
def api_get_audit():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, username, action, details FROM audit_logs ORDER BY id DESC LIMIT 50")
        logs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    try:
        from waitress import serve
        print(f"[WSGI] Starting production multi-threaded Waitress server on {config['bind_host']}:{config['bind_port']}...")
        serve(app, host=config["bind_host"], port=config["bind_port"], threads=8)
    except ImportError:
        app.run(host=config["bind_host"], port=config["bind_port"], debug=False)

