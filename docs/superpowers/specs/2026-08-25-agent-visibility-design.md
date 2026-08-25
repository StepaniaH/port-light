# Agent Visibility Suite — Design (targets 0.8.0)

Date: 2026-08-25 · Status: approved direction, pre-implementation

## Problem

Port-Light 0.7.0 shipped agent integrations (suggest API, TTL leases, MCP stdio
server, agent skill), but they are effectively invisible:

- Docker users get an image that does not contain `mcp/` or `skills/`
  (Dockerfile copies only `backend/` and `frontend/`).
- The Settings → Advanced card shows static config flags, never usage.
- Leases appear as ordinary amber cards; humans cannot tell agent activity
  from their own manual entries or see time-to-expiry.

## Goal

Three pieces, decided in brainstorming:

1. **Guidance panel** — Settings gains a dedicated **Automation** tab with
   copy-paste MCP registration, agent-skill install hint, curl examples.
2. **Usage monitoring** — every successful suggest call becomes an event;
   summary + recent events shown in Settings; exposed counts stay aggregate.
3. **Lease visibility** — badge on grid cards, expiry countdown in the detail
   drawer, active-leases list with release buttons in Settings.

## Non-goals

- Conflict alerts when an agent takes a port a human wanted (0.9 candidate)
- Agent identity / client-id parameter
- ntfy / push notifications
- MCP HTTP transport (stdio stays the only transport)
- README restructuring (tracked separately)

## Data model

New table in the existing `history.db`, written by a new `backend/agent_events.py`
that mirrors `history.py`'s lock/connection/retention pattern:

```sql
CREATE TABLE IF NOT EXISTS agent_events (
  ts INTEGER NOT NULL,
  count INTEGER NOT NULL,
  scope TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  leased INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_events_ts ON agent_events(ts);
```

- One row per successful `GET /api/ports/suggest` call.
- `count` = number of ports actually returned; `scope` = `self` or
  `all:<reachable>/<total>`; `leased` = reserve or ttl was requested.
- No token, no IP, no host name. `label` is whatever the caller sent (it is
  already public on the grid as `manual_label`).
- Retention follows `HISTORY_RETENTION_DAYS`; sweep runs piggybacked on insert.
  When history is disabled (`0`), agent events are not recorded either — no new
  env var (YAGNI).

## API changes (extend existing routes; no new route surface)

`GET /api/meta` → `automation` block gains:

```json
"agent_events": {
  "total": 14,
  "last_used_at": 1756100000,
  "recent": [{"ts": 1756099940, "count": 2, "scope": "self",
               "label": "preview", "leased": true}],
  "active_leases": 2,
  "lease_rows": [{"port": 8081, "label": "preview", "expires_at": 1756103540}]
}
```

- `recent` capped at 10 rows, newest first; empty list when disabled.
- `active_leases` counted from manual entries with `expires_at` in the future
  (port_store already exposes `expires_at` per row); `lease_rows` carries the
  matching `{port, label, expires_at}` rows (cap 64, mirroring the suggest cap)
  so the Settings Leases card needs no additional endpoint.
- The block is omitted entirely when history is disabled, so old UIs are safe.

`suggest_ports`: records one event after a successful call (including calls
that return zero ports). Token failures are not recorded.

## Frontend

Vanilla ES modules, no build step — same patterns as existing panels.

1. `frontend/index.html`: new nav tab button after Occupancy:
   `settings-tab-automation` / `data-settings-panel="automation"`.
2. `frontend/js/state.js`: `SETTINGS_PANELS = ['appearance', 'occupancy',
   'automation', 'advanced']`.
3. `frontend/js/settings.js`: new panel with three cards
   (`settingsCard(titleKey, blurbKey, html)`):
   - **Connect** — MCP registration snippets with per-snippet copy buttons:
     - Compose/image form: `docker exec -i port-light python mcp/server.py`
       (works once the image ships `mcp/`)
     - From-source form: `python /path/to/port-light/mcp/server.py`
     - Skill install hint (file lives at `/app/skills/port-light/SKILL.md` in
       the image; copy into the agent's skills directory)
     - curl example built from `location.origin`; shows
       `-H "X-Agent-Token: <your-token>"` placeholder **only** when
       `/api/meta` reports `agent_token: true`. Token value is never echoed.
   - **Activity** — summary line (relative last-used time, total calls, active
     leases) + recent-events table (time, count, scope, label, lease flag).
   - **Leases** — one row per active lease: port, label, time remaining,
     Release button calling existing `DELETE /api/manual-ports/{port}`; empty
     state text when none.
4. `frontend/js/grid.js`: cards whose payload has `expires_at` render a small
   lease badge (clock glyph, localized aria-label). Pure client-side derivation;
   no backend change.
5. `frontend/js/detail.js`: when `expires_at` is present, an info-box shows
   "expires in Xm"; refreshed with the drawer's normal re-render cycle.
6. `frontend/style.css`: `.lease-badge` and compact table styles using existing
   theme variables.
7. i18n: new keys (`settings.nav.automation`, `settings.auto.connect.*`,
   `settings.auto.activity.*`, `settings.auto.leases.*`) added to all four
   locales (en, zh-CN, zh-TW, ja).

## Packaging

Dockerfile adds:

```dockerfile
COPY mcp/ ./mcp/
COPY skills/ ./skills/
```

Both are tiny (one stdlib-only Python file + markdown); image size impact is
negligible. `docs/integrations.md` updated: image now ships both, with the
docker-exec registration snippet.

## Testing

Backend (pytest):

- `agent_events.record` inserts; `recent()` caps and orders; retention sweep
  deletes old rows; disabled mode writes nothing.
- `/api/meta` carries the `agent_events` block; omits it when disabled.
- suggest records correct fields for plain, reserve, ttl, and `scope=all`
  (with unreachable-peer degradation) calls.

Frontend (node:test, same style as `frontend/test/*.test.mjs`):

- settings renderer emits the automation tab and both MCP snippet variants.
- grid badge present iff `expires_at` set; countdown formatter unit-tested.
- detail drawer renders the expiry info-box.

## Security / privacy review

- Event rows carry no secrets (verified above); data never leaves the volume.
- `AGENT_TOKEN` is never included in any API response or rendered snippet.
- New `/api/meta` fields pass through the existing global auth gate unchanged.
- Suggest endpoint behavior for agents is unchanged (additive bookkeeping only).

## Release

Ships as 0.8.0. No data migration (`CREATE TABLE IF NOT EXISTS`). Feature is
inert until the first suggest call arrives.
