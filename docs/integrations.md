# Integrations

Port-Light is read-mostly by design: everything an external tool needs is a
small JSON API plus a few opt-in push channels. This page documents the
surfaces for scripts, monitoring stacks, and coding agents.

All examples assume an instance on `http://127.0.0.1:2100`. When Basic Auth
is configured, add `-u user:password` (curl) or the `Authorization` header.

For an operator-friendly wrapper around the check, reserve, release, and
diagnostic APIs, see the [command-line client guide](cli.md). The CLI and MCP
server share one dependency-free HTTP client and error model.

## Connect an AI assistant

Open **Settings → Automation** and copy the installation prompt into your AI
tool. The running instance serves the [installation guide](ai-setup.md) at
`/ai-setup.md` and the portable skill at `/skill.md`; both follow instance Basic
Auth. The guide covers local and Docker clients, existing-config updates and
read-only verification. The AI execution environment must be able to reach
the instance.

## HTTP API

### Suggest free ports

```bash
# Generate once, securely retain, and reuse this key for retries of this request.
export PORT_LIGHT_REQUEST_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
curl -s -X POST "http://127.0.0.1:2100/api/reservations" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $PORT_LIGHT_REQUEST_KEY" \
  -d '{"count":2,"label":"preview","ttl":3600}'
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
- POST atomically records the returned ports as reservations
  (configured / amber on every map). Save each token: the server stores only its hash and can reconstruct it
  when you retry with the same secret request key. Release its port with `DELETE /api/reservations/{port}`
  and `X-Reservation-Token: <token>`. Manual entries use the manual-port API.
- `ttl=<seconds>` (60–604800) turns reservations into leases: they disappear
  on their own once expired. The response carries `expires_at`.
- `scope=all` also avoids ports occupied or hidden on configured peers. If any
  peer response is unavailable, stale, locked, truncated, or incomplete, the
  request returns `503` and reserves nothing. A successful response reports
  `"scope": "all:<reachable>/<total>"`.
- `count` is capped at 64.
- `require_count=true` makes the selection all-or-none: if the requested count
  does not fit, the response contains no ports and nothing is reserved. The CLI
  uses this mode so concurrent automation never receives a silent partial set.
- A local failed, incomplete, or stale scan returns `503`. Peer summaries must explicitly report `scan_complete: true`; older peers need an upgrade before `scope=all` can allocate ports.
- Allocation coordinates this Port-Light process only. It does not bind OS sockets; another process can still claim a port after a scan.

`scope` controls which hosts are checked. Even with `scope=all`, reservations
are saved only on the instance receiving the request; peer data is used as an
observation. Independent hubs can therefore select the same port.

### Reservation recovery

GET `/api/ports/suggest` only plans; `reserve=true` or `ttl` on that endpoint
returns 405. Upgrade older CLI/MCP clients together with the server.
POST `/api/reservations` accepts the options above as JSON (without `reserve`)
and defaults to `require_count=true`. `Idempotency-Key` must contain 43–128
URL-safe characters; generate it randomly and keep it secret like a release token.
The same key and parameters return the original active claim without rescanning;
different parameters or partially/fully inactive claims return 409 and never reallocate.
GET `/api/reservations/request` with that header recovers still-active claims
without creating anything (404 if absent, 409 if all original ports are inactive).
For a partially released batch it returns the active subset and `inactive_ports`. Both endpoints enforce Basic Auth and the
configured agent token. Neither recovery nor retries extend the original TTL.

Active receipts are retained for the reservation lifetime; inactive receipts may
be removed after seven days from creation. Never resubmit an old key after that
window: use the recovery GET instead. The ledger permits at most 4096 receipts;
when full it rejects new reservations instead of discarding active recovery data.
Existing reservations created before this change cannot gain lost credentials.

### Scanner selection and peer compatibility

`PORT_LIGHT_SCANNERS` defaults to `listen,docker,compose`. An enabled source
must complete successfully before Port-Light can confirm free ports. A source
that completes with no rows is `ok`; a source that cannot be read is `failed`.
Omitting a source explicitly marks it `disabled` and excludes its occupancy
from the checks. At least one source must remain enabled.

The same selection is editable as one set under **Settings → Machines & scanning** and
through `PUT /api/settings` with `local_scanners: ["listen", "docker"]`.
Saved values override environment defaults unless settings are locked to the
environment. Changes apply to subsequent scans without a container restart.

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
`/api/ports/suggest`, POST `/api/reservations`, and GET
`/api/reservations/request`. Basic Auth credentials continue to work alongside it.

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

A successful response contains `{"status":"ok","ports":[3000,3001,3002,3003,3004,3005,3006,3007]}`. Ranges must contain 1–64 ports. If any selected port is occupied, reserved, or hidden at the recheck, the response is `409` and no selected port is added. Storage failures return `500` for writes or `503` for unreadable/invalid data. These entries use ordinary manual-port editing and deletion; the agent-token gate continues to apply to suggestions and reservation creation/recovery.

### Check one port

```bash
curl -s "http://127.0.0.1:2100/api/ports/5432"
```

Returns the full row (status, source type, containers, Compose configs,
guessed URLs). If occupancy cannot be confirmed and no occupied/configured row is known, the response is `503`. Hidden rows are withheld unless the request carries
`X-Hidden-Unlock` and the instance has `HIDDEN_UNLOCK_PASSWORD` or Basic Auth.

### Read a port observation

```bash
curl -s "http://127.0.0.1:2100/api/observations/ports/5432?protocol=all"
```

This is the evidence-oriented counterpart to the full port row. It returns one
captured local snapshot with a `capture_id`, scan quality, a separate `tcp` and
`udp` entry, broad bind categories, local state history, and recent
deterministic changes. It deliberately excludes service names, container names,
paths, URLs, raw IP addresses, process details, and manual labels. Consumers
must keep `evidence_refs` with any explanation they produce.

`protocol` accepts `tcp`, `udp`, or `all`. `all` never combines TCP and UDP
into one occupancy claim. A scan that is incomplete or stale still returns its
known facts and marks the limitation; it does not imply that an unobserved
protocol is free. `history.resolution` is currently `port_state_only`, so its
entries cannot establish a past owner, protocol, or bind scope. Recent
`events` include recent deterministic changes. When history is enabled they
are stored in the same local retention window as state transitions, so a
report can retain evidence across a restart.

Hidden ports return `404` unless `include_hidden=true` accompanies the same
current hidden-unlock authorization used by the normal port API. A caller that
cannot see a hidden port also cannot receive its observation events. An
authorized hidden response includes `requires_hidden_access: true`; report
stores use it to require the same current visibility on later reads.

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

`port_light_client/mcp.py` is a dependency-free MCP stdio adapter over the same
shared client as the CLI; `mcp/server.py` remains a compatibility entry point.
It exposes eight tools: `doctor`, `suggest_ports`, `reserve_ports`, `check_port`,
`list_occupancy`, `port_history`, `list_degradations`, `release_port`.

The `port-light-cli` package now also installs `port-light-mcp`. Both use
Python 3.11+ without runtime dependencies. Use an absolute executable path in
GUI clients and set `PORT_LIGHT_URL` to the instance reachable from the agent's
computer. A source checkout is no longer required for a separately installed
MCP client. This entry point requires v0.8.4 or later; older wheels contain only the CLI.

Use `port-light mcp-config --client codex` or `--client claude-code` to generate
the correct registration format. `port-light verify` checks the actual adapter,
diagnostics and read-only agent gate without reserving ports. See the
[AI setup guide](ai-setup.md) for client-specific installation and updates.

The stdio adapter supports protocol versions `2024-11-05`, `2025-06-18`
and `2025-11-25`. An unsupported requested version negotiates
`2025-11-25`; it is never echoed as supported. Tools require `initialize` followed
by `notifications/initialized`. UTF-8 JSON messages are newline-delimited and
limited to 1 MiB each. Malformed input returns a protocol error and the next line
can still be processed. Notifications never execute tools.

New integrations should use:

- `doctor`: read-only connection and scan diagnostics.
- `suggest_ports`: planning, without `reserve` or `ttl`.
- `reserve_ports`: all-or-none reservation with a one-hour default lease,
  persistent retry recovery, and privately saved tokens omitted from tool output.
- `release_port`: release by port using the saved token for that URL; an explicit
  token remains supported for claims from another client.

Keep the same `PORT_LIGHT_URL` and `PORT_LIGHT_STATE_DIR` during retries, releases
and updates. `reserve_ports` accepts `count`, `start`, `end`, `rule`, `scope`,
`label` and `ttl` (60–604800 seconds). A pending failed call must be retried with
identical arguments; a completed call followed by another call creates a new
reservation. For explicit recovery use the companion CLI's `requests` / `recover`
commands. If release succeeds but local cleanup fails, the result includes
`remote_released: true` and a warning.

Legacy `suggest_ports(reserve=true, ...)` keeps its original behavior and token
output. Use an explicit TTL with that path; without one it creates persistent
reservations. Tool descriptions and initialization instructions explain the
workflow, and annotations distinguish read-only tools from mutating tools.
`suggest_ports` is marked potentially mutating because of its legacy options.

The published image ships the server at `/app/mcp/server.py`, so Docker
deployments do not need a source checkout:

```json
{
  "mcpServers": {
    "port-light": {
      "command": "docker",
      "args": ["exec", "-i", "-e", "PORT_LIGHT_URL", "port-light", "python", "/app/mcp/server.py"],
      "env": {"PORT_LIGHT_URL": "http://127.0.0.1:2100"}
    }
  }
}
```

The agent skill is included at `/app/skills/port-light/SKILL.md`.

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

For Docker, add `"-e", "PORT_LIGHT_AUTH"` and/or
`"-e", "PORT_LIGHT_AGENT_TOKEN"` before the container name when setting those
variables in the MCP process environment. `env` alone does not forward host
variables into `docker exec`. This route requires access to the Docker daemon
hosting Port-Light, not just a Docker installation on the agent’s computer.

Point `PORT_LIGHT_URL` at a peer to query another machine; add
`PORT_LIGHT_AUTH=user:password` when that instance uses Basic Auth, and add
`PORT_LIGHT_AGENT_TOKEN=<token>` when it sets `AGENT_TOKEN`.
`PORT_LIGHT_TIMEOUT=<seconds>` and `PORT_LIGHT_CA_FILE=/path/to/ca.pem` apply
to the shared MCP/CLI HTTP client.

## Port rules

Available since v0.8.3. `GET /api/port-rules` returns the local rule document. `PUT /api/port-rules` replaces it; Basic Auth and settings read-only mode apply.

```json
{
  "rules": [
    {"name": "development", "start": 20000, "end": 29999, "projects": ["wiki"]},
    {"name": "infrastructure", "start": 10000, "end": 19999, "projects": []}
  ]
}
```

`GET /api/ports/suggest?rule=development&count=2` selects that rule’s range. `POST /api/reservations` accepts an optional `rule` field with the same semantics and still requires a retained `Idempotency-Key`. Numeric bounds may narrow the selected range. Unknown rules or bounds outside it return `400`; malformed rule documents return `422`.

MCP `suggest_ports` accepts `rule`. `/api/meta` advertises `port_rules: 1`. Rules are stored in the existing data directory. Deleting a rule leaves active reservations and their recovery receipts intact.

`GET /api/reservations` lists local manual entries and reservations with public labels, machine names, optional expiry, and `is_reservation`. Hidden-port authorization filters this list; request keys and release tokens are never returned. Token-based release continues to use `DELETE /api/reservations/{port}`.
