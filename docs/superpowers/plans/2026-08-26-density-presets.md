# Display Density Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two display sliders (`card_scale`, `text_scale`) with one three-preset density choice — `loose` / `standard` / `compact` — reusing the retired `grid_density` setting; text size is no longer adjustable.

**Architecture:** Backend keeps a single choice FieldSpec with legacy-value coercion; frontend geometry collapses from linear anchor interpolation to a three-row preset lookup that sets the same six `--cell-*` CSS variables. The settings UI renders the field through the existing generic `segmented` control branch; the live preview strip keeps working via shared CSS variables.

**Tech Stack:** Python/FastAPI backend, vanilla ES-module frontend (no build step), pytest + node:test suites.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-26-density-presets-design.md`.
- Preset geometry (exact): loose `{minW:164, gap:10, padX:14, padT:12, padB:16, minH:76}`, standard `{minW:138, gap:8, padX:12, padT:10, padB:12, minH:64}`, compact `{minW:112, gap:6, padX:10, padT:8, padB:8, minH:52}`.
- Text size fixed everywhere: no `--port-font` writes from JS; CSS fallbacks in `style.css` stay.
- Gates run from repo root: `.venv/bin/ruff check .`, `.venv/bin/pytest`, `node --test "frontend/test/*.test.mjs"` (quote the glob), `python3 scripts/locale-scaffold.py --check`.
- Branch: `feature/density-presets` off current `main`. Commit after each task. No push.
- Locale edits must touch all 7 files (en, fr, de, es, zh-CN, zh-TW, ja) — key-tree parity test enforces it.
- Historical specs/plans under `docs/superpowers/` are never edited.

---

### Task 1: Backend — three-choice `grid_density`, drop scales, legacy coercion

**Files:**
- Modify: `backend/settings.py` (FieldSpec at lines 63–77, `_coerce` choice branch ~line 219, seed logic at lines 285–286)
- Modify: `tests/test_settings.py` (replace lines 124–144)
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: existing FieldSpec pipeline (`_coerce`, `resolve`, `snapshot`, `apply_patch`) unchanged.
- Produces: `values["grid_density"] ∈ {"loose","standard","compact"}` (default `"standard"`); snapshot fields contain neither `card_scale` nor `text_scale`; PUT of `"comfortable"` returns 200 and stores `"standard"`.

- [ ] **Step 1: Rewrite the backend tests**

In `tests/test_settings.py` replace the three tests at lines 124–144 with:

```python
def test_density_defaults_to_standard(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    values, _ = app_settings.resolve()
    assert values["grid_density"] == "standard"
    assert "card_scale" not in values
    assert "text_scale" not in values


def test_density_maps_legacy_comfortable(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GRID_DENSITY", "comfortable")
    values, _ = app_settings.resolve()
    assert values["grid_density"] == "standard"
    client = TestClient(app)
    ok = client.put("/api/settings", json={"grid_density": "comfortable"})
    assert ok.status_code == 200
    assert ok.json()["values"]["grid_density"] == "standard"


def test_density_rejects_unknown_and_snapshot_has_no_scales(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    res = client.put("/api/settings", json={"grid_density": "cozy"})
    assert res.status_code == 400
    snap = client.get("/api/settings").json()
    keys = [f["key"] for f in snap["fields"]]
    assert "card_scale" not in keys
    assert "text_scale" not in keys
    assert snap["values"]["grid_density"] == "standard"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_settings.py -x -q`
Expected: FAIL — `card_scale` still present / default still `comfortable`.

- [ ] **Step 3: Edit `backend/settings.py`**

Replace the three FieldSpecs (lines 63–77) with:

```python
    FieldSpec(
        "grid_density", "choice", "GRID_DENSITY", "standard", "appearance", "Card density",
        "Three visual presets: Loose spreads cards out, Compact packs them tight.",
        choices=("loose", "standard", "compact"),
    ),
```

In `_coerce`, inside the `if spec.kind == "choice":` branch, add before the membership check:

```python
    if spec.kind == "choice":
        if spec.key == "grid_density" and text == "comfortable":
            return "standard"
        if text not in spec.choices:
            raise ValueError(f"{spec.key} must be one of {', '.join(spec.choices)}")
        return text
```

Delete the two seed lines:

```python
    if origins["card_scale"] == "default" and values["grid_density"] == "compact":
        values["card_scale"] = 100
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_settings.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/settings.py tests/test_settings.py
git commit -m "Grid density becomes a loose/standard/compact choice; drop card/text scale"
```

---

### Task 2: Frontend geometry — `DENSITY_PRESETS` + `applyDensity`

**Files:**
- Modify: `frontend/js/state.js:148-184`
- Modify: `frontend/js/app.js:49-50` (hydration block)
- Test: `frontend/test/settings.test.mjs`

**Interfaces:**
- Produces: `export const DENSITY_PRESETS = { loose, standard, compact }` where each entry has keys `minW, gap, padX, padT, padB, minH` (numbers); `export function applyDensity(name)` sets `--cell-min-w/--cell-gap/--cell-pad-x/--cell-pad-t/--cell-pad-b/--cell-min-h` on `document.documentElement` and falls back to `standard` for unknown names. `applyDisplayScale` and `CARD_ANCHORS` are deleted.

- [ ] **Step 1: Update node tests**

In `frontend/test/settings.test.mjs` line 17 change the import destructure to `applyDensity` instead of `applyDisplayScale`. Replace the test `'applyDisplayScale maps anchors exactly at 0/50/100'` (lines 272–286) with:

```js
test('applyDensity applies presets exactly and falls back to standard', () => {
  const html = document.documentElement;
  applyDensity('loose');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '164px');
  assert.equal(html.style.getPropertyValue('--cell-gap'), '10px');
  applyDensity('standard');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '138px');
  assert.equal(html.style.getPropertyValue('--cell-min-h'), '64px');
  assert.equal(html.style.getPropertyValue('--cell-pad-t'), '10px');
  assert.equal(html.style.getPropertyValue('--cell-pad-b'), '12px');
  applyDensity('compact');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '112px');
  assert.equal(html.style.getPropertyValue('--cell-gap'), '6px');
  assert.equal(html.style.getPropertyValue('--cell-pad-t'), '8px');
  assert.equal(html.style.getPropertyValue('--cell-pad-b'), '8px');
  applyDensity('nonsense');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '138px');
});
```

Add after it:

```js
test('applyAppearance mirrors density without scale keys', () => {
  S.settings.grid_density = 'compact';
  applyAppearance();
  const stored = JSON.parse(localStorage.getItem('port-light-settings'));
  assert.equal(stored.grid_density, 'compact');
  assert.ok(!('card_scale' in stored));
  assert.ok(!('text_scale' in stored));
});
```

(`applyAppearance` must be added to the line-17 import list.)

- [ ] **Step 2: Run to verify failure**

Run: `node --test frontend/test/settings.test.mjs`
Expected: FAIL — `applyDensity` not exported.

- [ ] **Step 3: Implement in `state.js`**

Replace lines 148–169 (`CARD_ANCHORS` + `applyDisplayScale`) with:

```js
export const DENSITY_PRESETS = {
  loose: { minW: 164, gap: 10, padX: 14, padT: 12, padB: 16, minH: 76 },
  standard: { minW: 138, gap: 8, padX: 12, padT: 10, padB: 12, minH: 64 },
  compact: { minW: 112, gap: 6, padX: 10, padT: 8, padB: 8, minH: 52 },
};

const CELL_VAR_NAMES = {
  minW: '--cell-min-w', gap: '--cell-gap', padX: '--cell-pad-x',
  padT: '--cell-pad-t', padB: '--cell-pad-b', minH: '--cell-min-h',
};

export function applyDensity(name) {
  const preset = DENSITY_PRESETS[name] || DENSITY_PRESETS.standard;
  const html = document.documentElement;
  Object.keys(CELL_VAR_NAMES).forEach(function (key) {
    html.style.setProperty(CELL_VAR_NAMES[key], preset[key] + 'px');
  });
}
```

In `applyAppearance()` (line 173) swap the call to `applyDensity(S.settings.grid_density);` and reduce the localStorage mirror object (lines 175–182) to:

```js
    localStorage.setItem('port-light-settings', JSON.stringify({
      theme_mode: S.settings.theme_mode,
      theme_palette: S.settings.theme_palette || '',
      grid_density: S.settings.grid_density,
      locale: S.settings.locale || 'auto',
    }));
```

In `S.settings` (line 72) set the default `grid_density: 'standard',`.

- [ ] **Step 4: Trim the hydration block in `app.js`**

Delete lines 49–50:

```js
    if (Number.isFinite(cached.card_scale)) S.settings.card_scale = cached.card_scale;
    if (Number.isFinite(cached.text_scale)) S.settings.text_scale = cached.text_scale;
```

(Line 48 `if (cached.grid_density) …` stays.)

- [ ] **Step 5: Run to verify pass**

Run: `node --test frontend/test/settings.test.mjs`
Expected: PASS. (Other suites may still fail until Task 3 — acceptable mid-stream, but note it.)

- [ ] **Step 6: Commit**

```bash
git add frontend/js/state.js frontend/js/app.js frontend/test/settings.test.mjs
git commit -m "Density geometry engine: three presets replace continuous interpolation"
```

---

### Task 3: Settings UI — segmented control, remove slider machinery

**Files:**
- Modify: `frontend/js/settings.js` (import line 3, renderField 494–498, input delegate 765–774, panel assembly 863–884)
- Modify: `frontend/style.css` (delete 643–681)
- Test: `frontend/test/settings.test.mjs`

**Interfaces:**
- Consumes: `applyDensity` from Task 2 (only imported where needed); server snapshot `grid_density` choice field from Task 1.
- Produces: layout card order = segmented density → preview → card toggles. Slider code paths gone.

- [ ] **Step 1: Update the appearance-panel test**

In `frontend/test/settings.test.mjs` appearance test (lines 215–225): replace the `card_scale` and `text_scale` field entries with

```js
      { key: 'grid_density', type: 'choice', group: 'appearance', choices: ['loose', 'standard', 'compact'], origin: 'default' },
```

and add `grid_density` to the mocked `PortLightI18n.t` usage — no change needed since `t(key)` returns the key. Replace assertions at lines 238–245 with:

```js
  const panelHtml = host.innerHTML;
  const iDensity = panelHtml.indexOf('name="grid_density"');
  const iPrev = panelHtml.indexOf('data-display-preview');
  const iStatus = panelHtml.indexOf('name="show_status_text"');
  assert.ok(iDensity > -1 && iDensity < iPrev, 'density segmented control renders ahead of the preview');
  assert.ok(iPrev < iStatus, 'preview precedes the card toggles');
```

Replace the `'scale fields render as range inputs'` test (lines 263–270) with:

```js
test('density renders as a segmented radiogroup', () => {
  const { renderField } = mod;
  const html = renderField({ key: 'grid_density', type: 'choice', group: 'appearance', choices: ['loose', 'standard', 'compact'], origin: 'default' }, 'standard', false);
  assert.match(html, /class="segmented"/);
  assert.match(html, /name="grid_density"/);
  assert.match(html, /value="loose"/);
  assert.match(html, /value="standard" checked/);
  assert.match(html, /value="compact"/);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test frontend/test/settings.test.mjs`
Expected: FAIL — `grid_density` filtered out of the panel; range-input branch still present.

- [ ] **Step 3: Edit `settings.js`**

Line 3 import: replace `applyDisplayScale` with nothing (settings.js no longer imports it — live application happens through the existing `change` handler in `app.js` which calls `applyAppearance`). If no other use remains, the import shrinks to:

```js
import { S, SETTINGS_PANELS, CARD_FIELD_KEYS, CORE_THEMES, PALETTE_VARIANTS, CUSTOM_PREFIX, resolveMode, paletteAvailable, applyAppearance, saveView } from './state.js?v=74';
```

Delete the slider branch in `renderField` (lines 494–498).

Delete the range branch of the document-level `input` delegate (the `const range = …` through its closing `return; }` at lines 766–774). Keep the colorRow/hexInput branches.

Panel assembly (lines 863–871) becomes:

```js
    const appearanceFields = byGroup.appearance || [];
    const themeFields = appearanceFields.filter(function (f) {
      return !CARD_FIELD_KEYS[f.key] && f.key !== 'locale' && f.key !== 'grid_density';
    });
    const languageFields = appearanceFields.filter(function (f) { return f.key === 'locale'; });
    const densityFields = appearanceFields.filter(function (f) { return f.key === 'grid_density'; });
    const cardFields = appearanceFields.filter(function (f) { return CARD_FIELD_KEYS[f.key]; });
```

and the Layout card line (884) becomes:

```js
          rowsFor(densityFields) + displayPreviewHtml() + rowsFor(cardFields))) +
```

- [ ] **Step 4: Delete the slider CSS**

Remove `frontend/style.css` lines 643–681 (from `/* Display sliders … */` through the `@media (max-width: 900px) { .slider-wrap … }` block inclusive). Keep the `.display-preview` comment/block that follows.

- [ ] **Step 5: Run to verify pass**

Run: `node --test frontend/test/settings.test.mjs && rg -n "slider|card_scale|text_scale" frontend/js/ frontend/style.css`
Expected: tests PASS; grep finds no matches in js/css (app.js Task-2 edit already removed hydration lines).

Note: `app.js` change handler lines 591–596 already handle `field === 'grid_density'` (sets `S.settings.grid_density`, calls `applyAppearance()`, marks dirty) — this previously-dead branch becomes live again; no edit needed there. Preview restyles via CSS variables with zero JS.

- [ ] **Step 6: Commit**

```bash
git add frontend/js/settings.js frontend/style.css frontend/test/settings.test.mjs
git commit -m "Settings UI: segmented density control replaces sliders"
```

---

### Task 4: Cold-boot bootstrap + cache-bust sweep

**Files:**
- Modify: `frontend/index.html` (inline script lines 21–41; `?v=` refs at lines 44, 301, 302)
- Modify: `frontend/i18n.js` (`CACHE_BUST = '74'` line 7)
- Modify: every `frontend/js/*.js` import specifier `?v=74` → `?v=75`
- Test: `frontend/test/settings.test.mjs` (new bootstrap-consistency test)

**Interfaces:**
- Consumes: localStorage key `port-light-settings` → `grid_density`.
- Produces: boot-time CSS vars identical to runtime presets.

- [ ] **Step 1: Add the consistency test**

Append to `frontend/test/settings.test.mjs` (needs `DENSITY_PRESETS` in the state.js import list):

```js
test('inline bootstrap table matches DENSITY_PRESETS', async () => {
  const fs = await import('node:fs');
  const src = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  for (const name of Object.keys(DENSITY_PRESETS)) {
    for (const [k, v] of Object.entries(DENSITY_PRESETS[name])) {
      assert.ok(src.includes(k + ':' + v), name + ' ' + k + ':' + v + ' present in bootstrap');
    }
  }
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test frontend/test/settings.test.mjs`
Expected: FAIL — bootstrap still carries the old interpolation code.

- [ ] **Step 3: Replace the inline script body**

Swap lines 21–41 of `frontend/index.html` for:

```js
      var DENSITY = {loose:{minW:164,gap:10,padX:14,padT:12,padB:16,minH:76},standard:{minW:138,gap:8,padX:12,padT:10,padB:12,minH:64},compact:{minW:112,gap:6,padX:10,padT:8,padB:8,minH:52}};
      var d = DENSITY[s.grid_density] || DENSITY.standard;
      for (var key in d) {
        var name = '--cell-' + key.toLowerCase().replace('minw','min-w').replace('padx','pad-x').replace('padt','pad-t').replace('padb','pad-b').replace('minh','min-h');
        document.documentElement.style.setProperty(name, d[key] + 'px');
      }
```

- [ ] **Step 4: Cache-bust sweep 74 → 75**

- `frontend/i18n.js`: `var CACHE_BUST = '75';`
- `frontend/index.html`: `?v=74` → `?v=75` (style.css, i18n.js, app.js refs).
- All `frontend/js/*.js`: internal imports `./x.js?v=74` → `?v=75`.

```bash
perl -pi -e 's/\?v=74/?v=75/g' frontend/index.html frontend/js/*.js frontend/i18n.js
rg -n 'v=74' frontend/ && echo "leftover v=74" || echo clean
```

(The settings tests derive their specifier from `app.js` source, so they follow automatically.)

- [ ] **Step 5: Run full node suite**

Run: `node --test "frontend/test/*.test.mjs"`
Expected: all PASS, including the new bootstrap test and `cache_bust_matches_index`.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/i18n.js frontend/js/ frontend/test/settings.test.mjs
git commit -m "Bootstrap applies density presets directly; cache-bust v75"
```

---

### Task 5: Locales ×7 + docs

**Files:**
- Modify: `frontend/locales/{en,fr,de,es,zh-CN,zh-TW,ja}.json`
- Modify: `CHANGELOG.md` (Unreleased), `README.md:125`, `README.zh-CN.md:125`
- Test: guarded by `tests/test_i18n.py` + scaffold script (no direct test edits)

**Interfaces:**
- Consumes: FieldSpec choices from Task 1 (`choice.loose/standard/compact` required by `test_settings_fields_have_locale_copy`).
- Produces: parity across locales; no orphaned `card_scale`/`text_scale` copy.

- [ ] **Step 1: en.json**

- `settings.fields.grid_density.label` → `"Card density"`; `.help` → `"Loose spreads cards out, Standard balances, Compact packs them tight."`
- Delete the complete `settings.fields.card_scale` and `settings.fields.text_scale` objects.
- Note: `settings.groups.appearance.*` looks orphaned but `test_settings_fields_have_locale_copy`
  requires `groups.{group}.title/blurb` for every field's group — leave those keys alone
  (ledger item stays deferred).
- In `choice`: delete `"comfortable"`, keep existing `"compact": "Compact"`, add `"loose": "Loose"` and `"standard": "Standard"` (insert alphabetically-ish near `compact`).

- [ ] **Step 2: Apply the same shape to fr, de, es, zh-CN, zh-TW, ja**

Reuse each file's existing `choice.compact` translation verbatim; only translate label/help/loose/standard:

| locale | fields.grid_density.label | fields.grid_density.help | choice.loose | choice.standard |
|---|---|---|---|---|
| fr | `"Densité des cartes"` | `"Aérée étale les cartes, Standard équilibre, Compact les serre."` | `"Aérée"` | `"Standard"` |
| de | `"Kartendichte"` | `"Weitläufig verteilt die Karten, Standard hält die Balance, Kompakt packt sie eng."` | `"Weitläufig"` | `"Standard"` |
| es | `"Densidad de tarjetas"` | `"Espaciado ensancha las tarjetas, Estándar equilibra, Compacto las aprieta."` | `"Espaciado"` | `"Estándar"` |
| zh-CN | `"卡片密度"` | `"宽松舒展、标准适中、紧凑高密度三种视觉效果。"` | `"宽松"` | `"标准"` |
| zh-TW | `"卡片密度"` | `"寬鬆舒展、標準適中、緊湊高密度三種視覺效果。"` | `"寬鬆"` | `"標準"` |
| ja | `"カードの密度"` | `"ゆとりは広く、コンパクトは狭く、標準はその中間です。"` | `"ゆとり"` | `"標準"` |

Also update each file's `settings.fields.card_scale`/`text_scale` deletion exactly as en (same tree positions).

- [ ] **Step 3: Verify i18n gates**

Run: `.venv/bin/pytest tests/test_i18n.py -q && python3 scripts/locale-scaffold.py --check`
Expected: PASS — key-tree parity, fields-have-copy (new choices covered), orphan scan clean.

- [ ] **Step 4: Docs**

`CHANGELOG.md` Unreleased — replace the display-sliders bullet with:

```markdown
- Display: the two display sliders are replaced by one card-density choice with three
  visual presets — Loose / Standard / Compact (`GRID_DENSITY`, default `standard`; legacy
  `comfortable` behaves as `standard`). Text size is fixed again; the unreleased
  `card_scale`/`text_scale` settings are gone.
```

`README.md` env-table row:

```markdown
| `GRID_DENSITY` | `standard` | Card-density preset: `loose`, `standard`, or `compact`. A stored legacy `comfortable` behaves as `standard`. |
```

`README.zh-CN.md` 对应行：

```markdown
| `GRID_DENSITY` | `standard` | 卡片密度预设：`loose`（宽松）、`standard`(标准)、`compact`（紧凑）。旧值 `comfortable` 视同 `standard`。 |
```

Then sweep for stragglers: `rg -n "card_scale|text_scale" README.md README.zh-CN.md docs/ *.md` (ignore `docs/superpowers/` history) and fix any remaining prose mentions outside history dirs.

- [ ] **Step 5: Commit**

```bash
git add frontend/locales CHANGELOG.md README.md README.zh-CN.md
git commit -m "Locale copy and docs for density presets"
```

---

### Task 6: Full gates, ledger, merge

**Files:**
- Modify: `.superpowers/sdd/progress.md` (append section)

- [ ] **Step 1: Repo-wide leftover scan**

Run: `rg -n "card_scale|text_scale|applyDisplayScale|CARD_ANCHORS|slider-wrap|slider-out|choice.comfortable|\"comfortable\"" backend/ frontend/ tests/ scripts/ *.md`
Expected: only hits inside `docs/superpowers/` history. Fix anything else.

- [ ] **Step 2: Full gates**

```bash
.venv/bin/ruff check . && .venv/bin/pytest -q && node --test "frontend/test/*.test.mjs"
```

Expected: ruff clean; pytest all pass (~292); node all pass (~66).

- [ ] **Step 3: Smoke on a real server**

```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 2100 &
curl -s http://127.0.0.1:2100/api/settings | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['values']['grid_density'], [f['key'] for f in d['fields'] if f['key'] in ('grid_density','card_scale','text_scale')])"
```

Expected output: `standard ['grid_density']`. Then `PUT {"grid_density":"compact"}` returns 200; kill the server by PID afterwards (lsof on port, precise kill — leave the port closed unless asked).

- [ ] **Step 4: Ledger + merge**

Append to `.superpowers/sdd/progress.md`:

```markdown
# Density Presets — SDD Progress
Plan: docs/superpowers/plans/2026-08-26-density-presets.md
Spec: docs/superpowers/specs/2026-08-26-density-presets-design.md
Branch: feature/density-presets (from main@<start-sha>)
Task 1..6: <fill per-task commits + deviations during execution>
DENSITY PRESETS: COMPLETE (<range>, merged ff to main, branch deleted; unpushed per convention)
```

Merge (convention: ff, delete branch, hold push):

```bash
git checkout main && git merge --ff-only feature/density-presets && git branch -d feature/density-presets
```

- [ ] **Step 5: Commit ledger**

```bash
git add .superpowers/sdd/progress.md && git commit -m "Record density-preset epic progress"
```
