# Troubleshooting

## Occupancy scan warning

The warning “Occupancy data is incomplete or stale; free ports cannot be confirmed” means that at least one enabled scanner failed, returned incomplete data, or did not refresh in time. Port-Light keeps known occupied ports visible but does not treat missing observations as proof that a port is free.

Hover over the information icon, or focus or click the warning, to see guidance based on the current scan. Press Escape to close the disclosure. Remote-machine warnings describe that machine; the hub's local settings do not modify a peer. The guidance does not include raw log messages, private paths, or environment values.

### Upgrading to v0.7.9

No configuration migration is required. Existing peers and saved settings are preserved. Settings changes now save automatically, and one- or two-machine waterfall layouts expand across the available desktop width. Choose tabs under Settings → Appearance → Cards if preferred.

For deployments upgrading from v0.7.6, the scanner fixes introduced in v0.7.7 prevent discovery beyond the selected depth and repeated interleaved degradation logs. These fixes do not grant access to unreadable directories or Docker sockets.

If your enabled scanners already work, updating the image and recreating the container is enough. Change a pinned image tag to `v0.7.9` before running these commands; pulling an older version tag does not upgrade it. Deployments using `latest` can retain that tag:

```bash
docker compose pull port-light
docker compose up -d port-light
```

Existing scanner selections and deployment permissions are not changed automatically.

If the warning remains, follow the relevant check below.

| Current failure | What to check |
| --- | --- |
| Docker | Daemon availability, socket mount or `DOCKER_HOST`, and socket permissions for the container user |
| Host listeners | The host `/proc` mount at `/host/proc` and its read permissions; for direct installation, `ss` or `/proc` availability |
| Compose directory | The `/compose` mount and directory read permissions; limit discovery to configuration directories |
| Incomplete Compose scan | Directory/file read permissions and scan scope; container logs for invalid YAML, missing required variables, or unresolved `include`/`extends` files |
| Compose file limit | Narrow discovery, exclude non-configuration folders, or increase the maximum file count |
| Stale snapshot | Wait for the next scan, then check logs for persistent scan failures or timeouts |

### Non-root Docker socket access

On the Docker host, obtain the socket group:

```bash
stat -c '%g' /var/run/docker.sock
```

Store that numeric result as `DOCKER_SOCKET_GID` in the deployment's `.env`, then add this to the existing Port-Light service:

```yaml
services:
  port-light:
    group_add:
      - "${DOCKER_SOCKET_GID}"
```

Use the GID from your host, not a copied example. Do not use `chmod 666`, privileged mode, or switch to root just to suppress the warning. A read-only socket mount does not bypass Unix permissions or make Docker API calls read-only; a [socket proxy](deployment.md#docker-socket-proxy) can restrict API access.

Apply changes to `user`, `group_add`, mounts, or environment variables with `docker compose up -d port-light`. A plain `docker compose restart` restarts the existing container with its old configuration.

### Compose discovery and scanner selection

In **Settings → Occupancy**, choose the local scanners, scan depth, excluded folder names, and maximum Compose file count. These settings are applied to subsequent scans without restarting the container. Set the mount source and `COMPOSE_SCAN_DIR` in the deployment configuration; the web UI cannot grant filesystem permissions or mount host directories.

For example, if each stack has its Compose file directly under a common parent and its database data below that, a scan depth of `1` reads the stack-level files without entering their children. Alternatively, exclude specific data-folder names using the settings page or `COMPOSE_SCAN_EXCLUDE_DIRS=data,postgres-data`. Exclusions apply to directory discovery; explicit Compose `include` and `extends` references are still followed and must remain readable.

Disable a scanner only if that source is intentionally outside your occupancy checks. Disabling Compose omits declarations for services that are not running. Disabling Docker omits information that only the Docker API supplies. A successful scan is complete only for the sources and discovery scope you selected.

Saved settings normally override environment defaults. If `PORT_LIGHT_SETTINGS_SOURCE=env` or `SETTINGS_READONLY=1` is set, edit the deployment configuration instead; the settings page remains read-only. For v0.7.6 instances that do not yet expose scanner selection in the UI, use `PORT_LIGHT_SCANNERS` in Compose, then recreate the container. Do not assume that upgrading the hub upgrades its peers.

An invalid or empty scanner selection blocks occupancy checks instead of silently enabling all sources. The settings page remains available: select at least one valid source and let the change save automatically, or correct `PORT_LIGHT_SCANNERS` in the deployment if settings are locked. Valid names are `listen`, `docker`, and `compose`.
