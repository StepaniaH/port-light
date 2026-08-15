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

Optional HTTP Basic Auth is applied as middleware to every path except `/api/health`. Responses set `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, and `Content-Security-Policy` (`script-src` allows the inline theme boot script). `/api/*` is `Cache-Control: no-store`; `/static/*` is long-lived (`?v=` busts); `/` is `no-cache` so a new `?v=` actually loads. `GET /api/ports` sends an `ETag`; an unchanged occupancy map returns `304`.

## Listening ports

Order of attempts:

1. **`/host/proc/1/net/{tcp,tcp6,udp,udp6}`** — used in the published Docker image when `/proc` is mounted at `/host/proc`. `/host/proc/net` is the *container* namespace. PID 1 is the host init network namespace.
2. **`ss -tulnpH`** — bare metal or `network_mode: host`. This is the only path that fills `process_name` / `pid`.
3. **Local `/proc/net/*`** — last resort (usually the container’s own listeners).

UDP bound sockets (`st=07` in proc, `UNCONN` in ss) are included. Duplicate listen entries (IPv4 + IPv6, TCP + UDP, or `0.0.0.0` + a specific address) collapse to one map key: the port number. The payload keeps `ips`, `protocol` (`tcp`, `udp`, or `tcp,udp`), and `bind_scope` (`public` / `lan` / `localhost`).

## Docker

`docker.from_env()` talks to the mounted socket. Port mappings come from `HostConfig.PortBindings`, then `NetworkSettings.Ports`.

`network_mode: host`:

- `Config.ExposedPorts` is treated as host ports (same number).
- Running containers also contribute sockets whose inodes appear in `/host/proc/<pid>/fd` and match `/proc/net/*` inodes, so anonymous listeners get a container name when `/proc` is mounted.

Traefik `Host(\`…\`)` / `Host(\`a\`, \`b\`)` / `HostSNI(\`…\`)` rules, a `caddy:` / `caddy_0` site label, and Unraid `net.unraid.docker.webui` (`[IP]` / `[PORT:n]`) become `urls` on the port. `traefik.enable=false` drops Traefik hosts (homepage/wud hrefs still count).

Stopped containers still contribute PortBindings — those become amber if nothing is listening.

## Compose

`COMPOSE_SCAN_DIR` is walked up to `COMPOSE_SCAN_DEPTH` (default 4), skipping `.git` / `node_modules` / venvs, capped by `COMPOSE_SCAN_MAX_FILES`. Files named `compose.yml` / `docker-compose.yml` (and the matching `.yaml` / `.override.yml` variants) are parsed.

`include:` paths (string or `{ path: }`) are followed. `{ path, env_file }` interpolates the included file with those env files (Compose spec). Sibling `.env` lines may start with `export `. Compose **profiles** are ignored: every service’s published ports count, because the map is about occupancy, not the currently selected profile.

Supported port syntax:

- Short: `8080:80`, `0.0.0.0:8080:80`, `127.0.0.1:8080:80`, `8080:80/tcp`
- Long: `{ published, target, protocol, host_ip }` (published may be `"5353/udp"`)
- `${VAR}` / `$VAR` / `${VAR:-default}` / `${VAR-default}` from the sibling `.env` plus process environment
- Ranges expanded (`3000-3002:80` → 3000, 3001, 3002), capped at 128 ports per mapping
- `network_mode: host` plus `expose:` — those container ports are host ports (bridge `expose` is ignored)

A host port listed in more than one **project directory** is marked `conflict: true` only when the **protocol** matches and the bind addresses overlap (`0.0.0.0` / `::` overlaps everything; `127.0.0.1` and `10.0.0.5` do not; TCP vs UDP on the same number is not a conflict). Two files in the same stack (`compose.yml` plus `compose.override.yml`) are not a conflict. That flag is a Compose-declaration clash, not a runtime bind failure. Distinct `host_ip` mappings for the same service are kept as separate `compose_configs` rows.

## Classification

For each port in the union of listeners ∪ Docker mappings ∪ Compose ∪ manual entries:

| Status | Rule |
|--------|------|
| `used` | Listening, or a Docker mapping whose container status is `running` |
| `configured` | Compose declaration or manual entry, and not `used` |
| `free` | Not in the union — only synthesized in the UI during numeric search |

`source_type` is a rough tag for filters (`docker` / `system` / `host` / `manual`). System vs host uses the known-port category, not the OS.

Hidden ports are omitted from the payload unless `include_hidden=true` **and** the request may see them (open LAN, or Basic Auth / `X-Hidden-Unlock`).

Guessed access URLs: loopback binds use `127.0.0.1`; a LAN-only bind uses that address; `0.0.0.0` / `::` uses `URL_HOST` or `localhost`. `URL_HOST` always wins when set (except loopback).

## Persistence

One file: `$PORT_LIGHT_DATA_DIR/port_light.json`. A process-wide `threading.Lock` wraps read-modify-write. Fine for one replica. Do not run two containers on the same file.

The `machines` array may still exist in older `port_light.json` files. It is not scanned and is not shown in the UI.

`settings` in that file is the Web UI / API overlay. Resolution is **default < env < file**, unless `PORT_LIGHT_SETTINGS_SOURCE=env` (Compose wins, PUT returns 403). Paths and secrets (`COMPOSE_SCAN_DIR`, `AUTH_*`, `HIDDEN_UNLOCK_PASSWORD`) are env-only.

## Frontend

No framework. Hash routes: `#/` grid, `#/settings` settings, `#/port/8096` detail. Appearance is cached in `localStorage` only to avoid a flash before `/api/settings` returns. UI copy lives in `frontend/locales/{en,zh-CN,zh-TW,ja}.json`; `frontend/i18n.js` sets `html lang`. An optional `HIDDEN_UNLOCK_PASSWORD` is sent as `X-Hidden-Unlock` from `sessionStorage` (not a client-side hash). Hide-from-grid without that env (and without Basic Auth) is a UI filter on rows the API already returned.

## Why not Kubernetes / remote Docker

Remote `DOCKER_HOST`, Swarm, and Kubernetes need a different agent. See [roadmap.md](roadmap.md).
