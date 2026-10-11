# Unraid template

[`port-light.xml`](port-light.xml) installs a pinned release of the published `stepaniah/port-light` image. It uses bridge networking, exposes the Web UI on port 2100, and stores settings and history under `/mnt/user/appdata/port-light`.

## Installation

Review the template, then load it through **Docker → Add Container**. For local template installation before catalog submission, follow the [installation checks](TESTING.md#load-a-local-template).

The default installation scans host listeners and Docker containers (`PORT_LIGHT_SCANNERS=listen,docker`). Compose files are not required. Check these mappings before applying:

| Setting | Container target | Host value | Access |
|---------|------------------|------------|--------|
| WebUI | `2100/tcp` | An available host port; default `2100` | TCP |
| Docker socket | `/var/run/docker.sock` | `/var/run/docker.sock` | Read-only mount |
| Host proc | `/host/proc` | `/proc` | Read-only |
| Appdata | `/data` | `/mnt/user/appdata/port-light`, or another dedicated directory | Read-write |

Keep bridge networking and privileged mode off. If host port 2100 is occupied, change only the host port; leave the container port at 2100. The WebUI shortcut follows the mapping.

The Docker socket grants Docker API access even with a read-only filesystem mount. See the [socket proxy option](../../docs/deployment.md#docker-socket-proxy) to restrict API access. Appdata must be writable by the container and may contain credentials; keep backups private.

## Optional Compose scanning

In **Advanced View**, set **Compose stacks** to a readable host directory containing Compose projects. Leave the container path as `/compose` and access as read-only. An empty host path is omitted from the container mounts.

Apply the container change, then enable Compose in **Port-Light → Settings → Machines & scanning**. For a new appdata directory, setting **Enabled scanners** to `listen,docker,compose` before installation also enables it. Saved scanner choices in Port-Light take precedence over this environment default.

To stop scanning Compose, disable it in Port-Light Settings before removing the mount. Do not enable a source that the installation cannot read: unavailable enabled sources suspend free-port suggestions and reservations.

## Optional HTTP Basic Auth

Authentication is off by default. Use the default installation only on a trusted network, or put it behind an authenticated proxy. Public access requires HTTPS and access control.

To enable Basic Auth, edit the container and use **Add another Path, Port, Variable, Label or Device** to add both entries before applying:

| Config Type | Name / Key | Value | Password Mask |
|-------------|------------|-------|---------------|
| Variable | `AUTH_USER` | Your non-empty username | No |
| Variable | `AUTH_PASSWORD` | Your non-empty password | Yes |

Do not save credentials in the public XML template. Unraid passes empty variable entries to the container; an empty username or password causes Port-Light to return `503 authentication_misconfigured`. This also happens when only one variable is present. The default template therefore omits both entries.

To disable Basic Auth, remove both variable entries and apply the change. Clearing their values is not sufficient. Leave working credentials intact when upgrading. `HIDDEN_UNLOCK_PASSWORD` controls hidden-port access and `AGENT_TOKEN` controls allocation requests; neither replaces Basic Auth for the dashboard.

## Upgrades and existing installations

The template pins a `v*` image tag for reproducibility. Unraid updates the image referenced in the container's saved configuration; **Update** or **Force Update** does not select a newer version tag. Changes to the public template do not migrate an existing installation's paths, variables, or image tag. See Unraid's [saved template documentation](https://docs.unraid.net/community-applications/) and [container update implementation](https://github.com/unraid/webgui/blob/master/emhttp/plugins/dynamix.docker.manager/include/CreateDocker.php).

For an image upgrade:

1. Read the target release's notes and record the current **Repository** value and container settings privately.
2. Stop Port-Light and back up its entire appdata directory, including SQLite files and CLI state. Keep the original host path mapped to `/data`.
3. Edit **Repository** to the desired published tag, such as `stepaniah/port-light:vX.Y.Z` with an actual release number, and apply. Preserve the port mapping, mounts, and any configured credentials.
4. Open the WebUI, check the version, run Doctor, and verify settings, manual entries, reservations, and history. Container health alone only confirms HTTP availability; `/api/health` should also report `occupancy.ready: true` after scanning.

If rollback is needed, stop the container, restore the pre-upgrade appdata backup, and restore its matching image tag. Do not assume an older version can read data written by a newer version.

For installations created from an older template, review the existing configuration instead of overwriting its saved XML:

- If Basic Auth is not intended, remove both empty `AUTH_USER` and `AUTH_PASSWORD` entries. If it is intended, keep both entries with non-empty values.
- If Compose is unused, select only Listen and Docker in Port-Light Settings, set `PORT_LIGHT_SCANNERS=listen,docker` in the container, and remove an unused Compose mount. If Compose is used, keep its mount and explicitly enable all required sources in Port-Light Settings.
- Replace the old `THEME` variable with `THEME_MODE` (`system`, `dark`, or `light`) and, for a named palette, `THEME_PALETTE`. Appearance can also be set in Port-Light Settings.

Template-only corrections can be applied to the existing image. They do not require publishing or rebuilding it.

## Verification

Follow [installation and upgrade checks](TESTING.md) on an Unraid host. Local automated tests verify the template's application configuration; they do not replace Docker Manager installation and upgrade checks.

## Community Applications submission

The repository contains the application template, root `ca_profile.xml`, MIT license, icon, project URL, and issue tracker URL required for review. It is not yet listed in Community Applications.

After these files are available on the public `main` branch:

1. Sign in at the [Community Apps submission portal](https://ca.unraid.net/submit/new).
2. Select `https://github.com/StepaniaH/port-light` as the repository.
3. Run Validate and Scan. Check that `deploy/unraid/port-light.xml` is the application entry and `ca_profile.xml` supplies the repository profile.
4. Review the preview, complete the installation and upgrade checks on an Unraid host, and submit it for review.

The portal’s [submission guide](https://ca.unraid.net/submit/help), [template format](https://ca.unraid.net/submit/help/repository-xml), and [repository profile format](https://ca.unraid.net/submit/help/repository-info-xml) describe the current requirements. A local XML check cannot verify an Unraid installation or guarantee catalog acceptance.
