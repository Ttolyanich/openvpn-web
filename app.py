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

# Отключение предупреждений об отсутствии SSL-сертификатов
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Загрузка переменных окружения из .env
load_dotenv()

# Загрузка конфигурации
CONFIG_PATH = os.getenv("OPENVPN_WEB_CONFIG", "/opt/openvpn-web/config.json")
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
    "dev_node_name": os.getenv("DEV_NODE_NAME", os.getenv("DEV_NODE_PREFIX", "ITSG-OVPN")),
    "dev_node_prefix": os.getenv("DEV_NODE_NAME", os.getenv("DEV_NODE_PREFIX", "ITSG-OVPN")),
    "dev_node_mode": "common", # "common" (общий по умолчанию), "none", "custom"
    "configs_dir": os.getenv("CONFIGS_DIR", "./data/configs")
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
if os.getenv("DEV_NODE_ENABLED"):
    config["dev_node_enabled"] = os.getenv("DEV_NODE_ENABLED").lower() in ["true", "1", "yes"]
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

EASY_RSA_DIR = os.getenv("EASY_RSA_DIR", "/etc/openvpn/server/easy-rsa")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/root/openvpn")
CONFIGS_DIR = config["configs_dir"]
CLIENT_COMMON = os.getenv("CLIENT_COMMON", "/etc/openvpn/server/client-common.txt")
INDEX_FILE = os.getenv("INDEX_FILE", f"{EASY_RSA_DIR}/pki/index.txt")
OPENVPN_SERVICE = os.getenv("OPENVPN_SERVICE", "openvpn-server@server.service")

NODE_DB = os.getenv("DATABASE_PATH", "/opt/openvpn-web/node.db")

CCD_DIR = os.getenv("CCD_DIR", "/etc/openvpn/server/ccd")
SERVER_CONF = os.getenv("SERVER_CONF", "/etc/openvpn/server/server.conf")
IPP_FILE = os.getenv("IPP_FILE", "/etc/openvpn/server/ipp.txt")

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
    conn.commit()
    conn.close()

init_db()

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

# Декоратор авторизации с TTL-кешированием валидности сессии (180 секунд)
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

# Вспомогательный метод для работы с systemctl на хост-системе
def run_systemctl(args, check=False, capture_output=False):
    nsenter_path = None
    for path in ["/usr/bin/nsenter", "/bin/nsenter"]:
        if os.path.exists(path):
            nsenter_path = path
            break

    if nsenter_path:
        cmd = [nsenter_path, "-t", "1", "-m", "-u", "-n", "-i", "systemctl"] + args
        try:
            env = os.environ.copy()
            env["SYSTEMD_IGNORE_CHROOT"] = "1"
            if capture_output:
                res = subprocess.run(cmd, capture_output=True, text=True, env=env, check=check, timeout=5)
            else:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=check, timeout=5)
            if "Permission denied" not in (res.stderr or "") and "Could not open" not in (res.stderr or ""):
                return res
        except Exception:
            pass

    # Фолбек на прямой запуск systemctl
    if capture_output:
        return subprocess.run(["systemctl"] + args, capture_output=True, text=True, check=check)
    else:
        return subprocess.run(["systemctl"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)

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

def find_free_ip():
    assigned_ips = set(get_static_ip_map().values())
    for ip in get_subnet_hosts():
        if ip not in assigned_ips:
            return ip
    return None

def get_online_clients():
    online_users = set()
    paths = ["/run/openvpn-server/status-server.log", "/var/log/openvpn/openvpn-status.log"]
    status_log_path = None
    
    for p in paths:
        if os.path.exists(p):
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

# Генерация клиентского .ovpn с поддержкой Wintun / dev-node и платформ
def generate_ovpn(client_name, platform='windows', dev_node=None):
    try:
        common = ""
        if os.path.exists(CLIENT_COMMON):
            with open(CLIENT_COMMON, "r") as f:
                common = f.read()
                
        ca = ""
        ca_path = f"{EASY_RSA_DIR}/pki/ca.crt"
        if os.path.exists(ca_path):
            with open(ca_path, "r") as f:
                ca = f.read()
                
        cert = ""
        cert_path = f"{EASY_RSA_DIR}/pki/issued/{client_name}.crt"
        if os.path.exists(cert_path):
            with open(cert_path, "r") as f:
                cert_content = f.read()
                cert = cert_content[cert_content.find("-----BEGIN CERTIFICATE-----"):]
                
        key = ""
        key_path = f"{EASY_RSA_DIR}/pki/private/{client_name}.key"
        if os.path.exists(key_path):
            with open(key_path, "r") as f:
                key = f.read()
            
        tc = ""
        for tc_cand in ["/etc/openvpn/server/tc.key", f"{EASY_RSA_DIR}/tc.key"]:
            if os.path.exists(tc_cand):
                with open(tc_cand, "r") as f:
                    tc_content = f.read()
                    idx = tc_content.find("-----BEGIN OpenVPN Static key")
                    if idx != -1:
                        tc = tc_content[idx:]
                    else:
                        tc = tc_content
                break

        # Сборка кастомных директив под платформу
        platform_directives = []
        if platform == 'windows':
            platform_directives.append("# Windows Wintun driver and tunnel interface isolation")
            platform_directives.append("windows-driver wintun")
            target_dev_node = dev_node if dev_node is not None else config.get("dev_node_name", "ITSG-OVPN")
            if target_dev_node and target_dev_node != "none":
                platform_directives.append(f'dev-node "{target_dev_node}"')
        elif platform == 'linux':
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
            f"<tls-crypt>\n{tc}\n</tls-crypt>\n"
        )

        # Сохраняем в каталог CONFIGS_DIR (./data/configs) и OUTPUT_DIR (/root/openvpn)
        os.makedirs(CONFIGS_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        path1 = os.path.join(CONFIGS_DIR, f"{client_name}.ovpn")
        path2 = os.path.join(OUTPUT_DIR, f"{client_name}.ovpn")
        
        with open(path1, "w", encoding="utf-8") as f:
            f.write(ovpn_content)
        try:
            with open(path2, "w", encoding="utf-8") as f:
                f.write(ovpn_content)
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"Error generating config for {client_name}: {e}")
        return False

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

            clients.append({
                "name": name,
                "status": "Active" if status == "V" else "Revoked",
                "online": is_online,
                "last_seen": last_seen_val,
                "ip": ip_address or meta.get("ip_address") or "Dynamic",
                "created_by": meta.get("created_by") or "system",
                "created_at": meta.get("created_at") or parts[1] if len(parts) > 1 else "—",
                "platform": meta.get("platform") or "windows",
                "dev_node": meta.get("dev_node") or "—"
            })
    return clients

# Главный маршрут
@app.route('/')
@login_required
def index():
    subnet_ip, netmask = parse_openvpn_server_subnet()
    dev_node_name = config.get("dev_node_name", config.get("dev_node_prefix", "ITSG-OVPN"))
    return render_template(
        'index.html', 
        node_name=NODE_NAME,
        auth_mode=config.get("auth_mode", "central"),
        subnet=f"{subnet_ip}/{netmask}",
        dev_node_prefix=dev_node_name,
        dev_node_name=dev_node_name
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
                session["logged_in"] = True
                session["username"] = username
                session["role"] = "admin"
                session["auth_mode"] = "standalone"
                log_action(username, "LOGIN", "Успешный автономный вход (Standalone)")
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
                    session["logged_in"] = True
                    session["username"] = res_data.get("username", username)
                    session["role"] = res_data.get("role", "admin")
                    session["auth_mode"] = "central"
                    session["last_verified"] = time.time()
                    log_action(username, "LOGIN", f"Успешный вход через Central Auth ({session['role']})")
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
            prefix = config.get("dev_node_name", config.get("dev_node_prefix", "ITSG-OVPN"))
            assigned_dev_node = f"{prefix}_{client_name}"
        else: # common (общий вариант по умолчанию)
            assigned_dev_node = config.get("dev_node_name", config.get("dev_node_prefix", "ITSG-OVPN"))

    # Определение статического IP для CCD
    subnet_ip, netmask = parse_openvpn_server_subnet()
    ip_to_assign = None
    if custom_ip:
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

@app.route('/api/clients/rebuild', methods=['POST'])
@login_required
def api_rebuild_client():
    data = request.json or {}
    client_name = sanitize_name(data.get('name', '').strip())
    if not client_name:
        return jsonify({"error": "Имя клиента не указано"}), 400
        
    # Считываем сохраненную платформу и dev-node из метаданных
    platform = "windows"
    dev_node = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT platform, dev_node FROM vpn_clients_meta WHERE client_name = ?", (client_name,))
        row = cursor.fetchone()
        if row:
            platform = row['platform'] or "windows"
            dev_node = row['dev_node'] if row['dev_node'] != "—" else None
        conn.close()
    except Exception as e:
        print(f"Error fetching client meta for rebuild: {e}")

    if generate_ovpn(client_name, platform=platform, dev_node=dev_node):
        log_action(session.get("username"), "REBUILD_CLIENT", f"Пересобран конфиг для {client_name}")
        return jsonify({"success": True})
        
    log_action(session.get("username"), "REBUILD_CLIENT_FAILED", f"Ошибка пересборки для {client_name}")
    return jsonify({"error": "Не удалось пересобрать конфигурационный файл"}), 500

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
        
        crl_target = "/etc/openvpn/server/crl.pem"
        if os.path.exists(f"{EASY_RSA_DIR}/pki/crl.pem"):
            shutil.copy(f"{EASY_RSA_DIR}/pki/crl.pem", crl_target)
            try:
                shutil.chown(crl_target, user="nobody", group="nogroup")
            except Exception:
                pass
        
        run_systemctl(["reload", OPENVPN_SERVICE])

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
    
    # 1. Проверяем локальный рабочий каталог CONFIGS_DIR (./data/configs)
    file_path1 = os.path.join(CONFIGS_DIR, f"{client_name}.ovpn")
    if os.path.exists(file_path1):
        log_action(session.get("username"), "DOWNLOAD_CONFIG", f"Скачан конфиг клиента: {client_name}")
        return send_file(file_path1, as_attachment=True, download_name=f"{client_name}.ovpn")
        
    # 2. Проверяем OUTPUT_DIR (/root/openvpn)
    file_path2 = os.path.join(OUTPUT_DIR, f"{client_name}.ovpn")
    if os.path.exists(file_path2):
        log_action(session.get("username"), "DOWNLOAD_CONFIG", f"Скачан конфиг клиента: {client_name}")
        return send_file(file_path2, as_attachment=True, download_name=f"{client_name}.ovpn")
        
    # 3. Пересобираем на лету
    if generate_ovpn(client_name):
        if os.path.exists(file_path1):
            return send_file(file_path1, as_attachment=True, download_name=f"{client_name}.ovpn")

    abort(404)

@app.route('/api/service/status', methods=['GET'])
@login_required
def service_status():
    res = run_systemctl(["is-active", OPENVPN_SERVICE], capture_output=True)
    status = res.stdout.strip()
    return jsonify({"status": "active" if status == "active" else "failed"})

@app.route('/api/service/restart', methods=['POST'])
@login_required
def service_restart():
    try:
        run_systemctl(["restart", OPENVPN_SERVICE], check=True)
        log_action(session.get("username"), "RESTART_SERVICE", "Перезапуск службы OpenVPN")
        return jsonify({"success": True})
    except subprocess.CalledProcessError:
        log_action(session.get("username"), "RESTART_SERVICE_FAILED", "Ошибка перезапуска службы OpenVPN")
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
        "dev_node_name": config.get("dev_node_name", config.get("dev_node_prefix", "ITSG-OVPN")),
        "dev_node_prefix": config.get("dev_node_name", config.get("dev_node_prefix", "ITSG-OVPN")),
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

