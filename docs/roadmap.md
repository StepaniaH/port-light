# Roadmap

Port-Light is a **port occupancy map**: host listen tables, Docker mappings, and Compose declarations. It is not a container manager.

## Shipped in 0.7.0

- Live updates over SSE, local history timeline, optional webhooks and metrics
- Free-run planner with one-click reservation, Docker label naming
- Agent integrations: `GET /api/ports/suggest` (leases, peer-aware scope, token gate), MCP stdio server, agent skill

## Shipped in 0.6.0

- Read-only multi-host viewer: the hub pulls peer `GET /api/ports`; each host still scans itself

## Shipped in 0.5.5

- Occupancy honesty: Compose include / override / extends / env_file, required interpolation, hidden unlock, 304 polls, last-publish LRU
- Settings splits into Appearance, Occupancy, and Advanced
- Compose scan walks directories named `data`

## Shipped in 0.5.4

- Occupancy-map polish: Compose macvlan / include / extends, host `ns:`, protocol union, hidden cells, untrusted listen scan
- Named appearance palettes on Settings (Gruvbox, Catppuccin, Nord, …)

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

- Community Apps listing (Unraid) once a template repo exists
- Working Podman rootless report from a real host

## Out of scope

- Start/stop containers, logs, image pulls
- Bookmark / homepage dashboards
- Kubernetes control plane
- Cloud-hosted scanning (the data is local: `docker.sock`, `/proc`, Compose files)
