# Security Policy

## What this software is

Port-Light is a **LAN dashboard**. It is meant to run on a homelab host you already administer, reachable by people you already trust, or gated by a reverse proxy / VPN you control.

It is **not** hardened for the public internet.

## Report a vulnerability

Please do **not** open a public issue for anything that could help an attacker use the Docker socket or read host network state.

Email or Ko-fi contact via [https://ko-fi.com/stepaniah](https://ko-fi.com/stepaniah), or use GitHub’s private vulnerability reporting if it is enabled on this repository.

## Trust model (current)

| Surface | Reality |
|---------|---------|
| HTTP API | No authentication unless `AUTH_USER` and `AUTH_PASSWORD` are set. `/api/health` is always unauthenticated. |
| Hidden ports | Display filter by default. When `AUTH_*` or `HIDDEN_UNLOCK_PASSWORD` is set, `include_hidden` and `/api/hidden` require a valid Basic Auth session or `X-Hidden-Unlock` header. Client-side hashes are not used. |
| Docker socket | Often mounted into the container. Read-only is **not** equivalent to safe; the Docker API can still be a path to host compromise depending on what is allowed |
| `/host/proc` | Read-only view of host network tables (and whatever else is under `/proc` for PID 1) |
| Compose mount | Read-only view of whatever you bind to `/compose` (including `.env` files next to stacks) |
| Data volume | Writable JSON under `/data` |

Anyone who can reach port 2100 can:

- Read the merged port map (services, container names, images, Compose paths)
- Add/delete manual ports and hide/unhide entries
- See “hidden” ports via the API

Treat the UI like `docker ps` plus your Compose tree on a webpage.

## Deploying less badly

1. Do not publish `2100` to `0.0.0.0` on a VPS. Bind to LAN, Tailscale, or localhost + reverse proxy.
2. Prefer **application Basic Auth** (`AUTH_USER` / `AUTH_PASSWORD`) and/or a reverse proxy gate (Caddy/Traefik basic auth, Authelia, Authentik, SSO). `/api/health` remains open for Docker healthchecks.
3. Prefer a [Docker socket proxy](docs/deployment.md#docker-socket-proxy) that allows only the reads Port-Light needs. Do not enable container create/exec.
4. Do not add `privileged`, extra capabilities, or `pid: host` “to make scanning work.” Bridge + `/host/proc` + socket (or proxy) is the intended path. `NET_ADMIN` is optional and unused in that path.
5. Run as a non-root `user:` if the `/data` mount ownership allows it.
6. Do not point `COMPOSE_SCAN_DIR` at home directories that contain secrets you would not paste into a wiki.

## Hidden ports

**Hide from grid** is a display filter so a screenshot or shoulder-surfer is less likely to see a port number.

- With no `AUTH_*` and no `HIDDEN_UNLOCK_PASSWORD`, the API still has the data. Anyone who can reach the UI can unhide or call the API. That matches the LAN-without-login model.
- When either secret is set, hidden port rows are omitted until the caller is authorized. This is still not a confidentiality boundary for `docker.sock` or Compose `.env` files.

## Supply chain

Images are built on GitHub Actions from this repository and pushed to Docker Hub (`stepaniah/port-light`) and GHCR (`ghcr.io/<owner>/port-light`). Pin a `v*` digest or tag. There is no SLSA provenance attestation yet.
