import os
import re
import subprocess
import sqlite3
import json
import time
import socket
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, abort, redirect, url_for, session

# Загрузка конфигурации
CONFIG_PATH = os.getenv("OPENVPN_WEB_CONFIG", "/opt/openvpn-web/config.json")
config = {
    "secret_key": "default-secret-key-32-chars-long-please-change",
    "central_auth_url": "http://127.0.0.1:5001",
    "node_api_token": "default-token",
    "bind_host": "0.0.0.0"
}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            config.update(json.load(f))
    except Exception as e:
        print(f"Error loading config: {e}")

# Автогенерация secret_key при обнаружении дефолтного значения
if config.get("secret_key") == "default-secret-key-32-chars-long-please-change" or not config.get("secret_key"):
    import secrets
    config["secret_key"] = secrets.token_hex(16)
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        print(f"AUTO-CONFIG: Generated secure random secret_key and saved to {CONFIG_PATH}")
    except Exception as e:
        print(f"Error saving auto-generated secret key: {e}")

# Предупреждение на ноде, если используется дефолтный токен
if config.get("node_api_token") == "default-token":
    print("*" * 60)
    print("WARNING: Node is using default 'node_api_token'!")
    print("Please set a secure shared token in config.json that matches the Auth Server.")
    print("*" * 60)

NODE_NAME = config.get("node_name", socket.gethostname())

app = Flask(__name__)
app.secret_key = config["secret_key"]

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

EASY_RSA_DIR = "/etc/openvpn/server/easy-rsa"
OUTPUT_DIR = "/root/openvpn"
CLIENT_COMMON = "/etc/openvpn/server/client-common.txt"
INDEX_FILE = f"{EASY_RSA_DIR}/pki/index.txt"
OPENVPN_SERVICE = "openvpn-server@server.service"

NODE_DB = "/opt/openvpn-web/node.db"

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(NODE_DB)
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
    conn.commit()
    conn.close()

# Логирование действий пользователей (Audit Log)
def log_action(username, action, details=""):
    try:
        conn = sqlite3.connect(NODE_DB)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (username, action, details) VALUES (?, ?, ?)",
            (username, action, details)
        )
        conn.commit()
        # Автоматическая очистка: храним только последние 1000 записей
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

# Декоратор авторизации
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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

def get_clients():
    clients = []
    if not os.path.exists(INDEX_FILE):
        return clients
    
    online_set = get_online_clients()
    
    # Сохраняем активных клиентов в БД ноды
    if online_set:
        try:
            conn = sqlite3.connect(NODE_DB)
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for cn in online_set:
                cursor.execute("""
                    INSERT INTO client_activity (common_name, last_seen)
                    VALUES (?, ?)
                    ON CONFLICT(common_name) DO UPDATE SET last_seen=excluded.last_seen
                """, (cn, now_str))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error updating client activity: {e}")
            
    # Читаем историю активности
    last_seen_map = {}
    try:
        conn = sqlite3.connect(NODE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT common_name, last_seen FROM client_activity")
        for row in cursor.fetchall():
            last_seen_map[row[0].lower()] = row[1]
        conn.close()
    except Exception as e:
        print(f"Error reading client activity: {e}")
            
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
                
            clients.append({
                "name": name,
                "status": "Active" if status == "V" else "Revoked",
                "online": is_online,
                "last_seen": last_seen_val
            })
    return clients

def sanitize_name(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)

def generate_ovpn(client_name):
    try:
        with open(CLIENT_COMMON, "r") as f:
            common = f.read()
        with open(f"{EASY_RSA_DIR}/pki/ca.crt", "r") as f:
            ca = f.read()
        with open(f"{EASY_RSA_DIR}/pki/issued/{client_name}.crt", "r") as f:
            cert_content = f.read()
            cert = cert_content[cert_content.find("-----BEGIN CERTIFICATE-----"):]
        with open(f"{EASY_RSA_DIR}/pki/private/{client_name}.key", "r") as f:
            key = f.read()
            
        with open("/etc/openvpn/server/tc.key", "r") as f:
            tc_content = f.read()
            idx = tc_content.find("-----BEGIN OpenVPN Static key")
            if idx != -1:
                tc = tc_content[idx:]
            else:
                tc = tc_content

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        ovpn_path = os.path.join(OUTPUT_DIR, f"{client_name}.ovpn")
        with open(ovpn_path, "w") as f:
            f.write(f"{common}\n")
            f.write(f"<ca>\n{ca}\n</ca>\n")
            f.write(f"<cert>\n{cert}\n</cert>\n")
            f.write(f"<key>\n{key}\n</key>\n")
            f.write(f"<tls-crypt>\n{tc}\n</tls-crypt>\n")
        return True
    except Exception as e:
        print(f"Error generating config: {e}")
        return False

# Главный маршрут
@app.route('/')
@login_required
def index():
    return render_template('index.html', node_name=NODE_NAME)

# Вход в систему
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({"error": "Заполните все поля"}), 400
            
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        central_url = f"{config['central_auth_url'].rstrip('/')}/api/auth/verify"
        try:
            resp = requests.post(
                central_url,
                json={"username": username, "password": password},
                headers={"X-Node-Token": config["node_api_token"]},
                timeout=5,
                verify=False
            )
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("success"):
                    session["logged_in"] = True
                    session["username"] = username
                    log_action(username, "LOGIN", "Успешный вход в систему")
                    return jsonify({"success": True})
            
            error_msg = "Неверный логин или пароль"
            if resp.status_code != 200:
                try:
                    error_msg = resp.json().get("error", error_msg)
                except:
                    pass
            log_action(username or "unknown", "LOGIN_FAILED", f"Неудачная попытка входа")
            return jsonify({"error": error_msg}), 401
        except requests.exceptions.RequestException as e:
            print(f"Central auth connection error: {e}")
            return jsonify({"error": "Центральный сервер авторизации недоступен"}), 503
            
    return render_template('login.html', node_name=NODE_NAME)

# Выход из системы
@app.route('/logout', methods=['POST'])
def logout():
    username = session.get("username", "unknown")
    log_action(username, "LOGOUT", "Выход из системы")
    session.pop("logged_in", None)
    session.pop("username", None)
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
    if not raw_name:
        return jsonify({"error": "Имя не может быть пустым"}), 400
        
    client_name = sanitize_name(raw_name)
    existing_clients = [c['name'] for c in get_clients() if c['status'] == 'Active']
    if client_name in existing_clients:
        log_action(session.get("username"), "CREATE_CLIENT_FAILED", f"Клиент {client_name} уже существует")
        return jsonify({"error": f"Активный клиент {client_name} уже существует"}), 400

    try:
        subprocess.run(
            ["./easyrsa", "--batch", "--days=3650", "build-client-full", client_name, "nopass"],
            cwd=EASY_RSA_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if generate_ovpn(client_name):
            log_action(session.get("username"), "CREATE_CLIENT", f"Выпущен клиент: {client_name}")
            return jsonify({"success": True, "client": client_name})
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
        
    if generate_ovpn(client_name):
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
        
        subprocess.run(["cp", f"{EASY_RSA_DIR}/pki/crl.pem", "/etc/openvpn/server/crl.pem"], check=True)
        subprocess.run(["chown", "nobody:nogroup", "/etc/openvpn/server/crl.pem"], check=True)
        
        subprocess.run(["systemctl", "reload", OPENVPN_SERVICE])

        for folder, ext in [("reqs", "req"), ("private", "key")]:
            file_path = f"{EASY_RSA_DIR}/pki/{folder}/{client_name}.{ext}"
            if os.path.exists(file_path):
                os.remove(file_path)
                
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
    file_path = os.path.join(OUTPUT_DIR, f"{client_name}.ovpn")
    if os.path.exists(file_path):
        log_action(session.get("username"), "DOWNLOAD_CONFIG", f"Скачан конфиг клиента: {client_name}")
        return send_file(file_path, as_attachment=True)
    else:
        abort(404)

@app.route('/api/service/status', methods=['GET'])
@login_required
def service_status():
    res = subprocess.run(["systemctl", "is-active", OPENVPN_SERVICE], capture_output=True, text=True)
    status = res.stdout.strip()
    return jsonify({"status": "active" if status == "active" else "failed"})

@app.route('/api/service/restart', methods=['POST'])
@login_required
def service_restart():
    try:
        subprocess.run(["systemctl", "restart", OPENVPN_SERVICE], check=True)
        log_action(session.get("username"), "RESTART_SERVICE", "Перезапуск службы OpenVPN")
        return jsonify({"success": True})
    except subprocess.CalledProcessError:
        log_action(session.get("username"), "RESTART_SERVICE_FAILED", "Ошибка перезапуска службы OpenVPN")
        return jsonify({"error": "Не удалось перезапустить службу"}), 500

# Эндпоинт получения логов аудита
@app.route('/api/audit', methods=['GET'])
@login_required
def api_get_audit():
    try:
        conn = sqlite3.connect(NODE_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, username, action, details FROM audit_logs ORDER BY id DESC LIMIT 50")
        logs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    bind_host = config.get("bind_host", "0.0.0.0")
    app.run(host=bind_host, port=5000, debug=False)
