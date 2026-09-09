<p align="center">
  <img src="docs/icon.png" width="96" height="96" alt="Port-Light">
</p>

# Port-Light

A self-hosted dashboard for host port occupancy. It combines host listeners, Docker mappings, and Compose declarations in a traffic-light grid.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/port-light?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/port-light)
[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/port-light)](https://hub.docker.com/r/stepaniah/port-light)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/port-light?label=version)](https://github.com/StepaniaH/port-light/tags)

[English](README.md) · [简体中文](README.zh-CN.md)

[Quick start](#quick-start) · [Unraid](docs/deployment.md#unraid) · [Documentation](docs/port-management.md)

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Port-Light dashboard showing two adaptive host boards">
</p>

## Quick start

Image: [`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light) (`linux/amd64`, `linux/arm64`). Also published to GHCR on tagged releases (`ghcr.io/stepaniah/port-light`). Pin a version tag or digest for reproducible deployments.

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.8.2
    container_name: port-light
    restart: unless-stopped
    ports:
      - "2100:2100"
    volumes:
      - /path/to/your/compose-stacks:/compose:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/host/proc:ro
      - ./data:/data
    environment:
      COMPOSE_SCAN_DIR: /compose
```

```bash
mkdir -p data
docker compose up -d
```

Open `http://localhost:2100`.

Mounting `/var/run/docker.sock` grants broad Docker API access; a read-only mount does not restrict API operations. Prefer a [socket proxy](docs/deployment.md#docker-socket-proxy) if the UI might be reachable by anyone you do not fully trust. More install options (Unraid template, Podman, reverse proxy, build from source): [docs/deployment.md](docs/deployment.md).

`NET_ADMIN` is **not required** in the usual bridge setup. It only helps the `ss` fallback on bare metal.

All three scanners are enabled by default. If an enabled source fails, Compose scanning is incomplete, or a snapshot expires, the map shows a warning and unconfirmed ports remain unknown. Free-port planning and batch reservations return `503`. For a native installation without Docker, set `PORT_LIGHT_SCANNERS=listen,compose`; use `listen` to scan only host listeners. Occupancy in disabled sources is outside the checks.

## What it does

Port-Light merges three local sources into one grid:

| Source | What you learn |
|--------|----------------|
| Host listen tables (`/proc` or `ss`) | TCP/UDP ports that are actually bound |
| Docker API | Container name, status, image, published mappings |
| Compose files | Ports that are **declared** even if the stack is stopped |

| Color | State | Meaning |
|-------|-------|---------|
| Blue | In use | Something is listening, or a container is running with that mapping |
| Amber | Configured | Declared in Compose (or added manually), but nothing is listening |
| Green | Free | Shown when you search for a port number — nearby unused ports are offered as alternatives |

The default grid lists **occupied and declared** ports only. It does not paint every unused port from 1–9999.

This is a **port occupancy map**, not a container manager. It does not start/stop containers, tail logs, or replace Portainer.

## Features

- Search by port, service, project, process, or bind address; filter and sort occupied and declared ports.
- Group Compose ports by project or service. Expand contiguous ranges to inspect individual ports.
- Review Compose conflicts, choose an available replacement, and copy a mapping for the selected service.
- Manage manual entries and reservations, including labels, expiry filters, and release commands for reservations created in the current browser tab.
- Define named port ranges, reserve through a selected rule, and flag declarations outside an assigned project range.
- View up to 32 other Port-Light instances. Each host scans its own listeners, Docker API, and Compose files.
- Use the dependency-free CLI or MCP server for checks and recoverable reservations.
- Configure appearance, seven UI languages, history, webhooks, and optional Basic Auth. Setup / Doctor provides a sanitized diagnostic report.

Grouping, management pages, and named port rules are available in the development branch after v0.8.2. To try them, [run from source](CONTRIBUTING.md#development).

See the [port management guide](docs/port-management.md), [CLI documentation](docs/cli.md), and [API reference](docs/integrations.md).

### Limitations

- **LAN tool.** There is no login unless you set `AUTH_USER` and `AUTH_PASSWORD`. Put it behind a reverse proxy or keep it off the public internet. See [SECURITY.md](SECURITY.md).
- **Hide from grid** is a display filter. It becomes an API gate only when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set.
- **Multi-host is a read-only viewer.** Each machine still runs Port-Light. One UI can pull up to 32 peers over LAN or Tailscale (Settings → Occupancy). The refresh control shows an advisory capacity for the chosen interval; requests are still internally bounded if you exceed it. Do not expose port 2100 to the public internet. The hub fetches those URLs itself; a Docker bridge container often cannot reach Tailscale `100.x` — use a LAN IP, or `network_mode: host` on the hub.
- Process names come from `/host/proc` (inode → `comm`) when that mount is present — the usual image. Without it, the grid shows Docker container names. `ss -tlnp` names still need a host-network / bare-metal path.
- Host-network containers are matched via `/proc/<pid>/fd` socket inodes when `/host/proc` is mounted; otherwise they fall back to `ExposedPorts`.

Roadmap and architecture: [docs/roadmap.md](docs/roadmap.md), [docs/architecture.md](docs/architecture.md). Command line: [docs/cli.md](docs/cli.md). API and MCP integrations: [docs/integrations.md](docs/integrations.md).

If an occupancy warning persists after upgrading, hover over its information icon, or focus or select the warning, for scanner-specific guidance. See the [troubleshooting and upgrade guide](docs/troubleshooting.md#occupancy-scan-warning) for configuration changes that an image update cannot apply.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT_LIGHT_SCANNERS` | `listen,docker,compose` | Enabled sources, comma-separated; omitted sources are explicitly disabled. Select at least one. Also configurable under Settings → Occupancy. |
| `PORT_LIGHT_SCAN_TIMEOUT_S` | `10` | Background refresh deadline in seconds (1–60). A timeout retains the snapshot and marks it stale. Env only. |
| `COMPOSE_SCAN_DIR` | `/compose` | Directory to scan for `compose.y*ml` / `docker-compose.y*ml` (env only) |
| `COMPOSE_SCAN_DEPTH` | `4` | Max subdirectory depth under the scan dir |
| `COMPOSE_SCAN_EXCLUDE_DIRS` | unset | Comma-separated folder names to skip during automatic Compose discovery. Explicit `include` / `extends` files are still read. |
| `COMPOSE_SCAN_MAX_FILES` | `400` | Cap on compose files parsed per refresh |
| `PORT_RANGE_START` | `1` | Start of the range used for the **free** summary count |
| `PORT_RANGE_END` | `9999` | End of that range (does not fill the grid with green cells) |
| `PORT_LIGHT_DATA_DIR` | `/data` | Manual ports, hidden list, and saved settings (JSON) |
| `PORT_LIGHT_PORT` | `2100` | Port uvicorn listens on inside the container; surfaced via `/api/meta` so the Automation panel builds MCP snippets without hardcoding |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | Extra / overriding port names (env only) |
| `THEME_MODE` | `system` | `system` / `dark` / `light` — the resolved brightness; palettes follow it |
| `THEME_PALETTE` | built-in | Color family layered on top: `gruvbox`, `catppuccin`, `solarized`, `nord`, `dracula`, `tokyo-night`, `one-dark`, `everforest`, `rose-pine`, `kanagawa`. Empty uses the built-in colors. |
| `LOCALE` | `auto` | `auto` / `en` / `fr` / `de` / `es` / `zh-CN` / `zh-TW` / `ja`. Auto follows the browser. |
| `GRID_DENSITY` | `standard` | Card-density preset: `loose`, `standard`, or `compact`. A stored legacy `comfortable` behaves as `standard`. |
| `SHOW_BIND_ADDRESSES` | `false` | Show compact bind-address summaries on occupied cards. |
| `SHOW_BIND_IPV4` | `true` | Include IPv4 addresses when card bind summaries are enabled. |
| `SHOW_BIND_IPV6` | `true` | Include IPv6 addresses when card bind summaries are enabled. |
| `REFRESH_MS` | `5000` | Dashboard polling interval (1,000–300,000 ms). Settings offers 5s–5m choices and shows the recommended peer capacity. The local background scanner remains capped at a 30s interval. |
| `PORT_LIGHT_HOST_LAYOUT` | `waterfall` | Responsive waterfall showing all machines, or `tabs` showing one machine at a time. Both desktop and mobile honor this choice. |
| `URL_HOST` | empty | Hostname used in guessed `http(s)://` links |
| `URL_SCHEME` | `auto` | `auto` / `http` / `https` |
| `AUTH_USER` / `AUTH_PASSWORD` | unset | Optional HTTP Basic Auth for the UI and API. `/api/health` stays open. Env only. Both values must be nonempty; partial/blank configuration returns 503. Unset both to disable. |
| `HIDDEN_UNLOCK_PASSWORD` | unset | If set (or if Basic Auth is set), hidden-from-grid ports are withheld from the API until you unlock. Env only. |
| `PORT_LIGHT_SETTINGS_SOURCE` | `auto` | `auto`: Web UI values override env defaults. `env`: Compose is the only source and the Settings page is read-only. |
| `PORT_LIGHT_HOST_NAME` | hostname | Label for this machine when other occupancy maps are shown. Also configurable under Settings → Occupancy. |
| `PORT_LIGHT_HOST_DESCRIPTION` | empty | Optional plain-text note under this machine's name in the multi-host view, up to 120 characters. |
| `PORT_LIGHT_PEERS` | unset | JSON array of up to 32 `{name, url, description?, username?, password?}` entries, used when the data file has no `peers` key or when `PORT_LIGHT_SETTINGS_SOURCE=env`. Descriptions are optional plain text, up to 120 characters. Same lock as Settings. |
| `PORT_LIGHT_LOG_LEVEL` | `warning` | Backend log level (`debug` / `info` / `warning` / `error`). Degraded scans (Docker unreachable, unreadable Compose file, …) log one line and show up in `/api/health` under `degradations`. Env-only. |
| `WEBHOOK_URL` | unset | Opt-in webhook target (`http(s)` only). With `WEBHOOK_EVENTS=new_listener,conflict`, Port-Light POSTs `{event, port}` JSON (fire-and-forget). |
| `WEBHOOK_SECRET` | unset | Sent as `X-Port-Light-Secret`. |
| `WEBHOOK_EVENTS` | unset | Comma list: `new_listener`, `conflict`. |
| `METRICS_ENABLED` | unset | Set to `1` to expose `GET /api/metrics` (Prometheus text format: used/configured/free counts, hidden, degradations, Compose files). Aggregates only — never ports or names. Env-only. |
| `AGENT_TOKEN` | unset | When set, suggestions and reservation creation/recovery require a matching `X-Agent-Token` header. Env-only. |

Most options (except timeout, paths, and secrets) can also be changed on **Settings** in the UI. This includes the local machine name, scanner selection, Compose discovery options, and peers. Changes save automatically into `/data/port_light.json`; only changed fields are submitted, and a saved override can be restored to its inherited environment/default value. OpenAPI is at `/docs`.

The status line reports pending, saved, or failed changes. Complete each machine's name and URL before leaving the page. Creating, importing, or deleting a custom palette remains an explicit action in the palette editor.

Copy [custom_ports.example.json](custom_ports.example.json) to `custom_ports.json` (gitignored). Categories: `system`, `web`, `database`, `message`, `proxy`, `vpn`, `selfhosted`, `dev`, `infra`, `gaming`.

If you bind-mount `custom_ports.json` in Compose, create the **file** on the host first. Mounting a missing path makes Docker create a directory and the app will fail to read it.

If an existing `port_light.json` is unreadable, malformed, or contains invalid records, affected APIs return `503` and leave it intact for repair. Only a missing file initializes an empty store.

## Privacy

- No telemetry or analytics.
- The app makes no outbound HTTP requests unless you configure peer occupancy pulls or a webhook (`WEBHOOK_URL`, `{event, port}` JSON only).
- Scan data is stored locally and served to dashboard and API clients. Configured hubs receive their peers' occupancy responses. Enable authentication to restrict who can read this data.
- Bind addresses are already present in the API and port details. Enabling card summaries makes them more visible in screenshots; review screenshots before sharing them.
- Machine descriptions are plain text visible to anyone with dashboard or API access and may appear in screenshots. Do not include passwords, tokens, or other secrets in them.
- Doctor reports are generated from an explicit aggregate allowlist. They include statuses, counts, safe source enums, and known failure reasons, but omit machine identity, peer details, bind addresses, ports, filesystem paths, environment values, credentials, and degradation scopes. Unknown event sources and reasons are redacted.
- Sibling `.env` files next to Compose stacks are read locally for `${VAR}` substitution and are never uploaded.
- Browser-created reservation keys and release tokens stay in the current tab’s session storage. Copied release commands contain a secret token.
- Port rules and assigned project names are visible to authenticated dashboard/API users, or to anyone who can reach the instance when authentication is disabled.
- Manual labels, port history, agent-call labels, peer settings, and Docker-side CLI release tokens stay in the data volume. Saved peer passwords are stored in `port_light.json`, while CLI tokens use `/data/cli-state`; protect the data volume as you would any other credentials store.

## Tech stack

- Backend: Python 3.11+ (CI covers 3.11–3.13), FastAPI, Uvicorn
- Frontend: static HTML/CSS/JS
- Image: `python:3.12-slim` + `iproute2`

## License

[MIT](LICENSE) © 2026 StepaniaH

[Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Ko-fi](https://ko-fi.com/stepaniah)
