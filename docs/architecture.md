# Architecture

Port-Light is a single-process FastAPI app that serves a static UI and a JSON API. There is no database, no message queue, and no frontend build.

## Request path

```
Browser  ──GET /──►  frontend/index.html
         ──GET /api/ports──►  merge three scanners + JSON store
```

`GET /api/ports` is the only hot path. Every refresh (default 5s) re-runs:

1. `backend/port_scanner.py` — listening TCP sockets
2. `backend/docker_scanner.py` — container list + published ports
3. `backend/compose_scanner.py` — declared host ports in Compose files
4. `backend/port_store.py` — manual / hidden / machine JSON
5. `backend/main.py` `_classify()` — union, status, source type, conflicts
6. `backend/known_ports.py` — names and access/internal flags

The UI (`frontend/app.js`) filters, sorts, and searches **in the browser**. `include_hidden` is the exception: when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set, the server withholds those rows unless the request is authorized (`backend/auth.py`).

Optional HTTP Basic Auth is applied as middleware to every path except `/api/health`.

## Listening ports

Order of attempts:

1. **`/host/proc/1/net/tcp` and `tcp6`** — used in the published Docker image when `/proc` is mounted at `/host/proc`. `/host/proc/net/tcp` is the *container* namespace (symlink to `/proc/self/net`). PID 1 on the host is the namespace that has the real host listeners.
2. **`ss -tlnpH`** — bare metal or `network_mode: host`. This is the only path that fills `process_name` / `pid`.
3. **Local `/proc/net/tcp`** — last resort (usually the container’s own listeners).

The scanner keeps a result from (1) only if it finds **more than one** port. That heuristic avoids treating a nearly empty file as success; it can also hide a host that truly has a single listener.

No UDP. Duplicate listen entries (IPv4 + IPv6, or `0.0.0.0` + a specific address) collapse to one map key: the port number.

## Docker

`docker.from_env()` talks to the mounted socket. Port mappings come from `HostConfig.PortBindings` only.

Consequences:

- Published ports on a bridge network show up with container name and status.
- `network_mode: host` containers typically have **empty** PortBindings. Their sockets still appear in the host TCP table, but without a container name unless `ss` could see the process.
- Stopped containers still contribute mappings if Docker recorded PortBindings — those become amber if nothing is listening.

## Compose

`COMPOSE_SCAN_DIR` is globbed with:

- `*/docker-compose.y*ml`, `*/compose.y*ml`
- `docker-compose.y*ml`, `compose.y*ml` at the scan root

That is **one** subdirectory level. Nested gitops trees and `include:` files are not followed.

Supported port syntax:

- Short: `8080:80`, `0.0.0.0:8080:80`, `8080:80/tcp`
- Long: `{ published, target, protocol }`
- `${VAR}` / `$VAR` from the sibling `.env` plus process environment
- Ranges: **first** number only (`3000-3002:80` → `3000`)

A host port listed in more than one parsed file is marked `conflict: true`. That is a Compose-declaration conflict, not a runtime bind failure.

## Classification

For each port in the union of listeners ∪ Docker mappings ∪ Compose ∪ manual entries:

| Status | Rule |
|--------|------|
| `used` | Listening, or a Docker mapping whose container status is `running` |
| `configured` | Compose declaration or manual entry, and not `used` |
| `free` | Not in the union — only synthesized in the UI during numeric search |

`source_type` is a rough tag for filters (`docker` / `system` / `host` / `manual`). System vs host uses the known-port category, not the OS.

Hidden ports are omitted from the payload unless `include_hidden=true`. That query flag is **not** authenticated.

## Persistence

One file: `$PORT_LIGHT_DATA_DIR/port_light.json`. A process-wide `threading.Lock` wraps read-modify-write. Fine for one replica. Do not run two containers on the same file.

The `machines` array may still exist in older `port_light.json` files. It is not scanned and is not shown in the UI.

## Frontend

No framework. Settings live in `localStorage`. An optional `HIDDEN_UNLOCK_PASSWORD` is sent as `X-Hidden-Unlock` from `sessionStorage` (not a client-side hash). Hide-from-grid without that env (and without Basic Auth) is a UI filter on rows the API already returned.

## Why not Kubernetes / remote Docker

The product assumption is “one homelab machine, Compose files on disk, a Docker socket.” Remote `DOCKER_HOST`, Swarm, and Kubernetes need a different agent and a different trust model. See [roadmap.md](roadmap.md).
