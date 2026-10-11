# Unraid installation and upgrade checks

Use a disposable container and a dedicated appdata directory. Do not reuse a production Port-Light data directory. These checks cover Docker Manager behavior that local Python tests cannot verify.

## Load a local template

1. Copy the reviewed `port-light.xml` to `/boot/config/plugins/dockerMan/templates-user/my-port-light-validation.xml` on the test server. Choose another filename if it already exists; do not overwrite an existing user template.
2. Open **Docker → Add Container** and select the local template from the **Template** list.
3. Use container name `port-light-validation`, a new appdata directory such as `/mnt/user/appdata/port-light-validation`, and an available host port such as `22100`. Keep container port `2100`, bridge networking, and privileged mode off.
4. Leave **Compose stacks** empty. Do not add `AUTH_USER` or `AUTH_PASSWORD`. Keep **Enabled scanners** at `listen,docker`.
5. Review the fields and apply. Check the generated Docker command: the Compose mount and Basic Auth variables should be absent. A local reviewed file can be tested before it is pushed to GitHub or submitted to Community Applications.

Unraid saves user templates in the directory above; see the [Community Applications documentation](https://docs.unraid.net/community-applications/). Test the exact file under review, rather than an older copy fetched from `main`.

## Installation checks

| Check | Action | Expected result |
|-------|--------|-----------------|
| Default installation | Install without Compose files or authentication variables; open WebUI. | Dashboard loads without an authentication configuration error. After scanning, `/api/health` reports `occupancy.ready: true`; Listen and Docker are `ok`, Compose is `disabled`. |
| Host visibility | Compare a known host listener and a bridge container's published port with the dashboard. | Both host ports appear. An internal container port without a host mapping is not treated as a published host port. |
| Custom web port | Use an available host port other than 2100. | The Unraid WebUI shortcut opens that host port; container port remains 2100. |
| Optional Compose | Map a directory containing a Compose declaration for an unused host port, apply, then enable Compose in Port-Light Settings. Do not start the declared service. | The declaration appears as configured. Disabling Compose in Settings and removing the mount restores the default scan without a missing-directory warning. |
| Basic Auth | Add both authentication variables with temporary non-empty values and mask the password; apply. Test in a private browser window. | Requests without credentials receive `401`; correct credentials open the dashboard. `/api/health` remains accessible. |
| Invalid authentication | In this disposable container only, leave a password empty or remove one of the two variables; apply. | The dashboard returns `503 authentication_misconfigured`. A `200` health response does not establish that authenticated pages work. Restore both values or remove both variables to recover. |
| Restart and recreate | Save a language/theme choice and a manual port entry. Restart, then apply a harmless container edit using the same `/data` mapping. | The settings and entry remain present; scanning resumes. |

For port checks, use services you control and distinguish host ports from container ports. Record the actual network mode if testing host, custom bridge, macvlan, or ipvlan networking beyond the default bridge configuration.

## Upgrade checks

Use the [upgrade procedure](README.md#upgrades-and-existing-installations) with two published versions you intend to support. Recreating the same version checks persistence but is not a cross-version upgrade test.

1. Install the source version in the disposable container. Save a manual entry and settings, create a reservation with time remaining, and let port history record an event.
2. Stop it and back up the complete appdata directory and its saved container configuration. Do not include credentials in the test report.
3. Change **Repository** to the target version and apply, retaining the same appdata mapping, web port, and any configured credentials.
4. Verify the target version, Doctor results, settings, manual entry, active reservation, and earlier history. Confirm Basic Auth still protects access if enabled. A previously saved Compose scanner selection must remain effective despite the new template's `listen,docker` default.
5. If testing rollback, stop the container and restore both the source version and its matching pre-upgrade data backup. Verify the original state again.

For older-template migration, also verify that removing unwanted empty Basic Auth entries fixes access, and that explicitly disabling an unused Compose source removes the occupancy warning. Do not remove working authentication or disable a Compose source that is in use.

## Report results

Record the Unraid version, Community Applications version if used, image tags, template revision, network mode, and pass/fail result for each check. Mark checks that were not run. Include sanitized errors or a Doctor report when needed; exclude passwords, tokens, private hostnames, appdata contents, and screenshots containing credentials.

Keep individual test reports out of this repository. The checklist is reusable; installation results belong in the review or support conversation.

## Local checks

From the project root:

```bash
.venv/bin/python -m pytest tests/release/test_unraid.py tests/release/test_distribution.py tests/api/test_api_auth.py -q
.venv/bin/python scripts/dev.py test
```

The template tests apply its environment variables to the application and exercise default scanning, optional Compose, authentication, and saved settings. Host listeners and Docker responses are controlled test inputs. They do not run Unraid, pull a release image, or prove cross-version data compatibility.
