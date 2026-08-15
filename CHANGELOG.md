# Changelog

Versions follow git tags and image tags (`stepaniah/port-light:vX.Y.Z`).

## Unreleased

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
