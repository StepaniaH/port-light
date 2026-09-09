# Unraid template

`port-light.xml` installs the published `stepaniah/port-light:v0.8.3` image. It uses bridge networking, exposes the Web UI on port 2100, and stores settings and history under `/mnt/user/appdata/port-light`.

Set the host path for Compose projects before applying the template. The Docker socket and host `/proc` mounts provide occupancy sources. The Docker socket grants Docker API access even with a read-only filesystem mount. To enable HTTP Basic Auth, set both `AUTH_USER` and `AUTH_PASSWORD`.

## Community Applications submission

The repository contains the application template, root `ca_profile.xml`, MIT license, icon, project URL, and issue tracker URL required for review. It is not yet listed in Community Applications.

After these files are available on the public `main` branch:

1. Sign in at the [Community Apps submission portal](https://ca.unraid.net/submit/new).
2. Select `https://github.com/StepaniaH/port-light` as the repository.
3. Run Validate and Scan. Check that `deploy/unraid/port-light.xml` is the application entry and `ca_profile.xml` supplies the repository profile.
4. Review the preview, install the template on an Unraid host, and submit it for review.

The portal’s [submission guide](https://ca.unraid.net/submit/help), [template format](https://ca.unraid.net/submit/help/repository-xml), and [repository profile format](https://ca.unraid.net/submit/help/repository-info-xml) describe the current requirements. A local XML check cannot verify an Unraid installation or guarantee catalog acceptance.
