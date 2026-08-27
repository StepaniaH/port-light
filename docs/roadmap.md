# Roadmap

Port-Light is a **port occupancy map**: host listen tables, Docker mappings, and Compose declarations. It is not a container manager.

## Shipped in 0.7.4

- Appearance persistence has one owner: unsaved previews no longer leak into the next page load; deleting a selected custom theme clears the stale reference everywhere
- Appearance panel order: language picker first, then theme, then cards
- Internal quality pass: occupancy snapshot cache extracted (`backend/occupancy_cache.py`), store reads memoized by file mtime (the SSE change-detector stats instead of re-parsing), `safe_http_url` moved to `netaddr`, unused `machines` writer API and `/api/meta` appearance echo removed; `app.js` ghost copies and dead imports dropped (~270 lines)
- Guardrails: the i18n missing-key test now covers every frontend JS module; README locale tables list all seven languages

## Shipped in 0.7.3

- Custom themes: build a palette in the advanced theme editor (15 core colors), stored server-side, import/export as JSON, selected as `@custom:<id>`
- Localization: Français, Deutsch, Español join the UI, with locale-parity tooling
- Display: one card-density choice — Loose / Standard / Compact — with a live sample-card preview (replaces the never-released size sliders)
- UX polish: settings sections regrouped, live display preview, theme-aware icon traced from the original artwork, macOS listen sources via `lsof`

## Shipped in 0.7.2

- No hardcoded ports in agent-facing copy: MCP snippets derive their URL from the instance (`/api/meta` → `listen_port`, env `PORT_LIGHT_PORT`) or fall back to placeholders; the agent skill points at your dashboard URL instead of a literal localhost port

## Shipped in 0.7.1

- Automation tab: MCP registration snippets, agent-skill install hint, suggest-call activity log, active leases with release buttons
- Agent visibility: local agent-event store beside the history db, lease badges and expiry countdown on the grid, `expires_at` on `/api/ports`
- Image ships `mcp/server.py` and `skills/port-light/SKILL.md`
- Themes: brightness (system / light / dark) and color palette are independent controls; legacy `theme` values and the `THEME` env var migrate to `THEME_MODE` + `THEME_PALETTE`

## Shipped in 0.7.0

- Live updates over SSE, local history timeline, optional webhooks and metrics
- Free-run planner with one-click reservation, Docker label naming
- Agent integrations: `GET /api/ports/suggest` (leases, peer-aware scope, token gate), MCP stdio server, agent skill

## Shipped in 0.6.0

- Read-only multi-host viewer: the hub pulls peer `GET /api/ports`; each host still scans itself

## Shipped in 0.5.5

- Occupancy honesty: Compose include / override / extends / env_file, required interpolation, hidden unlock, 304 polls, last-publish LRU
- Settings splits into Appearance, Occupancy, and Advanced
- Compose scan walks directories named `data`

## Shipped in 0.5.4

- Occupancy-map polish: Compose macvlan / include / extends, host `ns:`, protocol union, hidden cells, untrusted listen scan
- Named appearance palettes on Settings (Gruvbox, Catppuccin, Nord, …)

## Shipped in 0.5.3

- Keyboard occupancy grid, detail-panel action errors, two-line card labels
- Compose override files; conflict is per project, not per file
- Security headers, static cache, poll skip when the map is unchanged

## Shipped in 0.5.2

- Settings page (`#/settings`): Compose env or Web UI, file overlay in the data volume
- Light / system / dark theme
- Four UI locales (en, zh-CN, zh-TW, ja)
- Broader known-port table
- Unraid XML template, Podman notes, image healthcheck
- Ruff + Dependabot; GitHub Release from changelog on new tags

## Shipped in 0.5.0

- Optional Basic Auth and a real gate for hidden ports when secrets are set
- UDP, bind scope, host-network attribution
- Nested Compose files, `include:`, port ranges
- Traefik / Caddy URLs in the detail panel
- Tests + CI; images on Docker Hub and GHCR

## Later

- Community Apps listing (Unraid) once a template repo exists
- Working Podman rootless report from a real host

## Out of scope

- Start/stop containers, logs, image pulls
- Bookmark / homepage dashboards
- Kubernetes control plane
- Cloud-hosted scanning (the data is local: `docker.sock`, `/proc`, Compose files)
