# Port-Light

A local web dashboard that shows **which host ports are taken**, as a traffic-light grid. Built for homelabbers who run many Compose stacks and keep losing track of bindings.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/port-light?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/port-light)
[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/port-light)](https://hub.docker.com/r/stepaniah/port-light)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/port-light?label=version)](https://github.com/StepaniaH/port-light/tags)

[English](README.md) · [简体中文](README.zh-CN.md)

<a href="https://www.producthunt.com/products/port-light?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-port-light" target="_blank" rel="noopener noreferrer"><img alt="Port Light - A web dashboard shows your port usage as a traffic-light. | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1203037&theme=light&t=1784992647570"></a>

![Port-Light screenshot](https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/screenshot.png)

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
- Filters: running, in use, configured, system, Docker, access, UDP, localhost
- Sort by port, name, or status; clamp the visible range
- Manual entries for things the scanners miss
- Compose conflict warning when the same host port is declared in more than one file
- Built-in names for common homelab ports (SSH, Jellyfin, Postgres, …), plus a local override file
- 5-second auto-refresh (toggle in settings)
- Copy the port number on click
- Dark / light / system theme (settings)
- Optional HTTP Basic Auth (`AUTH_USER` / `AUTH_PASSWORD`)
- UDP as well as TCP; bind scope (`0.0.0.0` / localhost / LAN)
- Vanilla HTML/CSS/JS frontend — no npm, no build step

### Known limits (read before you deploy)

- **LAN tool.** There is no login unless you set `AUTH_USER` and `AUTH_PASSWORD`. Put it behind a reverse proxy or keep it off the public internet. See [SECURITY.md](SECURITY.md).
- **Hide from grid** is a display filter. It becomes an API gate only when `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set.
- **Multi-host is not implemented.** Run one Port-Light per machine.
- Typical Docker deploy **cannot show process names** (only container names from the Docker API). `ss -tlnp` process names need a host-network / bare-metal path.
- Host-network containers are matched via `/proc/<pid>/fd` socket inodes when `/host/proc` is mounted; otherwise they fall back to `ExposedPorts`.

Roadmap and design notes: [docs/roadmap.md](docs/roadmap.md), [docs/architecture.md](docs/architecture.md).

## Quick start

Image: [`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light) (`linux/amd64`, `linux/arm64`). Also published to GHCR on tagged releases (`ghcr.io/stepaniah/port-light`). Prefer a version tag over `latest` on machines you care about.

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.5.0
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
| `COMPOSE_SCAN_DIR` | `/compose` | Directory to scan for `compose.y*ml` / `docker-compose.y*ml` |
| `COMPOSE_SCAN_DEPTH` | `4` | Max subdirectory depth under the scan dir |
| `COMPOSE_SCAN_MAX_FILES` | `400` | Cap on compose files parsed per refresh |
| `PORT_RANGE_START` | `1` | Start of the range used for the **free** summary count |
| `PORT_RANGE_END` | `9999` | End of that range (does not fill the grid with green cells) |
| `PORT_LIGHT_DATA_DIR` | `/data` | Manual ports and hidden-port list (JSON) |
| `CUSTOM_PORTS_FILE` | `/data/custom_ports.json` | Extra / overriding port names |
| `AUTH_USER` / `AUTH_PASSWORD` | unset | Optional HTTP Basic Auth for the UI and API. `/api/health` stays open. |
| `HIDDEN_UNLOCK_PASSWORD` | unset | If set (or if Basic Auth is set), hidden-from-grid ports are withheld from the API until you unlock. |

Copy [custom_ports.example.json](custom_ports.example.json) to `custom_ports.json` (gitignored). Categories: `system`, `web`, `database`, `message`, `proxy`, `vpn`, `selfhosted`, `dev`, `infra`, `gaming`.

If you bind-mount `custom_ports.json` in Compose, create the **file** on the host first. Mounting a missing path makes Docker create a directory and the app will fail to read it.

## Privacy

- No telemetry or analytics. The app does not make outbound requests.
- All data stays on the machine that runs the container (listen tables, Docker API, Compose files, `/data` JSON).
- Sibling `.env` files next to Compose stacks are read locally for `${VAR}` substitution and are never uploaded.

## Tech stack

- Backend: Python 3.12, FastAPI, Uvicorn
- Frontend: static HTML/CSS/JS
- Image: `python:3.12-slim` + `iproute2`

## License

[MIT](LICENSE) © 2026 StepaniaH

[Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Ko-fi](https://ko-fi.com/stepaniah)
