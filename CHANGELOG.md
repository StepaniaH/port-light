# Changelog

Versions follow git tags and image tags (`stepaniah/port-light:vX.Y.Z`).

## Unreleased

### Changed

- Settings splits into Appearance, Occupancy, and Advanced, so theme palettes are not on the same scroll as Compose scan depth and host paths.
- FastAPI / Pydantic / pytest minimums, and GitHub Actions `checkout` / `setup-python` / QEMU / docker-login majors.
- Occupancy Settings shows the toolbar range (the range the map is using). Turning auto-refresh off dims the interval field.
- Known-port table: Transmission on 9091 (access), plus n8n, Proxmox, wg-easy, Frigate, Calibre-Web, Homebridge, Komga, Actual, Technitium, Z-Wave JS UI.

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
- Traefik/homepage URLs attach to the app publish, not every sidecar; Caddy `*.home.arpa` is not a live link.
- Unraid `[PORT:n]` uses the host mapping when the container port was remapped.
- Discarding a detail hide/label error no longer lights the map-sync banner; empty-grid search keeps focus in the search box.
- The Hidden chip is restored with `include_hidden`; unlock failures stay in the modal instead of sticking a password in session storage.
- Desktop detail no longer traps Tab; numeric search neighbors are capped; settings segmented controls and the port-copy control have accessible names.

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
