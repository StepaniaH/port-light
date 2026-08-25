# Theme Orthogonalization Design

Date: 2026-08-25
Status: Approved (user confirmed 2026-08-25)

## Problem

The Appearance settings tab exposes two overlapping mechanisms:

1. **Theme** (`system` / `dark` / `light`) — sets `data-theme` on `<html>`.
2. **Palettes** — 13 named presets (Gruvbox, Catppuccin, Solarized, Nord, …), each also setting `data-theme` to its own id.

Selecting any palette overrides the theme choice, so the System/Dark/Light control becomes inert. Three families ship two entries each (Gruvbox + Gruvbox Light, Catppuccin + Catppuccin Latte, Solarized + Solarized Light), forcing users to re-pick when switching brightness. The two controls have no defined relationship; the logic is confusing.

## Goal

Make mode (brightness) and palette (color family) orthogonal:

- One **mode** control: `system` / `light` / `dark`.
- One **palette** list: one entry per family, no light/dark duplicates.
- The resolved variant follows the mode.

## Interaction design

### Mode control

Unchanged visually. Semantics become purely "resolve brightness": `dark`/`light` force a mode; `system` follows the OS and re-resolves on change.

### Palette list

One entry per family (10 entries):

| Family | Dark variant | Light variant |
|---|---|---|
| Gruvbox | `gruvbox` | `gruvbox-light` |
| Catppuccin | `catppuccin` | `catppuccin-latte` |
| Solarized | `solarized` | `solarized-light` |
| Nord | `nord` | — |
| Dracula | `dracula` | — |
| Tokyo Night | `tokyo-night` | — |
| One Dark | `one-dark` | — |
| Everforest | `everforest` | — |
| Rosé Pine | `rose-pine` | — |
| Kanagawa | `kanagawa` | — |

### Availability rule

A single-variant palette is selectable only when the resolved mode matches its variant. Mismatched swatches render greyed out and non-interactive (e.g. Dracula under forced `light`; dynamic under `system` as the OS flips).

### System-mode boundary behaviour

If the OS switches while a now-mismatched palette is selected (Dracula selected, OS goes light):

- The selection is **kept** (no toast, no reset).
- Rendering falls back to the built-in variables for the current mode.
- When the OS returns to a compatible mode, the palette re-activates automatically.

Swatch previews always render the colours that would actually apply under the current resolved mode.

## Data model

`backend/settings.py` replaces the single `theme` choice field with:

- `theme_mode`: `system` | `light` | `dark`, default `system`, env `THEME_MODE`.
- `theme_palette`: one of the 10 family ids or empty string (built-in colours), default `""`, env `THEME_PALETTE`.

### Migration (on read, in-memory)

Migration runs every time settings are read. The stored file is rewritten in the new format only when the user next saves settings — no write-on-migration.

| Legacy `theme` value | New `theme_mode` | New `theme_palette` |
|---|---|---|
| `system` / `dark` / `light` | same | `""` |
| `<family>` where family has both variants (gruvbox, catppuccin, solarized) | `dark` | `<family>` |
| `<family>` dark-only (nord, dracula, tokyo-night, one-dark, everforest, rose-pine, kanagawa) | `dark` | `<family>` |
| `<family>-light` (gruvbox-light, catppuccin-latte, solarized-light) | `light` | base family id |
| unknown value | `system` | `""` + degradation report |

Note: unqualified `solarized` is the dark variant today (`color-scheme: dark` in style.css), hence row 3.

## CSS architecture

`<html>` carries two attributes set by the frontend:

- `data-mode="light" | "dark"` — resolved mode (system resolved against `prefers-color-scheme`).
- `data-palette="<family-id>"` — present only when a palette is selected.

Rules:

- Built-in light/dark variable blocks key off `[data-mode]` (replacing today's `[data-theme="dark"]` / `[data-theme="light"]`).
- Each dual-variant family becomes two blocks: `[data-palette="gruvbox"][data-mode="dark"] { … }` and `[data-palette="gruvbox"][data-mode="light"] { … }`. Single-variant families define only their matching combination.
- `color-scheme` is owned by `[data-mode]` only; palette blocks stop declaring it.
- Fallback for the system-mode boundary case: when `data-palette` has no block matching the current `data-mode`, the built-in `[data-mode]` variables apply — this falls out of the cascade with no extra CSS.
- The old `data-theme` attribute and its selectors are removed in the same change; no dual-track compatibility window (self-hosted single-user settings file migrates on read).

## Frontend changes

- `state.js`: resolve mode → set both attributes; export the family/variant map used by settings UI.
- `settings.js`: palette radio list renders families (not variants); applies availability greying from resolved mode; swatch preview resolves variant per mode.
- `app.js`: on OS scheme change while `theme_mode === 'system'`, re-resolve attributes and refresh palette availability.
- Locales: remove `choice.gruvbox-light`, `choice.catppuccin-latte`, `choice.solarized-light` keys; all other `choice.*` keys stay; identical key sets across en/zh-CN/zh-TW/ja (enforced by `tests/test_i18n.py`). Bump cache-bust `?v=` on changed JS/CSS and i18n `CACHE_BUST`.

## Backend changes

- `backend/settings.py`: field split + choices update + read-time migration helper (pure function, unit-testable).
- `backend/main.py`: `/api/settings` GET/PUT expose `theme_mode` + `theme_palette`; `/api/meta` echoes them the way it echoed `theme`.
- Unknown/invalid values degrade via `degradations.report("settings", "theme", "unknown value reset")`.

## Testing

Backend (`tests/`):

- Migration: legacy plain modes, dual-variant families, `-light` suffixes, dark-only families, unknown value (asserts reset + degradation).
- Settings round-trip: PUT new fields persists; invalid palette rejected/reset per rule.

Frontend (`frontend/test/`):

- Mode resolution incl. system flip while mismatched palette selected (attributes + fallback rendering class).
- Palette list renders 10 families; greying matches resolved mode.
- Swatch preview picks the correct variant per mode.

Manual check: switch OS appearance live with Dracula selected under `system`.

## Out of scope

- No new palettes, no cross-family mixing, no custom-variable editor.
- Occupancy/Advanced panels untouched.
- No macOS host listen scanner (separate issue per CONTRIBUTING).

## Non-goals guardrails

Keep the codebase small (CONTRIBUTING): the whole change should land as one focused PR touching settings.py, main.py echo points, style.css variable organization, state.js/settings.js/app.js, four locale files, tests, CHANGELOG Unreleased entry.
