# Deployment

Published image: [`stepaniah/port-light`](https://hub.docker.com/r/stepaniah/port-light) (Docker Hub) and `ghcr.io/stepaniah/port-light` (GHCR, tagged releases).

| Tag | Meaning |
|-----|---------|
| `v0.7.0` (and other `v*`) | Release built from that git tag |
| `latest` | Same as the newest `v*` tag at build time |
| `dev` | Manual `workflow_dispatch` builds |

Pin a `v*` tag on hosts you care about.

## Docker Compose (image)

```yaml
services:
  port-light:
    image: stepaniah/port-light:v0.7.3
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
      # AUTH_USER: admin
      # AUTH_PASSWORD: change-me
      # HIDDEN_UNLOCK_PASSWORD: change-me-too
```

The image includes a `HEALTHCHECK` on `GET /api/health`. Compose does not need a duplicate unless you want different intervals.

Create `./data` first if you run the container as a non-root `user:` so the bind mount is writable.

Do **not** add `cap_add: NET_ADMIN` unless you are on the bare-metal `ss` path. The usual `/host/proc` mount already fills process names via socket inodes.

## Docker run

```bash
docker run -d \
  --name port-light \
  --restart unless-stopped \
  -p 2100:2100 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /proc:/host/proc:ro \
  -v /path/to/your/compose-stacks:/compose:ro \
  -v port-light-data:/data \
  -e COMPOSE_SCAN_DIR=/compose \
  stepaniah/port-light:v0.7.3
```

## Build from this repo

The `docker-compose.yml` at the repo root **builds from source**. It is for development.

```bash
git clone https://github.com/StepaniaH/port-light.git
cd port-light
cp .env.example .env   # set COMPOSE_SCAN_DIR to your stacks
mkdir -p data
docker compose up -d --build
```

Root `docker-compose.yml` also bind-mounts `./custom_ports.json`. Create that **file** before the first `up`, or remove the volume line. A missing path becomes a directory and YAML load fails.

## Docker socket proxy

Mounting `docker.sock` into a container that serves a web UI is the main risk. A dedicated proxy can expose only `GET` on containers/images.

Example using `tecnativa/docker-socket-proxy` (adjust the allow-list to what you need):

```yaml
services:
  socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    restart: unless-stopped
    environment:
      CONTAINERS: 1
      POST: 0
      ALLOW_RESTARTS: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - internal

  port-light:
    image: stepaniah/port-light:v0.7.3
    restart: unless-stopped
    ports:
      - "2100:2100"
    environment:
      DOCKER_HOST: tcp://socket-proxy:2375
      COMPOSE_SCAN_DIR: /compose
    volumes:
      - /path/to/your/compose-stacks:/compose:ro
      - /proc:/host/proc:ro
      - ./data:/data
    networks:
      - internal
      - default

networks:
  internal:
    internal: true
```

Port-Light uses the official Docker SDK `from_env()`, so `DOCKER_HOST` is honored. Keep the proxy on an internal network; do not publish `2375`.

## Reverse proxy

If `AUTH_USER` / `AUTH_PASSWORD` are unset, the proxy must be the gate (VPN, SSO, basic auth, or bind to localhost / LAN only).

Caddy sketch:

```
port.home.arpa {
    reverse_proxy localhost:2100
}
```

`/api/health` is always unauthenticated so Docker can healthcheck the container.

## Unraid

A Docker template lives at [`deploy/unraid/port-light.xml`](../deploy/unraid/port-light.xml). In Unraid you can:

1. Docker → Add Container → fill the fields from that file, or
2. Point a template URL at the raw GitHub copy once you trust it.

Required mappings:

- Host path of your compose stacks → `/compose` (read-only)
- `/var/run/docker.sock` → `/var/run/docker.sock` (read-only)
- `/proc` → `/host/proc` (read-only). This is the Unraid host proc; the grid is empty without it in a bridge container.
- Appdata → `/data` (read-write)

Use a `v*` tag, not `latest`. Do not enable privileged mode. Optional: `AUTH_USER` / `AUTH_PASSWORD` / `HIDDEN_UNLOCK_PASSWORD`.

Community Applications listing is a separate templates repository; this XML is the starting point.

## Podman

The app uses the Docker SDK `from_env()`. Podman’s API socket is usually enough:

```bash
# Rootful
podman run -d --name port-light --restart unless-stopped \
  -p 2100:2100 \
  -v /run/podman/podman.sock:/var/run/docker.sock:ro \
  -v /proc:/host/proc:ro \
  -v /path/to/your/compose-stacks:/compose:ro \
  -v port-light-data:/data \
  -e COMPOSE_SCAN_DIR=/compose \
  docker.io/stepaniah/port-light:v0.7.3
```

Rootless socket is typically `$XDG_RUNTIME_DIR/podman/podman.sock`. SELinux hosts often need `:z` (or `:Z`) on the volume flags.

What still bites:

- `/host/proc/1/net/tcp` is the **host init netns**. In a rootless container that may not be the ports you care about.
- Host-network inode matching assumes Linux `/proc/<pid>/fd` as Docker presents it.
- Compose scan is just files on disk; that part does not need a socket.

If a Quadlet or rootless snippet works on your machine, open an issue or PR with the exact unit.

## Health

`GET /api/health` returns process liveness plus `{ proc, docker, compose, listen_source }` . It does not require Basic Auth and does not include port data. `proc: false` / `docker: false` means the UI can be up while the grid is empty. `listen_source` is `host_proc`, `ss`, `proc`, or `none` when the Host pill is trusted green.

## Settings (Compose and Web UI)

Non-secret options (theme, language, range, refresh, `URL_HOST`, compose scan depth, …) can be set as environment variables **or** on the Settings page (`#/settings`). The UI writes `settings` into `/data/port_light.json`. That overlay wins on the next refresh.

To keep GitOps / Compose as the only source:

```yaml
environment:
  PORT_LIGHT_SETTINGS_SOURCE: env
  THEME_MODE: dark
  LOCALE: en
  URL_HOST: nas.lan
```

Other machines: add them on Settings → Occupancy, or set `PORT_LIGHT_PEERS` (JSON) when the source is `env`. Each peer still runs its own Port-Light; this instance only pulls occupancy JSON. Keep those URLs on LAN or Tailscale.

This instance fetches those URLs; the browser does not. From the default Docker bridge, Tailscale `100.x` (or MagicDNS) is often unreachable because the container does not have the host's Tailscale interface. Use a LAN IP, or run the hub with `network_mode: host`. Peers can stay on bridge.

Paths (`COMPOSE_SCAN_DIR`) and secrets (`AUTH_*`, `HIDDEN_UNLOCK_PASSWORD`) cannot be stored from the UI.

Interactive API docs: `http://localhost:2100/docs`.
