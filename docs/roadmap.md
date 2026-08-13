# Roadmap

Port-Light is a **port occupancy map**: host listen tables, Docker mappings, and Compose declarations. It is not a container manager.

## Shipped in 0.5.0

- Optional Basic Auth and a real gate for hidden ports when secrets are set
- UDP, bind scope, host-network attribution
- Nested Compose files, `include:`, port ranges
- Traefik / Caddy URLs in the detail panel
- Tests + CI; images on Docker Hub and GHCR

## Next

- Podman / rootless notes from a working setup
- Light theme
- Read-only multi-host agent (snapshot JSON, then UI)
- More entries in the known-port table (PRs welcome)

## Out of scope

- Start/stop containers, logs, image pulls
- Bookmark / homepage dashboards
- Kubernetes control plane
- Cloud-hosted scanning (the data is local: `docker.sock`, `/proc`, Compose files)
