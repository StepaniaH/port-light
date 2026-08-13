# Contributing

Thanks for looking at Port-Light. It is a small codebase on purpose. Please keep it that way.

## Before you write code

Open an issue if the change is more than a typo, especially for:

- New scanners (UDP, Podman, remote Docker)
- Auth
- UI rewrites / new frameworks

The product is a **port occupancy map**, not a Docker control plane. Features that start/stop containers or tail logs will be declined.

Known gaps worth fixing are listed in [docs/roadmap.md](docs/roadmap.md) (P0–P1) and [docs/architecture.md](docs/architecture.md).

## Dev loop

Python 3.12+, Docker optional if you only touch parsers.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# COMPOSE_SCAN_DIR can be any folder of compose projects
uvicorn backend.main:app --reload --port 2100
```

UI: edit `frontend/*` and hard-refresh. Cache-bust query strings live in `frontend/index.html` (`?v=`). Bump them when you change JS/CSS.

Full stack:

```bash
docker compose up --build
```

The root Compose file **builds from source**. It bind-mounts `./custom_ports.json` — create a file there or comment the volume out.

## Code style

- Backend: stdlib + the packages in `requirements.txt`. No extra frameworks.
- Frontend: vanilla JS, no bundler, no JSX. Keep `escapeHtml` on any user/container/compose string.
- Do not add npm, React, or a CSS framework for a visual tweak.
- Scanner functions should stay pure enough to unit-test (string in, dataclass out).

When tests exist (`tests/`), run:

```bash
pip install -r requirements-dev.txt
pytest
```

Until then, at least hit `/api/health` and `/api/ports` against a host with Docker.

## Pull requests

- One concern per PR.
- Update [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.
- If you change behavior, update README / `docs/` in the same PR. Do not document features that are not in the branch.
- Do not commit `custom_ports.json`, `.env`, or `/data/`.
- Be excellent; see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Release (maintainers)

1. Changelog section for the version
2. Bump `VERSION` in `backend/main.py`
3. Git tag `vX.Y.Z` and push the tag — [docker-publish.yml](.github/workflows/docker-publish.yml) builds amd64+arm64 and pushes `stepaniah/port-light:vX.Y.Z` and `:latest`
4. Create a GitHub Release from that tag
