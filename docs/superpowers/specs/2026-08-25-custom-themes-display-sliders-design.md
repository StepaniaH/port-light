# Custom Themes and Display Sliders — Design

Date: 2026-08-25
Status: Approved (user confirmed storage, palette scope, slider shape, panel structure)
Follow-up epic: localization (fr/de/es + drift tooling) lands after this epic freezes its UI keys.

## Problem

The Appearance settings offer only built-in palettes and a binary comfortable/compact
density toggle. Users cannot tune colors beyond picking a family, cannot save their own
palette, and cannot adjust card or text size continuously.

## User decisions

1. Custom themes are stored **server-side** in the data dir, so they follow the instance
   across browsers and peers.
2. The editor exposes exactly the **15 core color variables**; every other theme token is
   derived via `color-mix()` and follows automatically.
3. Density toggle is replaced by **two continuous sliders**: card size and text size.
4. Settings keep four panels and existing routes; the Appearance panel gains internal
   sections instead of a fifth panel.

## Data model

New file `<PORT_LIGHT_DATA_DIR>/themes.json`, same ownership pattern as
`custom_ports.json`:

```json
[
  {
    "id": "a1b2c3d4",
    "name": "Warm Gruvbox",
    "basedOn": "gruvbox",
    "mode": "dark",
    "colors": {
      "bg": "#282828", "elevated": "#3c3836", "card": "#32302f",
      "cardHover": "#3c3836", "border": "#504945", "text": "#fbf1c7",
      "textDim": "#bdae93", "used": "#83a598", "configured": "#fabd2f",
      "free": "#b8bb26", "accent": "#fe8019", "conflict": "#fb4934",
      "access": "#d3869b", "hidden": "#928374", "danger": "#cc241d"
    }
  }
]
```

- `id`: 8 hex chars, generated server-side.
- `mode`: `"dark"` or `"light"` — the variant the palette was tuned under. Reuses the
  existing `PALETTE_VARIANTS` availability rule: a dark-only custom palette greys out
  when light mode is active.
- Keys are camelCase here because they map to CSS custom properties
  (`--bg`, `--card-hover`, `--text-dim`, …) at render time.

## API

| Route | Behavior |
|---|---|
| `GET /api/custom-themes` | List themes (no auth beyond instance auth) |
| `POST /api/custom-themes` | Create. Body: `{name, basedOn, mode, colors}`. Server assigns id |
| `PUT /api/custom-themes/{id}` | Replace name/colors/mode |
| `DELETE /api/custom-themes/{id}` | Remove |

Validation (rejects the whole payload, mirrors `settings.apply_patch` style):

- `name`: non-empty after trim, ≤ 40 chars.
- `basedOn`: must be a known family id or empty string.
- `mode`: `dark` | `light`.
- every color: fullmatch `^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$` after
  strip. Nothing else — no `rgb()`, no `url()`, no names. This makes CSS injection
  structurally impossible rather than filtered.
- File cap: ≤ 24 stored themes.
- Corrupt/unreadable file: quarantine it to `themes.json.bad`, return empty list,
  report one degradation line (same pattern as the port store).
- Write endpoints return 403 when settings are readonly
  (`PORT_LIGHT_SETTINGS_SOURCE=env` / `SETTINGS_READONLY=1`).

Selection reuses the existing `theme_palette` setting with value `@custom:<id>`. Backend
`theme_palette` coercion currently validates against a fixed choice tuple, so custom ids
are accepted by a dedicated branch (`@custom:` prefix + id exists in themes.json), not by
loosening the enum for arbitrary strings. Deleting a selected theme resets
`theme_palette` to `""` (built-in).

`GET /api/settings` snapshot gains `custom_themes: [...]` so the palette picker can list
them without an extra round trip.

## Frontend

### Theme application

`state.js applyTheme()` keeps setting `data-mode`/`data-palette` for built-ins. For a
custom selection it sets no `data-palette` and instead writes the 15 values as inline
custom properties on `document.documentElement.style` (inline wins the cascade over any
static block). Switching away removes those properties. No server-generated CSS, no
build step.

### Appearance panel sections

The panel renders three labeled sections inside `#/settings/appearance`:

1. **Theme** — mode picker, palette picker (built-ins + custom entries marked with a
   localized badge), and the advanced editor.
2. **Layout** — the two sliders, status text, badges.
3. **Language** — locale picker (moved here; unchanged behavior).

### Advanced theme editor

Collapsed by default (`<details>`-style disclosure inside the Theme section).

- "Start from current preset" button fills the form with the 15 effective values of the
  active palette (reads computed styles once, converts to hex).
- 15 rows: native `<input type="color">` + synced hex text input + localized label per
  variable.
- Name field required on save; Save creates or updates a chosen target (select existing
  custom theme to overwrite, or new entry).
- Live preview: edits apply to the document immediately but only in memory; leaving the
  panel with unsaved changes prompts (existing dirty-settings pattern).
- Import/export: export downloads the JSON of one theme; import posts through the same
  validated endpoint. No separate parsing path.
- Delete button per custom theme in the picker row (confirm via existing modal helper).

### Display sliders

- Card size slider uses one 0–100 scale: **50 reproduces today's comfortable geometry,
  100 reproduces today's compact geometry**, values below 50 extrapolate airier than
  comfortable (linear interpolation/extrapolation through those two anchor sets). One
  handle writes three properties (`--cell-min-w`, cell padding, cell min-height) so the
  grid stays coherent.
- Text size slider maps 0–100 onto grid base font size 12–18px linearly, **50 =
  today's effective grid font size** (measured during planning); cards use
  `em`-relative sizing already where feasible, and the plan task defines the exact
  property set after measuring current rules.
- Both sliders write `S.settings.card_scale` / `text_scale` (int 0–100), debounced
  preview during drag, persisted through the normal settings PUT pipeline as two new
  backend int fields (min 0 max 100, default 50).
- Migration: `grid_density` stays in FIELDS for env compat; resolve-time it no longer
  drives `data-density`. Stored/env value `compact` seeds initial `card_scale = 100`,
  `comfortable` → `50`; the UI hides the old choice field. Removing the attribute means
  `[data-density]` CSS rules are deleted.

## i18n impact

New keys (~35): editor labels, 15 variable labels, section headers, slider labels/hints,
custom badge, confirm/delete copy — added to en/zh-CN/zh-TW/ja simultaneously
(test_i18n key-tree parity enforces). fr/de/es intentionally out of scope here (Epic B).

## Docs impact

- README (+zh-CN): appearance feature paragraph mentions custom themes and sliders;
  env table unchanged (`GRID_DENSITY` still honored, documented as seeding card_scale).
- CHANGELOG: Unreleased bullets for shipped behavior only.
- docs/architecture.md settings/theme paragraphs updated to describe inline injection
  and the two scale fields.
- docs/deployment.md: no change expected (no new env vars).

## Privacy

Themes contain colors and names only. No telemetry, no identifiers, no URLs. Import
files are read client-side and posted to the user's own instance only. The research note
`docs/research/2026-08-25-integration-directions.md` was checked: no personal hosts,
domains, or tokens.

## Testing

- pytest: CRUD happy paths, each validation rejection (bad hex, long name, unknown
  basedOn, >24 themes), `@custom:` selection + delete-resets-selection, readonly 403,
  corrupt-file quarantine + degradation, grid_density → card_scale seed logic.
- node:test: editor HTML builder (15 rows, preset fill), palette picker custom entries +
  badge, slider rendering and curve endpoints (0 → compact geometry, 100/50 mapping),
  applyTheme inline-property path incl. cleanup on switch, locale parity (existing).
- Manual smoke: drag sliders live-preview; fork gruvbox → tweak → save → reload persists;
  delete selected theme resets to built-in; readonly mode disables editor writes.

## Out of scope

- fr/de/es locales and anti-drift tooling (Epic B, next).
- Per-variable derived-token overrides; multiple named variants per family beyond what
  custom themes already allow.
