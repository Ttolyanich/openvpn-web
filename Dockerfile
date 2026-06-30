FROM python:3.11-slim

# Install systemd to get systemctl client
RUN apt-get update && \
    apt-get install -y --no-install-recommends systemd && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Environment variables defaults
ENV PORT=5000
ENV BIND_HOST=0.0.0.0
ENV DATABASE_PATH=/app/data/node.db
ENV OPENVPN_WEB_CONFIG=/app/data/config.json
ENV EASY_RSA_DIR=/etc/openvpn/server/easy-rsa
ENV OUTPUT_DIR=/root/openvpn
ENV CLIENT_COMMON=/etc/openvpn/server/client-common.txt
ENV OPENVPN_SERVICE=openvpn-server@server.service

EXPOSE 5000

# Run the application
CMD ["python", "app.py"]
