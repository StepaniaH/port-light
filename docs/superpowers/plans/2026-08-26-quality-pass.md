# Quality Pass: Ghost Cleanup, Guardrails, OccupancyCache, Appearance Ownership

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the six accepted quality improvements — app.js ghost-code removal, i18n/doc guardrails, `safe_http_url` relocation, dead-API removal, an `OccupancyCache` module, a store mtime memo, and single-owner appearance persistence — with zero user-visible behavior change except the changelogged items.

**Architecture:** Deletions and guardrails first (tasks 1–4), then the one structural extraction (task 5, `backend/occupancy_cache.py`), then the store read memo (task 6), then the frontend persistence ownership change (task 7), then plan archival (task 8). Existing tests pin every behavior; exactly one new test is added (store memo invalidation).

**Tech Stack:** Python 3.11+ / FastAPI backend, vanilla ES-module frontend (no build), pytest + node:test suites.

## Global Constraints

- Branch: current `main`. **Never push any branch or tag to any remote.**
- Gates per task: `.venv/bin/ruff check backend tests mcp` clean, `.venv/bin/python -m pytest -q` all green, `node --test "frontend/test/*.test.mjs"` green, `node --check` on every touched JS module.
- Manual UI recipe: `PORT_LIGHT_DATA_DIR=$(mktemp -d) .venv/bin/uvicorn backend.main:app --port 2100`, open `http://localhost:2100`.
- Python floor 3.11. No new runtime dependencies.
- API shapes stay byte-compatible except `/api/meta` dropping four unconsumed echo fields (task 4, changelogged).
- Privacy: no secrets, env values, or credential-bearing URLs in logs, degradations, or docs.
- Test scale: ONE new test total (task 6). Existing tests are adapted, never multiplied. No new defensive assertions.
- Docs ride with the commits that make them true: CHANGELOG `## Unreleased`, docs/architecture.md sections.
- Commit style: short imperative subject, no attribution footers, no generated-by trailers.
- Historical plans under `docs/superpowers/plans/` get a one-line status banner only; nothing else is rewritten.

---

### Task 1: Remove app.js ghost helpers, dead imports, and excision scars

**Files:**
- Modify: `frontend/js/app.js` (914 lines → ~640)

**Interfaces:**
- Consumes: `syncHeaderHeight`, `markRefreshed`, `setSyncError` from `dom.js`; `openModal`, `closeModals`, `modalOpen` from `modal.js` — already imported at lines 12/14, currently shadowed.
- Produces: no interface change. App body behavior identical.

- [ ] **Step 1: Delete the six local duplicate functions.** Remove `syncHeaderHeight` (86–90), `openModal`/`closeModals`/`modalOpen` (286–318), `markRefreshed` (677–686), `setSyncError` (688–695). Each is byte-identical to the module export; the imports become live.

- [ ] **Step 2: Trim dead imports.** Verified dead by occurrence count (name appears only on its import line): `appEl`, `detailContent`, `settingsBtn` (dom.js); `CARD_FIELD_KEYS`, `CORE_THEMES` (state.js); entire `kinds.js` line; `collate`, `tx`, `safeHref` (text.js); `renderSummary`, `renderHostSwitcher`, `renderHostBoards`, `portFromList`, `freeStub`, `pendingStub`, `prefetchKnown`, `getCellLabel`, `hiddenOccupancy`, `probeLockedHit`, `buildSearchContext`, `getKnownForFree`, `matchesFilter`, `sortPorts`, `renderGrid`, `showCopyToast`, `snapshotGridFocus`, `syncAddButton`, `applyPendingGridFocus` (grid.js); `listedHosts`, `hostById`, `hostName`, `occupancyUrl`, `portApiUrl`, `gridHash`, `portHash`, `dataForHost`, `fetchHealth`, `fetchHostHealth`, `fetchPorts`, `loadPorts`, `renderScanners` (api.js); `showPortDetail` (detail.js); `loadSettingsPage`, `showSettingsPanel`, `revertUnsavedSettings` (settings.js). Resulting import lists:

```js
import { S, SETTINGS_PANELS, applyTheme, applyAppearance, saveView } from './state.js?v=75';
import { errorText, escapeHtml, t } from './text.js?v=75';
import { moveChipFocus, trapTab } from './a11y.js?v=75';
import {
  grid, hostBoards, hostSwitcher, summary,
  detailPanel, detailBackdrop,
  searchInput, rangeStartInput, rangeEndInput,
  sortSelect, unhideBtn,
  syncHeaderHeight, markRefreshed, setSyncError,
} from './dom.js?v=75';
import { openModal, closeModals, modalOpen } from './modal.js?v=75';
import { applyRoute, parseHash, leaveSettingsOrStay } from './router.js?v=75';
import { render, syncFilterUI, syncHiddenButton, gridRootFrom, moveGridFocus } from './grid.js?v=75';
import {
  hasPeers, api, fetchMeta, fetchHosts, retryHost, setupRefresh, tick,
  startEventStream,
} from './api.js?v=75';
import { closeDetail, showDetailError, syncDetailModal, unlockHidden, addManualPort } from './detail.js?v=75';
import {
  goSettingsPanel, saveSettingsPage, applyServerSettings, markDirty,
  syncDependentSettings, fetchSettings, syncLocaleTrigger, closeLocaleMenu,
  moveLocaleHighlight, renderPeersEditor, readPeersDraftFromForm, syncPaletteAvailability,
} from './settings.js?v=75';
```

- [ ] **Step 3: Collapse blank-line runs.** Verify no backticks in the file (`grep -c '\`' frontend/js/app.js` → 0), then reduce every run of 2+ blank lines to one blank line (the ~220 scar lines left by the M6 extraction).

- [ ] **Step 4: Fix stray token.** Line 362 `errorText({}, 0) ;` → `errorText({}, 0);`

- [ ] **Step 5: Verify.** `node --check frontend/js/app.js`; `node --test "frontend/test/*.test.mjs"` (tests regex the `?v=` out of app.js source — still present); `.venv/bin/python -m pytest -q` (static/i18n tests read app.js); manual click-through: grid, search, add-port modal, unhide modal, settings tabs, theme switch, locale switch.

- [ ] **Step 6: CHANGELOG + commit.** `## Unreleased` → `### Changed`: "app.js no longer carries duplicate copies of the dom/modal helpers; its imports match what it uses (no behavior change)." Commit: `Drop duplicated helpers and dead imports from app.js`

---

### Task 2: i18n missing-key check covers every frontend module; README locale rows fixed

**Files:**
- Modify: `tests/test_i18n.py:85-97`
- Modify: `README.md:124`, `README.zh-CN.md:124`

**Interfaces:**
- Produces: `test_markup_i18n_keys_exist_in_english` now scans `frontend/index.html` + `frontend/js/*.js` + `frontend/i18n.js` (same file set the orphan test already uses).

- [ ] **Step 1: Widen the test glob.**

```python
def test_markup_i18n_keys_exist_in_english():
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = ""
    for pattern in ("js/*.js", "i18n.js"):
        for path in sorted((ROOT / "frontend").glob(pattern)):
            js += path.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria)?="([a-zA-Z0-9_.]+)"', html + js))
    keys |= set(re.findall(r"""\bt\(\s*['"]([a-zA-Z0-9_.]+)['"]""", js))
    keys = {key for key in keys if key and not key.endswith('.')}
    assert "filter.udp" in keys
    assert "filter.localhost" in keys
    for key in sorted(keys):
        _lookup(english, key)
    for prefix in sorted(set(re.findall(r"""\btx\(\s*['"]([a-zA-Z0-9_.]+)['"]""", js))):
        assert isinstance(english.get(prefix), dict), prefix
```

- [ ] **Step 2: Run it.** `.venv/bin/python -m pytest tests/test_i18n.py -q`. If it fails, each failure is a real missing key — fix the offending literal in the JS module (or add the key to all seven locale files if it is legitimately new copy; use `scripts/locale-scaffold.py`).

- [ ] **Step 3: Fix README rows.** Both line 124s become the full seven-language list:
  - en: `` | `LOCALE` | `auto` | `auto` / `en` / `fr` / `de` / `es` / `zh-CN` / `zh-TW` / `ja`. Auto follows the browser. |``
  - zh: `` | `LOCALE` | `auto` | `auto` / `en` / `fr` / `de` / `es` / `zh-CN` / `zh-TW` / `ja`。`auto` 跟随浏览器。 |``
  Then `grep -rn "zh-TW" README.md README.zh-CN.md CONTRIBUTING.md docs/*.md` and confirm no other stale language enumerations.

- [ ] **Step 4: Full gates + two commits.**
  - `Check i18n key references across every frontend module`
  - `List all seven locales in the README env tables`

---

### Task 3: `safe_http_url` moves to netaddr (classification purity)

**Files:**
- Modify: `backend/netaddr.py` (add function + constant + `urlparse` import; docstring gains one sentence)
- Modify: `backend/docker_scanner.py` (delete def at :794 and `_BAD_URL_SCHEMES` at :33; add to netaddr import)
- Modify: `backend/classification.py:10` (`from .netaddr import clean_bind_ip, proto_family_of, safe_http_url`; delete the docker_scanner import)
- Modify: `tests/test_parsers.py` (update whichever import line pulls `safe_http_url` from docker_scanner — check with grep first)

**Interfaces:**
- Produces: `backend.netaddr.safe_http_url(url: str | None) -> str | None`, `backend.netaddr._BAD_URL_SCHEMES`. `docker_scanner` re-exports nothing; importers switch to netaddr.

- [ ] **Step 1: Move the function verbatim** into netaddr.py (after `proto_family_of`), with `_BAD_URL_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "blob", "about"})` above it and `from urllib.parse import urlparse` in the imports. Docstring addition: one sentence — `` `safe_http_url` keeps label-derived links to plain http(s) URLs. ``

- [ ] **Step 2: Rewire importers.** docker_scanner: `from .netaddr import binding_ips, prefixless, safe_http_url, strip_brackets`; delete the local def and constant (check `urlparse` still used elsewhere in docker_scanner before removing its import). classification: drop `from .docker_scanner import safe_http_url`. test_parsers: switch its import source.

- [ ] **Step 3: Purity check.** `.venv/bin/python -c "import backend.classification, sys; assert 'backend.docker_scanner' not in sys.modules, 'still transitive'"` — must pass.

- [ ] **Step 4: Gates + docs + commit.** Full pytest + ruff. docs/architecture.md:22 tail already names netaddr for normalization — extend that clause with safe_http_url. CHANGELOG Changed: "`safe_http_url` lives in `backend/netaddr.py`; importing classification no longer pulls the Docker scanner." Commit: `Move safe_http_url into netaddr`

---

### Task 4: Delete the dead machines writer API and `/api/meta` appearance echo

**Files:**
- Modify: `backend/port_store.py:337-368` (delete `── Machines ──` section: `get_machines`, `add_machine`, `remove_machine`)
- Modify: `backend/main.py:162-165` (drop `refresh_ms`, `theme_mode`, `theme_palette`, `grid_density` from `meta()`)
- Modify: `tests/test_store.py:49-53` (drop get_machines assertions; keep junk-tolerance checks)
- Modify: whichever tests assert those meta fields — find with `grep -rn "theme_mode\|refresh_ms" tests/`

**Interfaces:**
- Consumes: verification that nothing reads those fields: `grep -rn "meta\.theme\|meta\.grid_density\|meta\.refresh_ms" frontend mcp skills` → zero; frontend uses `/api/settings` values for appearance and refresh.
- Produces: `/api/meta` returns version/auth/settings_readonly/automation only. `_load` still tolerates a `machines` array in old files (docstring line 20 stays true); `_load`'s empty default drops its `"machines": []` entry.

- [ ] **Step 1: Verify zero consumers** (greps above). If any consumer appears, keep that field and note it in the task comment.

- [ ] **Step 2: Delete + adapt tests.** port_store machines section gone; `_load` empty defaults become `{"manual_ports": [], "hidden_ports": [], "peers": []}`. test_store junk test keeps manual/hidden assertions, drops the machines ones. Update meta tests to the new shape.

- [ ] **Step 3: Gates + docs + commit.** Full pytest + ruff. CHANGELOG Changed: "The `machines` writer API (never reachable from the UI) is gone — old files carrying a `machines` array still load; `/api/meta` no longer echoes `theme_mode` / `theme_palette` / `grid_density` / `refresh_ms` (nothing consumed them — use `GET /api/settings`)." Commit: `Remove the unused machines writer API and /api/meta appearance echo`

---

### Task 5: Extract `backend/occupancy_cache.py`

**Files:**
- Create: `backend/occupancy_cache.py`
- Modify: `backend/main.py:54-59, 254-365, 641-695` (globals, `_scan_snapshot`, `_packed_occupancy`, `get_port` packed-map decode; new `_classify_snapshot` used by metrics/suggest/free_runs/get_port)
- Modify: `tests/test_api_auth.py` (three snapshot tests' seams; grep `_occ_snap\|_occ_building\|_OCC_TTL\|_STALE_SERVE_AFTER` across tests/ first)

**Interfaces:**
- Produces:

```python
def pack_key(start: int, end: int, show_hidden: bool, hidden_locked: bool) -> tuple: ...

class OccupancyCache:
    def __init__(self, ttl: float = 2.0, stale_after: float = 4.0) -> None: ...
    def get_or_build(self, key, build) -> dict:
        """One in-flight build; reuse fresh snapshots; serve-stale past the deadline.
        build() returns the raw scan dict; the cache stamps key/at/packed."""
    def snapshot(self) -> dict | None: ...
    def reset(self) -> None: ...
    def lookup_packed(self, snap: dict, key: tuple): ...
    def remember_packed(self, snap: dict, key: tuple, packed) -> None: ...
    def visibility_entries(self, snap: dict, show_hidden: bool, hidden_locked: bool) -> list[tuple[int, int, tuple]]:
        """(start, end, packed) for every memoized range at this visibility."""
```

Full module body (move, don't rewrite, the wait-loop from `_scan_snapshot`):

```python
"""Occupancy snapshot cache.

Concurrent callers share one in-flight scan; a finished snapshot is reused
for ``ttl`` seconds; when a rebuild outruns ``stale_after`` waiters get the
last good snapshot marked ``stale`` instead of blocking. Memoized
classification results hang off each snapshot keyed by range and visibility.
"""

from __future__ import annotations

import threading
import time


def pack_key(start: int, end: int, show_hidden: bool, hidden_locked: bool) -> tuple:
    return (start, end, show_hidden, hidden_locked)


class OccupancyCache:
    def __init__(self, ttl: float = 2.0, stale_after: float = 4.0) -> None:
        self.ttl = ttl
        self.stale_after = stale_after
        self._cond = threading.Condition()
        self._snap: dict | None = None
        self._building = False

    def get_or_build(self, key, build) -> dict:
        now = time.monotonic()
        deadline = now + min(2 * self.ttl, self.stale_after)
        with self._cond:
            snap = self._snap
            if snap and snap["key"] == key and now - snap["at"] < self.ttl:
                return snap
            while self._building:
                remaining = deadline - time.monotonic()
                if remaining <= 0 and snap is not None:
                    stale = dict(snap)
                    stale["stale"] = True
                    return stale
                self._cond.wait(timeout=0.25 if remaining <= 0 else min(0.25, remaining))
                snap = self._snap
                now = time.monotonic()
                if snap and snap["key"] == key and now - snap["at"] < self.ttl:
                    return snap
            self._building = True
        try:
            snap = build()
            snap["key"] = key
            snap["at"] = time.monotonic()
            snap.setdefault("packed", {})
            with self._cond:
                self._snap = snap
                return snap
        finally:
            with self._cond:
                self._building = False
                self._cond.notify_all()

    def snapshot(self) -> dict | None:
        with self._cond:
            return self._snap

    def reset(self) -> None:
        with self._cond:
            self._snap = None
            self._building = False

    def lookup_packed(self, snap: dict, key: tuple):
        with self._cond:
            return snap.get("packed", {}).get(key)

    def remember_packed(self, snap: dict, key: tuple, packed) -> None:
        with self._cond:
            snap.setdefault("packed", {})[key] = packed

    def visibility_entries(self, snap: dict, show_hidden: bool, hidden_locked: bool) -> list:
        with self._cond:
            packed_map = snap.get("packed") or {}
        return [
            (start, end, packed)
            for (start, end, sh, hl), packed in packed_map.items()
            if sh == show_hidden and hl == hidden_locked
        ]
```

- [ ] **Step 1: Create the module** exactly as above.

- [ ] **Step 2: Rewire main.py.** Replace globals 54–59 with `_occ = occupancy_cache.OccupancyCache()` (add module import). `_scan_snapshot` becomes:

```python
def _scan_snapshot(values: dict) -> dict:
    """Reuse Docker / listen / Compose scans for a couple of seconds.

    Opening `#/port/N` otherwise re-walks the same trees the grid just polled.
    Store writes bump ``store_generation`` so a hide / rename is visible immediately.
    Concurrent polls share one in-flight scan; a rebuild longer than the stale
    deadline serves the last good snapshot marked ``stale``.
    """
    return _occ.get_or_build(_scan_key(values), lambda: _build_snapshot(values))


def _build_snapshot(values: dict) -> dict:
    containers = scan_containers()
    prefer: list[int] = []
    for c in containers:
        prefer.extend(c.pids or [])
    return {
        "containers": containers,
        "listening": scan_listening_ports(prefer_pids=prefer),
        "compose_scan": scan_compose_tree(
            _compose_dir(),
            max_depth=values["compose_scan_depth"],
            max_files=values["compose_scan_max_files"],
        ),
        "user_state": port_store.occupancy_user_state(),
    }
```

Add the shared classify preamble and use it at all five sites (metrics :206, `_packed_occupancy` :336, suggest :576, get_port hidden branch :679, free_runs :832):

```python
def _classify_snapshot(snap: dict, values: dict, start: int, end: int,
                       show_hidden: bool = True, hidden_locked: bool = False) -> dict:
    manuals, hidden = snap["user_state"]
    return classify(
        snap["listening"],
        snap["containers"],
        snap["compose_scan"].ports,
        manuals,
        hidden,
        start,
        end,
        show_hidden,
        hidden_locked=hidden_locked,
        options=values,
    )
```

`_packed_occupancy` swaps `with _occ_lock` blocks for `_occ.lookup_packed` / `_occ.remember_packed` and `pkey = pack_key(start, end, show_hidden, hidden_locked)`. `get_port` swaps its packed-map decode for:

```python
    snap = _scan_snapshot(_values())
    entries = _occ.visibility_entries(snap, show_hidden, hidden_locked)
    for _start, _end, packed in entries:
        for row in packed[0]["ports"]:
            if row["port"] == port:
                return row
    if not entries:
        payload, _body, _etag = _packed_occupancy(request, 1, 65535, include_hidden)
        for row in payload["ports"]:
            if row["port"] == port:
                return row
```

- [ ] **Step 3: Adapt test seams.** Every `main._occ_snap = None; main._occ_building = False` → `main._occ.reset()`. In `test_slow_rebuild_serves_previous_snapshot`: `monkeypatch.setattr(main, "_STALE_SERVE_AFTER", 0.2)` → `monkeypatch.setattr(main._occ, "stale_after", 0.2)`; `main._occ_snap["at"] -= main._OCC_TTL + 0.05` → `main._occ.snapshot()["at"] -= main._occ.ttl + 0.05`. Grep other test files for the old globals.

- [ ] **Step 4: Gates + docs + commit.** Full pytest + ruff + node tests; manual: boot, grid, `#/port/2100`, settings, peer view. docs/architecture.md request-path paragraph: name `backend/occupancy_cache.py` where it describes snapshot reuse / serve-stale / memoized classification. CHANGELOG Changed: "The occupancy snapshot cache (in-flight dedupe, TTL reuse, serve-stale) lives in `backend/occupancy_cache.py`; API behavior unchanged." Commit: `Give the occupancy snapshot cache its own module`

---

### Task 6: Memoize store reads by file stat

**Files:**
- Modify: `backend/port_store.py:57-74` (`_load`)
- Modify: `tests/test_store.py` (one new test)

**Interfaces:**
- Produces: `_FILE_MEMO: dict[str, tuple[tuple[int, int], dict]]` keyed by resolved path; token = `(st_mtime_ns, st_size)`. External edits and this process's own `_save` both change the token, so behavior is unchanged — only repeated parses disappear (the SSE change-detector polls `_values()` every 500 ms per client).

- [ ] **Step 1: Write the failing test.**

```python
def test_load_picks_up_external_file_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    assert port_store.get_stored_settings() == {}
    (tmp_path / "port_light.json").write_text(json.dumps({
        "settings": {"locale": "de"},
        "manual_ports": [{"port": 5, "label": "ext"}],
    }), encoding="utf-8")
    assert port_store.get_stored_settings()["locale"] == "de"
    assert port_store.get_manual_ports()[0]["port"] == 5
```

Run: `.venv/bin/python -m pytest tests/test_store.py -q` — passes before the change too (it pins the invariant the memo must preserve), then stays green after.

- [ ] **Step 2: Implement the memo.**

```python
_FILE_MEMO: dict[str, tuple[tuple[int, int], dict]] = {}


def _load() -> dict:
    """Load the full data structure from disk, memoized by mtime + size.

    Any change to the file — from this process or a hand edit — alters the
    stat token, so the memo never serves an outdated document.
    """
    empty = {"manual_ports": [], "hidden_ports": [], "peers": []}
    f = _data_file()
    try:
        st = f.stat()
    except OSError:
        return empty
    token = (st.st_mtime_ns, st.st_size)
    memo = _FILE_MEMO.get(str(f))
    if memo is not None and memo[0] == token:
        return memo[1]
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError:
        corrupt = f.parent / (f.name + ".corrupt")
        try:
            os.replace(f, corrupt)
        except OSError:
            pass
        degradations.report("store", f.name, "corrupt file quarantined")
        return empty
    except OSError:
        return empty
    _FILE_MEMO[str(f)] = (token, data)
    return data
```

(The `machines` key is gone from the empty default — nothing has read it since task 4.)

- [ ] **Step 3: Gates + docs + commit.** Full pytest + ruff. docs/architecture.md persistence section: one sentence — "Store reads go through an mtime-keyed memo, so the SSE change-detector's half-second ticks no longer re-parse `port_light.json`." CHANGELOG Changed: "Repeated store reads reuse a stat-keyed memo; hand edits still apply immediately." Commit: `Memoize store reads by file mtime`

---

### Task 7: Appearance persistence gets one owner, previews stop leaking

**Files:**
- Modify: `frontend/js/state.js` (LIVE_APPLY_KEYS, hydrateCachedAppearance, applyAppearance loses its write, new persistAppearance)
- Modify: `frontend/js/app.js` (hydration → helper; live-apply branch becomes generic)
- Modify: `frontend/js/settings.js` (applyServerSettings persists; no-op filter at :289 deleted)

**Interfaces:**
- Produces from state.js: `LIVE_APPLY_KEYS = ['theme_mode', 'theme_palette', 'grid_density', 'locale']`, `hydrateCachedAppearance()`, `persistAppearance()`. `applyAppearance()` becomes write-free. settings.js keeps its layout filter (it routes fields to cards — layout, not live-apply policy; only app.js consumes LIVE_APPLY_KEYS).
- Invariant: `port-light-settings` in localStorage is written ONLY by `persistAppearance` — on boot (after `/api/settings` lands), on save, on revert, on theme-editor save/import/delete. Previews never persist. The pre-paint inline script in index.html and `i18n.js:69-75` stay read-only (load-order constraint: they run before modules).

- [ ] **Step 1: state.js changes.**

```js
export const LIVE_APPLY_KEYS = ['theme_mode', 'theme_palette', 'grid_density', 'locale'];

export function hydrateCachedAppearance() {
  try {
    const cached = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    if (cached.theme_mode) S.settings.theme_mode = cached.theme_mode;
    if (typeof cached.theme_palette === 'string') S.settings.theme_palette = cached.theme_palette;
    if (cached.grid_density) S.settings.grid_density = cached.grid_density;
    if (cached.locale) S.settings.locale = cached.locale;
  } catch (e) {}
}

export function applyAppearance() {
  applyTheme();
  applyDensity(S.settings.grid_density);
}

export function persistAppearance() {
  try {
    localStorage.setItem('port-light-settings', JSON.stringify({
      theme_mode: S.settings.theme_mode,
      theme_palette: S.settings.theme_palette || '',
      grid_density: S.settings.grid_density,
      locale: S.settings.locale || 'auto',
    }));
  } catch (e) {}
}
```

- [ ] **Step 2: app.js.** Replace the hydration try-block (44–50) with `hydrateCachedAppearance();`. Replace the hard-coded field chain (589–593) with:

```js
    if (LIVE_APPLY_KEYS.indexOf(field) >= 0) {
      S.settings[field] = e.target.value;
      applyAppearance();
      if (field === 'theme_mode') syncPaletteAvailability();
      if (field === 'locale' && window.PortLightI18n) { /* unchanged reload block */ }
    }
```

- [ ] **Step 3: settings.js.** In `applyServerSettings`, after `applyAppearance();` add `persistAppearance();` (import both). Delete the no-op at :289: `const customs = (S.customThemes || []).filter(function (t) { return true; });` → `const customs = S.customThemes || [];`

- [ ] **Step 4: Check test fallout.** `grep -rn "applyAppearance\|port-light-settings" frontend/test/` — adapt any assertion that expected the old write-on-apply behavior to call `persistAppearance` explicitly.

- [ ] **Step 5: Verify.** `node --check` on all three modules; node tests; pytest; manual: (a) switch theme, leave settings without saving, reload → old theme, no flash of the discarded one; (b) save, reload → new theme; (c) delete the selected custom theme, reload → built-in, and localStorage no longer carries the dead `@custom:` id; (d) env-pinned `LOCALE` still wins after settings land.

- [ ] **Step 6: Docs + commit.** docs/architecture.md frontend section: "Appearance is cached in `localStorage` only to avoid a flash before `/api/settings` returns" — extend with "written only when settings are saved or reverted (`state.js` owns the key)". CHANGELOG Fixed: "Unsaved appearance previews no longer leak into the next page load: the localStorage copy is written only on save/revert, by a single owner." Commit: `Persist appearance to localStorage only on save`

---

### Task 8: Archive finished plans, final sweep

**Files:**
- Delete: `PLAN.local.md` (all milestones shipped through 0.7.3; rollback tags live in git; content is the misleading-rewrite risk)
- Modify: the five plans under `docs/superpowers/plans/` — add one status line under each title, nothing else

- [ ] **Step 1: Check references.** `grep -rn "PLAN.local" . --include="*.md" --include="*.py" --include="*.yml"` — expect none outside itself; remove the file.

- [ ] **Step 2: Banner each shipped plan** (`2026-08-25-theme-orthogonalization.md`, `2026-08-25-custom-themes-display-sliders.md`, `2026-08-25-localization-fr-de-es.md`, `2026-08-25-agent-visibility.md`, `2026-08-26-density-presets.md`) with, under the H1:

```markdown
> **Status: shipped (v0.7.1–v0.7.3). Historical record — do not execute.**
```

- [ ] **Step 3: Final sweep.** `grep -rn "_occ_snap\|get_machines\|_occ_building" backend tests docs` → zero hits. Full gates: ruff, pytest, node tests, plus the manual UI recipe once more end-to-end (grid, detail drawer, settings save round-trip, theme editor, locale switch, peers editor).

- [ ] **Step 4: Commit.** `Archive finished implementation plans`

---

## Self-review

- Spec coverage: six approved points → Task 1 (ghost code), Task 2 (guardrails + README), Tasks 3+4 (small backend tidy: purity, machines, meta echo), Task 5 (OccupancyCache), Task 6 (SSE/store memo), Task 7 (appearance ownership), Task 8 (doc archival). The classify-preamble duplication folds into Task 5.
- Placeholder scan: every code block is complete; grep commands named; no TBD.
- Type consistency: `pack_key`/`visibility_entries` shapes match Task 5's main.py rewrite; `persistAppearance` name is used consistently in Task 7's steps.
- Test budget: one new test (Task 6 Step 1); all other test edits are seam adaptations of existing tests.
