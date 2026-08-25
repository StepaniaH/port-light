<p align="center">
  <img src="docs/icon.png" width="96" height="96" alt="Port-Light">
</p>

# Port-Light

A local web dashboard that shows **which host ports are taken**, as a traffic-light grid. Built for homelabbers who run many Compose stacks and keep losing track of bindings.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/port-light?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/port-light)
[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/port-light)](https://hub.docker.com/r/stepaniah/port-light)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/port-light?label=version)](https://github.com/StepaniaH/port-light/tags)

[English](README.md) · [简体中文](README.zh-CN.md)

<a href="https://www.producthunt.com/products/port-light?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-port-light" target="_blank" rel="noopener noreferrer"><img alt="Port Light - A web dashboard shows your port usage as a traffic-light. | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1203037&theme=light&t=1784992647570"></a>

![Port-Light screenshot](https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/screenshot.png)

Other Port-Light instances on the LAN or Tailscale:

![Two occupancy maps side by side](https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/screenshot-multi-host.png)

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

- Container / service names on the cards
- Search by port number, with nearby free alternatives if it is taken
- Occupancy counts filter in-use / configured; kind chips for running, system, Docker, web, UDP, localhost, public, hidden
- Sort by port, name, or status; clamp the visible range
- Manual entries for things the scanners miss
- Compose conflict warning when two projects publish the same host port on overlapping bind addresses
- Built-in names for common homelab ports (SSH, Jellyfin, Postgres, …), plus a local override file
- 5-second auto-refresh (toggle in settings)
- Copy the port number on click
- One UI can pull occupancy maps from other Port-Light instances (LAN / Tailscale). Each host still scans itself.
- Appearance on Settings: brightness (system / light / dark) and color palette (Gruvbox, Catppuccin, Solarized, Nord, Dracula, Tokyo Night, One Dark, Everforest, Rosé Pine, Kanagawa) are independent controls. Compact grid, UI language (English, 简体中文, 繁體中文, 日本語). Also via Compose env.
- Optional HTTP Basic Auth (`AUTH_USER` / `AUTH_PASSWORD`)
- Annotate ports with labels: `port-light.port.<port>.name` / `.category` in Compose or Docker
- Find free ports: toolbar button (or `GET /api/free-runs?count=N`) returns the largest contiguous free runs in your range, with one-click reservation
- Coding-agent ready: `GET /api/ports/suggest` hands out genuinely free ports (with optional reservation or expiring leases), plus an MCP stdio server and an agent skill under `skills/`
- Local history: port state transitions land in `history.db` inside your data volume (default 7 days; `HISTORY_RETENTION_DAYS=0` disables) — the detail drawer shows recent changes and `GET /api/ports/{n}/history` exposes them
- Optional webhooks: `WEBHOOK_URL` + `WEBHOOK_EVENTS=new_listener,conflict` POST JSON when a port starts being used or two stacks collide
- Instant refresh: open UIs subscribe to `GET /api/events` (SSE) and re-poll the moment occupancy changes, instead of waiting for the next 5s tick
- UDP as well as TCP; bind scope (`0.0.0.0` / localhost / LAN)
- Vanilla HTML/CSS/JS frontend served as native ES modules — no npm, no build step

### Known limits (read before you deploy)

- **LAN tool.** There is no login unless you set `AUTH_USER` and `AUTH_PASSWORD`. Put it behind a reverse proxy or keep it off the public internet. See [SECURITY.md](SECURITY.md).
- **Hide from grid** is a display filter. It becomes an API gate only when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set.
- **Multi-host is a read-only viewer.** Each machine still runs Port-Light. One UI can pull the others over LAN or Tailscale (Settings → Occupancy). Do not expose port 2100 to the public internet. The hub fetches those URLs itself; a Docker bridge container often cannot reach Tailscale `100.x` — use a LAN IP, or `network_mode: host` on the hub.
- Process names come from `/host/proc` (inode → `comm`) when that mount is present — the usual image. Without it, the grid shows Docker container names. `ss -tlnp` names still need a host-network / bare-metal path.
- Host-network containers are matched via `/proc/<pid>/fd` socket inodes when `/host/proc` is mounted; otherwise they fall back to `ExposedPorts`.

Roadmap and design notes: [docs/roadmap.md](docs/roadmap.md), [docs/architecture.md](docs/architecture.md). Automation and coding-agent integrations: [docs/integrations.md](docs/integrations.md).

## Quick start

Image: [`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light) (`linux/amd64`, `linux/arm64`). Also published to GHCR on tagged releases (`ghcr.io/stepaniah/port-light`). Prefer a version tag over `latest` on machines you care about.

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.7.2
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

`/var/run/docker.sock` is powerful, even mounted read-only. Prefer a [socket proxy](docs/deployment.md#docker-socket-proxy) if the UI might be reachable by anyone you do not fully trust. More install options (Unraid template, Podman, reverse proxy, build from source): [docs/deployment.md](docs/deployment.md).

`NET_ADMIN` is **not required** in the usual bridge setup. It only helps the `ss` fallback on bare metal.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPOSE_SCAN_DIR` | `/compose` | Directory to scan for `compose.y*ml` / `docker-compose.y*ml` (env only) |
| `COMPOSE_SCAN_DEPTH` | `4` | Max subdirectory depth under the scan dir |
| `COMPOSE_SCAN_MAX_FILES` | `400` | Cap on compose files parsed per refresh |
| `PORT_RANGE_START` | `1` | Start of the range used for the **free** summary count |
| `PORT_RANGE_END` | `9999` | End of that range (does not fill the grid with green cells) |
| `PORT_LIGHT_DATA_DIR` | `/data` | Manual ports, hidden list, and saved settings (JSON) |
| `PORT_LIGHT_PORT` | `2100` | Port uvicorn listens on inside the container; surfaced via `/api/meta` so the Automation panel builds MCP snippets without hardcoding |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | Extra / overriding port names (env only) |
| `THEME_MODE` | `system` | `system` / `dark` / `light` — the resolved brightness; palettes follow it |
| `THEME_PALETTE` | built-in | Color family layered on top: `gruvbox`, `catppuccin`, `solarized`, `nord`, `dracula`, `tokyo-night`, `one-dark`, `everforest`, `rose-pine`, `kanagawa`. Empty uses the built-in colors. |
| `LOCALE` | `auto` | `auto` / `en` / `zh-CN` / `zh-TW` / `ja`. Auto follows the browser. |
| `GRID_DENSITY` | `comfortable` | `comfortable` or `compact` |
| `REFRESH_MS` | `5000` | Auto-refresh interval |
| `URL_HOST` | empty | Hostname used in guessed `http(s)://` links |
| `URL_SCHEME` | `auto` | `auto` / `http` / `https` |
| `AUTH_USER` / `AUTH_PASSWORD` | unset | Optional HTTP Basic Auth for the UI and API. `/api/health` stays open. Env only. |
| `HIDDEN_UNLOCK_PASSWORD` | unset | If set (or if Basic Auth is set), hidden-from-grid ports are withheld from the API until you unlock. Env only. |
| `PORT_LIGHT_SETTINGS_SOURCE` | `auto` | `auto`: Web UI save wins over env. `env`: Compose is the only source and the Settings page is read-only. |
| `PORT_LIGHT_HOST_NAME` | hostname | Label for this machine when other occupancy maps are shown. |
| `PORT_LIGHT_PEERS` | unset | JSON array of `{name, url, username?, password?}` used when the data file has no `peers` key, or when `PORT_LIGHT_SETTINGS_SOURCE=env`. Same lock as Settings. |
| `PORT_LIGHT_LOG_LEVEL` | `warning` | Backend log level (`debug` / `info` / `warning` / `error`). Degraded scans (Docker unreachable, unreadable Compose file, …) log one line and show up in `/api/health` under `degradations`. Env-only. |
| `WEBHOOK_URL` | unset | Opt-in webhook target (`http(s)` only). With `WEBHOOK_EVENTS=new_listener,conflict`, Port-Light POSTs `{event, port}` JSON (fire-and-forget). |
| `WEBHOOK_SECRET` | unset | Sent as `X-Port-Light-Secret`. |
| `WEBHOOK_EVENTS` | unset | Comma list: `new_listener`, `conflict`. |
| `METRICS_ENABLED` | unset | Set to `1` to expose `GET /api/metrics` (Prometheus text format: used/configured/free counts, hidden, degradations, Compose files). Aggregates only — never ports or names. Env-only. |
| `AGENT_TOKEN` | unset | When set, `GET /api/ports/suggest` requires a matching `X-Agent-Token` header. Env-only. |

Most of the table (except paths and secrets) can also be changed on **Settings** in the UI. Saves go into `/data/port_light.json`. OpenAPI is at `/docs`.

Copy [custom_ports.example.json](custom_ports.example.json) to `custom_ports.json` (gitignored). Categories: `system`, `web`, `database`, `message`, `proxy`, `vpn`, `selfhosted`, `dev`, `infra`, `gaming`.

If you bind-mount `custom_ports.json` in Compose, create the **file** on the host first. Mounting a missing path makes Docker create a directory and the app will fail to read it.

## Privacy

- No telemetry or analytics.
- The app does not phone home. Optional outbound HTTP is limited to occupancy pulls from Port-Light peers you add, and webhooks you configure (`WEBHOOK_URL`, `{event, port}` JSON only).
- All scan data stays on the machine that runs that instance (listen tables, Docker API, Compose files, `/data` JSON).
- Sibling `.env` files next to Compose stacks are read locally for `${VAR}` substitution and are never uploaded.

## Tech stack

- Backend: Python 3.11+ (CI covers 3.11–3.13), FastAPI, Uvicorn
- Frontend: static HTML/CSS/JS
- Image: `python:3.12-slim` + `iproute2`

## License

[MIT](LICENSE) © 2026 StepaniaH

[Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Ko-fi](https://ko-fi.com/stepaniah)
