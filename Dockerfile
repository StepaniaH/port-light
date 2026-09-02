FROM python:3.12-slim

LABEL org.opencontainers.image.title="Port-Light"
LABEL org.opencontainers.image.description="Traffic-light web UI for monitoring port usage on Docker hosts"
LABEL org.opencontainers.image.source="https://github.com/StepaniaH/port-light"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# iproute2 provides `ss` for port scanning (used when running on bare metal
# or with host network mode). In bridge containers we read /host/proc/1/net/tcp
# directly, so ss is optional — but keeping it for the fallback path.
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY mcp/ ./mcp/
COPY skills/ ./skills/

# Ensure files are readable by non-root users (e.g. when container runs with --user 1000:1000)
RUN chmod -R a+r /app

ENV COMPOSE_SCAN_DIR=/compose
ENV COMPOSE_SCAN_DEPTH=4
ENV COMPOSE_SCAN_MAX_FILES=400
ENV PORT_RANGE_START=1
ENV PORT_RANGE_END=9999
ENV PORT_LIGHT_DATA_DIR=/data
ENV CUSTOM_PORTS_FILE=/data/custom_ports.json
# Port uvicorn listens on inside the container; /api/meta exposes it so the
# Automation panel can render copy-paste MCP snippets without hardcoding.
ENV PORT_LIGHT_PORT=2100

RUN mkdir -p /data

EXPOSE 2100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ['PORT_LIGHT_PORT'])"

CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port \"${PORT_LIGHT_PORT}\""]
