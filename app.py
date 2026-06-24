import os
import re
import subprocess
from flask import Flask, render_template, request, jsonify, send_file, abort

app = Flask(__name__)

EASY_RSA_DIR = "/etc/openvpn/server/easy-rsa"
OUTPUT_DIR = "/root/openvpn"
CLIENT_COMMON = "/etc/openvpn/server/client-common.txt"
INDEX_FILE = f"{EASY_RSA_DIR}/pki/index.txt"

def get_clients():
    clients = []
    if not os.path.exists(INDEX_FILE):
        return clients
    
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
            
        name = cn_part.split("CN=")[-1]
        
        if name != "server":
            clients.append({
                "name": name,
                "status": "Active" if status == "V" else "Revoked"
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/clients', methods=['GET'])
def api_get_clients():
    return jsonify(get_clients())

@app.route('/api/clients', methods=['POST'])
def api_create_client():
    data = request.json or {}
    raw_name = data.get('name', '') or ''
    raw_name = raw_name.strip()
    if not raw_name:
        return jsonify({"error": "Имя не может быть пустым"}), 400
        
    client_name = sanitize_name(raw_name)
    existing_clients = [c['name'] for c in get_clients() if c['status'] == 'Active']
    if client_name in existing_clients:
        return jsonify({"error": f"Активный клиент {client_name} уже существует"}), 400

    try:
        subprocess.run(
            ["./easyrsa", "--batch", "--days=3650", "build-client-full", client_name, "nopass"],
            cwd=EASY_RSA_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if generate_ovpn(client_name):
            return jsonify({"success": True, "client": client_name})
        else:
            return jsonify({"error": "Ошибка при сборке .ovpn файла"}), 500
    except subprocess.CalledProcessError:
        return jsonify({"error": "Ошибка выполнения Easy-RSA"}), 500

@app.route('/api/clients/revoke', methods=['POST'])
def api_revoke_client():
    data = request.get_json(silent=True) or request.json or {}
    raw_name = data.get('name', '') or ''
    client_name = raw_name.strip()
    
    if not client_name:
        return jsonify({"error": "Имя клиента не указано или передано некорректно"}), 400

    try:
        subprocess.run(["./easyrsa", "--batch", "revoke", client_name], cwd=EASY_RSA_DIR, check=True)
        subprocess.run(["./easyrsa", "--batch", "--days=3650", "gen-crl"], cwd=EASY_RSA_DIR, check=True)
        
        subprocess.run(["cp", f"{EASY_RSA_DIR}/pki/crl.pem", "/etc/openvpn/server/crl.pem"], check=True)
        subprocess.run(["chown", "nobody:nogroup", "/etc/openvpn/server/crl.pem"], check=True)
        
        for folder, ext in [("reqs", "req"), ("private", "key")]:
            file_path = f"{EASY_RSA_DIR}/pki/{folder}/{client_name}.{ext}"
            if os.path.exists(file_path):
                os.remove(file_path)
                
        return jsonify({"success": True})
    except subprocess.CalledProcessError as e:
        print(f"Subprocess error during revoke: {e}")
        return jsonify({"error": "Не удалось отозвать сертификат через Easy-RSA"}), 500

@app.route('/api/clients/download/<string:client_name>', methods=['GET'])
def download_config(client_name):
    client_name = sanitize_name(client_name)
    file_path = os.path.join(OUTPUT_DIR, f"{client_name}.ovpn")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        abort(404)

if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app.run(host='127.0.0.1', port=5000, debug=False)
