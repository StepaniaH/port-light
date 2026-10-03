# Project conventions

1. Run `.venv/bin/python scripts/dev.py test` for Python and frontend checks. Run the relevant `npm run smoke:*` command for browser changes, or add `--browser` to run all four browser flows.
2. Keep the frontend in vanilla JavaScript and native ES modules without a bundler. Keep import query versions aligned with `frontend/index.html`; bundled analysis assets use a content hash.
3. Use `frontend/locales/en.json` as the UI copy source. Keep all seven locales aligned in keys and placeholder tokens, and update the i18n cache version when translations change.
4. Keep `port_light_client/` dependency-free. Put shared HTTP transport and response validation in `client.py`, with CLI output and MCP protocol handling in their respective adapters.
5. Read the project version from `port_light_client/__init__.py`. Follow the release sequence in `CONTRIBUTING.md`; `.github/workflows/release.yml` publishes only commits that passed CI on `main`.
