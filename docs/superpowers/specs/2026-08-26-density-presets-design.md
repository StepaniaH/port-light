# Display Density Presets (宽松 / 标准 / 紧凑) — Design

Date: 2026-08-26
Status: Approved (user confirmed single control, fixed standard text size, Approach A)
Supersedes the slider half of `2026-08-25-custom-themes-display-sliders-design.md`.
Custom themes are untouched.

## Problem

After several iterations the two continuous sliders (`card_scale`, `text_scale`) proved
hard to tune and hard to reason about. The user wants them gone, replaced by one
"visual effect" choice with three presets — 宽松 (loose), 标准 (standard), 紧凑
(compact) — that is easier to polish.

## User decisions

1. **One control governs everything**: card geometry only. Text size stays fixed at the
   standard size in all three presets.
2. Preset geometry reuses the existing anchor table endpoints; 标准 reproduces today's
   default look exactly:

   | Preset   | minW | gap | padX | padT | padB | minH |
   |----------|------|-----|------|------|------|------|
   | loose    | 164  | 10  | 14   | 12   | 16   | 76   |
   | standard | 138  | 8   | 12   | 10   | 12   | 64   |
   | compact  | 112  | 6   | 10   | 8    | 8    | 52   |

3. Revive the retired `grid_density` setting instead of inventing a new field name
   (Approach A). No coexistence/migration plumbing for a second key.

## Backend (`backend/settings.py`)

- `grid_density` FieldSpec: type `choice`, env `GRID_DENSITY`,
  choices `("loose", "standard", "compact")`, default `"standard"`, group appearance.
  Help text updated to describe three visual presets.
- Delete the `card_scale` and `text_scale` FieldSpecs and the
  `GRID_DENSITY=compact ⇒ card_scale=100` seed migration.
- Coercion: a stored/env value of `"comfortable"` maps to `"standard"`; any unknown
  value falls back to `"standard"` (standard choice-field validation already coerces
  unknown values; add explicit `comfortable` handling). The sliders never shipped in a
  release (main is unpushed), so no numeric-scale migration is needed.
- Settings snapshot no longer contains `card_scale` / `text_scale`. Extra keys sent by
  old clients in PUT are ignored by the existing field filter.

## Frontend geometry (`frontend/js/state.js`)

- Replace `applyDisplayScale(cardScale, textScale)` with `applyDensity(name)`:
  looks up `DENSITY_PRESETS` (the table above) and sets the six `--cell-*` custom
  properties on `<html>`. Unknown names fall back to `standard`.
- Remove the `--port-font` dynamic computation; CSS fallback sizing stays as-is.
- Remove `CARD_ANCHORS` interpolation.
- `applyAppearance()` calls `applyDensity(S.settings.grid_density)`; the localStorage
  mirror (`port-light-settings`) stores `grid_density` and drops `card_scale` /
  `text_scale`.

## Cold boot (`frontend/index.html`)

Inline bootstrap reads `grid_density` from localStorage and applies the same preset
table inline (no interpolation, no font math). Keeps anti-flash behavior.

## Settings UI (`frontend/js/settings.js`, `frontend/style.css`)

- Delete the slider render branch, the document-level `input` delegate for sliders,
  and the `SLIDER_KEYS` filter. `grid_density` renders through the generic choice
  branch → segmented radiogroup with three options.
- Layout card order unchanged: density segmented control → display preview → toggles.
- Changing the preset live-applies `applyDensity` and refreshes the preview strip via
  the existing change path.
- Remove slider CSS (`.slider-wrap`, `.slider-out`, thumb focus ring block).
- Dead-code sweep while touching this area: remove the retired `grid_density`
  change-handler branch in `app.js` if still present.

## i18n (7 locales)

- Add `choice.loose`, `choice.standard`, `choice.compact` to all locale files.
- Remove orphaned keys: `settings.fields.card_scale.*`, `settings.fields.text_scale.*`,
  plus `settings.groups.appearance.*` strays flagged in the ledger.
- Bump `CACHE_BUST` in `frontend/i18n.js` and every `?v=` asset ref
  (74 → 75).

## Testing

- node (`frontend/test/settings.test.mjs` and friends):
  - preset exactness: `applyDensity('standard') ⇒ --cell-min-w '138px'` (and one more
    var per preset);
  - unknown/legacy name falls back to standard;
  - segmented UI renders three options before the preview; preview refreshes on change;
  - bootstrap inline table matches `DENSITY_PRESETS`;
  - localStorage mirror no longer carries scale keys.
- pytest: coercion (`comfortable→standard`, junk→default), choices present in snapshot,
  env override still wins.
- Gates: `.venv/bin/ruff check`, `.venv/bin/pytest`, quoted-glob node suite.

## Out of scope

- Custom themes CRUD/editor (unchanged).
- Per-axis fine tuning (explicitly rejected by user).
