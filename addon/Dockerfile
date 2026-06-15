FROM python:3.12-slim

WORKDIR /app

# Install system utilities needed by the entrypoint script
RUN apt-get update && \
    apt-get install -y --no-install-recommends jq && \
    rm -rf /var/lib/apt/lists/*

# Install bridge package dependencies first (layer caching)
COPY bridge/pyproject.toml ./
COPY bridge/tuya_psk_bridge/ ./tuya_psk_bridge/
RUN pip install --no-cache-dir .

# Copy entrypoint script
COPY addon/run.sh /run.sh
RUN chmod +x /run.sh

# The bridge listens for incoming TLS-PSK connections; expose the PSK port.
# Actual port is configured via add-on options at runtime.
EXPOSE 8886

CMD ["/run.sh"]
