FROM python:3.12-slim

LABEL org.opencontainers.image.title="Port-Light"
LABEL org.opencontainers.image.description="Traffic-light web UI for monitoring port usage on Docker hosts"
LABEL org.opencontainers.image.source="https://github.com/StepaniaH/port-light"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# iproute2 provides `ss` for port scanning
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV COMPOSE_SCAN_DIR=/compose
ENV PORT_RANGE_START=1
ENV PORT_RANGE_END=9999
ENV PORT_LIGHT_DATA_DIR=/data
ENV CUSTOM_PORTS_FILE=/data/custom_ports.json

RUN mkdir -p /data

EXPOSE 2100

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "2100"]
