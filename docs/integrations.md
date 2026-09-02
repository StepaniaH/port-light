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
- A local failed, incomplete, or stale scan returns `503`. Peer summaries must explicitly report `scan_complete: true`; older peers need an upgrade before `scope=all` can allocate ports.
- Allocation coordinates this Port-Light process only. It does not bind OS sockets; another process can still claim a port after a scan.

`scope` controls which hosts are checked. Even with `scope=all`, reservations
are saved only on the instance receiving the request; peer data is used as an
observation. Independent hubs can therefore select the same port.

### Scanner selection and peer compatibility

`PORT_LIGHT_SCANNERS` defaults to `listen,docker,compose`. An enabled source
must complete successfully before Port-Light can confirm free ports. A source
that completes with no rows is `ok`; a source that cannot be read is `failed`.
Omitting a source explicitly marks it `disabled` and excludes its occupancy
from the checks. At least one source must remain enabled.

For a native installation with listeners and Compose files but no Docker:

```yaml
environment:
  PORT_LIGHT_SCANNERS: listen,compose
```

Use `listen` when only host listeners are in scope, or `listen,docker` when
Compose declarations are intentionally excluded. Correct a temporary failure
of a required source rather than removing it from the configured coverage.

For `scope=all`, every configured peer must return `summary.scan_complete: true`
and pass the freshness and visibility checks. A legacy peer without this field
can still be viewed, but causes the allocation to return `503` without reserving
anything. `scope=self` does not consult peers. Upgrade peers to code supporting
this response field before enabling cross-host allocation; a displayed version
number alone does not establish compatibility. Merging source does not update
running instances or published images.

### Agent token

Set `AGENT_TOKEN` to require an `X-Agent-Token` header on
`/api/ports/suggest`. Basic Auth credentials continue to work alongside it;
every other endpoint is unaffected.

### Largest contiguous runs

```bash
curl -s "http://127.0.0.1:2100/api/free-runs?count=8&start=3000&end=4000"
```

Returns up to ten `{start, end, size}` runs with at least `count` ports, largest first. The same completeness and freshness checks as suggestions apply. Claim an exact selection in one transaction:

```bash
curl -s -X POST http://127.0.0.1:2100/api/manual-ports/batch \
  -H 'Content-Type: application/json' \
  -d '{"start":3000,"end":3007,"label":"Preview services"}'
```

A successful response contains `{"status":"ok","ports":[3000,3001,3002,3003,3004,3005,3006,3007]}`. Ranges must contain 1–64 ports. If any selected port is occupied, reserved, or hidden at the recheck, the response is `409` and no selected port is added. Storage failures return `500` for writes or `503` for unreadable/invalid data. These entries use ordinary manual-port editing and deletion; the agent-token gate continues to apply only to `/api/ports/suggest`.

### Check one port

```bash
curl -s "http://127.0.0.1:2100/api/ports/5432"
```

Returns the full row (status, source type, containers, Compose configs,
guessed URLs). If occupancy cannot be confirmed and no occupied/configured row is known, the response is `503`. Hidden rows are withheld unless the request carries
`X-Hidden-Unlock` and the instance has `HIDDEN_UNLOCK_PASSWORD` or Basic Auth.

### Poll efficiently

`GET /api/ports` answers with a strong ETag; repeat polls send
`If-None-Match` and receive `304` while nothing changed. Requests read the last completed background snapshot without waiting for a scan.
Snapshots older than two scan intervals (at least 4 seconds) carry
`summary.stale: true`. `summary.sources` reports `ok`, `failed`, or `disabled` for each scanner. Check `summary.scan_complete === true` before interpreting a missing row as free. Otherwise `summary.free` is null, and unconfirmed visible rows have `status: "unknown"`. A disabled source is excluded from this guarantee.

## Server-sent events

`GET /api/events` streams SSE frames. A `hello` event arrives on connect;
`refresh` fires when the monitor detects changed occupancy or stored state,
including OS, Docker, and Compose changes. Treat it as a hint to fetch
`/api/ports`. Periodic polling remains useful for reconnects and peer maps;
ETags deduplicate unchanged results:

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
`port_light_compose_files`, `port_light_compose_incomplete`, `port_light_up`, and `port_light_ready`.
`port_light_ready` is 1 only for a complete, current snapshot. The free-count metric is `NaN` otherwise; occupied counts may contain retained observations. Before any snapshot exists the metrics endpoint returns `503`.
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
