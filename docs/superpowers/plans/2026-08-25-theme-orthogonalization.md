# Theme Orthogonalization Implementation Plan

> **Status: shipped (through v0.7.3). Historical record — do not execute.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make theme mode (system/light/dark) and color palette orthogonal — one mode control, one palette entry per family, variant follows mode.

**Architecture:** Backend splits the `theme` choice field into `theme_mode` + `theme_palette` with a read-time migration for stored legacy values. Frontend sets two `<html>` attributes (`data-mode`, `data-palette`) instead of one (`data-theme`); CSS variables key off the pair; single-variant families grey out under a mismatched forced mode. System-mode flips keep an incompatible selection but fall back to built-in colors via the cascade.

**Tech Stack:** Python 3.11 stdlib + FastAPI (existing), vanilla ES modules, node:test, pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-theme-orthogonalization-design.md`

## Global Constraints

- Backend stdlib + requirements.txt only; no new frameworks.
- Frontend vanilla JS, no bundler; `escapeHtml` on every interpolated string.
- Locale parity across `frontend/locales/{en,zh-CN,zh-TW,ja}.json` (tests/test_i18n.py enforces).
- Bump `?v=` on style.css/app.js/i18n.js tags in index.html AND `CACHE_BUST` inside i18n.js when those files change.
- CHANGELOG.md gets an Unreleased bullet documenting shipped behavior only.
- Do not commit `.env`, `/data/`, `custom_ports.json`.
- One concern per commit; commit after each task's green tests.

## Palette reference (used by every task)

| Family id | Dark CSS block today | Light CSS block today |
|---|---|---|
| gruvbox | `gruvbox` | `gruvbox-light` |
| catppuccin | `catppuccin` | `catppuccin-latte` |
| solarized | `solarized` | `solarized-light` |
| nord / dracula / tokyo-night / one-dark / everforest / rose-pine / kanagawa | same id | — |

---

### Task 1: Backend field split + legacy migration

**Files:**
- Modify: `backend/settings.py` (FIELDS tuple ~line 47-58; add helpers near `_FIELD_BY_KEY`)
- Modify: `tests/test_settings.py` (two tests reference legacy `theme`)
- Create: `tests/test_theme_migration.py`

**Interfaces:**
- Produces: `migrate_theme(raw: Any) -> tuple[str, str]` (pure, exported for tests); settings values dict gains `theme_mode`, `theme_palette`; loses `theme`. Later tasks consume `values["theme_mode"]` / `values["theme_palette"]`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_theme_migration.py`:

```python
from __future__ import annotations

import pytest

from backend import degradations
from backend import settings as app_settings
from backend.main import app  # noqa: F401  (import order keeps TestClient cheap)
from backend import port_store


@pytest.mark.parametrize("raw,mode,pal", [
    ("system", "system", ""),
    ("dark", "dark", ""),
    ("light", "light", ""),
    ("gruvbox", "dark", "gruvbox"),
    ("catppuccin", "dark", "catppuccin"),
    ("solarized", "dark", "solarized"),
    ("nord", "dark", "nord"),
    ("dracula", "dark", "dracula"),
    ("tokyo-night", "dark", "tokyo-night"),
    ("one-dark", "dark", "one-dark"),
    ("everforest", "dark", "everforest"),
    ("rose-pine", "dark", "rose-pine"),
    ("kanagawa", "dark", "kanagawa"),
    ("gruvbox-light", "light", "gruvbox"),
    ("catppuccin-latte", "light", "catppuccin"),
    ("solarized-light", "light", "solarized"),
])
def test_migrate_known_values(raw, mode, pal):
    assert app_settings.migrate_theme(raw) == (mode, pal)


def test_migrate_unknown_resets_and_reports():
    before = len(degradations.recent(20))
    assert app_settings.migrate_theme("neon-dream") == ("system", "")
    assert len(degradations.recent(20)) > before


def test_migrate_none_defaults():
    assert app_settings.migrate_theme(None) == ("system", "")


def test_resolve_migrates_legacy_stored_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "auto")
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    port_store.update_stored_settings({"theme": "gruvbox-light"})
    values, origins = app_settings.resolve()
    assert values["theme_mode"] == "light"
    assert values["theme_palette"] == "gruvbox"
    assert origins["theme_palette"] == "file"


def test_put_rejects_legacy_key(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    res = client.put("/api/settings", json={"theme": "dark"})
    assert res.status_code == 400
```

In `tests/test_settings.py`, rewrite `test_settings_file_overrides_env` and `test_settings_source_env_locks_ui` to use `THEME_MODE`/`THEME_PALETTE` and `theme_mode`/`theme_palette` keys:

```python
def test_settings_file_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THEME_MODE", "dark")
    monkeypatch.setenv("THEME_PALETTE", "nord")
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "auto")
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    values, origins = app_settings.resolve()
    assert values["theme_mode"] == "dark"
    assert values["theme_palette"] == "nord"
    assert origins["theme_mode"] == "env"

    client = TestClient(app)
    res = client.put("/api/settings", json={"theme_mode": "light", "refresh_ms": 8000})
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["theme_mode"] == "light"
    assert body["origins"]["theme_mode"] == "file"
    assert body["values"]["refresh_ms"] == 8000


def test_settings_source_env_locks_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "env")
    monkeypatch.setenv("THEME_MODE", "dark")
    client = TestClient(app)
    locked = client.put("/api/settings", json={"theme_mode": "light"})
    assert locked.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_theme_migration.py tests/test_settings.py -x -q`
Expected: FAIL — `AttributeError: module 'backend.settings' has no attribute 'migrate_theme'`.

- [ ] **Step 3: Implement**

In `backend/settings.py` add import at top: `from . import degradations` (degradations imports only logging/threading/time — no cycle).

Replace the `theme` FieldSpec (lines 47-58) with two specs:

```python
    FieldSpec(
        "theme_mode", "choice", "THEME_MODE", "system", "appearance", "Theme",
        "System follows the OS. Palettes recolor within the chosen brightness.",
        choices=("system", "dark", "light"),
    ),
    FieldSpec(
        "theme_palette", "choice", "THEME_PALETTE", "", "appearance", "Palette",
        "Color family layered on top. Empty uses the built-in colors.",
        choices=(
            "", "gruvbox", "catppuccin", "solarized",
            "nord", "dracula", "tokyo-night", "one-dark",
            "everforest", "rose-pine", "kanagawa",
        ),
    ),
```

After `_FIELD_BY_KEY` add:

```python
_LIGHT_ALIAS = {
    "gruvbox-light": "gruvbox",
    "catppuccin-latte": "catppuccin",
    "solarized-light": "solarized",
}


def migrate_theme(raw: Any) -> tuple[str, str]:
    """Map a legacy ``theme`` value onto (theme_mode, theme_palette)."""
    text = "" if raw is None else str(raw).strip()
    if text in ("system", "dark", "light"):
        return text, ""
    if text in _LIGHT_ALIAS:
        return "light", _LIGHT_ALIAS[text]
    palette = _FIELD_BY_KEY["theme_palette"]
    if text in palette.choices:
        return "dark", text
    degradations.report("settings", "theme", "unknown value reset")
    return "system", ""


def _migrate_stored(stored: dict[str, Any]) -> dict[str, Any]:
    legacy = stored.get("theme")
    if legacy is None or "theme_mode" in stored or "theme_palette" in stored:
        return stored
    out = {k: v for k, v in stored.items() if k != "theme"}
    mode, palette = migrate_theme(legacy)
    out["theme_mode"], out["theme_palette"] = mode, palette
    return out
```

In `resolve()` change line 203 to:

```python
    stored = _migrate_stored(port_store.get_stored_settings())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_theme_migration.py tests/test_settings.py -q`
Expected: PASS (all). Then full suite: `ruff check backend tests mcp && python -m pytest -q` — PASS except any test asserting `/api/meta "theme"` (that is Task 2; if such a test fails here, note it and fix in Task 2).

- [ ] **Step 5: Commit**

```bash
git add backend/settings.py tests/test_theme_migration.py tests/test_settings.py
git commit -m "Split theme setting into mode and palette with legacy migration"
```

---

### Task 2: Echo points in /api/meta

**Files:**
- Modify: `backend/main.py` (~line 154, meta endpoint return)

**Interfaces:**
- Consumes: `values["theme_mode"]`, `values["theme_palette"]` from Task 1.
- Produces: `/api/meta` JSON keys `theme_mode`, `theme_palette` (replaces `theme`). Frontend Task 5 consumes these via the settings doc, not meta — meta is informational.

- [ ] **Step 1: Write failing test**

Append to `tests/test_theme_migration.py`:

```python
def test_meta_echoes_mode_and_palette(client_factory=None):
    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)
    body = client.get("/api/meta").json()
    assert "theme" not in body
    assert body["theme_mode"] in ("system", "dark", "light")
    assert isinstance(body["theme_palette"], str)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_theme_migration.py::test_meta_echoes_mode_and_palette -q`
Expected: FAIL (`assert "theme" not in body` — key still present).

- [ ] **Step 3: Implement**

In `backend/main.py` meta endpoint return (~line 154) replace `"theme": values["theme"],` with:

```python
        "theme_mode": values["theme_mode"],
        "theme_palette": values["theme_palette"],
```

- [ ] **Step 4: Verify**

Run: `python -m pytest -q`
Expected: whole suite PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_theme_migration.py
git commit -m "Echo theme_mode/theme_palette from /api/meta"
```

---

### Task 3: Frontend state — variant map, mode resolution, attribute writer

**Files:**
- Modify: `frontend/js/state.js` (line 12 `CORE_THEMES`; ~line 36 defaults; lines 76-95 applyTheme/applyAppearance)

**Interfaces:**
- Produces (consumed by Tasks 4-6):
  - `export const CORE_THEMES = ['system', 'dark', 'light']` (unchanged)
  - `export const PALETTE_VARIANTS` — object `{ familyId: ['dark'|'light', …] }`
  - `export function resolveMode(requested: string, prefersLight: boolean): 'light'|'dark'`
  - `export function paletteAvailable(family: string, resolved: string): boolean`
- `S.settings` keys become `theme_mode`, `theme_palette`.

- [ ] **Step 1: Write failing test**

Create `frontend/test/theme.test.mjs` following the repo's string/pure-test convention:

```js
/* Pure-function tests for the theme model in state.js. No DOM. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { PALETTE_VARIANTS, resolveMode, paletteAvailable } = await import('../js/state.js?' + V);

test('resolveMode honors explicit modes', () => {
  assert.equal(resolveMode('dark', true), 'dark');
  assert.equal(resolveMode('light', false), 'light');
});

test('resolveMode falls back to prefersLight for system', () => {
  assert.equal(resolveMode('system', true), 'light');
  assert.equal(resolveMode('system', false), 'dark');
  assert.equal(resolveMode('', false), 'dark');
});

test('dual-variant families are available in both modes', () => {
  for (const f of ['gruvbox', 'catppuccin', 'solarized']) {
    assert.ok(paletteAvailable(f, 'dark'));
    assert.ok(paletteAvailable(f, 'light'));
  }
});

test('single-variant families grey out on mismatch', () => {
  assert.ok(!paletteAvailable('dracula', 'light'));
  assert.ok(paletteAvailable('dracula', 'dark'));
  assert.ok(!paletteAvailable('nord', 'light'));
});

test('unknown family is never available', () => {
  assert.ok(!paletteAvailable('nope', 'dark'));
});

test('variant map covers exactly ten families', () => {
  assert.equal(Object.keys(PALETTE_VARIANTS).length, 10);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test frontend/test/theme.test.mjs`
Expected: FAIL — `SyntaxError`/undefined export `PALETTE_VARIANTS`.

- [ ] **Step 3: Implement**

In `frontend/js/state.js`:

Line 12 stays: `export const CORE_THEMES = ['system', 'dark', 'light'];`

Add next to it:

```js
export const PALETTE_VARIANTS = {
  gruvbox: ['dark', 'light'],
  catppuccin: ['dark', 'light'],
  solarized: ['dark', 'light'],
  nord: ['dark'], dracula: ['dark'], 'tokyo-night': ['dark'],
  'one-dark': ['dark'], everforest: ['dark'], 'rose-pine': ['dark'],
  kanagawa: ['dark'],
};
```

In the `S` defaults object (~line 36) replace `theme: 'system',` with:

```js
  theme_mode: 'system',
  theme_palette: '',
```

Replace `applyTheme()` (lines ~76-82):

```js
export function resolveMode(requested, prefersLight) {
  if (requested === 'light' || requested === 'dark') return requested;
  return prefersLight ? 'light' : 'dark';
}

export function paletteAvailable(family, resolved) {
  var variants = PALETTE_VARIANTS[family];
  return !!variants && variants.indexOf(resolved) >= 0;
}

export function applyTheme() {
  var prefersLight = false;
  try {
    prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
  } catch (e) {}
  var mode = resolveMode(S.settings.theme_mode || 'system', prefersLight);
  var html = document.documentElement;
  html.setAttribute('data-mode', mode);
  html.removeAttribute('data-theme');
  var pal = S.settings.theme_palette || '';
  if (pal && paletteAvailable(pal, mode)) {
    html.setAttribute('data-palette', pal);
  } else {
    html.removeAttribute('data-palette');
  }
}
```

In `applyAppearance()` (~line 93) change the cached object to:

```js
      theme_mode: S.settings.theme_mode,
      theme_palette: S.settings.theme_palette || '',
```

(The old cached `theme` key is simply no longer written; stale copies in browsers are ignored — one-time default flash, accepted in spec.)

- [ ] **Step 4: Verify**

Run: `node --test frontend/test/theme.test.mjs`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/js/state.js frontend/test/theme.test.mjs
git commit -m "Add orthogonal mode/palette model to frontend state"
```

---

### Task 4: Frontend pickers — split rows, availability greying, mode-aware previews

**Files:**
- Modify: `frontend/js/settings.js` (imports line 3; `renderThemePicker` lines 207-232; `renderField` branches lines 244-245; `wide` class line 263)

**Interfaces:**
- Consumes: `PALETTE_VARIANTS`, `paletteAvailable`, `resolveMode` from Task 3.
- Produces: `renderModePicker(choices, value, disabled)` and `renderPalettePicker(choices, value, resolvedMode, disabled)` returning HTML strings; radios named `theme_mode` / `theme_palette` (the generic save loop in app.js collects them by name — no save-code change needed); internal helper `currentMode()` shared with Task 5's `syncPaletteAvailability`.

- [ ] **Step 1: Write failing test**

Append to `frontend/test/settings.test.mjs` (imports at top gain `renderModePicker, renderPalettePicker` from the existing dynamic import of settings.js):

```js
test('mode picker renders three core radios named theme_mode', () => {
  const html = renderModePicker(['system', 'dark', 'light'], 'system', '');
  assert.match(html, /name="theme_mode" value="system"/);
  assert.match(html, /name="theme_mode" value="dark"/);
  assert.match(html, /name="theme_mode" value="light"/);
  assert.doesNotMatch(html, /name="theme"/);
});

test('palette picker renders builtin plus ten families', () => {
  const choices = ['', 'gruvbox', 'catppuccin', 'solarized', 'nord', 'dracula',
    'tokyo-night', 'one-dark', 'everforest', 'rose-pine', 'kanagawa'];
  const html = renderPalettePicker(choices, '', 'dark', '');
  assert.match(html, /name="theme_palette" value=""/);
  assert.match(html, /name="theme_palette" value="dracula"/);
  assert.equal((html.match(/class="theme-swatch/g) || []).length, 11);
});

test('palette picker greys mismatched single-variant families', () => {
  const choices = ['', 'nord', 'dracula', 'gruvbox'];
  const light = renderPalettePicker(choices, '', 'light', '');
  assert.match(light, /is-unavailable[^>]*>\s*<input type="radio" name="theme_palette" value="dracula"[^>]*disabled/s);
  assert.doesNotMatch(light, /value="gruvbox"[^>]*disabled/);
  const dark = renderPalettePicker(choices, '', 'dark', '');
  assert.doesNotMatch(dark, /value="dracula"[^>]*disabled/);
});

test('palette preview resolves variant per current mode', () => {
  const choices = ['', 'gruvbox'];
  assert.match(renderPalettePicker(choices, '', 'light', ''), /data-theme-preview="gruvbox-light"/);
  assert.match(renderPalettePicker(choices, '', 'dark', ''), /data-theme-preview="gruvbox"(?!-)/);
});
```

Note: adjust the first assertion's regex to however the label/input ordering lands — the contract is: unavailable swatches carry BOTH class `is-unavailable` on the label AND `disabled` on their input; available ones carry neither.

- [ ] **Step 2: Run to verify failure**

Run: `node --test frontend/test/settings.test.mjs`
Expected: FAIL — `renderModePicker is not defined`.

- [ ] **Step 3: Implement**

Update the state.js import (line 3) to also bring `PALETTE_VARIANTS, paletteAvailable, resolveMode` (add them to the existing braces; keep `?v=` suffix synced with whatever Task 7 bumps — during development keep as-is).

Replace `renderThemePicker` (lines 207-232) with:

```js
  function modeSwatch(c, current, disabled) {
    const on = c === current;
    const preview = c === 'system'
      ? '<span class="theme-swatch-preview is-system" aria-hidden="true">' +
        '<span class="theme-swatch-half dark"></span><span class="theme-swatch-half light"></span></span>'
      : '<span class="theme-swatch-preview" aria-hidden="true"><i class="used"></i><i class="configured"></i><i class="free"></i></span>';
    return '<label class="theme-swatch" data-theme-preview="' + escapeHtml(c) + '">' +
      '<input type="radio" name="theme_mode" value="' + escapeHtml(c) + '"' +
      (on ? ' checked' : '') + disabled + '>' + preview +
      '<span class="theme-swatch-name" data-i18n="choice.' + c + '">' +
      escapeHtml(choiceLabel(c)) + '</span></label>';
  }

  export function renderModePicker(choices, value, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : 'system';
    const label = escapeHtml(t('settings.fields.theme_mode.label'));
    const core = CORE_THEMES.filter(function (c) { return choices.indexOf(c) >= 0; });
    return '<div class="theme-picker" role="radiogroup" aria-label="' + label + '">' +
      '<div class="theme-picker-core">' + core.map(function (c) {
        return modeSwatch(c, current, disabled);
      }).join('') + '</div></div>';
  }

  function currentMode() {
    let prefersLight = false;
    try {
      prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    } catch (e) {}
    return resolveMode(S.settings.theme_mode || 'system', prefersLight);
  }

  export function renderPalettePicker(choices, value, resolvedModeValue, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : '';
    const mode = resolvedModeValue || currentMode();
    const label = escapeHtml(t('settings.fields.theme_palette.label'));

    function previewId(family) {
      if (mode === 'light' && PALETTE_VARIANTS[family].indexOf('light') >= 0) {
        return family + '-light';
      }
      return family;
    }

    function entry(family) {
      const on = family === current;
      const available = family === '' || paletteAvailable(family, mode);
      const cls = available ? 'theme-swatch' : 'theme-swatch is-unavailable';
      const dis = available ? disabled : ' disabled';
      const previewIdResolved = family === '' ? mode : previewId(family);
      const preview = '<span class="theme-swatch-preview" aria-hidden="true">' +
        '<i class="used"></i><i class="configured"></i><i class="free"></i></span>';
      const nameKey = family === '' ? 'settings.theme.builtin' : 'choice.' + family;
      const nameText = family === '' ? escapeHtml(t('settings.theme.builtin')) : escapeHtml(choiceLabel(family));
      return '<label class="' + cls + '" data-theme-preview="' + escapeHtml(previewIdResolved) + '">' +
        '<input type="radio" name="theme_palette" value="' + escapeHtml(family) + '"' +
        (on ? ' checked' : '') + dis + '>' + preview +
        '<span class="theme-swatch-name" data-i18n="' + nameKey + '">' + nameText + '</span></label>';
    }

    const families = choices.filter(function (c) { return c !== ''; });
    return '<div class="theme-picker" role="radiogroup" aria-label="' + label + '">' +
      '<p class="theme-picker-label" data-i18n="settings.theme.palettes">' +
      escapeHtml(t('settings.theme.palettes')) + '</p>' +
      '<div class="theme-picker-palettes">' + entry('').concat(families.map(entry).join('')) + '</div></div>';
  }
```

In `renderField` replace the `f.key === 'theme'` branch (lines 244-245) with:

```js
    } else if (f.key === 'theme_mode') {
      control = renderModePicker(f.choices || [], value, disabled);
    } else if (f.key === 'theme_palette') {
      control = renderPalettePicker(f.choices || [], value, currentMode(), disabled);
```

Line 263 wide class becomes:

```js
    const wide = f.key === 'theme_mode' || f.key === 'theme_palette' ? ' is-wide' : '';
```

The builtin entry's system-style half/half preview is intentionally not used — its preview shows the resolved built-in colors via the existing `data-theme-preview="dark|light"` CSS blocks.

- [ ] **Step 4: Verify**

Run: `node --test frontend/test/settings.test.mjs frontend/test/theme.test.mjs`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/js/settings.js frontend/test/settings.test.mjs
git commit -m "Split settings theme picker into mode row and availability-aware palette row"
```

---

### Task 5: Frontend wiring — change handler, system listener, dirty marking

**Files:**
- Modify: `frontend/js/app.js` (line 121 listener guard; lines 583-600 change handler)

**Interfaces:**
- Consumes: `syncPaletteAvailability` exported from settings.js in this task; `S.settings.theme_mode/theme_palette` from Task 3.

- [ ] **Step 1: Implement** (wiring glue — covered by Task 8's end-to-end check; no DOM harness exists for string-only tests)

Export from `frontend/js/settings.js` (place after `renderPalettePicker`; reuses `currentMode` from Task 4):

```js
  export function syncPaletteAvailability() {
    const mode = currentMode();
    document.querySelectorAll('.theme-swatch[data-theme-preview]').forEach(function (labelEl) {
      const input = labelEl.querySelector('input[name="theme_palette"]');
      if (!input) return;
      const family = input.value;
      if (family === '') return;
      const available = paletteAvailable(family, mode);
      input.disabled = !available;
      labelEl.classList.toggle('is-unavailable', !available);
    });
  }
```

In `app.js`, check whether a `from './settings.js?…'` import already exists: extend it with `syncPaletteAvailability`, otherwise add a new import line next to the other module imports, matching the version query used elsewhere in that file:

```js
import { syncPaletteAvailability } from './settings.js?v=<V>';
```

Change line 121 guard to:

```js
      if ((S.settings.theme_mode || 'system') === 'system') {
        applyTheme();
        syncPaletteAvailability();
      }
```

Replace the change-handler block (lines 584-589) with:

```js
    if (field === 'theme_mode' || field === 'theme_palette' || field === 'grid_density' || field === 'locale') {
      if (field === 'theme_mode') S.settings.theme_mode = e.target.value;
      if (field === 'theme_palette') S.settings.theme_palette = e.target.value;
      if (field === 'grid_density') S.settings.grid_density = e.target.value;
      if (field === 'locale') S.settings.locale = e.target.value;
      applyAppearance();
      if (field === 'theme_mode') syncPaletteAvailability();
```

(keep the rest of that handler unchanged).

- [ ] **Step 2: Verify**

Run: `node --test "frontend/test/**/*.test.mjs"`
Expected: PASS (no regressions).

- [ ] **Step 3: Commit**

```bash
git add frontend/js/app.js frontend/js/settings.js
git commit -m "Wire mode changes to palette availability and system scheme flips"
```

---

### Task 6: CSS — mode/palette attribute selectors + unavailable style

**Files:**
- Modify: `frontend/style.css` (lines 4-368 theme blocks; append availability rule)

**Interfaces:**
- Consumes: `<html data-mode="…">` / `<html data-palette="…">` from Task 3.

- [ ] **Step 1: Rewrite selectors (mechanical, no variable-value changes)**

- Line 4: `[data-theme="dark"] {` → `[data-mode="dark"] {` — keep `color-scheme: dark;` and all variables.
- Line 46: `[data-theme="light"] {` → `[data-mode="light"] {` — likewise.
- Dual-variant merges (drop each block's `color-scheme:` line, move light block's variables under the merged selector):
  - `[data-theme="gruvbox"]` → `[data-palette="gruvbox"][data-mode="dark"]`
  - `[data-theme="gruvbox-light"]` → `[data-palette="gruvbox"][data-mode="light"]`
  - Same for catppuccin/catppuccin-latte and solarized/solarized-light.
- Dark-only (drop `color-scheme:` line):
  - `[data-theme="nord"]` → `[data-palette="nord"][data-mode="dark"]`
  - `[data-theme="dracula"]` → `[data-palette="dracula"][data-mode="dark"]`
  - `[data-theme="tokyo-night"]` → `[data-palette="tokyo-night"][data-mode="dark"]`
  - `[data-theme="one-dark"]` → `[data-palette="one-dark"][data-mode="dark"]`
  - `[data-theme="everforest"]` → `[data-palette="everforest"][data-mode="dark"]`
  - `[data-theme="rose-pine"]` → `[data-palette="rose-pine"][data-mode="dark"]`
  - `[data-theme="kanagawa"]` → `[data-palette="kanagawa"][data-mode="dark"]`
- Swatch preview blocks (lines ~937-982, `[data-theme-preview=…]`) stay byte-identical — Task 4 emits those exact ids.

- [ ] **Step 2: Add unavailable styling**

Append near the swatch styles:

```css
.theme-swatch.is-unavailable { opacity: 0.35; cursor: not-allowed; }
.theme-swatch.is-unavailable .theme-swatch-preview { filter: grayscale(1); }
```

- [ ] **Step 3: Sanity-grep**

Run: `grep -c 'data-theme="' frontend/style.css` — expected output includes ONLY the 15 `[data-theme-preview=` occurrences (13 palettes + dark/light) and zero bare `[data-theme=` selectors. Then `grep -c 'data-palette=' frontend/style.css` → 13.

- [ ] **Step 4: Commit**

```bash
git add frontend/style.css
git commit -m "Key palette variables off data-palette/data-mode pairs"
```

---

### Task 7: Locales, cache-bust, i18n parity

**Files:**
- Modify: `frontend/locales/{en,zh-CN,zh-TW,ja}.json`
- Modify: `frontend/index.html` (3 tag bumps), `frontend/i18n.js` (`CACHE_BUST`)
- Modify: every changed JS/CSS import chain keeps one shared version string — bump ALL `?v=61` to `?v=62` in index.html and set `CACHE_BUST = '62'` in i18n.js.

- [ ] **Step 1: Edit the four locale files identically (same keys)**

Remove keys: `choice.gruvbox-light`, `choice.catppuccin-latte`, `choice.solarized-light`, `settings.fields.theme.label`, `settings.fields.theme.help`.
Add keys (exact values):

en.json:
```json
"settings": {
  "fields": {
    "theme_mode": { "label": "Theme", "help": "System follows the OS. Palettes recolor within the chosen brightness." },
    "theme_palette": { "label": "Palette", "help": "Color family layered on top. Unavailable ones grey out." }
  },
  "theme": { "builtin": "Built-in" }
}
```
(Merge into the existing nested objects — do not replace siblings like `settings.theme.palettes`.)

zh-CN.json: labels `"主题"` / `"配色"`；help `"跟随系统明暗。配色在所选明暗内着色。"` / `"叠加的色彩家族。不适用的会置灰。"`；builtin `"内置"`。
zh-TW.json: `"主題"` / `"配色"`；help `"跟隨系統明暗。配色在所選明暗內著色。"` / `"疊加的色彩家族。不適用的會置灰。"`；builtin `"內建"`。
ja.json: `"テーマ"` / `"パレット"`；help `"システムに従う。パレットは選択した明暗の中で着色します。"` / `"重ねるカラーファミリー。利用できないものはグレーアウトします。"`；builtin `"組み込み"`。

- [ ] **Step 2: Bump cache-bust**

`frontend/index.html`: all three `?v=61` → `?v=62` (style.css line 18, i18n.js line 176, app.js line 177).
`frontend/i18n.js`: `CACHE_BUST = '61'` → `'62'`.

- [ ] **Step 3: Verify parity**

Run: `python -m pytest tests/test_i18n.py -q`
Expected: PASS.

Run: `node --test "frontend/test/**/*.test.mjs"`
Expected: PASS (settings.test.mjs asserts read the live `?v=` dynamically).

- [ ] **Step 4: Commit**

```bash
git add frontend/locales frontend/index.html frontend/i18n.js
git commit -m "Update locale keys and bump cache-bust for theme split"
```

---

### Task 8: CHANGELOG + full verification

**Files:**
- Modify: `CHANGELOG.md` (Unreleased section)

- [ ] **Step 1: Add Unreleased bullet** (merge into the section created by the agent-visibility work; create the section if absent):

```markdown
### Changed
- Themes: brightness (system / light / dark) and color palette are now independent controls; the palette list shows one entry per family and follows the chosen brightness. Replaces the `theme` setting with `theme_mode` + `theme_palette` — saved values migrate automatically; the `THEME` env var is replaced by `THEME_MODE` and `THEME_PALETTE`.
```

- [ ] **Step 2: Full verification**

```bash
ruff check backend tests mcp
python -m pytest -q
node --test "frontend/test/**/*.test.mjs"
```
Expected: ruff clean, all Python tests pass, all JS tests pass.

- [ ] **Step 3: Manual smoke (server already runs at localhost:2100)**

1. Open `http://localhost:2100/#/settings/appearance`.
2. Pick Theme = Light → Dracula greys out, Gruvbox preview turns light; grid falls back to built-in light.
3. Pick Palette = Gruvbox → colors switch to gruvbox-light; toggle Theme = Dark → gruvbox dark variant applies without touching the palette.
4. Set Theme = System, Palette = Dracula, flip OS appearance → colors fall back to built-in while OS is light, Dracula returns on dark.
5. Save → reload page → selections persist; `curl -s localhost:2100/api/meta | grep theme` shows `theme_mode`/`theme_palette`.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "Document theme orthogonalization in the changelog"
```
