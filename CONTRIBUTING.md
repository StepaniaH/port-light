# Contributing

Contributions should keep the project focused on port occupancy and avoid unnecessary dependencies.

## Scope

Port-Light focuses on port occupancy. Container lifecycle management and log streaming are outside its scope. See [docs/roadmap.md](docs/roadmap.md) and [docs/architecture.md](docs/architecture.md).

Open an issue first for new scanners (Podman, remote Docker), auth changes, or a frontend framework.

## Development

Python 3.11+. Docker is optional if you only touch parsers.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
npm ci
.venv/bin/python scripts/dev.py serve --compose-dir /path/to/compose-stacks --reload
.venv/bin/python scripts/dev.py test
npx playwright install chromium
.venv/bin/python scripts/dev.py test --browser
```

For a disposable preview with example projects, a conflict, and a 256-port range:

```bash
.venv/bin/python scripts/dev.py preview --port 2100
```

For a four-machine dashboard with synthetic listeners, distinct projects, conflicts, and reservations:

```bash
.venv/bin/python scripts/dev.py preview --fleet --port 2100
```

The fleet uses a local dashboard and three peers on automatically assigned loopback ports. All demo data and processes are removed when it exits.

The single-machine preview binds to `127.0.0.1`, scans only generated Compose files, and removes its temporary directory on exit. Stop it with Ctrl-C. The `serve` command uses `./data` by default and accepts `--data-dir`; it does not load `.env` automatically.

Python tests are grouped under `tests/api`, `tests/scanners`, `tests/client`, `tests/storage`, `tests/frontend`, and `tests/release`. Run a directory with `pytest tests/scanners` when working on one domain. Browser flows are `npm run smoke:browser` for the existing fleet/settings workflow, `npm run smoke:management` for grouping, rules, reservations, and mobile layout, and `npm run smoke:fleet` for adaptive card columns, multi-host navigation, the header menu, and management page themes.

`PORT_LIGHT_DATA_DIR` (default `/data`) must be writable by the process. Local uvicorn usually wants `PORT_LIGHT_DATA_DIR=./data`. Set `PORT_LIGHT_SCANNERS=listen,compose` when Docker is intentionally absent, and point `COMPOSE_SCAN_DIR` to a readable directory. If `./data` is a leftover Docker bind owned by `nobody`, pick another directory instead of sharing that volume.

`npm run smoke:browser` starts a temporary eight-instance local fleet and checks startup, recovery from invalid scanner configuration, default waterfall and saved tab layouts, independent settings and peer saves, custom-theme feedback, persisted machine descriptions, slider focus and refresh-capacity guidance, detail, a saved label, local scanner settings, warning disclosures, bind-address rendering, atomic batch reservation with a conflicting writer and retry, keyboard/mobile host switching, and the sanitized Doctor report in Chromium. It removes its data and stops every server on exit. Set `PYTHON` to override the Python executable.

Edit `frontend/*` and hard-refresh. Cache-bust query strings are in `frontend/index.html` (`?v=`). Bump them when JS or CSS changes.

Settings that belong on both Compose and the UI live in `backend/settings.py`. Secrets and filesystem paths stay env-only.

The dependency-free command-line client lives in `port_light_client/`. Run it
from a checkout with `python -m port_light_client`, or install the console
entry point in a disposable environment with `pipx install .` / `uv tool
install .`. HTTP transport and response validation belong in the shared client module.
Keep CLI output and MCP protocol handling in their respective adapters.

CI checks both installed client entry points outside the source tree and repeats
a wheel installation to check state preservation. Run the same check in a
disposable environment with:

```bash
python scripts/check_ai_install.py /path/to/venv/bin/python /path/to/client.whl
```

This command reinstalls the supplied wheel in that environment. It does not
register an AI tool or contact a Port-Light instance.

## Adding UI copy

Edit `frontend/locales/en.json`, run `.venv/bin/python scripts/locale-scaffold.py`
to copy the new keys into the other locale files, and translate the copied values.
`tests/frontend/test_i18n.py` enforces key parity, non-empty values, placeholder tokens, and
rejects orphaned keys nobody references. `--untranslated` lists suspicious
still-equal-to-English values per locale.

## Locales

UI copy lives in `frontend/locales/{en,fr,de,es,zh-CN,zh-TW,ja}.json`. English is the source tree; the other six files must use the same keys (`tests/frontend/test_i18n.py` checks this). `frontend/i18n.js` resolves `auto` from `navigator.languages`, sets `html lang`, and interpolates `{name}` placeholders. Do not concatenate translated fragments. Language names in `choice.*` stay in their own script in every file.

Bump the `?v=` on `i18n.js` and the `CACHE_BUST` constant inside it when locale JSON changes.

The root `docker-compose.yml` **builds from source**. It bind-mounts `./custom_ports.json` — create that file first, or drop the volume.

## Documentation

Maintain technical documentation under `docs/` in English. Keep the existing Simplified Chinese landing page in `README.zh-CN.md`; do not combine translated sections in one document. UI translations remain supported in all seven locales.

## Style

- Backend: stdlib plus `requirements.txt`. No extra frameworks.
- Frontend: vanilla JS, no bundler. Run `escapeHtml` on container, compose, and user strings.
- Keep scanner helpers pure enough to unit-test.

## Pull requests

- One concern per PR.
- Update [CHANGELOG.md](CHANGELOG.md) under Unreleased.
- Document behavior that lands in the same PR. Do not describe features that are not in the branch.
- Do not commit `custom_ports.json`, `.env`, or `/data/`.

## Release (maintainers)

Submit pull requests against `main`.

[CI](.github/workflows/ci.yml) runs on pushes to `main` and pull requests targeting `main`, not on version tags. It tests Python 3.11–3.13 and the frontend, including the Chromium smoke flow. Ruff runs once, on Python 3.13. A newer push cancels an older CI run for the same branch or pull request.

1. Move Unreleased notes into a version section in `CHANGELOG.md`.
2. Bump `__version__` in `port_light_client/__init__.py`; the backend, CLI,
   MCP server, package metadata, and release check all read that one value.
3. Update pinned image examples in both READMEs, `docs/deployment.md`, and `deploy/unraid/port-light.xml`. Remove the development-only notice when these features are included in the release.
4. Merge the release changes into `main` and wait for its CI to pass.
5. Tag that tested commit `vX.Y.Z` and push the tag. [Release](.github/workflows/release.yml) verifies that the tagged commit belongs to `main` and has a successful `main` push run of `ci.yml`. It waits up to 15 minutes if CI has not finished; a failed, cancelled, or timed-out check blocks publication.
6. Release verifies that the backend, CLI, and tag versions match, then builds
   amd64+arm64 images for Docker Hub and GHCR plus a platform-independent CLI
   wheel. Only after the images are pushed does it create the GitHub Release
   from the changelog section and attach the wheel. It does not repeat the test
   suite.

If publication is blocked by CI, fix or rerun the failing CI check first, then rerun the failed Release jobs in Actions. If a code change is needed, release a new tested commit; do not move an existing version tag. Manual branch builds no longer publish a `dev` image tag.

The release workflow also updates the Docker Hub description from the tagged README, resolving relative documentation and screenshot URLs to that tag. Preview the output with `python scripts/dockerhub_description.py --ref main --output /tmp/port-light-dockerhub.md`. The update uses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. Updating the repository description requires a personal access token with **Read, Write & Delete** permission and repository admin access; a token that can push images may still be rejected for this operation. If the description job returns 403, check these permissions and update the Actions secret, then rerun the failed job. Successful image and GitHub Release jobs do not need to be rerun. Community Applications submission details are in [deploy/unraid/README.md](deploy/unraid/README.md).

## Troubleshooting workspace

The bundled runtime and its static assets live in `backend/analysis/`. Its host
owns startup and teardown separately from scanning. Keep model calls explicit and
keep local checks available when no model provider is configured.

Run `pytest tests/analysis tests/api/test_observation_batches.py tests/api/test_port_observations.py` for the evidence and storage
contracts, `npm test` for display/localization tests, and `npm run smoke:analysis`
for the complete synthetic browser flow. The browser test exercises seven locales,
mobile layouts, immutable reports, rechecks, and BYOK configuration without sending
requests to a real model. Runtime assets use a content hash for cache invalidation.
