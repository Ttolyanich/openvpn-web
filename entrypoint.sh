#!/bin/bash
set -e

echo "=== [OpenVPN Hub] Initializing Containerized OpenVPN Stack ==="

# 1. TUN Device Check / Creation
if [ ! -c /dev/net/tun ]; then
    echo "[Init] Creating TUN device node (/dev/net/tun)..."
    mkdir -p /dev/net
    mknod /dev/net/tun c 10 200 || true
    chmod 600 /dev/net/tun || true
fi

# 2. Directory structure inside persistent storage
DATA_DIR="/app/data"
SERVER_DIR="${DATA_DIR}/server"
CCD_DIR="${SERVER_DIR}/ccd"
CLIENTS_DIR="${DATA_DIR}/clients"
EASY_RSA_DIR="${DATA_DIR}/easy-rsa"

mkdir -p "${SERVER_DIR}"
mkdir -p "${CCD_DIR}"
mkdir -p "${CLIENTS_DIR}"
mkdir -p "${EASY_RSA_DIR}"
mkdir -p /run/openvpn
mkdir -p /var/log/openvpn

# 3. Check for existing setup or run Zero-Config Auto-Bootstrap
if [ ! -f "${SERVER_DIR}/server.conf" ]; then
    echo "[Init] No existing OpenVPN server.conf found in ${SERVER_DIR}. Running Zero-Config Bootstrap..."
    
    # Инициализация Easy-RSA PKI
    if [ ! -f "${EASY_RSA_DIR}/pki/ca.crt" ]; then
        echo "[Bootstrap] Creating Easy-RSA PKI..."
        make-cadir "${EASY_RSA_DIR}" || cp -rf /usr/share/easy-rsa/* "${EASY_RSA_DIR}/"
        cd "${EASY_RSA_DIR}"
        ./easyrsa --batch init-pki
        EASYRSA_REQ_CN="OpenVPN-CA" ./easyrsa --batch build-ca nopass
        ./easyrsa --batch --days=3650 build-server-full server nopass
        ./easyrsa --batch --days=3650 gen-crl
    fi

    # Генерация ключа tls-crypt
    if [ ! -f "${SERVER_DIR}/tc.key" ]; then
        echo "[Bootstrap] Generating tls-crypt key..."
        openvpn --genkey secret "${SERVER_DIR}/tc.key"
    fi

    # Стандартные DH параметры (RFC 7919 FFDHE 2048-bit)
    if [ ! -f "${SERVER_DIR}/dh.pem" ]; then
        echo "[Bootstrap] Writing standard RFC 7919 DH parameters..."
        cat << 'EOF' > "${SERVER_DIR}/dh.pem"
-----BEGIN DH PARAMETERS-----
MIIBCAKCAQEA//////////+t+FRYortKmq/cViAnPTzx2LnFg84tNpWp4TZBFGQz
+8yTnc4kmz75fS/jY2MMddj2gbICrsRhetPfHtXV/WVhJDP1H18GbtCFY2VVPe0a
87VXE15/V8k1mE8McODmi3fipona8+/och3xWKE2rec1MKzKT0g6eXq8CrGCsyT7
YdEIqUuyyOP7uWrat2DX9GgdT0Kj3jlN9K5W7edjcrsZCwenyO4KbXCeAvzhzffi
7MA0BM0oNC9hkXL+nOmFg/+OTxIy7vKBg8P+OxtMb61zO7X8vC7CIAXFjvGDfRaD
ssbzSibBsu/6iGtCOGEoXJf//////////wIBAg==
-----END DH PARAMETERS-----
EOF
    fi

    # Копирование сертификатов сервера
    cp "${EASY_RSA_DIR}/pki/ca.crt" "${SERVER_DIR}/ca.crt"
    cp "${EASY_RSA_DIR}/pki/issued/server.crt" "${SERVER_DIR}/server.crt"
    cp "${EASY_RSA_DIR}/pki/private/server.key" "${SERVER_DIR}/server.key"
    cp "${EASY_RSA_DIR}/pki/crl.pem" "${SERVER_DIR}/crl.pem"

    # Определение внешнего IP
    PUBLIC_IP="${PUBLIC_IP:-$(curl -s4 -m 5 https://api.ipify.org || hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")}"
    PORT="${OPENVPN_PORT:-1194}"
    PROTO="${OPENVPN_PROTO:-udp}"

    echo "[Bootstrap] Creating server.conf (Port: ${PORT}, Proto: ${PROTO}, External IP: ${PUBLIC_IP})..."
    cat << EOF > "${SERVER_DIR}/server.conf"
port ${PORT}
proto ${PROTO}
dev tun
ca ca.crt
cert server.crt
key server.key
dh dh.pem
auth SHA512
tls-crypt tc.key
topology subnet
server 10.8.0.0 255.255.255.0
client-config-dir ccd
ifconfig-pool-persist ipp.txt
keepalive 10 120
persist-key
persist-tun
verb 3
crl-verify crl.pem
status /app/data/openvpn-status.log 5
status-version 2
management /run/openvpn.sock unix
explicit-exit-notify
EOF

    echo "[Bootstrap] Creating client-common.txt template..."
    cat << EOF > "${SERVER_DIR}/client-common.txt"
client
dev tun
proto ${PROTO}
remote ${PUBLIC_IP} ${PORT}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
auth SHA512
verb 3
EOF

    echo "[Bootstrap] OpenVPN Zero-Config Auto-Bootstrap complete!"
else
    echo "[Init] Found existing server.conf in ${SERVER_DIR}."
fi

# 4. Проверка и инъекция необходимых директив в server.conf (management socket & status)
if [ -f "${SERVER_DIR}/server.conf" ]; then
    sed -i 's|client-config-dir /etc/openvpn/server/ccd|client-config-dir ccd|g' "${SERVER_DIR}/server.conf"
    if ! grep -q "management /run/openvpn.sock" "${SERVER_DIR}/server.conf" 2>/dev/null; then
        echo "management /run/openvpn.sock unix" >> "${SERVER_DIR}/server.conf"
    fi

    if ! grep -q "status /app/data/openvpn-status.log" "${SERVER_DIR}/server.conf" 2>/dev/null; then
        echo "status /app/data/openvpn-status.log 5" >> "${SERVER_DIR}/server.conf"
        echo "status-version 2" >> "${SERVER_DIR}/server.conf"
    fi

    if ! grep -q "client-config-dir" "${SERVER_DIR}/server.conf" 2>/dev/null; then
        echo "client-config-dir ccd" >> "${SERVER_DIR}/server.conf"
    fi
fi

echo "=== [OpenVPN Hub] Starting Supervisord Services ==="
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
