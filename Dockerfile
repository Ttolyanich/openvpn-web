FROM python:3.11-slim

# Install system dependencies: OpenVPN, Easy-RSA, Supervisor, network utilities
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openvpn \
        easy-rsa \
        supervisor \
        curl \
        iproute2 \
        procps \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Setup supervisor and entrypoint
RUN mkdir -p /etc/supervisor/conf.d && \
    cp supervisord.conf /etc/supervisor/conf.d/supervisord.conf && \
    chmod +x /app/entrypoint.sh

# Environment variables defaults
ENV PORT=5000
ENV BIND_HOST=0.0.0.0
ENV DATABASE_PATH=/app/data/node.db
ENV OPENVPN_WEB_CONFIG=/app/data/config.json
ENV EASY_RSA_DIR=/app/data/easy-rsa
ENV OUTPUT_DIR=/app/data/clients
ENV CLIENT_COMMON=/app/data/server/client-common.txt
ENV SERVER_CONF=/app/data/server/server.conf
ENV CCD_DIR=/app/data/server/ccd
ENV IPP_FILE=/app/data/server/ipp.txt
ENV STATUS_LOG=/app/data/openvpn-status.log
ENV MGMT_SOCKET=/run/openvpn.sock

EXPOSE 5000
EXPOSE 1194/udp

ENTRYPOINT ["/app/entrypoint.sh"]
