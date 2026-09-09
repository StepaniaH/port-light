# Security Policy

Port-Light is a LAN dashboard. Run it on a host you administer, on a network you trust, or behind a reverse proxy / VPN. It is not hardened for the public internet.

## Reporting a vulnerability

Do not open a public issue for anything that could help someone abuse the Docker socket or read host network state.

Use [GitHub private vulnerability reporting](https://github.com/StepaniaH/port-light/security/advisories/new) if it is enabled, or contact the maintainer via [Ko-fi](https://ko-fi.com/stepaniah).

## Trust model

| Surface | Behavior |
|---------|----------|
| HTTP API | Unauthenticated only when both `AUTH_USER` and `AUTH_PASSWORD` are absent. Invalid partial configuration returns 503. `/api/health` is always open. |
| Hidden ports | Display filter by default. With `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD`, rows are withheld until Basic Auth or `X-Hidden-Unlock` succeeds. |
| Docker socket | Often mounted into the container. Read-only is not the same as safe. |
| `/host/proc` | Read-only view of host network tables (and other `/proc` data for PID 1). |
| Compose mount | Read-only view of the bind you set, including sibling `.env` files. |
| Data volume | Local JSON and SQLite under `/data`. Saved peer passwords are stored in `port_light.json`; Docker-side CLI release tokens use `/data/cli-state`; file writes use owner-only permissions. Configured hubs can read peer snapshots, and opt-in webhooks send event names and port numbers. |
| Peer URLs | `PUT /api/hosts` stores origins + optional Basic Auth. The hub fetches occupancy, detail, history, and health from those origins. Redirects and environment proxies are disabled. Every resolved address must pass the private-address policy; see the DNS limits below. |
| Doctor report | Auth follows the rest of the UI/API. The report contains aggregate statuses, counts, safe enums, and allowlisted failure reasons; it omits identities, peer details, ports, paths, credentials, environment values, and degradation scopes. |
| Browser reservations | Request keys and release tokens remain in the tab’s session storage. They are never included in reservation list responses. Copied release commands contain a secret token. |
| Port rules | Rule names, ranges, and assigned project names use the same read authorization as other configuration. Rule changes require editable settings. |
| CLI | An HTTP client; it has no direct Docker, `/proc`, or Compose access. Basic Auth and agent tokens come from environment variables. Per-port release tokens are saved in owner-only local state unless `--no-save` is explicitly used with JSON output. |

Without auth, anyone who can reach port 2100 can read the port map (names, images, bind addresses, Compose paths), machine descriptions, and peer connection settings other than passwords. They can also change manual/hidden entries and editable settings.

## Authentication and recovery credentials

Basic Auth is disabled only when both `AUTH_USER` and `AUTH_PASSWORD` are absent.
If either is present but the pair is incomplete or blank, protected routes return
503 and the public health endpoint reports `degraded`. Credentials are compared
as UTF-8 bytes. Health responses omit diagnostic scopes until the configured
Basic Auth and hidden-data gates permit disclosure.

Reservation request keys grant access to release credentials. CLI/MCP save them
in private local files before sending a reservation request. Server state stores
hashes, not plaintext request keys or release tokens. Keep the client state
directory persistent and private; exclude `Idempotency-Key` headers and JSON
reservation/recovery output from shared logs. Stateless CLI use requires a
caller-retained `PORT_LIGHT_REQUEST_KEY` as well as `--no-save --json`.

## Recommendations

1. Do not publish `2100` on a public VPS. Bind to LAN, Tailscale, or localhost plus a reverse proxy.
2. Set `AUTH_USER` / `AUTH_PASSWORD` and/or put SSO / basic auth on the proxy. `/api/health` stays open for Docker healthchecks.
3. Prefer a [Docker socket proxy](docs/deployment.md#docker-socket-proxy) limited to reads. Do not allow create/exec.
4. Do not add `privileged`, extra capabilities, or `pid: host`. Bridge + `/host/proc` + socket (or proxy) is enough. `NET_ADMIN` is optional.
5. Run as a non-root `user:` when the `/data` mount allows it.
6. Do not point `COMPOSE_SCAN_DIR` at trees that contain secrets you would not put in a screenshot.
7. Bind addresses appear in port details and can optionally appear on cards. Review screenshots before sharing them.
8. Protect backups and mounts of `/data`; they can contain peer credentials,
   Docker-side CLI release tokens, manual labels, and local history.
9. Treat machine descriptions as public to dashboard/API users. Do not store passwords, tokens, or other secrets in these notes.
10. Review a Doctor report before attaching it to a public issue, as with any diagnostic output.
11. Keep CLI JSON reservation output and `PORT_LIGHT_STATE_DIR` private. JSON
    includes release tokens; labels are visible in the dashboard and must not
    contain secrets.

## Peer DNS validation

Every request resolves the peer hostname and checks all returned IPv4/IPv6 addresses. A public, IPv4 link-local, multicast, or unspecified address causes rejection of the entire result. IPv4-mapped IPv6 addresses receive the IPv4 checks too. Empty or failed DNS responses make the peer unavailable.

The HTTP client resolves again when connecting. Validation does not pin the destination IP, so a DNS change between validation and connection can bypass the preflight check. Use literal private IPs or DNS you control. System DNS resolution is not covered by the 4-second HTTP socket timeout.

## Data file failures

Unreadable or invalid `port_light.json` files block dependent reads and writes with `503`; they are preserved for repair. Error responses do not include file contents or peer credentials. Failed scans retain earlier observations and cannot certify free ports or allocate through `/api/reservations` and `/api/manual-ports/batch`. Disabled scanners are outside that coverage.

## Hidden ports

Hide-from-grid only reduces what shows up in the UI (and, when secrets are set, in the API). It is not a confidentiality boundary for `docker.sock` or Compose `.env` files.

## Supply chain

Images are built on GitHub Actions and pushed to Docker Hub (`stepaniah/port-light`) and GHCR (`ghcr.io/stepaniah/port-light`). Pin a `v*` tag or digest.
