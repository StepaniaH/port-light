# Changelog

Versions follow git tags and image tags (`stepaniah/port-light:vX.Y.Z`).

## Unreleased

### Fixed

- Unsaved appearance previews no longer leak into the next page load: the `localStorage` copy is written only when settings are saved, reverted, or first loaded from the server (`state.js` is now its single writer), so a discarded theme or locale no longer flashes on reload and deleting a selected custom theme clears the stale reference everywhere.

### Changed

- Internal: `app.js` no longer carries duplicate copies of the dom/modal helpers left behind by the module split; its imports now match what it uses (no behavior change).
- Internal: `safe_http_url` lives in `backend/netaddr.py`; importing `classification` no longer pulls the Docker scanner.
- Internal: the unused `machines` writer API is gone from the store — older data files carrying a `machines` array still load. `/api/meta` no longer echoes `theme_mode` / `theme_palette` / `grid_density` / `refresh_ms` (nothing consumed them; appearance lives on `GET /api/settings`).
- Internal: the occupancy snapshot cache (in-flight dedupe, TTL reuse, serve-stale) lives in `backend/occupancy_cache.py`; API behavior unchanged.
- Internal: repeated store reads reuse a stat-keyed memo, so the SSE change-detector no longer re-parses the data file every half second; hand edits still apply immediately.

## 0.7.3 — 2026-08-26

- Localization: three new interface languages — Français, Deutsch, Español — alongside
  tooling that keeps every locale in lockstep (placeholder parity and unused-key tests,
  plus `scripts/locale-scaffold.py` for adding copy).
- Appearance: build your own palettes in a new advanced theme editor — start from the current palette, tweak the 15 core colors, save named custom palettes server-side (`<data-dir>/themes.json`), import/export as JSON. Selection rides the existing `theme_palette` setting as `@custom:<id>`; deleting a selected theme resets to built-in.
- Display: the two display sliders are replaced by one card-density choice with three
  visual presets — Loose / Standard / Compact (`GRID_DENSITY`, default `standard`; legacy
  `comfortable` behaves as `standard`). Text size is fixed again; the unreleased
  `card_scale`/`text_scale` settings are gone. The sample-card preview stays.
- Settings API: `GET/POST/PUT/DELETE /api/custom-themes`; `GET /api/settings` responses gain `custom_themes`. Writes refuse when settings are readonly.

## 0.7.2 — 2026-08-25

- The Automation connect card no longer hardcodes the docker-exec MCP endpoint: the backend now exposes its listen port via `/api/meta` (`PORT_LIGHT_PORT`, new env var, default `2100` in the Dockerfile/compose) and the snippet is built from it, falling back to a `<port>` placeholder with a hint explaining the container-internal target (all four locales)
- `skills/port-light/SKILL.md` no longer tells agents to export a literal `http://127.0.0.1:2100`; it points at whatever URL you use to open the dashboard, so host-mapped ports and reverse proxies work
- The Dockerfile `HEALTHCHECK`, container `CMD`, and the compose healthcheck all read `PORT_LIGHT_PORT` instead of repeating the port

## 0.7.1 — 2026-08-25

- Settings gains an **Automation** tab: copy-paste MCP registration (docker-exec or source forms), agent-skill install hint, curl examples, live usage activity, and an active-leases list with release buttons
- `GET /api/ports/suggest` calls are now recorded locally (time, count, scope, label, leased — never tokens or IPs) and summarized under `/api/meta` → `automation.agent_events`; retention follows `HISTORY_RETENTION_DAYS`
- Lease visibility: cards whose reservation carries a TTL show a badge on the grid and an expiry countdown in the detail drawer
- The published image now includes `mcp/server.py` and `skills/port-light/SKILL.md`, so Docker users can register the MCP server without cloning the repo
- Themes: brightness (system / light / dark) and color palette are now independent controls; the palette list shows one entry per family and follows the chosen brightness. Replaces the `theme` setting with `theme_mode` + `theme_palette` — saved values migrate automatically; the `THEME` env var is replaced by `THEME_MODE` and `THEME_PALETTE`.

## 0.7.0 — 2026-08-24

### Added

- Local history: state transitions are recorded to `history.db` in the data volume (default 7 days, `HISTORY_RETENTION_DAYS=0` disables). The detail drawer lists recent changes; `GET /api/ports/{n}/history?hours=N` queries it.
- Live updates over SSE: `GET /api/events` pushes a refresh hint when the scan key or store generation changes; the UI pulls immediately. Interval polling remains as fallback.
- Optional webhooks: get a JSON ping when a port starts being used or two Compose projects collide (`WEBHOOK_URL`, `WEBHOOK_EVENTS`, `WEBHOOK_SECRET`). Fire-and-forget; failures show up as degradations.
- Free-port planner: a new toolbar action finds the largest contiguous free runs in the current range and can reserve one for you (`GET /api/free-runs`).
- Name ports with labels: `port-light.port.<container-or-host-port>.name` (and `.category`) on a container override the known-services name for its published rows.
- `GET /api/metrics` (opt-in via `METRICS_ENABLED=1`): Prometheus text exposition of used / configured / free counts, hidden ports, degradation count, and Compose file stats. Aggregates only; Basic Auth applies.
- Degraded scans are no longer silent: when Docker is unreachable, the listen table is untrusted, `ss` fails, or the data file is quarantined, one line goes to the log (`PORT_LIGHT_LOG_LEVEL`, default `warning`) and the newest events appear in `/api/health` under `degradations`.
- When a background rebuild runs long (slow `ss`, big Compose tree), polls now get the last good occupancy snapshot with `summary.stale: true` instead of blocking; stale results are never cached.
- Agent-facing port suggestions: `GET /api/ports/suggest` hands out genuinely free ports with optional reservation, label, expiring leases (`ttl`), and `scope=all` across configured peers. Setting `AGENT_TOKEN` requires a matching `X-Agent-Token` header.
- Automation integrations: a dependency-free MCP stdio server (`mcp/server.py`, six tools), an agent skill under `skills/port-light/`, and reference docs in `docs/integrations.md`. Settings → Advanced shows what the instance currently exposes (`/api/meta.automation`).

### Changed

- The frontend is now a set of native ES modules under `/static/js/` (state store, data layer, router, grid, detail drawer, settings), with `app.js` reduced to boot and event wiring. Module chunks revalidate (`Cache-Control: no-cache`); classic assets keep their immutable cache and `?v=` busting. Still no build step.
- IP-address and protocol normalization helpers from the four scanners are consolidated in `backend/netaddr.py`. No behavior changes.
- Occupancy classification moved from `backend/main.py` into `backend/classification.py`; Docker publish rows are typed (`PortMapping`) and grid rows documented as `OccupancyRow`. No API or behavior changes.
- The repo now has a `.gitignore` (bytecode, virtualenvs, `.env`, `data/`, `custom_ports.json`).
- The frontend modules are covered by a stdlib-only test suite (`node --test`, no npm) and CI additionally runs Python 3.13.

### Fixed

- A Compose file that fails to parse (bad YAML, unreadable, not a mapping) no longer vanishes from the map: the stack is flagged like any other `compose_incomplete` case (amber pill) and reported in `/api/health` degradations. Empty YAML documents still parse as no services.
- Compose files are loaded once per scan instead of up to three times (include discovery, macvlan names, service parsing).
- Bare-metal installs never saw listening ports: the `ss` invocation carried a help flag that exits 0 with usage text, which was mistaken for an idle machine. The flag is gone, and unparseable `ss` output now falls through to the next variant instead of counting as "nothing listening".
- A webhook URL with embedded credentials is now ignored (same policy as peer URLs), and failed deliveries report only the hostname.
- The first degradation report after startup is no longer swallowed when the monotonic clock is younger than the repeat-suppression window (fresh containers and CI machines).

## 0.6.0 — 2026-08-16

### Added

- One UI can show occupancy maps from other Port-Light instances (LAN / Tailscale). Each machine still scans itself; this instance only pulls `GET /api/ports` and `/api/health`. Desktop uses side-by-side columns; under 900px a host switcher shows one map. Search, sort, range, and kind chips apply to every map. Hide, labels, and add stay on this machine. Configure peers on Settings → Occupancy, or `PORT_LIGHT_PEERS` when settings are env-locked. Older releases that already serve `/api/ports` work as peers. A Docker bridge hub often cannot reach Tailscale `100.x` — use a LAN IP, or `network_mode: host` on the hub.
- A failed host column can be retried without waiting for the next occupancy poll.

### Changed

- `GET /api/hosts` returns the saved peer username so Settings can refill the field. The password is still never echoed.

### Fixed

- Leaving Settings with unsaved changes prompts on the logo click (and Esc) before the hash changes, so a suppressed `confirm` during `hashchange` cannot trap you on the page.
- An unwritable data directory returns 500 with `permission denied` instead of a tempfile errno.

## 0.5.5 — 2026-08-16

### Changed

- Settings splits into Appearance, Occupancy, and Advanced, so theme palettes are not on the same scroll as Compose scan depth and host paths.
- FastAPI / Pydantic / pytest minimums, and GitHub Actions `checkout` / `setup-python` / QEMU / docker-login majors.
- Occupancy Settings shows the toolbar range (the range the map is using). Turning auto-refresh off dims the interval field.
- Known-port table: Transmission on 9091 (access), plus n8n, Proxmox, wg-easy, Frigate, Calibre-Web, Homebridge, Komga, Actual, Technitium, Z-Wave JS UI.
- Compose scan walks directories named `data` (only `.git` / `node_modules` / venvs stay skipped).

### Fixed

- Discarding Settings reverts the live theme, density, and language preview.
- Typing a manual-port label in the detail drawer is no longer wiped by auto-refresh.
- Compose `${VAR}` interpolation no longer reads Port-Light's own environment (`HOSTNAME`, `PORT_RANGE_*`, …).
- Turning on the Hidden chip no longer renders an empty grid before the include-hidden payload arrives.
- Switching language on Settings updates the unsaved status string; the host "settings source" row is translated.
- Unquoted Compose `22:22` / `8080:22` is occupancy, not a dropped YAML 1.1 sexagesimal integer.
- `include.project_directory` re-roots `.env` / `env_file`; nested includes inherit the parent interpolation env.
- `compose.override.yml` `ports: !reset` replaces the base file’s ports instead of unioning both.
- `ss` uses `-n` so `:ssh` / `:http` still count; a loopback listen no longer hides a public Docker/Compose bind.
- Traefik/homepage URLs attach to the app publish, not every sidecar; unmatched `VIRTUAL_PORT` / label port fall through to 80/443/8080/8443 or the lowest mapping; Caddy `*.home.arpa` is not a live link.
- Unraid `[PORT:n]` uses the host mapping when the container port was remapped; it is dropped when mappings are known and `n` is not published.
- Host-network inode-only Traefik/homepage URLs stay on 80/443/8080/8443 (or the lowest attributed listen), not every sidecar.
- Included Compose files are not scanned as their own stack; `external: true` networks with a static IP plus `expose` are LAN occupancy when Docker is down.
- Host-network Compose leftover `ports:` is ignored (Docker ignores it); `expose:` still counts.
- `container:` joiners match a unique id prefix of at least 12 characters, not a short prefix of another container.
- Sibling Compose includes share macvlan names; `path: [base, overlay]` overlays instead of unioning both files' ports.
- Including a shared `compose.yml` no longer auto-merges that directory's `compose.override.yml`.
- `network_mode: !reset` returns to bridge, so leftover host `expose` is not occupancy.
- Empty Docker `HostIp` is dual-stack (`0.0.0.0` and `::`); a stopped mixed static+ephemeral publish still recalls the last ephemeral host port.
- Stopped macvlan still uses `IPAMConfig` static IPs; Homepage/wud `{{hostname}}` hrefs are not occupancy links.
- Traefik URLs follow the Host() router's service port, not the first `loadbalancer.server.port`.
- A bridge container's PID no longer steals a host listen socket.
- Discarding a detail hide/label error no longer lights the map-sync banner; empty-grid search keeps focus in the search box.
- The Hidden chip is restored with `include_hidden`; unlock failures stay in the modal instead of sticking a password in session storage; unlock does not race the occupancy poll.
- Desktop detail no longer traps Tab; Close is auto-focused only when the drawer opens; numeric search neighbors are capped; settings segmented controls and the port-copy control have accessible names.
- Turning the Hidden eye on persists `showHidden`; a manual-port label draft restores only for the same port; the last-refresh time and sync-error banner remeasure the sticky header.
- Hidden unlock aborts the in-flight occupancy poll so a locked response cannot clobber the unlocked map; focus returns to the Hidden chip or count that opened the modal.
- Copy-on-click toasts the cell (or detail port) still on screen; the grid is `aria-busy` on every poll; re-picking the current language is not unsaved; the unlock password field is required.
- `include.project_directory` also re-roots nested `include:` paths and `extends.file`, not only `.env`.
- `services: !override` / `!reset` replaces the mapping (or that service); overlay / extends copies `deploy.ports` the same way as `ports`.
- Unquoted Compose `.env` values drop inline comments (`8080 # public`); interpolation understands `:+` / `+` and nested `${}`.
- A mobile detail overlay marks the skip link, header, and views `inert`, traps Tab even before focus is inside the panel, and drops both after a resize to desktop.
- Hiding a free port still occupies the map when included: `include_hidden` emits `status: "free"`; `GET /api/ports/{N}` no longer 404s that cell; numeric search uses `hidden_occupancy` instead of painting every hidden stub configured.
- Locked numeric search no longer paints an unknown hit as configured; opening a 404 only claims a locked hide when hidden ports are actually withheld.
- Stopped Docker last-publish recalls evict the oldest mapping, not the whole cache.
- Locked numeric search probes `GET /api/ports/{N}` so a withheld occupied port is not painted free; a real free hit still goes green after the lookup.
- `extends.file` interpolates the base file’s top-level `env_file`, not only its sibling `.env`.
- Occupancy `If-None-Match` accepts weak tags and lists; container/compose rows are sorted so a Docker reorder is still `304`.
- Compose `${VAR:?}` / `${VAR?}`, a missing required `env_file`, a missing `include` path, or a missing `extends.file` no longer keep the rest of that project’s declared ports (Compose would refuse the stack). `summary.compose_incomplete` turns the Compose pill amber. `required: false` env files may be absent; `${VAR:?}` still interpolates from top-level `env_file` after `.env`.

### Performance

- Free-count is occupancy-in-range, not a loop over every port in the toolbar range.
- Docker / listen / Compose scans are reused for a couple of seconds so `#/port/N` does not walk the trees again after the grid poll.
- Unchanged occupancy polls return `304` without classifying or hashing the map again.
- Known-port prefetch during numeric search coalesces to one grid render.
- Concurrent occupancy polls share one in-flight Docker / listen / Compose scan.

## 0.5.4 — 2026-08-15

### Added

- Public bind-scope filter. Access ports listening on all interfaces get a warning in the detail drawer.
- Manual ports can be renamed from the detail drawer.
- Last successful refresh time next to the scanner dots.
- Arrow keys on occupancy counts and kind chips.
- Confirm before deleting a manual port.
- Distinct known-port names (iSCSI, SIP, RTSP, Jaeger, OTLP, Zabbix, Git daemon, …).
- Host-network Compose `expose:` entries count as host ports.
- `Content-Security-Policy` on every response.
- `GET /api/ports` supports `If-None-Match` / `304`.
- Click the port number in the detail drawer to copy it.
- Unraid `net.unraid.docker.webui` labels become detail-panel URLs.
- Compose top-level `name:` is the project label in the detail drawer (conflicts still key off the folder).
- Ctrl/Cmd+S saves the settings page.
- Compose `extends` (same file or `file:`) contributes `ports` / host-network `expose`.
- Text search looks at the whole occupancy map, not only the toolbar range.
- nginx-proxy `VIRTUAL_HOST` / `LETSENCRYPT_HOST` (env or labels) become detail-panel URLs. `VIRTUAL_PORT` picks which published mapping gets the link (default 80; a lone mismatched publish still gets it).
- Traefik `HostHeader()`.
- `/proc` inode walk fills process names when `ss` is not used (the usual `/host/proc` mount).
- `compose.prod.yml` / `docker-compose*.yml` variants are scanned, not only the default filenames.
- Swarm `deploy.ports` contribute declared host ports.
- macvlan/ipvlan container IPs plus `EXPOSE` show as LAN occupancy.
- Swarm `mode: host` without `published` uses `target` as the host port.
- `/api/health` reports `listen_source` (`host_proc` / `ss` / `proc`). The Host pill is green for host-network `ss`, not only `/host/proc`.
- Occupancy summary includes `compose_truncated` / `compose_files` when the Compose walk hits `COMPOSE_SCAN_MAX_FILES`, and `hidden_ports` (unlocked) so numeric search does not paint a hidden cell free.
- Named appearance presets on Settings (Gruvbox, Catppuccin, Nord, Dracula, Tokyo Night, One Dark, Solarized, Everforest, Rosé Pine, Kanagawa) with a swatch picker.
- Compose macvlan/ipvlan `ipv4_address` / `ipv6_address` plus `expose` / published `target` count as LAN occupancy when Docker is not running. Driver definitions may live in `include:` / `extends.file`; child `networks:` overlays the parent.
- `network_mode: ns:/proc/1/ns/net` (and a `ns:` path that is the host pid 1 netns) is treated like `host`.

### Fixed

- Compose `127.0.0.1:host:container` (and long-syntax `host_ip`) now feeds bind scope when nothing is listening yet.
- `${VAR:-default}` treats an empty value as unset. `${VAR-default}` is also understood.
- A truncated `port_light.json` is moved aside instead of being overwritten with an empty store.
- Two Compose bind IPs on the same service were collapsed to one row.
- Traefik `Host(\`a\`, \`b\`)` only kept the first hostname.
- A string value in `custom_ports.json` no longer blows up the known-port table.
- Tab trap in the detail drawer skipped `position: fixed` controls.
- Sibling `.env` lines that start with `export ` are understood.
- Compose `include.env_file` interpolates the included file.
- `traefik.enable=false` no longer contributes Traefik Host URLs.
- Long-syntax `published: "5353/udp"` was dropped instead of recorded as UDP.
- A UTF-8 BOM on `.env` hid `export KEY=` lines.
- Guessed access URLs used `localhost` even when the port was bound to a LAN address only.
- Included Compose files were tagged as their own folder, so two stacks sharing `include:` looked like a conflict with `shared/` — or the second stack lost the ports entirely.
- Host port `0` / `65536` publishes (ephemeral / junk) no longer become occupancy cells.
- `ss` without `-H` (BusyBox / older iproute) is used when `ss -tulnpH` fails.
- Compose `$$` is a literal `$`. A UTF-8 BOM no longer hides the compose file.
- A junk row in `manual_ports` / `hidden_ports` no longer 500s occupancy.
- Binding a specific public IP is `public` (the Public chip and access-port warning), not LAN. Guessed links stay on localhost unless `URL_HOST` is set.
- A stopped container still claimed its published port as configured (it was painted free with the container name).
- Two Docker bind IPs on the same container were collapsed; bind scope ignored Docker `HostIp`.
- Compose `include.path` as a list crashed the scan.
- Caddy `caddy: reverse_proxy …` was turned into a fake `https://reverse_proxy` link.
- IPv6 zone ids (`fe80::1%eth0`) and hex-mapped `::ffff:c0a8:10a` now normalize.
- Compose top-level `env_file` (and `path:` mappings) interpolates published ports.
- `ss` `tcp4` / `udp4` rows were dropped.
- Custom `is_access_port: "false"` was treated as true.
- Free occupancy cells and `#/port/N` never opened the detail drawer (only used/configured rows were in the payload).
- Legend `used` / `configured` counted every occupied port while `free` used the toolbar range.
- A junk row in `manual_ports` still 500'd POST/PATCH/DELETE.
- Host scanner health was green whenever `/proc/net/tcp` existed, including a bridge container without `/host/proc`.
- A hidden port still counted as free in the legend.
- A paused or restarting container was painted configured (the host bind is still held).
- Two folders named `wiki` were treated as one Compose project (conflicts dropped, ports lost).
- A non-UTF-8 compose file aborted the whole Compose scan.
- `${VAR:?err}` / `${VAR?err}` never interpolated, so declared ports vanished.
- YAML `8080: 80` mapping entries and `8080:80/TCP` were dropped.
- `network_mode: service:…` / `container:…` still counted that service’s `ports:` as host occupancy.
- `extends.file` ignored the base file’s sibling `.env`.
- Compose `!reset` tags made `yaml.safe_load` discard the file.
- Host-network `EXPOSE` painted running containers green even when nothing listened.
- An empty `/host/proc` listen table fell through into the container netns.
- Traefik `entrypoints=web` links were always `https://`; `HostRegexp` templates became fake URLs; Caddy `http://host` was dropped.
- Traefik/Caddy links were copied onto every published port of the container.
- Link-local and `172.17.0.0/16` binds were treated as LAN for guessed URLs.
- Hidden occupied ports opened the detail drawer as free.
- Tab from the occupancy grid was trapped in the drawer on desktop.
- Escape cleared search before closing the detail drawer.
- Numeric search neighbors could exceed port 65535; digit search ignored kind filters for occupied neighbors.
- The hidden-port legend counted ports outside the toolbar range.
- Docker IPv6 `HostIp` values like `[::1]` were stored with brackets.
- Compose `include: ../shared/*.yml` globs were ignored.
- A TCP listen hid UDP occupancy on the same port (Docker publish or Compose).
- `network_mode: container:…` joiners of a host-network container were not attributed.
- Host-network worker/child PIDs were missed (only the container init pid).
- macvlan/ipvlan IPv6 (`GlobalIPv6Address`) occupancy was dropped.
- `GET /api/ports/{N}` synthesized a free stub for a hidden occupied port when `include_hidden` was off.
- Saving settings reset the toolbar range even when the form range was unchanged.
- The Running chip ignored paused/restarting containers (the bind is still held).
- Created/dead/removing containers were labeled exited in the detail drawer.
- Compose `extends` plus `ports: !reset` still inherited the parent’s ports.
- `/host/proc` without `1/net/tcp` fell through to `ss` / container `/proc`.
- YAML `{53/udp: 53}` and short `53/udp:53` were dropped.
- Swarm `mode: host` with `published: 0` / `""` dropped the mapping instead of using `target`.
- A dual-stack publish with an empty HostPort on one family lost that bind IP.
- macvlan/ipvlan secondary addresses and `IPv6Address` prefixes were ignored.
- Numeric search painted locked-hidden hits as free, and kept synthetic free neighbors under the in-use filter.
- `#/port/N` flashed a free detail before the lookup returned.
- Showing hidden ports hid occupancy cells (`.hidden { display: none }` also matched `.port-cell.hidden`).
- `ss` `tcp6` / `udp6` `*:443` was stored as IPv4 `0.0.0.0`.
- Long-syntax `target: "53/udp"` (and `mode: host` with that target) was dropped.
- SCTP occupancy was treated as TCP, so it conflicted with a TCP bind on the same port.
- A bridge container without a trusted host listen table still scanned `ss` / local `/proc` and painted container listeners as host used.
- Occupancy could tear across two unlocked reads of `port_light.json` (manuals vs hidden).
- `https://nas:192.168.1.10`-shaped extra_hosts values survived as detail URLs.
- `docker_available` stayed green after the Docker client failed to open.
- Tailscale 41641 had no access chip (unlike WireGuard) and would have guessed `http://`.

### Changed

- Occupancy JSON is written atomically (tempfile + `os.replace`).
- Compose conflict requires overlapping bind addresses **and** the same protocol, not just the same port number in two projects.
- `URL_HOST` may be an IPv6 address; guessed links use `[addr]:port`.

## 0.5.3 — 2026-08-15

### Added

- Keyboard navigation on the language dropdown (arrows, Home, End).
- Arrow keys, Home, End, Page Up, and Page Down move between occupancy cards; Enter still opens the detail panel. Closing the drawer returns focus to that card.
- Known-port names for Nginx Proxy Manager (81), SNMP, LDAP, syslog, and Homarr.
- Almost-unique defaults: TeamSpeak, Palworld, etcd, ZooKeeper, UniFi 8843/8880, Winbox, RouterOS API, Tor SOCKS, Privoxy, Transmission peer, Tailscale, SNMP trap, Plex GDM.
- `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy` on every response. API JSON is `Cache-Control: no-store`. `/static/` is a long-lived immutable cache (`?v=` busts).
- Skip link to the port grid.
- README screenshot of the redesigned grid chrome.

### Fixed

- UDP and localhost filter chips were not wired to the locale files.
- HTML entry (`/`) is served with `Cache-Control: no-cache` so a new `?v=` actually loads.
- Unlock-hidden used the wrong poll payload after refresh abort landed, so a wrong password could not be detected.
- Adding a manual port now keeps the dialog open and shows an error if the request fails.
- Hide, unhide, and delete failures show in the detail panel, not only next to the scanner dots.
- Duplicate `settings.discard` key in the English locale file.

### Changed

- Name sort uses the active UI locale. Occupancy counts expose a group label. Empty-grid copy distinguishes “nothing in range” from “filters hid everything”.
- In-flight `/api/ports` requests are aborted on the next refresh so a slow poll cannot overwrite a newer grid. A failed refresh shows a short error next to the scanner dots instead of looking idle.
- Docker socket client is reused across health checks and scans (5s availability cache). A successful container list marks Docker available so the next health check can skip `ping`. Health runs after the occupancy scan on the same tick.
- Occupancy polling does not rebuild the grid when the payload is unchanged, and pauses while the tab is hidden.
- Card labels wrap to two lines instead of a single ellipsis. The full string stays on `title`.
- Compose override files (`compose.override.yml`, `docker-compose.override.yml`) are scanned. A Compose conflict is two **projects** claiming the same host port, not two files in the same stack.
- Loopback bind scope covers the whole `127.0.0.0/8` range and IPv4-mapped IPv6.
- Detail-panel links are http(s) only.
- Escape clears the search box before closing the detail drawer.
- CI runs on every push (Python 3.11 and 3.12). Ruff floor is `>=0.16.2`.

## 0.5.2 — 2026-08-15

### Added

- Light theme (Settings: System / Dark / Light). Follows `prefers-color-scheme` when set to System.
- Settings page (`#/settings`) with the same knobs as Compose env. Saves persist in the data volume; `PORT_LIGHT_SETTINGS_SOURCE=env` locks the UI.
- `GET`/`PUT /api/settings`, `GET /api/ports/{port}`, `GET`/`PATCH /api/manual-ports`, `GET /api/known-ports/{port}`.
- Guessed access URLs can use `URL_HOST` / `URL_SCHEME`.
- Project icon (favicon, header, README).
- UDP and localhost filter chips; protocol badge on non-TCP cards.
- `/` focuses search; Escape closes the detail panel and modals.
- Dockerfile `HEALTHCHECK` against `/api/health` (no extra packages).
- `ruff` in CI; Dependabot for pip and GitHub Actions.
- GitHub Release notes from `CHANGELOG.md` when a `v*` tag is pushed.
- Unraid Docker template at `deploy/unraid/port-light.xml`.
- More built-in names (Home Assistant, Plex, Immich, Lidarr, Prowlarr 9696, Port-Light 2100, …).
- UI language: English, Simplified Chinese, Traditional Chinese, Japanese (`LOCALE` / Settings). `frontend/locales/` is the source; files share one key tree.

### Changed

- Grid chrome is one sticky header: search in the top row, occupancy counts as the status filter, kind chips and range on the second row. Settings is a gear, not a competing nav tab.
- Language picker is a dropdown: native name on the first line, current UI language on the second.
- Homelab-oriented labels on a few shared ports (3000 Grafana, 5000 Synology DSM, 9000 Portainer HTTP, 8787 Readarr). Override with `custom_ports.json` if you use them for something else.

## 0.5.0 — 2026-08-13

### Added

- Optional HTTP Basic Auth (`AUTH_USER` / `AUTH_PASSWORD`). `/api/health` stays unauthenticated.
- `HIDDEN_UNLOCK_PASSWORD` and the `X-Hidden-Unlock` header. When either auth or this secret is set, hidden ports are omitted from the API until unlocked.
- `GET /api/meta`; `/api/health` reports whether proc, Docker, and the compose dir are available.
- UDP sockets (bound/listening) alongside TCP.
- Bind scope on each port: `public`, `lan`, or `localhost`.
- Host-network container names via `/proc/<pid>/fd` socket inodes (`ExposedPorts` as fallback).
- Compose: nested trees (`COMPOSE_SCAN_DEPTH` / `COMPOSE_SCAN_MAX_FILES`), `include:`, published port ranges, `${VAR:-default}`.
- Detail-panel links: guessed `http(s)://` URLs for access ports, plus Traefik `Host()` and Caddy labels.
- Multi-arch images also pushed to GHCR (`ghcr.io/stepaniah/port-light`).
- CI workflow (`pytest` on push/PR).

### Changed

- Hide-from-grid is a display filter unless server secrets are configured (no client-side password hash).
- Machine selector removed. Multi-host scanning is not implemented.
- Port range changes refetch from the API.
- `custom_ports.json` is cached by mtime.
- Dev Compose file no longer adds `NET_ADMIN` by default.

### Documentation

- English and Chinese README, architecture, deployment, security, contributing, and a short roadmap.
- GitHub issue and pull request templates.

## 0.4.0 — 2026-07-25

### Added

- Copy port number on card click (settings toggle).
- Copy confirmation on the card.
- Product Hunt badge and Ko-fi funding link.

### Changed

- README deployment section for the published Docker Hub image.

## 0.3.0 — 2026-07-22

### Fixed

- Bridge containers read `/host/proc/1/net/tcp` (host PID 1 netns) instead of the container namespace.

## 0.2.0 — 2026-07-22

### Changed

- Left `network_mode: host` for a bridge container plus host `/proc` (nsenter path, replaced in 0.3.0).

## 0.1.0 — 2026-07-21

### Added

- GitHub Actions multi-arch publish to Docker Hub.
- First tagged image `stepaniah/port-light:v0.1.0`.
