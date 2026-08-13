# Changelog

All notable changes to this project are documented here. Versions match git tags and Docker Hub tags (`stepaniah/port-light:vX.Y.Z`).

## Unreleased

### Added

- Optional HTTP Basic Auth via `AUTH_USER` / `AUTH_PASSWORD` (`/api/health` stays open).
- `HIDDEN_UNLOCK_PASSWORD` + `X-Hidden-Unlock` so `include_hidden` is not a public switch when secrets are configured.
- `GET /api/meta` and richer `/api/health` scanner flags.
- GitHub Container Registry tags on the publish workflow (`ghcr.io/<owner>/port-light`).

### Changed

- App version string is `0.5.0`.
- Hide-from-grid copy and UI: no client-side password hash; machine selector removed (multi-host is not implemented).
- Changing the port range refetches from the API.
- UDP listen/bound sockets; IPv4-mapped IPv6 collapse; bind scope on each card (`public` / `lan` / `localhost`).
- Host-network containers matched by socket inode (and `ExposedPorts` fallback).
- Compose walk with depth/file caps, `include:`, expanded port ranges, `${VAR:-default}`.
- Traefik `Host()` / Caddy label URLs in the detail panel.
- `custom_ports.json` loaded with mtime cache.
- Parser tests and a GitHub Actions `CI` workflow.

### Documentation

- Honest README (English + 简体中文): removed claims for unimplemented multi-host and for password-protected hidden ports.
- Added architecture, deployment, security, contributing, and roadmap docs.
- GitHub issue / PR templates.
- Development Compose file: `NET_ADMIN` commented out (not required for `/host/proc` scanning).

## 0.4.0 — 2026-07-25

### Added

- Copy port number when clicking a card (settings toggle).
- Copy confirmation on the card.
- Product Hunt badge and Ko-fi funding link.

### Changed

- README deployment section aimed at the published Docker Hub image.

## 0.3.0 — 2026-07-22

### Fixed

- In a bridge container, listen detection now reads `/host/proc/1/net/tcp` (host PID 1 network namespace) instead of the container namespace.

## 0.2.0 — 2026-07-22

### Changed

- Moved off `network_mode: host` toward a bridge container plus host `/proc` (intermediate `nsenter` approach, replaced in 0.3.0).

## 0.1.0 — 2026-07-21

### Added

- Initial public image pipeline: GitHub Actions multi-arch push to Docker Hub.
- First tagged image `stepaniah/port-light:v0.1.0`.

The first import of the app itself is commit `7f6b387` (“Initial release: Port-Light v0.3.0”); early tags mainly track image/publish experiments.
