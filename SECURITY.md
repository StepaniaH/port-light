# Security Policy

Port-Light is a LAN dashboard. Run it on a host you administer, on a network you trust, or behind a reverse proxy / VPN. It is not hardened for the public internet.

## Reporting a vulnerability

Do not open a public issue for anything that could help someone abuse the Docker socket or read host network state.

Use [GitHub private vulnerability reporting](https://github.com/StepaniaH/port-light/security/advisories/new) if it is enabled, or contact the maintainer via [Ko-fi](https://ko-fi.com/stepaniah).

## Trust model

| Surface | Behavior |
|---------|----------|
| HTTP API | Unauthenticated unless `AUTH_USER` and `AUTH_PASSWORD` are set. `/api/health` is always open. |
| Hidden ports | Display filter by default. With `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD`, rows are withheld until Basic Auth or `X-Hidden-Unlock` succeeds. |
| Docker socket | Often mounted into the container. Read-only is not the same as safe. |
| `/host/proc` | Read-only view of host network tables (and other `/proc` data for PID 1). |
| Compose mount | Read-only view of the bind you set, including sibling `.env` files. |
| Data volume | Local JSON under `/data`. Occupancy scans never leave that host. Optional hub pulls to peer URLs you add are the only outbound HTTP. |
| Peer URLs | `PUT /api/hosts` stores origins + optional Basic Auth. The hub fetches only `/api/ports` and `/api/health` on those origins (no redirects, no public IPv4). |

Without auth, anyone who can reach port 2100 can read the port map (names, images, Compose paths) and change manual/hidden entries.

## Recommendations

1. Do not publish `2100` on a public VPS. Bind to LAN, Tailscale, or localhost plus a reverse proxy.
2. Set `AUTH_USER` / `AUTH_PASSWORD` and/or put SSO / basic auth on the proxy. `/api/health` stays open for Docker healthchecks.
3. Prefer a [Docker socket proxy](docs/deployment.md#docker-socket-proxy) limited to reads. Do not allow create/exec.
4. Do not add `privileged`, extra capabilities, or `pid: host`. Bridge + `/host/proc` + socket (or proxy) is enough. `NET_ADMIN` is optional.
5. Run as a non-root `user:` when the `/data` mount allows it.
6. Do not point `COMPOSE_SCAN_DIR` at trees that contain secrets you would not put in a screenshot.

## Hidden ports

Hide-from-grid only reduces what shows up in the UI (and, when secrets are set, in the API). It is not a confidentiality boundary for `docker.sock` or Compose `.env` files.

## Supply chain

Images are built on GitHub Actions and pushed to Docker Hub (`stepaniah/port-light`) and GHCR (`ghcr.io/stepaniah/port-light`). Pin a `v*` tag or digest.
