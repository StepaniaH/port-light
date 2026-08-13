# Changelog

Versions follow git tags and image tags (`stepaniah/port-light:vX.Y.Z`).

## Unreleased

### Added

- Light theme (Settings: System / Dark / Light). Follows `prefers-color-scheme` when set to System.
- Dockerfile `HEALTHCHECK` against `/api/health` (no extra packages).
- `ruff` in CI; Dependabot for pip and GitHub Actions.
- GitHub Release notes from `CHANGELOG.md` when a `v*` tag is pushed.
- Unraid Docker template at `deploy/unraid/port-light.xml`.
- More built-in names (Home Assistant, Plex, Immich, Lidarr, Prowlarr 9696, Port-Light 2100, …).

### Changed

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
