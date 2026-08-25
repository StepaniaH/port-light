# Custom Themes and Display Sliders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server-stored named custom palettes (15 core colors) editable in a new advanced theme editor, plus two continuous display sliders (card size, text size) replacing the binary density toggle.

**Architecture:** New `backend/themes.py` store mirrors `port_store`'s atomic-write/quarantine pattern in `<data-dir>/themes.json`. CRUD lives under `/api/custom-themes`; selection reuses `theme_palette=@custom:<id>`. Frontend applies custom palettes by writing the 15 CSS custom properties inline on `<html>` (inline wins the cascade over static `[data-palette]` blocks). Sliders are two new int settings (`card_scale`, `text_scale`, 0–100) rendered as `<input type="range">`; their change handlers map position onto geometry variables defined in this plan.

**Tech Stack:** Python 3.11 stdlib + FastAPI (existing), vanilla ES modules (no build step), pytest, node:test.

**Spec:** `docs/superpowers/specs/2026-08-25-custom-themes-display-sliders-design.md`

## Global Constraints

- Backend stdlib + requirements.txt only; no new frameworks.
- Frontend vanilla JS, no bundler; `escapeHtml` on every interpolated string.
- Locale parity across `frontend/locales/{en,zh-CN,zh-TW,ja}.json` — `tests/test_i18n.py` fails otherwise. Every new `FieldSpec` needs `settings.fields.<key>.label` + `.help`; every group needs `settings.groups.<group>.title` + `.blurb`.
- Color values accept ONLY `/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/` (lowercased on store). Never interpolate unvalidated strings into CSS or HTML.
- Bump `?v=` on style.css/app.js tags in index.html AND every `?v=` in frontend/js/*.js AND `CACHE_BUST` in i18n.js when those files change (version-parity test enforces consistency).
- CHANGELOG.md gets Unreleased bullets documenting shipped behavior only.
- Do not commit `.env`, `/data/`, `themes.json`.
- One concern per commit; commit after each task's green tests.
- Test commands: `.venv/bin/python -m pytest`, `node --test frontend/test/*.test.mjs`, `.venv/bin/ruff check .`.

## Geometry reference (anchors for Task 7)

Measured from `frontend/style.css` today:

| Token | Airy (scale 0) | Comfortable (scale 50) | Compact (scale 100) |
|---|---|---|---|
| cell min column width | 164px | 138px | 112px |
| grid gap | 10px | 8px | 6px |
| cell padding X | 14px | 12px | 10px |
| cell padding Y | 12px | 10px | 8px |
| cell min-height | 76px | 64px | 52px |

Linear interpolation between the Airy and Compact endpoints reproduces Comfortable exactly at 50: `value = airy + (compact − airy) × scale/100`.

## Custom-palette variable map (used by Tasks 4–5)

JSON key (camelCase) → CSS custom property:

```
bg→--bg  elevated→--elevated  card→--card  cardHover→--card-hover
border→--border  text→--text  textDim→--text-dim  used→--used
configured→--configured  free→--free  accent→--accent  conflict→--conflict
access→--access  hidden→--hidden  danger→--danger
```

---

### Task 1: `backend/themes.py` — validated custom-palette store

**Files:**
- Create: `backend/themes.py`
- Create: `tests/test_themes_store.py`

**Interfaces:**
- Consumes: `degradations.report(scope, subject, detail)` (existing).
- Produces (used by Tasks 2, 5, 6):
  - `THEME_ERROR_CLASS`: `class ThemeError(ValueError)`
  - `COLOR_KEYS: tuple[str, ...]` — the 15 camelCase keys
  - `validate(payload: object) -> dict` — returns `{name, basedOn, mode, colors}`; raises `ThemeError`
  - `list_themes() -> list[dict]`, `add_theme(payload) -> dict` (adds `id`),
    `update_theme(theme_id: str, payload) -> dict`, `delete_theme(theme_id: str) -> bool`,
    `theme_exists(theme_id: str) -> bool`
  - `MAX_THEMES = 24`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the custom-themes store: validation, cap, quarantine."""
from __future__ import annotations

import json

import pytest

from backend import themes


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))


def valid_colors():
    return {key: "#112233" for key in themes.COLOR_KEYS}


VALID = {"name": "Warm", "basedOn": "gruvbox", "mode": "dark", "colors": valid_colors()}


def test_add_and_list_roundtrip():
    saved = themes.add_theme(VALID)
    assert len(saved["id"]) == 8
    assert themes.list_themes() == [saved]


def test_validate_rejects_bad_payloads():
    bad = [
        None,
        {},
        {**VALID, "name": ""},
        {**VALID, "name": "x" * 41},
        {**VALID, "basedOn": "not-a-family"},
        {**VALID, "mode": "auto"},
        {**VALID, "colors": {**valid_colors(), "accent": "rgb(1,2,3)"}},
        {**VALID, "colors": {**valid_colors(), "accent": "url(javascript:alert(1))"}},
        {**VALID, "colors": {**valid_colors(), "accent": "#zzz"}},
        {**VALID, "colors": {}},
    ]
    for payload in bad:
        with pytest.raises(themes.ThemeError):
            themes.validate(payload)


def test_update_and_delete():
    saved = themes.add_theme(VALID)
    renamed = themes.update_theme(saved["id"], {**VALID, "name": "Cooler"})
    assert renamed["name"] == "Cooler"
    assert themes.delete_theme(saved["id"]) is True
    assert themes.list_themes() == []
    assert themes.delete_theme(saved["id"]) is False


def test_cap_at_max_themes(monkeypatch):
    for i in range(themes.MAX_THEMES):
        themes.add_theme({**VALID, "name": f"t{i}"})
    with pytest.raises(themes.ThemeError):
        themes.add_theme(VALID)


def test_corrupt_file_quarantined(tmp_path):
    f = tmp_path / "themes.json"
    f.write_text("{not json", encoding="utf-8")
    assert themes.list_themes() == []
    assert (tmp_path / "themes.json.bad").exists()


def test_invalid_entries_dropped_on_read(tmp_path):
    f = tmp_path / "themes.json"
    f.write_text(json.dumps([
        {"id": "aaaaaaaa", **VALID},
        {"id": "zzzzzzzz", **VALID},          # bad id chars
        {"id": "bbbbbbbb", "name": "", "colors": valid_colors(), "mode": "dark"},
        "garbage",
    ]), encoding="utf-8")
    listed = themes.list_themes()
    assert [t["id"] for t in listed] == ["aaaaaaaa"]
```

- [ ] **Step 2: Run tests, expect import failure**

Run: `.venv/bin/python -m pytest tests/test_themes_store.py -q`
Expected: FAIL (`ModuleNotFoundError: backend.themes` or equivalent collection error).

- [ ] **Step 3: Implement `backend/themes.py`**

```python
"""Named custom palettes stored in <data-dir>/themes.json.

Same ownership pattern as custom_ports.json: one JSON file in the data dir,
atomic writes, corrupt files quarantined with a degradation line. Colors are
validated against a strict hex grammar so nothing unvetted can reach a CSS
custom property.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

from . import degradations

MAX_THEMES = 24

COLOR_KEYS: tuple[str, ...] = (
    "bg", "elevated", "card", "cardHover", "border", "text", "textDim",
    "used", "configured", "free", "accent", "conflict", "access", "hidden",
    "danger",
)

_ID_RE = re.compile(r"[0-9a-f]{8}\Z")
_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")

_LOCK = threading.Lock()


class ThemeError(ValueError):
    """Raised for invalid payloads or capacity violations."""


def _file() -> Path:
    return Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data")) / "themes.json"


def _families() -> tuple[str, ...]:
    from .settings import _FIELD_BY_KEY

    return tuple(c for c in _FIELD_BY_KEY["theme_palette"].choices if c)


def validate(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ThemeError("body must be an object")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 40:
        raise ThemeError("name must be 1-40 characters")
    based_on = str(payload.get("basedOn") or "").strip()
    if based_on and based_on not in _families():
        raise ThemeError("unknown basedOn family: " + based_on)
    mode = payload.get("mode")
    if mode not in ("dark", "light"):
        raise ThemeError("mode must be dark or light")
    colors = payload.get("colors")
    if not isinstance(colors, dict):
        raise ThemeError("colors must be an object")
    clean: dict[str, str] = {}
    for key in COLOR_KEYS:
        value = str(colors.get(key) or "").strip()
        if not _COLOR_RE.fullmatch(value):
            raise ThemeError("color '" + key + "' must be a #hex value")
        clean[key] = value.lower()
    return {"name": name, "basedOn": based_on, "mode": mode, "colors": clean}


def _load() -> list[dict]:
    f = _file()
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            os.replace(f, f.parent / (f.name + ".bad"))
        except OSError:
            pass
        degradations.report("themes", f.name, "corrupt file quarantined")
        return []
    except OSError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        theme_id = str(item.get("id") or "")
        if not _ID_RE.fullmatch(theme_id):
            continue
        try:
            clean = validate({
                "name": item.get("name"),
                "basedOn": item.get("basedOn"),
                "mode": item.get("mode"),
                "colors": item.get("colors"),
            })
        except ThemeError:
            continue
        clean["id"] = theme_id
        out.append(clean)
    return out


def _save(items: list[dict]) -> None:
    d = _file().parent
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".themes.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, _file())
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise ThemeError("cannot write themes.json (permission denied)") from exc
        raise
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def list_themes() -> list[dict]:
    with _LOCK:
        return _load()


def theme_exists(theme_id: str) -> bool:
    with _LOCK:
        return any(t["id"] == theme_id for t in _load())


def add_theme(payload: object) -> dict:
    clean = validate(payload)
    with _LOCK:
        items = _load()
        if len(items) >= MAX_THEMES:
            raise ThemeError("custom theme limit reached (" + str(MAX_THEMES) + ")")
        clean["id"] = secrets.token_hex(4)
        items.append(clean)
        _save(items)
        return dict(clean)


def update_theme(theme_id: str, payload: object) -> dict:
    clean = validate(payload)
    with _LOCK:
        items = _load()
        for index, item in enumerate(items):
            if item["id"] == theme_id:
                clean["id"] = theme_id
                items[index] = clean
                _save(items)
                return dict(clean)
    raise ThemeError("no such theme")


def delete_theme(theme_id: str) -> bool:
    with _LOCK:
        items = _load()
        kept = [item for item in items if item["id"] != theme_id]
        if len(kept) == len(items):
            return False
        _save(kept)
        return True
```

- [ ] **Step 4: Run tests until green**

Run: `.venv/bin/python -m pytest tests/test_themes_store.py -q`
Expected: PASS (all).

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check .
git add backend/themes.py tests/test_themes_store.py
git commit -m "Add validated custom-theme store (themes.json)"
```

---

### Task 2: CRUD routes, snapshot exposure, `@custom:` selection

**Files:**
- Modify: `backend/main.py` (import block near line 20; `get_settings` at line 387; new routes after the settings routes ~line 409)
- Modify: `backend/settings.py` (`_coerce` at line 183)
- Create: `tests/test_custom_themes_api.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces:
  - Routes: `GET /api/custom-themes` → `{themes: [...]}`; `POST /api/custom-themes` (201 body = created theme); `PUT /api/custom-themes/{id}`; `DELETE /api/custom-themes/{id}` → `{removed: id}`. Writes 403 under readonly, 400 on `ThemeError`, DELETE 404 unknown id.
  - `GET /api/settings` response gains top-level `custom_themes: [...]`.
  - `theme_palette` accepts values matching `@custom:[0-9a-f]{8}` (format check only; existence enforced at delete time by resetting selection).

- [ ] **Step 1: Write failing tests**

```python
"""API surface for /api/custom-themes and @custom: palette selection."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import themes
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    yield


def colors():
    return {key: "#4488cc" for key in themes.COLOR_KEYS}


PAYLOAD = {"name": "Mine", "basedOn": "", "mode": "dark", "colors": colors()}


def test_crud_roundtrip():
    created = client.post("/api/custom-themes", json=PAYLOAD)
    assert created.status_code == 200
    theme = created.json()
    assert len(theme["id"]) == 8
    listed = client.get("/api/custom-themes").json()["themes"]
    assert listed == [theme]
    updated = client.put("/api/custom-themes/" + theme["id"],
                         json={**PAYLOAD, "name": "Renamed"})
    assert updated.json()["name"] == "Renamed"
    gone = client.delete("/api/custom-themes/" + theme["id"])
    assert gone.json() == {"removed": theme["id"]}
    missing = client.delete("/api/custom-themes/" + theme["id"])
    assert missing.status_code == 404


def test_post_rejects_injection_color():
    bad = {**PAYLOAD, "colors": {**colors(), "bg": "red url(x)"}}
    assert client.post("/api/custom-themes", json=bad).status_code == 400


def test_snapshot_carries_custom_themes():
    theme = client.post("/api/custom-themes", json=PAYLOAD).json()
    snap = client.get("/api/settings").json()
    assert snap["custom_themes"] == [theme]


def test_select_custom_palette_persists():
    theme = client.post("/api/custom-themes", json=PAYLOAD).json()
    sel = "@custom:" + theme["id"]
    put = client.put("/api/settings", json={"theme_palette": sel})
    assert put.status_code == 200
    assert put.json()["values"]["theme_palette"] == sel


def test_delete_selected_theme_resets_selection():
    theme = client.post("/api/custom-themes", json=PAYLOAD).json()
    sel = "@custom:" + theme["id"]
    client.put("/api/settings", json={"theme_palette": sel})
    client.delete("/api/custom-themes/" + theme["id"])
    values = client.get("/api/settings").json()["values"]
    assert values["theme_palette"] == ""


def test_writes_forbidden_when_readonly(monkeypatch):
    monkeypatch.setenv("SETTINGS_READONLY", "1")
    assert client.post("/api/custom-themes", json=PAYLOAD).status_code == 403
    assert client.put("/api/settings", json={"theme_palette": "@custom:11111111"}).status_code == 403
```

Note: `put_settings` already maps `PermissionError` → 403, and `ThemeError` being a
`ValueError` subclass means the generic 400 mapping applies wherever raised.

- [ ] **Step 2: Run tests, expect failures**

Run: `.venv/bin/python -m pytest tests/test_custom_themes_api.py -q`
Expected: FAIL — 404 routes, missing `custom_themes` key, 400 on `@custom:` select.

- [ ] **Step 3: Implement**

In `backend/main.py` extend the module import line:

```python
from . import agent_events, degradations, history, hosts, webhooks, port_store, themes
```

Replace `get_settings`:

```python
@app.get("/api/settings")
def get_settings() -> dict:
    body = app_settings.snapshot()
    body["custom_themes"] = themes.list_themes()
    return body
```

Add after `put_settings`:

```python
@app.get("/api/custom-themes")
def get_custom_themes() -> dict:
    return {"themes": themes.list_themes()}


@app.post("/api/custom-themes")
def post_custom_theme(body: dict = Body(...)) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    try:
        return themes.add_theme(body)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/custom-themes/{theme_id}")
def put_custom_theme(theme_id: str, body: dict = Body(...)) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    try:
        return themes.update_theme(theme_id, body)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/custom-themes/{theme_id}")
def delete_custom_theme(theme_id: str) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    if not themes.delete_theme(theme_id):
        raise HTTPException(status_code=404, detail="no such theme")
    current, _ = app_settings.resolve()
    if current.get("theme_palette") == "@custom:" + theme_id:
        app_settings.apply_patch({"theme_palette": ""})
    return {"removed": theme_id}
```

In `backend/settings.py` add `import re` to the stdlib imports, then insert at the top
of the string-handling section of `_coerce` (before the `choice` branch):

```python
    if spec.key == "theme_palette":
        custom = re.fullmatch(r"@custom:([0-9a-f]{8})", text)
        if custom:
            return text
```

- [ ] **Step 4: Run full python suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (new file green; no regressions).

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check .
git add backend/main.py backend/settings.py tests/test_custom_themes_api.py
git commit -m "Wire custom-theme CRUD into the API and palette selection"
```

---

### Task 3: Appearance panel split into Theme / Layout / Language sections

**Files:**
- Modify: `frontend/js/settings.js` (`renderSettingsForm`, lines ~573-594)
- Modify: `frontend/locales/en.json`, `zh-CN.json`, `zh-TW.json`, `ja.json` (new keys under `settings`)
- Modify: `frontend/index.html` + `frontend/js/*.js` + `frontend/i18n.js` (cache-bust `63` → `64`)
- Modify: `frontend/test/settings.test.mjs` (assertions about panel composition)

**Interfaces:**
- Consumes: existing `byGroup` rendering, `CARD_FIELD_KEYS`.
- Produces: appearance panel contains three `settings-card` sections keyed by
  `settings.sections.theme.*`, `settings.cards.*` (existing, becomes Layout), and
  `settings.sections.language.*`. Later tasks insert editor/sliders into the Theme and
  Layout sections respectively.

- [ ] **Step 1: Failing node test**

Append to `frontend/test/settings.test.mjs`:

```js
test('appearance panel renders theme/layout/language sections in order', () => {
  const { renderSettingsForm } = mod;
  const host = document.createElement('div');
  host.id = 'settings-fields';
  document.body.appendChild(host);
  const lead = document.createElement('p');
  lead.id = 'settings-lead';
  document.body.appendChild(lead);
  const status = document.createElement('p');
  status.id = 'settings-status';
  document.body.appendChild(status);
  const save = document.createElement('button');
  save.id = 'settings-save';
  document.body.appendChild(save);
  globalThis.window.PortLightI18n = {
    t(key) { return key; },
    load() { return Promise.resolve('en'); },
    applyDom() {},
  };
  renderSettingsForm({
    values: {}, fields: [], readonly: false, source: 'auto',
    env_only: {}, origins: {},
  });
  const panels = host.querySelectorAll('[data-settings-panel="appearance"] .settings-card > header h2');
  const titles = Array.from(panels).map((el) => el.getAttribute('data-i18n'));
  assert.deepEqual(titles, ['settings.sections.theme.title', 'settings.cards.title', 'settings.sections.language.title']);
  host.remove(); lead.remove(); status.remove(); save.remove();
});
```

At the top of the same file, extend the import destructure to also pull
`renderSettingsForm` and expose it as `mod.renderSettingsForm` (the file imports a fixed
set today; add `renderSettingsForm` to the destructured list and assign
`const mod = { renderSettingsForm }` right after, so the assertion above resolves).

- [ ] **Step 2: Run, expect failure**

Run: `node --test frontend/test/settings.test.mjs`
Expected: FAIL — titles array is `['settings.groups.appearance.title', 'settings.cards.title']`.

- [ ] **Step 3: Implement**

In `renderSettingsForm`, split `lookFields` further and rebuild the appearance panel:

```js
    const appearanceFields = byGroup.appearance || [];
    const themeFields = appearanceFields.filter(function (f) {
      return !CARD_FIELD_KEYS[f.key] && f.key !== 'locale' && f.key !== 'grid_density';
    });
    const languageFields = appearanceFields.filter(function (f) { return f.key === 'locale'; });
    const cardFields = appearanceFields.filter(function (f) { return CARD_FIELD_KEYS[f.key]; });
```

and the panel HTML becomes:

```js
      settingsPanelHtml('appearance',
        settingsCard('settings.sections.theme.title', 'settings.sections.theme.blurb',
          '<div data-appearance-section="theme">' + rowsFor(themeFields) + '</div>') +
        settingsCard('settings.cards.title', 'settings.cards.blurb', rowsFor(cardFields)) +
        settingsCard('settings.sections.language.title', 'settings.sections.language.blurb',
          rowsFor(languageFields))) +
```

(`grid_density` is dropped here ahead of Task 7's slider replacement; nothing renders it
anymore.)

Locale additions — insert under `"settings"` in **each** of the four files (translations
as given):

en.json:
```json
"sections": {
  "theme": {
    "title": "Theme",
    "blurb": "Brightness, palette, and your own custom palettes."
  },
  "language": {
    "title": "Language",
    "blurb": "Interface language."
  }
},
```

zh-CN.json:
```json
"sections": {
  "theme": {
    "title": "主题",
    "blurb": "明暗、色板与你的自定义色板。"
  },
  "language": {
    "title": "语言",
    "blurb": "界面显示语言。"
  }
},
```

zh-TW.json:
```json
"sections": {
  "theme": {
    "title": "主題",
    "blurb": "明暗、色板與你的自訂色板。"
  },
  "language": {
    "title": "語言",
    "blurb": "介面顯示語言。"
  }
},
```

ja.json:
```json
"sections": {
  "theme": {
    "title": "テーマ",
    "blurb": "明るさ・パレットと自分のカスタムパレット。"
  },
  "language": {
    "title": "言語",
    "blurb": "インターフェースの表示言語。"
  }
},
```

Cache-bust: `sed -i '' 's/?v=63/?v=64/g' frontend/index.html frontend/js/*.js` and
`CACHE_BUST = '63'` → `'64'` in `frontend/i18n.js`.

- [ ] **Step 4: Run suites**

Run: `node --test frontend/test/*.test.mjs && .venv/bin/python -m pytest tests/test_i18n.py -q`
Expected: all PASS (parity test forces all four locales to carry the new keys).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Split appearance settings into theme, layout, and language sections"
```

---

### Task 4: Apply and pick custom palettes

**Files:**
- Modify: `frontend/js/state.js` (`applyTheme`, `PALETTE_VARIANTS` area)
- Modify: `frontend/js/settings.js` (`renderPalettePicker`, `syncPaletteAvailability`, `applyServerSettings`)
- Create: `frontend/test/theme-custom.test.mjs`
- Cache-bust `64` → `65` (same sweep as Task 3)

**Interfaces:**
- Consumes: `doc.custom_themes` from Task 2; `CUSTOM_PREFIX = '@custom:'`.
- Produces:
  - `S.customThemes: array` (mirrors snapshot list)
  - `state.js exports CUSTOM_PREFIX` and `customPaletteVars(colors)` returning
    `[['--bg', '#…'], …]` (15 pairs, camelCase → css-name per the map above)
  - `applyTheme()` writes those properties inline when the selection is a custom theme
    whose `mode` matches, and removes them otherwise
  - Picker renders one extra swatch per custom theme (badge + delete button), greyed
    out on mode mismatch

- [ ] **Step 1: Failing tests**

Create `frontend/test/theme-custom.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const V = 'v=' + (entrySrc.match(/\?v=(\d+)/) || ['', '63'])[1];

const { S, applyTheme, CUSTOM_PREFIX, customPaletteVars } =
  await import('../js/state.js?' + V);

const THEME = {
  id: 'abcd1234', name: 'Mine', basedOn: 'gruvbox', mode: 'dark',
  colors: Object.fromEntries(['bg', 'elevated', 'card', 'cardHover', 'border', 'text',
    'textDim', 'used', 'configured', 'free', 'accent', 'conflict', 'access', 'hidden',
    'danger'].map((k) => [k, '#112233'])),
};

test('customPaletteVars maps camelCase to css names', () => {
  const vars = customPaletteVars(THEME.colors);
  const flat = Object.fromEntries(vars);
  assert.equal(flat['--bg'], '#112233');
  assert.equal(flat['--card-hover'], '#112233');
  assert.equal(flat['--text-dim'], '#112233');
  assert.equal(vars.length, 15);
});

test('applyTheme injects custom vars on match and clears on switch away', () => {
  S.settings.theme_mode = 'dark';
  S.settings.theme_palette = CUSTOM_PREFIX + THEME.id;
  S.customThemes = [THEME];
  applyTheme();
  const html = document.documentElement;
  assert.equal(html.style.getPropertyValue('--bg'), '#112233');
  assert.equal(html.getAttribute('data-palette'), null);

  S.settings.theme_palette = '';
  applyTheme();
  assert.equal(html.style.getPropertyValue('--bg'), '');
});

test('applyTheme falls back to built-in when custom mode mismatches', () => {
  S.settings.theme_mode = 'light';
  S.settings.theme_palette = CUSTOM_PREFIX + THEME.id;
  S.customThemes = [THEME];
  applyTheme();
  assert.equal(document.documentElement.style.getPropertyValue('--bg'), '');
  S.settings.theme_mode = 'system';
});
```

- [ ] **Step 2: Run, expect failure**

Run: `node --test frontend/test/theme-custom.test.mjs`
Expected: FAIL — `CUSTOM_PREFIX`/`customPaletteVars` undefined.

- [ ] **Step 3: Implement state.js changes**

```js
export const CUSTOM_PREFIX = '@custom:';

const CUSTOM_VAR_NAMES = {
  bg: '--bg', elevated: '--elevated', card: '--card', cardHover: '--card-hover',
  border: '--border', text: '--text', textDim: '--text-dim', used: '--used',
  configured: '--configured', free: '--free', accent: '--accent',
  conflict: '--conflict', access: '--access', hidden: '--hidden', danger: '--danger',
};

export function customPaletteVars(colors) {
  return Object.keys(CUSTOM_VAR_NAMES).map(function (key) {
    return [CUSTOM_VAR_NAMES[key], colors[key]];
  });
}

function clearCustomVars(html) {
  Object.keys(CUSTOM_VAR_NAMES).forEach(function (key) {
    html.style.removeProperty(CUSTOM_VAR_NAMES[key]);
  });
}

function findCustom(id) {
  return (S.customThemes || []).find(function (t) { return t.id === id; }) || null;
}
```

Replace `applyTheme()`:

```js
export function applyTheme() {
  var prefersLight = false;
  try {
    prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
  } catch (e) {}
  var mode = resolveMode(S.settings.theme_mode || 'system', prefersLight);
  var html = document.documentElement;
  html.setAttribute('data-mode', mode);
  html.removeAttribute('data-theme');
  clearCustomVars(html);
  var pal = S.settings.theme_palette || '';
  var customId = pal.indexOf(CUSTOM_PREFIX) === 0 ? pal.slice(CUSTOM_PREFIX.length) : '';
  var custom = customId ? findCustom(customId) : null;
  if (custom && custom.mode === mode) {
    customPaletteVars(custom.colors).forEach(function (pair) {
      html.style.setProperty(pair[0], pair[1]);
    });
    html.removeAttribute('data-palette');
    return;
  }
  if (pal && !customId && paletteAvailable(pal, mode)) {
    html.setAttribute('data-palette', pal);
  } else {
    html.removeAttribute('data-palette');
  }
}
```

Also give `S` an initial `customThemes: []` next to `settingsDraft`-adjacent keys
(add the line `customThemes: [],` right before `settingsDoc: null,`).

In `settings.js applyServerSettings(doc)` add after the `S.settings` merge:

```js
    S.customThemes = Array.isArray(doc.custom_themes) ? doc.custom_themes : [];
```

- [ ] **Step 4: Picker entries**

In `renderPalettePicker(choices, value, resolvedModeValue, disabled)`:

```js
    function customEntry(t) {
      const sel = CUSTOM_PREFIX + t.id;
      const on = sel === current;
      const available = t.mode === mode;
      const cls = available ? 'theme-swatch is-custom' : 'theme-swatch is-custom is-unavailable';
      const dis = available ? disabled : ' disabled';
      const dots = ['used', 'configured', 'free'].map(function (kind) {
        return '<i class="' + kind + '" style="background:' + escapeHtml(t.colors[kind]) + '"></i>';
      }).join('');
      return '<span class="' + cls + '" data-theme-preview="">' +
        '<label><input type="radio" name="theme_palette" value="' + escapeHtml(sel) + '"' +
        (on ? ' checked' : '') + dis + '>' +
        '<span class="theme-swatch-preview" aria-hidden="true">' + dots + '</span>' +
        '<span class="theme-swatch-name">' + escapeHtml(t.name) +
        '<em class="theme-badge">' + escapeHtml(t('settings.theme.customBadge')) + '</em></span></label>' +
        '<button type="button" class="btn-delete" data-delete-theme="' + escapeHtml(t.id) + '"' +
        disabled + '>' + escapeHtml(t('settings.auto.leases.release')) + '</button></span>';
    }

    const customs = (S.customThemes || []).filter(function (t) { return true; });
```

Change the wrapper line to append customs and import `CUSTOM_PREFIX` in settings.js:

```js
'<div class="theme-picker-palettes">' + entry('').concat(families.map(entry).join(''), customs.map(customEntry).join('')) + '</div></div>';
```

Extend `syncPaletteAvailability` to handle custom selections (insert before the
`PALETTE_VARIANTS` lookup):

```js
      if (family.indexOf(CUSTOM_PREFIX) === 0) {
        const id = family.slice(CUSTOM_PREFIX.length);
        const themeRow = (S.customThemes || []).find(function (x) { return x.id === id; });
        const ok = !!themeRow && themeRow.mode === mode;
        input.disabled = !ok;
        labelEl.classList.toggle('is-unavailable', !ok);
        return;
      }
```

Delete-delegate (place next to `ensureAutomationDelegates` pattern; call
`ensureThemeDelegates()` from `renderSettingsForm`):

```js
  let _themeDelegated = false;
  function ensureThemeDelegates() {
    if (_themeDelegated) return;
    _themeDelegated = true;
    document.addEventListener('click', async function (e) {
      const btn = e.target.closest('[data-delete-theme]');
      if (!btn) return;
      const id = btn.getAttribute('data-delete-theme');
      const res = await api('/api/custom-themes/' + id, { method: 'DELETE' });
      if (!res.ok) return;
      const docRes = await api('/api/settings');
      if (docRes.ok) applyServerSettings(await docRes.json());
      renderSettingsForm(S.settingsDoc);
    });
  }
```

CSS (`frontend/style.css`) minimal support:

```css
.theme-swatch.is-custom > label { cursor: pointer; }
.theme-swatch.is-custom .btn-delete { height: 22px; padding: 0 8px; font-size: 0.68rem; margin-left: 6px; }
.theme-badge { font-style: normal; font-size: 0.62rem; opacity: 0.75; margin-left: 4px; }
```

Locale keys ×4: `"theme": { ..., "customBadge": "Custom" }` (zh-CN 自定义, zh-TW 自訂,
ja カスタム).

Cache-bust `64` → `65` everywhere per Global Constraints.

- [ ] **Step 5: Run suites**

Run: `node --test frontend/test/*.test.mjs && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Apply and manage custom palettes in the theme picker"
```

---

### Task 5: Advanced theme editor

**Files:**
- Modify: `frontend/js/settings.js` (new exports + insertion into the Theme section from Task 3's `[data-appearance-section="theme"]` div)
- Modify: `frontend/style.css` (editor rows)
- Modify: `frontend/locales/en.json`, `zh-CN.json`, `zh-TW.json`, `ja.json` (`settings.editor.*` subtree, 20 keys)
- Modify: `frontend/test/settings.test.mjs` (editor builder assertions)
- Cache-bust `65` → `66`

**Interfaces:**
- Consumes: `S.customThemes`, `customPaletteVars`, `CUSTOM_PREFIX`, routes from Task 2.
- Produces:
  - `EDITOR_VARS: [{key:'bg', labelKey:'settings.editor.vars.bg'}, …]` (15 entries)
  - `themeEditorHtml(readonly)` — `<details id="theme-editor">` with name input, target
    select (New + existing ids), 15 color rows (`<input type="color" data-editor-color>`
    + `<input type="text" data-editor-hex>` per var), Start-from-preset button,
    Import file input, Export button, Save button.
  - `fillEditorFromPreset()` reads `getComputedStyle(document.documentElement)` for the
    15 properties, converts `rgb(a)` → hex, fills rows.
  - `saveEditorTheme()` POSTs or PUTs depending on target select, refetches snapshot via
    `fetchSettings()` + `applyServerSettings`, rerenders.
  - Delegates: color↔hex two-way sync, live preview (temporary `style.setProperty` on
    drag), import (FileReader → JSON.parse → POST), export (Blob download).

- [ ] **Step 1: Failing tests**

Append to `frontend/test/settings.test.mjs`:

```js
test('theme editor renders 15 color rows and controls', () => {
  const { themeEditorHtml } = mod;
  const out = themeEditorHtml(false);
  const rows = out.match(/data-editor-color=/g) || [];
  assert.equal(rows.length, 15);
  assert.ok(out.includes('id="theme-editor"'));
  assert.ok(out.includes('data-editor-preset'));
  assert.ok(out.includes('data-editor-save'));
  assert.ok(out.includes('data-editor-import'));
  assert.ok(out.includes('data-editor-export'));
  const locked = themeEditorHtml(true);
  assert.match(locked, /disabled/);
});
```

- [ ] **Step 2: Run, expect failure** — `themeEditorHtml` is not exported yet.

- [ ] **Step 3: Implement**

```js
  const EDITOR_VARS = [
    'bg', 'elevated', 'card', 'cardHover', 'border', 'text', 'textDim', 'used',
    'configured', 'free', 'accent', 'conflict', 'access', 'hidden', 'danger',
  ].map(function (key) {
    return { key: key, labelKey: 'settings.editor.vars.' + key };
  });

  export function editorDefaults() {
    const out = {};
    EDITOR_VARS.forEach(function (row) { out[row.key] = '#000000'; });
    return out;
  }

  export function themeEditorHtml(readonly) {
    const dis = readonly ? ' disabled' : '';
    const targets = ['<option value="">'+ escapeHtml(t('settings.editor.new')) + '</option>']
      .concat((S.customThemes || []).map(function (th) {
        return '<option value="' + escapeHtml(th.id) + '">' + escapeHtml(th.name) + '</option>';
      })).join('');
    const rows = EDITOR_VARS.map(function (row) {
      return '<div class="editor-row"><label for="ed-' + row.key + '" data-i18n="' + row.labelKey + '">' +
        escapeHtml(t(row.labelKey)) + '</label>' +
        '<input type="color" id="ed-' + row.key + '" data-editor-color="' + row.key + '" value="#000000"' + dis + '>' +
        '<input type="text" class="range-input" maxlength="9" data-editor-hex="' + row.key + '" value="#000000"' + dis + '>' +
        '</div>';
    }).join('');
    return '<details id="theme-editor"><summary data-i18n="settings.editor.summary">' +
      escapeHtml(t('settings.editor.summary')) + '</summary>' +
      '<p class="muted" data-i18n="settings.editor.hint">' + escapeHtml(t('settings.editor.hint')) + '</p>' +
      '<div class="editor-actions">' +
      '<button type="button" class="btn-secondary" data-editor-preset' + dis + '>' +
      escapeHtml(t('settings.editor.preset')) + '</button>' +
      '<select class="dropdown" id="editor-target"' + dis + '>' + targets + '</select>' +
      '<input type="text" class="range-input" id="editor-name" maxlength="40" placeholder="' +
      escapeHtml(t('modal.optional')) + '"' + dis + '>' +
      '<button type="button" class="btn-secondary" data-editor-export' + dis + '>' +
      escapeHtml(t('settings.editor.export')) + '</button>' +
      '<input type="file" id="editor-file" accept=".json,application/json" hidden' + dis + '>' +
      '<button type="button" class="btn-secondary" data-editor-import' + dis + '>' +
      escapeHtml(t('settings.editor.import')) + '</button>' +
      '<button type="button" class="btn-primary" data-editor-save' + dis + '>' +
      escapeHtml(t('settings.editor.save')) + '</button>' +
      '</div>' + rows + '</details>';
  }
```

Insert `themeEditorHtml(doc.readonly)` output inside the Theme section div right after
`rowsFor(themeFields)`:

```js
        '<div data-appearance-section="theme">' + rowsFor(themeFields) + themeEditorHtml(!!doc.readonly) + '</div>'
```

Behavior functions (same module; hex helpers included):

```js
  function rgbToHex(raw) {
    const m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(raw || '');
    if (!m) return /^#[0-9a-fA-F]{3,8}$/.test((raw || '').trim()) ? raw.trim() : '#000000';
    return '#' + m.slice(1).map(function (n) {
      return ('0' + Number(n).toString(16)).slice(-2);
    }).join('');
  }

  const CUSTOM_CSS_NAMES = {
    bg: '--bg', elevated: '--elevated', card: '--card', cardHover: '--card-hover',
    border: '--border', text: '--text', textDim: '--text-dim', used: '--used',
    configured: '--configured', free: '--free', accent: '--accent',
    conflict: '--conflict', access: '--access', hidden: '--hidden', danger: '--danger',
  };

  export function fillEditorFromPreset() {
    const cs = getComputedStyle(document.documentElement);
    EDITOR_VARS.forEach(function (row) {
      const hex = rgbToHex(cs.getPropertyValue(CUSTOM_CSS_NAMES[row.key]));
      const color = document.querySelector('[data-editor-color="' + row.key + '"]');
      const hexInput = document.querySelector('[data-editor-hex="' + row.key + '"]');
      if (color) color.value = hex.slice(0, 7);
      if (hexInput) hexInput.value = hex;
    });
  }

  function collectEditorColors() {
    const out = {};
    let ok = true;
    EDITOR_VARS.forEach(function (row) {
      const hexInput = document.querySelector('[data-editor-hex="' + row.key + '"]');
      const value = (hexInput && hexInput.value || '').trim();
      if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(value)) {
        out[row.key] = value;
      } else ok = false;
    });
    return ok ? out : null;
  }
```

Delegate wiring (extend `ensureThemeDelegates` from Task 4 with these branches):

```js
      const presetBtn = e.target.closest('[data-editor-preset]');
      if (presetBtn) { fillEditorFromPreset(); return; }
      const saveBtn = e.target.closest('[data-editor-save]');
      if (saveBtn) { saveEditorTheme(saveBtn); return; }
      const exportBtn = e.target.closest('[data-editor-export]');
      if (exportBtn) { exportEditorTheme(); return; }
      const importBtn = e.target.closest('[data-editor-import]');
      if (importBtn) {
        const file = document.getElementById('editor-file');
        if (file) { file.value = ''; file.click(); }
        return;
      }
      const colorInput = e.target.closest('[data-editor-color]');
      if (colorInput) {
        const hexInput = document.querySelector('[data-editor-hex="' + colorInput.getAttribute('data-editor-color') + '"]');
        if (hexInput) hexInput.value = colorInput.value;
        previewEditorColor(colorInput.getAttribute('data-editor-color'), colorInput.value);
        return;
      }
```

plus a `change` listener for `#editor-file` (import path) and an `input` listener on
`[data-editor-hex]` syncing back to the color row when valid. Live preview:

```js
  function previewEditorColor(key, hex) {
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
    document.documentElement.style.setProperty(CUSTOM_CSS_NAMES[key], hex);
  }
```

Save/export/import:

```js
  async function saveEditorTheme(btn) {
    const status = document.getElementById('settings-status');
    const colors = collectEditorColors();
    const name = ((document.getElementById('editor-name') || {}).value || '').trim();
    const target = (document.getElementById('editor-target') || {}).value || '';
    if (!colors || !name) {
      if (status) { status.className = 'is-error'; status.textContent = t('settings.editor.invalid'); }
      return;
    }
    const payload = { name: name, basedOn: '', mode: currentMode(), colors: colors };
    const url = target ? '/api/custom-themes/' + encodeURIComponent(target) : '/api/custom-themes';
    const res = await api(url, {
      method: target ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (status) { status.className = 'is-error'; status.textContent = errorText(body, res.status); }
      return;
    }
    const docRes = await api('/api/settings');
    if (docRes.ok) applyServerSettings(await docRes.json());
    renderSettingsForm(S.settingsDoc);
    if (status) { status.className = 'is-ok'; status.textContent = t('settings.saved'); }
  }

  function exportEditorTheme() {
    const colors = collectEditorColors() || editorDefaults();
    const name = ((document.getElementById('editor-name') || {}).value || 'port-light-theme');
    const blob = new Blob([JSON.stringify({ name: name, basedOn: '', mode: currentMode(), colors: colors }, null, 2)],
      { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = String(name).replace(/[^a-z0-9_-]+/gi, '-') + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
  }
```

Import handler inside the file-change delegate:

```js
      file.addEventListener('change', async function () {
        const f = file.files && file.files[0];
        if (!f) return;
        try {
          const payload = JSON.parse(await f.text());
          const res = await api('/api/custom-themes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: String(payload.name || f.name.replace(/\.json$/i, '')),
              basedOn: String(payload.basedOn || ''),
              mode: payload.mode === 'light' ? 'light' : 'dark',
              colors: payload.colors || {},
            }),
          });
          if (!res.ok) throw new Error(String(res.status));
          const docRes = await api('/api/settings');
          if (docRes.ok) applyServerSettings(await docRes.json());
          renderSettingsForm(S.settingsDoc);
        } catch (err) { /* status line shows nothing on cancel; invalid files are rejected server-side */ }
      });
```

CSS for `.editor-row`:

```css
#theme-editor { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px; }
#theme-editor summary { cursor: pointer; color: var(--text); font-size: 0.82rem; }
.editor-row { display: grid; grid-template-columns: 110px 44px 1fr; gap: 8px; align-items: center; margin-top: 6px; }
.editor-row label { font-size: 0.72rem; color: var(--text-dim); }
.editor-row input[type="color"] { width: 44px; height: 28px; padding: 0 2px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.editor-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 10px 0; }
```

Locale keys — add under `"settings"` a `"editor"` subtree ×4 languages. English:

```json
"editor": {
  "summary": "Advanced: create a custom palette",
  "hint": "Start from the current palette, tweak colors, then save. Custom palettes appear in the picker.",
  "preset": "Fill from current palette",
  "new": "New custom palette",
  "save": "Save",
  "export": "Export",
  "import": "Import",
  "invalid": "Fix the highlighted color values and give the palette a name.",
  "vars": {
    "bg": "Background", "elevated": "Elevated surfaces", "card": "Cards",
    "cardHover": "Card hover", "border": "Borders", "text": "Text",
    "textDim": "Secondary text", "used": "Used ports", "configured": "Configured ports",
    "free": "Free ports", "accent": "Accent", "conflict": "Conflicts",
    "access": "Access badge", "hidden": "Hidden", "danger": "Danger"
  }
},
```

zh-CN: 摘要「高级：创建自定义色板」/ 提示「从当前色板开始，微调颜色后保存。自定义色板会出现在选择器中。」/ 按钮「从当前色板填充」「新建自定义色板」「保存」「导出」「导入」/ 失效提示「修正标红的颜色值并填写名称。」/ vars：背景、浮层表面、卡片、卡片悬停、边框、文本、次要文本、占用端口、已配置端口、空闲端口、强调色、冲突、访问徽标、隐藏、危险。

zh-TW: 「進階：建立自訂色板」/「從目前色板開始，微調顏色後儲存。自訂色板會出現在選擇器中。」/「從目前色板填充」「新增自訂色板」「儲存」「匯出」「匯入」/「修正標紅的顏色值並填寫名稱。」/ vars：背景、浮層表面、卡片、卡片懸停、邊框、文字、次要文字、佔用連接埠、已設定連接埠、空閒連接埠、強調色、衝突、存取徽標、隱藏、危險。

ja: 「高度な設定：カスタムパレットを作成」/「現在のパレットを起点に色を調整して保存できます。カスタムパレットはセレクターに表示されます。」/「現在のパレットから生成」「新規カスタムパレット」「保存」「エクスポート」「インポート」/「強調された色の値を修正し、名前を入力してください。」/ vars：背景、浮き上がり面、カード、カードホバー、境界線、テキスト、補助テキスト、使用中ポート、設定済みポート、空きポート、アクセント、競合、アクセスバッジ、非表示、危険。

- [ ] **Step 4: Run suites**

Run: `node --test frontend/test/*.test.mjs && .venv/bin/python -m pytest tests/test_i18n.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add the advanced custom-theme editor"
```

---

### Task 6: `card_scale` / `text_scale` fields with density seeding

**Files:**
- Modify: `backend/settings.py` (FIELDS tuple; `resolve()` tail)
- Modify: `tests/test_settings.py` (seed behavior)

**Interfaces:**
- Consumes: existing FieldSpec machinery.
- Produces: `values['card_scale']` and `values['text_scale']` (int 0–100, default 50).
  Rule: when neither env nor file provides `card_scale` (origin `default`) and the
  resolved `grid_density` is `compact`, `card_scale` resolves to 100 instead of 50.

- [ ] **Step 1: Failing tests**

Append to `tests/test_settings.py`:

```python
def test_display_scales_default_50(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    values, _ = app_settings.resolve()
    assert values["card_scale"] == 50
    assert values["text_scale"] == 50


def test_compact_density_seeds_card_scale(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GRID_DENSITY", "compact")
    values, _ = app_settings.resolve()
    assert values["card_scale"] == 100
    assert values["grid_density"] == "compact"


def test_saved_card_scale_beats_density_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GRID_DENSITY", "compact")
    app_settings.apply_patch({"card_scale": 30})
    values, _ = app_settings.resolve()
    assert values["card_scale"] == 30
```

- [ ] **Step 2: Run, expect failure** — KeyError on `card_scale`.

- [ ] **Step 3: Implement**

Add to FIELDS after the `grid_density` entry:

```python
    FieldSpec(
        "card_scale", "int", "CARD_SCALE", 50, "appearance", "Card size",
        "0 is roomiest, 100 packs cards tightest. Replaces grid density.",
        min=0, max=100,
    ),
    FieldSpec(
        "text_scale", "int", "TEXT_SCALE", 50, "appearance", "Text size",
        "Grid text size from small (0) to large (100).",
        min=0, max=100,
    ),
```

In `resolve()` insert before the start/end clamp:

```python
    if origins["card_scale"] == "default" and values["grid_density"] == "compact":
        values["card_scale"] = 100
```

- [ ] **Step 4: Run suite**

Run: `.venv/bin/python -m pytest tests/test_settings.py -q && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check .
git add backend/settings.py tests/test_settings.py
git commit -m "Add card_scale and text_scale settings seeded from legacy density"
```

---

### Task 7: Sliders replace the density toggle

**Files:**
- Modify: `frontend/js/state.js` (`applyAppearance` gains scale application)
- Modify: `frontend/js/settings.js` (`renderField` special-cases the two int fields as ranges)
- Modify: `frontend/style.css` (variable-driven geometry; delete `[data-density]` rules at lines 574, 654, 668)
- Modify: `frontend/test/settings.test.mjs`
- Cache-bust `66` → `67`

**Interfaces:**
- Consumes: `card_scale`/`text_scale` values from Task 6; anchors table above.
- Produces:
  - `CARD_ANCHORS = {minW:[164,112], gap:[10,6], padX:[14,10], padY:[12,8], minH:[76,52]}`
  - `applyDisplayScale()` exported from state.js: writes `--cell-min-w`, `--cell-gap`,
    `--cell-pad-x`, `--cell-pad-y`, `--cell-min-h` and `--port-font` inline on
    `<html>`; called from `applyAppearance`.
  - Font mapping: `--port-font = round(basePx + (scale−50)·0.08)` px where `basePx` is
    the runtime-measured root font size (so scale 50 ≡ today). Range ≈ 46→54 at
    ±50 steps of 0.08 px per unit keeps text legible at extremes; clamp 12–18 hard.
  - `renderField` renders `f.key === 'card_scale' || f.key === 'text_scale'` as
    `<input type="range" min="0" max="100" step="1">` with a live value bubble.

- [ ] **Step 1: Failing test**

Append to `frontend/test/settings.test.mjs`:

```js
test('scale fields render as range inputs', () => {
  const { renderField } = mod;
  const card = renderField({ key: 'card_scale', type: 'int', min: 0, max: 100, group: 'appearance', origin: 'file' }, 72, false);
  assert.match(card, /type="range"/);
  assert.match(card, /value="72"/);
  const text = renderField({ key: 'text_scale', type: 'int', min: 0, max: 100, group: 'appearance', origin: 'default' }, 50, false);
  assert.match(text, /type="range"/);
});

test('applyDisplayScale maps anchors exactly at 0/50/100', () => {
  const { applyDisplayScale } = await0;
  const html = document.documentElement;
  applyDisplayScale(0, 50);
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '164px');
  applyDisplayScale(50, 50);
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '138px');
  assert.equal(html.style.getPropertyValue('--cell-min-h'), '64px');
  applyDisplayScale(100, 50);
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '112px');
  assert.equal(html.style.getPropertyValue('--cell-gap'), '6px');
});
```

(`await0` is a local alias: import `applyDisplayScale` from state.js at the top of the
file alongside the other state imports and drop the alias.)

- [ ] **Step 2: Run, expect failure** — `applyDisplayScale` missing; fields render numeric inputs.

- [ ] **Step 3: Implement state.js**

```js
const CARD_ANCHORS = { minW: [164, 112], gap: [10, 6], padX: [14, 10], padY: [12, 8], minH: [76, 52] };

export function applyDisplayScale(cardScale, textScale) {
  const html = document.documentElement;
  const c = Math.max(0, Math.min(100, Number(cardScale) || 0));
  const t = Math.max(0, Math.min(100, Number(textScale) || 0));
  const k = c / 100;
  Object.keys(CARD_ANCHORS).forEach(function (key) {
    const pair = CARD_ANCHORS[key];
    const value = Math.round(pair[0] + (pair[1] - pair[0]) * k);
    const cssName = '--cell-' + key.toLowerCase().replace('minw', 'min-w').replace('padx', 'pad-x').replace('pady', 'pad-y').replace('minh', 'min-h');
    html.style.setProperty(key === 'gap' ? '--cell-gap' : cssName, value + 'px');
  });
  let basePx = 16;
  try {
    basePx = parseFloat(getComputedStyle(html).fontSize) || 16;
  } catch (e) {}
  const fontPx = Math.max(12, Math.min(18, Math.round(basePx + (t - 50) * 0.08)));
  html.style.setProperty('--port-font', fontPx + 'px');
}
```

`applyAppearance()` calls `applyDisplayScale(S.settings.card_scale, S.settings.text_scale)`
after `applyTheme()` and drops the `data-density` line; localStorage mirror gains
`card_scale`/`text_scale`.

- [ ] **Step 4: settings.js renderField branch** (before the generic `int` branch):

```js
    } else if (f.type === 'int' && (f.key === 'card_scale' || f.key === 'text_scale')) {
      const n = Number(value);
      control = '<span class="slider-wrap"><input type="range" name="' + f.key + '"' +
        ' min="0" max="100" step="1" value="' + (Number.isFinite(n) ? n : 50) + '"' + disabled + '>' +
        '<output class="slider-out" data-slider-out="' + f.key + '">' + (Number.isFinite(n) ? n : 50) + '</output></span>';
    }
```

Live-update delegate (inside `ensureThemeDelegates`):

```js
      const range = e.target.closest('input[type="range"][name="card_scale"],input[type="range"][name="text_scale"]');
      if (range) {
        const out = document.querySelector('[data-slider-out="' + range.name + '"]');
        if (out) out.textContent = range.value;
        applyDisplayScale(
          Number((document.querySelector('input[name="card_scale"]') || {}).value || 50),
          Number((document.querySelector('input[name="text_scale"]') || {}).value || 50));
        return;
      }
```

(settings save already collects named form controls; ranges submit as strings parsed by
the existing int branch.)

- [ ] **Step 5: CSS swap**

Replace the four geometry rules with variable-driven versions:

```css
.host-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--cell-min-w, 138px), 1fr));
  gap: var(--cell-gap, 8px);
}

#grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--cell-min-w, 138px), 1fr));
  gap: var(--cell-gap, 8px);
}

.port-cell {
  /* …keep every other declaration… */
  padding: var(--cell-pad-y, 10px) var(--cell-pad-x, 12px);
  min-height: var(--cell-min-h, 64px);
}

.port-cell .port-num { font-size: var(--port-font, 1rem); /* rest unchanged */ }
.port-cell .port-label { font-size: calc(var(--port-font, 1rem) * 0.75); }
```

Delete `[data-density="compact"] .host-grid`, `[data-density="compact"] #grid`,
`[data-density="compact"] .port-cell`. Grep confirms zero remaining
`data-density` references (`grep -rn 'data-density' frontend/` → empty).

Cache-bust `66` → `67` per Global Constraints.

- [ ] **Step 6: Run suites + manual smoke**

Run: `node --test frontend/test/*.test.mjs && .venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Manual: start dev server, drag both sliders — grid reflows live; Save → reload → positions persist.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Replace density toggle with continuous card and text sliders"
```

---

### Task 8: Docs, changelog, final gates

**Files:**
- Modify: `README.md` + `README.zh-CN.md` (appearance feature bullets)
- Modify: `docs/architecture.md` (theme paragraph: inline injection + scale fields)
- Modify: `CHANGELOG.md` (Unreleased section)
- Delete check: `grep -rn 'grid_density\|GRID_DENSITY' docs/ README.md README.zh-CN.md` — update stale claims to describe the seeding behavior instead of a working toggle.

**Content:**

README feature bullet (en): "Make it yours: fork any palette in the advanced theme
editor (15 core colors, server-stored, import/export as JSON) and tune card and text
size with continuous sliders."

zh-CN: 「随心定制：在内置色板上分叉出自定义色板（15 个核心颜色，服务端存储，支持 JSON
导入导出），并用连续滑杆微调卡片与文字大小。」

CHANGELOG Unreleased:

```markdown
## Unreleased

- Appearance: build your own palettes in a new advanced theme editor — start from the current palette, tweak the 15 core colors, save named custom palettes server-side (`<data-dir>/themes.json`), import/export as JSON. Selection rides the existing `theme_palette` setting as `@custom:<id>`; deleting a selected theme resets to built-in.
- Display: the comfortable/compact toggle becomes two continuous sliders (card size, text size) with live preview; legacy `GRID_DENSITY=compact` seeds the initial slider position. New settings: `card_scale`, `text_scale` (0–100, default 50).
- Settings API: `GET/POST/PUT/DELETE /api/custom-themes`; `GET /api/settings` responses gain `custom_themes`. Writes refuse when settings are readonly.
```

architecture.md theme paragraph gains: custom palettes apply as inline custom
properties on `<html>` (winning the cascade over static palette blocks); `data-density`
is gone, replaced by five `--cell-*` variables and `--port-font` written from
`card_scale`/`text_scale`.

**Gates:** `.venv/bin/ruff check . && .venv/bin/python -m pytest -q && node --test frontend/test/*.test.mjs`
All green, then:

```bash
git add -A
git commit -m "Document custom themes and display sliders"
```

## Self-review notes

- Spec coverage: storage (T1), API+selection+readonly (T2), sections (T3), apply/picker
  (T4), editor+import/export+preset-fill (T5), scale fields+seeding (T6), sliders+density
  retirement (T7), docs+i18n parity enforced throughout by existing tests (T8).
- Type consistency: `CUSTOM_PREFIX`/`customPaletteVars`/`applyDisplayScale` names match
  across Tasks 4/5/7; route paths identical between T2 tests and implementation.
- Known deliberate simplifications recorded in spec: existence of `@custom:<id>` checked
  at delete/reset time, not during coercion; editor `basedOn` always posts `""`.
