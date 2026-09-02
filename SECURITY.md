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
| Data volume | Local JSON and SQLite under `/data`. Saved peer passwords are stored in `port_light.json`; file writes use owner-only permissions. Configured hubs can read peer snapshots, and opt-in webhooks send event names and port numbers. |
| Peer URLs | `PUT /api/hosts` stores origins + optional Basic Auth. The hub fetches occupancy, detail, history, and health from those origins. Redirects and environment proxies are disabled. Every resolved address must pass the private-address policy; see the DNS limits below. |

Without auth, anyone who can reach port 2100 can read the port map (names, images, bind addresses, Compose paths) and change manual/hidden entries.

## Recommendations

1. Do not publish `2100` on a public VPS. Bind to LAN, Tailscale, or localhost plus a reverse proxy.
2. Set `AUTH_USER` / `AUTH_PASSWORD` and/or put SSO / basic auth on the proxy. `/api/health` stays open for Docker healthchecks.
3. Prefer a [Docker socket proxy](docs/deployment.md#docker-socket-proxy) limited to reads. Do not allow create/exec.
4. Do not add `privileged`, extra capabilities, or `pid: host`. Bridge + `/host/proc` + socket (or proxy) is enough. `NET_ADMIN` is optional.
5. Run as a non-root `user:` when the `/data` mount allows it.
6. Do not point `COMPOSE_SCAN_DIR` at trees that contain secrets you would not put in a screenshot.
7. Bind addresses appear in port details and can optionally appear on cards. Review screenshots before sharing them.
8. Protect backups and mounts of `/data`; they can contain peer credentials, manual labels, and local history.

## Peer DNS validation

Every request resolves the peer hostname and checks all returned IPv4/IPv6 addresses. A public, IPv4 link-local, multicast, or unspecified address causes rejection of the entire result. IPv4-mapped IPv6 addresses receive the IPv4 checks too. Empty or failed DNS responses make the peer unavailable.

The HTTP client resolves again when connecting. Validation does not pin the destination IP, so a DNS change between validation and connection can bypass the preflight check. Use literal private IPs or DNS you control. System DNS resolution is not covered by the 4-second HTTP socket timeout.

## Data file failures

Unreadable or invalid `port_light.json` files block dependent reads and writes with `503`; they are preserved for repair. Error responses do not include file contents or peer credentials. Failed scans retain earlier observations and cannot certify free ports or allocate through `/api/ports/suggest` and `/api/manual-ports/batch`. Disabled scanners are outside that coverage.

## Hidden ports

Hide-from-grid only reduces what shows up in the UI (and, when secrets are set, in the API). It is not a confidentiality boundary for `docker.sock` or Compose `.env` files.

## Supply chain

Images are built on GitHub Actions and pushed to Docker Hub (`stepaniah/port-light`) and GHCR (`ghcr.io/stepaniah/port-light`). Pin a `v*` tag or digest.
