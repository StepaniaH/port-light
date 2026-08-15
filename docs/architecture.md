# Architecture

Port-Light is a single-process FastAPI app that serves a static UI and a JSON API. There is no database, no message queue, and no frontend build.

## Request path

```
Browser  ──GET /──►  frontend/index.html
         ──GET /api/ports──►  merge three scanners + JSON store
```

`GET /api/ports` is the only hot path. Every refresh (default 5s) re-runs:

1. `backend/docker_scanner.py` — container list + published ports (host-network pid trees first, so process names can prefer those PIDs)
2. `backend/port_scanner.py` — listening TCP/UDP sockets
3. `backend/compose_scanner.py` — declared host ports in Compose files
4. `backend/port_store.py` — manual / hidden / machine JSON
5. `backend/main.py` `_classify()` — union, status, source type, conflicts
6. `backend/known_ports.py` — names and access/internal flags

The UI (`frontend/app.js`) filters, sorts, and searches **in the browser**. `include_hidden` is the exception: when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set, the server withholds those rows unless the request is authorized (`backend/auth.py`).

Optional HTTP Basic Auth is applied as middleware to every path except `/api/health`. Responses set `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, and `Content-Security-Policy` (`script-src` allows the inline theme boot script). `/api/*` is `Cache-Control: no-store`; `/static/*` is long-lived (`?v=` busts); `/` is `no-cache` so a new `?v=` actually loads. `GET /api/ports` sends an `ETag`; an unchanged occupancy map returns `304`.

## Listening ports

Order of attempts:

1. **`/host/proc/1/net/{tcp,tcp6,udp,udp6}`** — used in the published Docker image when `/proc` is mounted at `/host/proc`. `/host/proc/net` is the *container* namespace. PID 1 is the host init network namespace.
2. **`ss -tulnpH`** (then `ss -tulnp`) — bare metal or `network_mode: host`.
3. **Local `/proc/net/*`** — last resort (usually the container’s own listeners). `/host/proc` inode → `/proc/<pid>/comm` fills process names when ss is not used.

The Host scanner pill is green for `/host/proc/1/net/tcp`, local `/proc/net/tcp` on a non-container host, or `ss` when the process looks like host netns (`docker0` / several host NICs). A bridge container without that mount stays red even though `/proc/net/tcp` exists. If the listen table is not trusted, the scan returns no listeners (no `ss` / local `/proc` fall-through). `/api/health` includes `listen_source`: `host_proc`, `ss`, `proc`, or `none`.

UDP bound sockets (`st=07` in proc, `UNCONN` in ss) are included. Duplicate listen entries (IPv4 + IPv6, TCP + UDP, or `0.0.0.0` + a specific address) collapse to one map key: the port number. The payload keeps `ips`, `protocol` (`tcp`, `udp`, `sctp`, or a comma union — listen, Docker, and Compose protocols are unioned; SCTP does not clash with TCP), and `bind_scope` (`public` / `lan` / `link` / `localhost`). An empty `/host/proc` listen table is authoritative (no fall-through into the container netns). If `/host/proc` is mounted but `/host/proc/1/net/tcp` is missing, the listen scan also stops (no `ss` / local `/proc` fall-through). `ss` `tcp6`/`udp6` `*:port` is `::`, not `0.0.0.0`.

## Docker

`docker.from_env()` talks to the mounted socket. Port mappings come from `HostConfig.PortBindings`, then `NetworkSettings.Ports`. Empty / `0` HostPort on one address family reuses a sibling assignment on the same spec (typical dual-stack `-P`). Unassigned ephemeral publishes stay off the map until Docker fills `NetworkSettings.Ports`.

`network_mode: host` and `network_mode: ns:/proc/1/ns/net` (or any `ns:` path that is the same inode as host pid 1):

- `Config.ExposedPorts` is **configured** occupancy (declared), not in-use, unless something is actually listening or the container pid/inode matches a listen row.
- Running containers also contribute sockets whose inodes appear in `/host/proc/<pid>/fd` **and descendant PIDs** (`task/*/children`), or listen rows whose `pid` is in that tree, so worker processes still get a container name.
- `network_mode: container:…` joiners of a host-network target are treated the same way (shared netns). Joiners of a bridge container are not matched by inode (inode numbers are per-netns). Arbitrary `ns:/var/run/docker/netns/<id>` is host only when it actually is the host netns.

macvlan/ipvlan networks: `ExposedPorts` plus the network IPv4/IPv6 addresses (including `IPv6Address` prefixes and secondary addresses) are treated as LAN occupancy (Docker does not publish those on the host).

Traefik `Host(\`…\`)` / `Host(\`a\`, \`b\`)` / `HostSNI(\`…\`)` / `HostHeader(\`…\`)` rules (http when the router entrypoint is `web`/`http` without TLS; regexp templates are dropped), a `caddy:` / `caddy_0` site address (including `http://…`), nginx-proxy `VIRTUAL_HOST` / `LETSENCRYPT_HOST` (on the published mapping whose container port is `VIRTUAL_PORT`, default 80), and Unraid `net.unraid.docker.webui` (`[IP]` / `[PORT:n]`) become `urls` on the port. `traefik.enable=false` drops Traefik hosts (homepage/wud hrefs still count). Label URLs attach to the Traefik loadbalancer port or to 80/443/8080/8443 when several mappings exist.

Stopped containers still contribute PortBindings — those become amber if nothing is listening.

## Compose

`COMPOSE_SCAN_DIR` is walked up to `COMPOSE_SCAN_DEPTH` (default 4), skipping `.git` / `node_modules` / venvs, capped by `COMPOSE_SCAN_MAX_FILES`. Hitting the cap sets `summary.compose_truncated` (the Compose pill turns amber). Files matching `compose*.yml|yaml` and `docker-compose*.yml|yaml` are parsed (including `compose.prod.yml`). A non-UTF-8 or tagged (`!reset` / `!override`) file still loads; `extends` plus `ports: !reset` **replaces** the parent ports instead of merging them.

`include:` paths (string, `{ path: }`, `{ path: [a.yml, b.yml] }`, or a glob like `../shared/*.yml`) are followed. `{ path, env_file }` interpolates the included file with those env files (Compose spec; `env_file.path` mappings too). `{ project_directory }` contributes that directory’s `.env`. Top-level `env_file` interpolates the compose file itself. Sibling `.env` lines may start with `export `. Compose **profiles** are ignored: every service’s published ports count, because the map is about occupancy, not the currently selected profile. `network_mode: service:…` / `container:…` ports are ignored (they belong to the target service).

Supported port syntax:

- Short: `8080:80`, `0.0.0.0:8080:80`, `127.0.0.1:8080:80`, `8080:80/tcp`, YAML `{8080: 80}` / `{53/udp: 53}` / `53/udp:53`
- Long: `{ published, target, protocol, host_ip, mode }` (`mode: host` without a usable `published` uses `target` as the host port; published or target may be `"5353/udp"` / `"53/udp"`)
- Swarm `deploy.ports`
- `${VAR}` / `$VAR` / `${VAR:-default}` / `${VAR-default}` / `${VAR:?err}` / `${VAR?err}` from the sibling `.env` plus process environment
- Ranges expanded (`3000-3002:80` → 3000, 3001, 3002), capped at 128 ports per mapping
- `network_mode: host` (or host `ns:`) plus `expose:` — those container ports are host ports (bridge `expose` is ignored)
- Compose macvlan/ipvlan `ipv4_address` / `ipv6_address` plus `expose` and/or published `target` — LAN occupancy on that address (bridge static IPs are not host occupancy). The macvlan/ipvlan driver may be declared in an `include:` file or an `extends.file`; child `networks:` overlays the parent.

Conflict keys use the Compose folder **relative to the scan root** (`apps/wiki` vs `other/wiki`), not the basename. `name:` is display-only. Two files in the same stack (`compose.yml` plus `compose.override.yml`) are not a conflict.

## Classification

For each port in the union of listeners ∪ Docker mappings ∪ Compose ∪ manual entries:

| Status | Rule |
|--------|------|
| `used` | Listening, or a Docker publish (not bare host-network `EXPOSE`) whose container is `running` / `paused` / `restarting` |
| `configured` | Compose, manual, host-network `EXPOSE`, or a stopped Docker publish, and not `used` |
| `free` | Not in the union — synthesized in the UI during numeric search; `#/port/N` loads `GET /api/ports/{N}` |

`source_type` is a rough tag for filters (`docker` / `system` / `host` / `manual`). System vs host uses the known-port category, not the OS.

Hidden ports are omitted from the payload unless `include_hidden=true` **and** the request may see them (open LAN, or Basic Auth / `X-Hidden-Unlock`). `GET /api/ports/{N}` returns 404 for a hidden port that was omitted — never a free stub. When hidden ports are not locked, `summary.hidden_ports` lists their numbers so numeric search can keep the hidden styling.

Guessed access URLs: loopback binds use `127.0.0.1`; a LAN-only bind uses that address; `0.0.0.0` / `::` uses `URL_HOST` or `localhost`. `URL_HOST` always wins when set (except loopback). Link-local (`169.254/16`, `fe80::`) and the default Docker bridge (`172.17.0.0/16`) are `link`, not LAN, and are not used as guessed hosts.

## Persistence

One file: `$PORT_LIGHT_DATA_DIR/port_light.json`. A process-wide `threading.Lock` wraps read-modify-write. Occupancy loads manuals and hidden ports from one snapshot. Fine for one replica. Do not run two containers on the same file.

The `machines` array may still exist in older `port_light.json` files. It is not scanned and is not shown in the UI.

`settings` in that file is the Web UI / API overlay. Resolution is **default < env < file**, unless `PORT_LIGHT_SETTINGS_SOURCE=env` (Compose wins, PUT returns 403). Paths and secrets (`COMPOSE_SCAN_DIR`, `AUTH_*`, `HIDDEN_UNLOCK_PASSWORD`) are env-only.

## Frontend

No framework. Hash routes: `#/` grid, `#/settings` settings, `#/port/8096` detail (free ports load `GET /api/ports/{N}`). Skip-to-content focuses `#grid` or `#settings-form`. Appearance is cached in `localStorage` only to avoid a flash before `/api/settings` returns. Theme is `system` / `dark` / `light` or a named palette (`gruvbox`, `catppuccin`, `nord`, …) applied as `data-theme` on `html`. UI copy lives in `frontend/locales/{en,zh-CN,zh-TW,ja}.json`; `frontend/i18n.js` sets `html lang`. An optional `HIDDEN_UNLOCK_PASSWORD` is sent as `X-Hidden-Unlock` from `sessionStorage` (not a client-side hash). Hide-from-grid without that env (and without Basic Auth) is a UI filter on rows the API already returned. Hidden occupancy cells keep used/configured color at reduced opacity (chrome `.hidden` is `display: none`).

## Why not Kubernetes / remote Docker

Remote `DOCKER_HOST`, Swarm, and Kubernetes need a different agent. See [roadmap.md](roadmap.md).
