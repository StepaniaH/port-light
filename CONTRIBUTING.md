# Contributing

Thanks for looking at Port-Light. Keep the codebase small.

## Scope

This is a port occupancy map, not a Docker control plane. PRs that start/stop containers or tail logs will be declined. See [docs/roadmap.md](docs/roadmap.md) and [docs/architecture.md](docs/architecture.md).

Open an issue first for new scanners (Podman, remote Docker), auth changes, or a frontend framework.

## Dev loop

Python 3.11+. Docker is optional if you only touch parsers.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# set COMPOSE_SCAN_DIR to a folder of compose projects
uvicorn backend.main:app --reload --port 2100
ruff check backend tests mcp
pytest
npm ci
npm run lint
npm test
npx playwright install chromium
npm run smoke:browser
```

`PORT_LIGHT_DATA_DIR` (default `/data`) must be writable by the process. Local uvicorn usually wants `PORT_LIGHT_DATA_DIR=./data`. Set `PORT_LIGHT_SCANNERS=listen,compose` when Docker is intentionally absent, and point `COMPOSE_SCAN_DIR` to a readable directory. If `./data` is a leftover Docker bind owned by `nobody`, pick another directory instead of sharing that volume.

`npm run smoke:browser` starts two temporary local instances and checks startup, detail, a saved label, bind-address settings and rendering, atomic batch reservation with a conflicting writer and retry, and host switching in Chromium. It removes its data and stops both servers on exit. Set `PYTHON` to override the Python executable.

Edit `frontend/*` and hard-refresh. Cache-bust query strings are in `frontend/index.html` (`?v=`). Bump them when JS or CSS changes.

Settings that belong on both Compose and the UI live in `backend/settings.py`. Secrets and filesystem paths stay env-only.

## Adding UI copy

Edit `frontend/locales/en.json`, run `.venv/bin/python scripts/locale-scaffold.py`
to copy the new keys into the other locale files, translate the copied values, done —
`tests/test_i18n.py` enforces key parity, non-empty values, placeholder tokens, and
rejects orphaned keys nobody references. `--untranslated` lists suspicious
still-equal-to-English values per locale.

## Locales

UI copy lives in `frontend/locales/{en,fr,de,es,zh-CN,zh-TW,ja}.json`. English is the source tree; the other six files must use the same keys (`tests/test_i18n.py` checks this). `frontend/i18n.js` resolves `auto` from `navigator.languages`, sets `html lang`, and interpolates `{name}` placeholders. Do not concatenate translated fragments. Language names in `choice.*` stay in their own script in every file.

Bump the `?v=` on `i18n.js` and the `CACHE_BUST` constant inside it when locale JSON changes.

The root `docker-compose.yml` **builds from source**. It bind-mounts `./custom_ports.json` — create that file first, or drop the volume.

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

1. Move Unreleased notes into a version section in `CHANGELOG.md`.
2. Bump `VERSION` in `backend/main.py`.
3. Update pinned image examples in both READMEs and `docs/deployment.md`.
4. Tag `vX.Y.Z` and push the tag. [docker-publish.yml](.github/workflows/docker-publish.yml) builds amd64+arm64 for Docker Hub and GHCR. [github-release.yml](.github/workflows/github-release.yml) opens a GitHub Release from the changelog section.
