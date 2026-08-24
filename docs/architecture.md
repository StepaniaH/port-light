# Architecture

Port-Light is a single-process FastAPI app that serves a static UI and a JSON API. There is no database, no message queue, and no frontend build.

## Request path

```
Browser  ──GET /──►  frontend/index.html
         ──GET /api/ports──►  merge three scanners + JSON store
         ──GET /api/hosts/{id}/ports──►  this host, or a proxied peer snapshot
```

`GET /api/ports` is the only hot path. Every refresh (default 5s) re-runs:

1. `backend/docker_scanner.py` — container list + published ports (host-network pid trees first, so process names can prefer those PIDs)
2. `backend/port_scanner.py` — listening TCP/UDP sockets
3. `backend/compose_scanner.py` — declared host ports in Compose files
4. `backend/port_store.py` — manual / hidden / machine JSON
5. `backend/classification.py` `classify()` — union, status, source type, conflicts (row contracts live in `backend/models.py`)
6. `backend/known_ports.py` — names and access/internal flags

Those three scanners plus the store snapshot are reused for about two seconds, so `GET /api/ports/{N}` (opening a free cell) does not walk Docker / `/proc` / Compose again. Concurrent polls share one in-flight scan instead of walking twice; when a rebuild runs past ~4s, waiters get the last good snapshot with `summary.stale: true` instead of blocking (stale results are never memoized). Classified JSON for the same range and hidden flags is reused too, so a `304` poll does not rebuild the occupancy table. `If-None-Match` accepts the strong tag, `W/"…"`, a comma list, or `*`. Container and Compose rows on each port are sorted before the ETag so a Docker list reorder is not a new map. A store write bumps a generation and drops the snapshot. The UI (`frontend/app.js`) filters, sorts, and searches **in the browser**. `include_hidden` is the exception: when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set, the server withholds those rows unless the request is authorized (`backend/auth.py`). IP-address and protocol normalization primitives are shared through `backend/netaddr.py`.

Optional HTTP Basic Auth is applied as middleware to every path except `/api/health`. Responses set `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, and `Content-Security-Policy` (`script-src` allows the inline theme boot script). `/api/*` is `Cache-Control: no-store`; `/static/*` is long-lived (`?v=` busts); `/` is `no-cache` so a new `?v=` actually loads. `GET /api/ports` sends an `ETag`; an unchanged occupancy map returns `304`.

## Listening ports

Order of attempts:

1. **`/host/proc/1/net/{tcp,tcp6,udp,udp6}`** — used in the published Docker image when `/proc` is mounted at `/host/proc`. `/host/proc/net` is the *container* namespace. PID 1 is the host init network namespace.
2. **`ss -tulnphH`** (then `-tulnph`, then the older `-tulnpH` / `-tulnp`) — bare metal or `network_mode: host`. `-n` keeps well-known ports numeric (`:ssh` is otherwise dropped). Service names are resolved as a fallback.
3. **Local `/proc/net/*`** — last resort (usually the container’s own listeners). `/host/proc` inode → `/proc/<pid>/comm` fills process names when ss is not used.

The Host scanner pill is green for `/host/proc/1/net/tcp`, local `/proc/net/tcp` on a non-container host, or `ss` when the process looks like host netns (`docker0` / several host NICs). A bridge container without that mount stays red even though `/proc/net/tcp` exists. If the listen table is not trusted, the scan returns no listeners (no `ss` / local `/proc` fall-through). `/api/health` includes `listen_source`: `host_proc`, `ss`, `proc`, or `none`. Degraded scans (Docker daemon unreachable, untrusted listen table, `ss` failure, quarantined data file, unreadable Compose file) are recorded by `backend/degradations.py`: each logs one line on the `port-light` logger (`PORT_LIGHT_LOG_LEVEL`, default `warning`) and the newest five appear in `/api/health` under `degradations`. When `METRICS_ENABLED` is set, `GET /api/metrics` serves a Prometheus text exposition of occupancy aggregates (used / configured / free / hidden, degradation count, Compose files) computed from the same snapshot; it passes through Basic Auth like every non-`/api/health` route.

UDP bound sockets (`st=07` in proc, `UNCONN` in ss) are included. Duplicate listen entries (IPv4 + IPv6, TCP + UDP, or `0.0.0.0` + a specific address) collapse to one map key: the port number. The payload keeps `ips`, `protocol` (`tcp`, `udp`, `sctp`, or a comma union — listen, Docker, and Compose protocols are unioned; SCTP does not clash with TCP), and `bind_scope` (`public` / `lan` / `link` / `localhost`). Bind scope unions listen addresses with Docker `HostIp` and Compose `host_ip` (a loopback listen does not hide a `0.0.0.0` publish). An empty `/host/proc` listen table is authoritative (no fall-through into the container netns). If `/host/proc` is mounted but `/host/proc/1/net/tcp` is missing, the listen scan also stops (no `ss` / local `/proc` fall-through). `ss` `tcp6`/`udp6` `*:port` is `::`, not `0.0.0.0`.

## Docker

`docker.from_env()` talks to the mounted socket. Port mappings come from `HostConfig.PortBindings`, then `NetworkSettings.Ports`. Empty / `0` HostPort on one address family reuses a sibling assignment on the same spec (typical dual-stack `-P`). Empty `HostIp` is dual-stack (`0.0.0.0` and `::`). Unassigned ephemeral publishes stay off the map until Docker fills `NetworkSettings.Ports`. After a host port has been assigned, a later stopped inspect with `HostPort: 0` keeps the last mapping in process memory (LRU, 256 container ids; including when a static publish remains and only the ephemeral `HostPort` is zeroed) so amber occupancy does not vanish across a restart flicker.

`network_mode: host` and `network_mode: ns:/proc/1/ns/net` (or any `ns:` path that is the same inode as host pid 1):

- `Config.ExposedPorts` is **configured** occupancy (declared), not in-use, unless something is actually listening or the container pid/inode matches a listen row.
- Running containers also contribute sockets whose inodes appear in `/host/proc/<pid>/fd` **and descendant PIDs** (`task/*/children`, cap 256), or listen rows whose `pid` is in that tree, so worker processes still get a container name. Only host-network pid trees are preferred when naming `/host/proc` sockets; a bridge container's PID does not steal a host listen.
- `network_mode: container:…` joiners of a host-network target are treated the same way (shared netns), including that joiner's `ExposedPorts`. Joiners match by exact name/id, or a unique id prefix of at least 12 characters. Joiners of a bridge container are not matched by inode (inode numbers are per-netns). Arbitrary `ns:/var/run/docker/netns/<id>` is host only when it actually is the host netns.

macvlan/ipvlan networks: `ExposedPorts` plus the network IPv4/IPv6 addresses (including `IPv6Address` prefixes, secondary addresses, and `IPAMConfig` static IPs when the runtime address was cleared on stop) are treated as LAN occupancy (Docker does not publish those on the host).

Traefik `Host(\`…\`)` / `Host(\`a\`, \`b\`)` / `HostSNI(\`…\`)` / `HostHeader(\`…\`)` rules (http when the router entrypoint is `web`/`http` without TLS; regexp templates are dropped), a `caddy:` / `caddy_0` site address (including `http://…`; wildcards are dropped), nginx-proxy `VIRTUAL_HOST` / `LETSENCRYPT_HOST` (on the published mapping whose container port is `VIRTUAL_PORT`, default 80; unmatched `VIRTUAL_PORT` falls through to 80/443/8080/8443 or the lowest host port), and Unraid `net.unraid.docker.webui` (`[IP]` / `[PORT:n]` resolved to the host port when that container port is published; dropped when mappings are known and `n` is not) become `urls` on the port. `traefik.enable=false` drops Traefik hosts (homepage/wud hrefs still count; `{{hostname}}` templates are dropped). Label URLs attach to the Traefik Host() router's service port, else 80/443/8080/8443, else the lowest host port when several mappings exist (not every sidecar, and not the first unrelated `loadbalancer.server.port`). Host-network inode-only rows (empty published list) use the same web/lowest heuristic on attributed listen ports.

Containers may carry `port-light.port.<n>.name` / `.category` labels (n = a host or container port); classification merges them into the row's known-service name (`from_label: true`). Stopped containers still contribute PortBindings — those become amber if nothing is listening.

## Compose

`COMPOSE_SCAN_DIR` is walked up to `COMPOSE_SCAN_DEPTH` (default 4), skipping `.git` / `node_modules` / venvs, capped by `COMPOSE_SCAN_MAX_FILES`. Hitting the cap sets `summary.compose_truncated` (the Compose pill turns amber). Directories named `data` are walked (they used to be skipped wholesale). Files matching `compose*.yml|yaml` and `docker-compose*.yml|yaml` are parsed (including `compose.prod.yml`). Each file is loaded once per scan (include discovery, macvlan names, and service parsing share one memoized doc). A non-UTF-8 or unreadable file raises `ComposeWouldFail`, which flags `summary.compose_incomplete` the same way a broken override does; an empty YAML document parses as no services. A `!reset` / `!override` tagged file still loads; `extends` plus `ports: !reset` **replaces** the parent ports instead of merging them.

`include:` paths (string, `{ path: }`, `{ path: [a.yml, b.yml] }`, or a glob like `../shared/*.yml`) are followed. A `path:` list (or glob) is one included app: later files overlay earlier ones. Included files are not scanned as their own stack (a shared `compose.yml` included from two apps is not a third `project_dir`), and include does not auto-merge that file's sibling `compose.override.yml` (list the override in `path:` if Compose would). Sibling `include:` entries share macvlan/ipvlan/external network names, so a service file can use a network declared in another include. `{ path, env_file }` interpolates the included file with those env files (Compose spec; `env_file.path` mappings too) and that env is inherited by nested `include:`s. `{ project_directory }` is the working directory for the included file’s `.env` / `env_file`, nested `include:` paths, and `extends.file` (not the included file’s parent). Top-level `env_file` interpolates the compose file itself. Sibling `.env` lines may start with `export `; an unquoted value stops at whitespace plus `#` (`WEB_PORT=8080 # public`), while a quoted `#` is kept. Compose **profiles** are ignored: every service’s published ports count, because the map is about occupancy, not the currently selected profile. `network_mode: service:…` / `container:…` ports are ignored (they belong to the target service). Host-network Compose leftover `ports:` / `deploy.ports` are ignored (Docker ignores them); only `expose:` counts. `network_mode: !reset` returns to default bridge.

A sibling `compose.override.yml` / `docker-compose.override.yml` is merged onto the matching base file (`ports: !reset` / `deploy.ports: !reset` replaces). `services: !override` / `!reset` replaces the whole mapping; `web: !override` replaces that service instead of field-merging. Override files are not scanned on their own when the base exists. Multi-document YAML (`---`) is merged the same way. Unquoted `22:22` stays a port mapping (YAML 1.2 integers; PyYAML sexagesimal is disabled). Long-syntax `host_ip: "[::1]"` drops the brackets. `published` without `target` is ignored unless `mode: host`.

Supported port syntax:

- Short: `8080:80`, `0.0.0.0:8080:80`, `127.0.0.1:8080:80`, `8080:80/tcp`, YAML `{8080: 80}` / `{53/udp: 53}` / `53/udp:53`
- Long: `{ published, target, protocol, host_ip, mode }` (`mode: host` without a usable `published` uses `target` as the host port; published or target may be `"5353/udp"` / `"53/udp"`)
- Swarm `deploy.ports` (override / extends merge like `ports`, including a child-only `deploy.ports`)
- `${VAR}` / `$VAR` / `${VAR:-default}` / `${VAR-default}` / `${VAR:+alt}` / `${VAR+alt}` / `${VAR:?err}` / `${VAR?err}` from the sibling `.env` and Compose `env_file` / `include.env_file` (not Port-Light's process environment). Defaults may contain `$FALLBACK` or nested `${INNER}`. Unset/empty `${VAR:?}` / unset `${VAR?}`, a missing **required** `env_file` (`required` defaults to true), a missing `include` path, or a missing `extends.file` skip **that whole Compose project** (Compose would refuse it) and set `summary.compose_incomplete` (Compose pill amber). `required: false` env files may be absent. The implicit sibling `.env` is still optional.
- Ranges expanded (`3000-3002:80` → 3000, 3001, 3002), capped at 128 ports per mapping
- `network_mode: host` (or host `ns:`) plus `expose:` — those container ports are host ports (bridge `expose` is ignored)
- Compose macvlan/ipvlan `ipv4_address` / `ipv6_address` plus `expose` and/or published `target` — LAN occupancy on that address (bridge static IPs are not host occupancy). The macvlan/ipvlan driver may be declared in an `include:` file or an `extends.file`; child `networks:` overlays the parent. `external: true` (or `{ name: … }`) networks with a static IP are treated the same way when Docker is down. `extends.file` interpolates that file’s sibling `.env` and top-level `env_file` (caller env still overlays `.env`).

Conflict keys use the Compose folder **relative to the scan root** (`apps/wiki` vs `other/wiki`), not the basename. `name:` is display-only. Two files in the same stack (`compose.yml` plus `compose.override.yml`) are merged before occupancy, so they are not a conflict.

## Classification

For each port in the union of listeners ∪ Docker mappings ∪ Compose ∪ manual entries:

| Status | Rule |
|--------|------|
| `used` | Listening, or a Docker publish (not bare host-network `EXPOSE`) whose container is `running` / `paused` / `restarting` |
| `configured` | Compose, manual, host-network `EXPOSE`, or a stopped Docker publish, and not `used` |
| `free` | Not in the union — synthesized in the UI during numeric search; `#/port/N` loads `GET /api/ports/{N}` |

`source_type` is a rough tag for filters (`docker` / `system` / `host` / `manual`). System vs host uses the known-port category, not the OS.

Hidden ports are omitted from the payload unless `include_hidden=true` **and** the request may see them (open LAN, or Basic Auth / `X-Hidden-Unlock`). `GET /api/free-runs?count=N&start=&end=` computes the largest contiguous free runs from the same snapshot (read-only planning; reserving is just manual-port entries). `GET /api/ports/{N}` returns 404 for a hidden port that was omitted — never a free stub. When hidden ports are included, a hide-only cell (no listen / Docker / Compose / manual) is `status: "free"` and `is_hidden: true`. When hidden ports are not locked, `summary.hidden_ports` lists their numbers and `summary.hidden_occupancy` lists `{port, status}` so numeric search can keep hidden styling without painting a used port as configured.

Guessed access URLs: loopback binds use `127.0.0.1`; a LAN-only bind uses that address; `0.0.0.0` / `::` uses `URL_HOST` or `localhost`. `URL_HOST` always wins when set (except loopback). Link-local (`169.254/16`, `fe80::`) and the default Docker bridge (`172.17.0.0/16`) are `link`, not LAN, and are not used as guessed hosts.

## Persistence

One file: `$PORT_LIGHT_DATA_DIR/port_light.json`. A process-wide `threading.Lock` wraps read-modify-write. Occupancy loads manuals and hidden ports from one snapshot. Fine for one replica. Do not run two containers on the same file.

The `machines` array may still exist in older `port_light.json` files. It is not scanned and is not shown in the UI. Other Port-Light instances are stored under `peers` (id, display name, origin URL, optional Basic Auth). `GET /api/hosts` returns the username so Settings can refill the field; it never returns the password.

When peers are configured, this process **pulls** `GET /api/ports` and `GET /api/health` from each origin (timeout 4s, no redirects, http/https only). Public IPv4 addresses and cloud metadata hosts are rejected; RFC1918, loopback, Tailscale CGNAT (`100.64/10`), and IPv6 ULA are allowed. Hostnames (MagicDNS, `.lan`) are allowed without a DNS check. The browser never talks to those origins directly (`connect-src 'self'`). Hide / labels / add are not forwarded. `X-Hidden-Unlock` is not sent to peers. A Docker bridge hub often cannot route to Tailscale `100.x`; use a LAN IP or `network_mode: host` on the hub.

`PORT_LIGHT_HOST_NAME` labels this column. `PORT_LIGHT_PEERS` is the env overlay when the file has no `peers` key, or when `PORT_LIGHT_SETTINGS_SOURCE=env` (PUT `/api/hosts` is then 403, same as Settings).

`settings` in that file is the Web UI / API overlay. Resolution is **default < env < file**, unless `PORT_LIGHT_SETTINGS_SOURCE=env` (Compose wins, PUT returns 403). Paths and secrets (`COMPOSE_SCAN_DIR`, `AUTH_*`, `HIDDEN_UNLOCK_PASSWORD`) are env-only.

## Frontend

No framework. Hash routes: `#/` grid, `#/settings` (and `#/settings/appearance|occupancy|advanced`) settings, `#/port/8096` detail on this machine (free ports load `GET /api/ports/{N}`), `#/h/{id}` and `#/h/{id}/port/8096` for another instance. Skip-to-content focuses `#grid`, the active host column, or `#settings-form`. Appearance is cached in `localStorage` only to avoid a flash before `/api/settings` returns. Sort, range, kind chips, and whether hidden ports are included (`showHidden`) live in `port-light-view` (turning the eye on persists that flag). Theme is `system` / `dark` / `light` or a named palette (`gruvbox`, `catppuccin`, `nord`, …) applied as `data-theme` on `html`. UI copy lives in `frontend/locales/{en,zh-CN,zh-TW,ja}.json`; `frontend/i18n.js` sets `html lang`. An optional `HIDDEN_UNLOCK_PASSWORD` is sent as `X-Hidden-Unlock` from `sessionStorage` (not a client-side hash); the unlock fetch aborts the occupancy poll so a locked response cannot clobber it, and focus returns to the Hidden chip or count that opened the modal. Hide-from-grid without that env (and without Basic Auth) is a UI filter on rows the API already returned. Hidden occupancy cells keep used/configured color at reduced opacity (chrome `.hidden` is `display: none`). The detail drawer is a modal (Tab trap, `aria-modal`, `inert` on the skip link / header / views) only under 900px; a resize to desktop drops the trap so Tab can return to the grid. Close is auto-focused only when the drawer opens; a manual-label draft restores only for the same port. The last-refresh time and sync-error banner both remeasure the sticky header. Copy-on-click toasts the cell (or detail port) still on screen. `#grid` (and each `.host-grid`) is `aria-busy` while occupancy is in flight. Re-selecting the current language is not an unsaved Settings change. Numeric search while hidden ports are locked does not paint a withheld hit free: it looks up `#/port/N` and keeps a locked cell until that 404/free answer arrives. With other machines configured, desktop shows one column per host (do not sum occupancy across hosts); under 900px a host switcher shows one map. Search/sort/range/kind chips apply to every column. Remote detail is read-only. The entry loads as a native ES module (`<script type="module" src="/static/js/app.js?v=N">`, still no build step). `frontend/js/` holds the split: `state.js` (single mutable store `S` plus appearance/view helpers), `dom.js` (element refs, header/sync utilities), `text.js` (i18n lookups and escaping), `kinds.js`, `a11y.js`, `modal.js`, `api.js` (host catalog, URL builders, fetchers, poll loop), `router.js` (hash routes), `grid.js` (summary bar and occupancy grid), `detail.js` (drawer/modal and hide flows), `settings.js` (panels, locale menu, theme picker, peers editor); `app.js` keeps boot, event wiring, and composition. Module chunks revalidate (`Cache-Control: no-cache`) so an upgrade never mixes generations; `?v=` on the entry and `CACHE_BUST` in i18n.js stay the release cache-busters.

## Why not Kubernetes / remote Docker

Remote `DOCKER_HOST`, Swarm, and Kubernetes need a different agent. See [roadmap.md](roadmap.md).
