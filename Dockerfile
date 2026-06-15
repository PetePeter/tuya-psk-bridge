FROM python:3.10-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential git libssl-dev && \
    rm -rf /var/lib/apt/lists/*

COPY bridge/pyproject.toml ./pyproject.toml
COPY bridge/tuya_psk_bridge/ ./tuya_psk_bridge/

RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends jq libssl3 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-deps /wheels/*.whl && rm -rf /wheels

COPY addon/run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8886

CMD ["/run.sh"]
