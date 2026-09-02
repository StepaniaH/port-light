# Integrations

Port-Light is read-mostly by design: everything an external tool needs is a
small JSON API plus a few opt-in push channels. This page documents the
surfaces for scripts, monitoring stacks, and coding agents.

All examples assume an instance on `http://127.0.0.1:2100`. When Basic Auth
is configured, add `-u user:password` (curl) or the `Authorization` header.

## HTTP API

### Suggest free ports

```bash
curl -s "http://127.0.0.1:2100/api/ports/suggest?count=2&reserve=true&label=preview"
```

```json
{
  "ports": [8081, 8082],
  "reserved": [8081, 8082],
  "failed": [],
  "reservations": [
    {"port": 8081, "token": "<save-this-token>", "expires_at": null},
    {"port": 8082, "token": "<save-this-token>", "expires_at": null}
  ],
  "range": {"start": 1, "end": 9999}
}
```

- Skips anything listening, published by Docker, declared in Compose,
  reserved manually, or hidden.
- `reserve=true` atomically records the returned ports as reservations
  (configured / amber on every map). Save each token: it is returned once and
  stored only as a hash. Release its port with `DELETE /api/reservations/{port}`
  and `X-Reservation-Token: <token>`. Manual entries use the manual-port API.
- `ttl=<seconds>` (60–604800) turns reservations into leases: they disappear
  on their own once expired. The response carries `expires_at`.
- `scope=all` also avoids ports occupied or hidden on configured peers. If any
  peer response is unavailable, stale, locked, truncated, or incomplete, the
  request returns `503` and reserves nothing. A successful response reports
  `"scope": "all:<reachable>/<total>"`.
- `count` is capped at 64.

### Agent token

Set `AGENT_TOKEN` to require an `X-Agent-Token` header on
`/api/ports/suggest`. Basic Auth credentials continue to work alongside it;
every other endpoint is unaffected.

### Largest contiguous runs

```bash
curl -s "http://127.0.0.1:2100/api/free-runs?count=8&start=3000&end=4000"
```

Returns up to ten `{start, end, size}` runs, largest first. Read-only —
reserving is done through manual ports.

### Check one port

```bash
curl -s "http://127.0.0.1:2100/api/ports/5432"
```

Returns the full row (status, source type, containers, Compose configs,
guessed URLs). Hidden rows are withheld unless the request carries
`X-Hidden-Unlock` and the instance has `HIDDEN_UNLOCK_PASSWORD` or Basic Auth.

### Poll efficiently

`GET /api/ports` answers with a strong ETag; repeat polls send
`If-None-Match` and receive `304` while nothing changed. When a background
rebuild takes longer than ~4s, waiters get the last good snapshot with
`summary.stale: true`.

## Server-sent events

`GET /api/events` streams SSE frames. A `hello` event arrives on connect;
`refresh` fires when settings or stored data changes. Treat it as a hint to
re-pull `/api/ports` — interval polling still discovers OS, Docker, and Compose
scanner changes, and the ETag deduplicates unchanged results:

```
retry: 3000

event: hello
data: {}

event: refresh
data: {}
```

## Prometheus metrics

Start the server with `METRICS_ENABLED=1`, then scrape:

```yaml
scrape_configs:
  - job_name: port-light
    static_configs:
      - targets: ["nas.lan:2100"]
    metrics_path: /api/metrics
```

Series: `port_light_ports{status="used|configured|free"}`,
`port_light_hidden`, `port_light_degradations`,
`port_light_compose_files`, `port_light_compose_incomplete`.
Aggregates only — no ports or service names are exposed.

## Webhooks

With `WEBHOOK_URL` set and `WEBHOOK_EVENTS=new_listener,conflict`, Port-Light
POSTs fire-and-forget JSON per event:

```json
{"event": "new_listener", "port": 6379}
```

Delivery has a 3s timeout and no retries; failures appear as degradation
events on `/api/health`. `WEBHOOK_SECRET` adds an `X-Port-Light-Secret`
header for the receiver to check.

See the environment tables in the README for the full variable list.

## Coding agents

### Agent skill

`skills/port-light/SKILL.md` packages the suggest/check/release flow for
skill-capable coding agents (Claude Code and compatible tools). Copy or link
it into the agent's skills directory; it teaches the agent to call the API
above instead of guessing ports. The published image includes it at
`/app/skills/port-light/SKILL.md`.

### MCP server (experimental)

`mcp/server.py` is a dependency-free MCP stdio server wrapping the same
endpoints as six tools: `suggest_ports`, `check_port`, `list_occupancy`,
`port_history`, `list_degradations`, `release_port`.

The published image ships the server at `/app/mcp/server.py`, so Docker
deployments do not need a source checkout:

```json
{
  "mcpServers": {
    "port-light": {
      "command": "docker",
      "args": ["exec", "-i", "port-light", "python", "mcp/server.py"],
      "env": {"PORT_LIGHT_URL": "http://127.0.0.1:2100"}
    }
  }
}
```

The agent skill rides along at `/app/skills/port-light/SKILL.md`.

```bash
PORT_LIGHT_URL=http://127.0.0.1:2100 python mcp/server.py
```

Client registration (Claude Code and MCP-compatible clients):

```json
{
  "mcpServers": {
    "port-light": {
      "command": "python",
      "args": ["/path/to/port-light/mcp/server.py"],
      "env": {"PORT_LIGHT_URL": "http://127.0.0.1:2100"}
    }
  }
}
```

Point `PORT_LIGHT_URL` at a peer to query another machine; add
`PORT_LIGHT_AUTH=user:password` when that instance uses Basic Auth, and add
`PORT_LIGHT_AGENT_TOKEN=<token>` when it sets `AGENT_TOKEN`.
