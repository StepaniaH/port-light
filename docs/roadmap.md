# Roadmap

Port-Light is a **port occupancy map**: host listen tables, Docker mappings, and Compose declarations. It is not a container manager.

## Shipped in 0.5.3

- Keyboard occupancy grid, detail-panel action errors, two-line card labels
- Compose override files; conflict is per project, not per file
- Security headers, static cache, poll skip when the map is unchanged

## Shipped in 0.5.2

- Settings page (`#/settings`): Compose env or Web UI, file overlay in the data volume
- Light / system / dark theme
- Four UI locales (en, zh-CN, zh-TW, ja)
- Broader known-port table
- Unraid XML template, Podman notes, image healthcheck
- Ruff + Dependabot; GitHub Release from changelog on new tags

## Shipped in 0.5.0

- Optional Basic Auth and a real gate for hidden ports when secrets are set
- UDP, bind scope, host-network attribution
- Nested Compose files, `include:`, port ranges
- Traefik / Caddy URLs in the detail panel
- Tests + CI; images on Docker Hub and GHCR

## Later

- Read-only multi-host agent (snapshot JSON, then UI)
- Community Apps listing (Unraid) once a template repo exists
- Working Podman rootless report from a real host

## Out of scope

- Start/stop containers, logs, image pulls
- Bookmark / homepage dashboards
- Kubernetes control plane
- Cloud-hosted scanning (the data is local: `docker.sock`, `/proc`, Compose files)
