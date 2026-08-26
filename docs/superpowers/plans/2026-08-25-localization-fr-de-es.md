# Localization fr/de/es Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship French, German, and Spanish locales at the existing quality bar, plus tooling that makes locale drift structurally visible (placeholder parity, orphan keys, scaffold script).

**Architecture:** No runtime architecture change. Tooling extends `tests/test_i18n.py` and adds `scripts/locale-scaffold.py` (stdlib-only). Each language lands as a complete `frontend/locales/<code>.json` mirroring the English tree, with `CODES` growing in the same task so every parity check applies immediately. Runtime wiring is two lists (backend choices, i18n.js SUPPORTED).

**Tech Stack:** Python stdlib + pytest; vanilla JS untouched except i18n.js SUPPORTED array.

**Spec:** `docs/superpowers/specs/2026-08-25-localization-fr-de-es-design.md`

## Global Constraints

- Locale parity across all CODES locales enforced by `tests/test_i18n.py`; placeholder tokens `{name}` style must match English exactly per key.
- Translation register: formal (vous / Sie / usted). Technical tokens unchanged: TCP, UDP, HTTP, JSON, YAML, Docker, Compose, MCP, API, SSE, TTL, hex, Port-Light.
- No external translation services; no network calls in scaffold script.
- Backend stdlib only; ruff clean; `.venv/bin/python -m pytest`, `node --test "frontend/test/*.test.mjs"` are the gates.
- One concern per commit.
- House rules: factual docs, no AI slop, no emojis, no personal hosts/domains/tokens.

## Shared translation rules (apply to Tasks 2–4)

1. Translate from `frontend/locales/en.json` values only; key order mirrors en exactly.
2. `{placeholder}` sequences copied verbatim; a translation may reorder around them but never drop, rename, or add one.
3. HTML entities appearing in en values (`&lt;your-token&gt;`) are kept as entities.
4. Glossary — fixed renderings per language:

| en | fr | de | es |
|---|---|---|---|
| port | port | Port | puerto |
| used / in use | utilisé / occupé | belegt | en uso |
| configured | configuré | deklariert | configurado |
| free | libre | frei | libre |
| occupancy map | carte d'occupation | Belegungskarte | mapa de ocupación |
| palette | palette | Palette | paleta |
| custom palette | palette personnalisée | eigene Palette | paleta personalizada |
| brightness | luminosité | Helligkeit | brillo |
| lease / reservation | réservation | Reservierung | reserva |
| grid | grille | Raster | cuadrícula |
| card | carte | Karte | tarjeta |
| host / machine | hôte / machine | Host / Rechner | equipo |
| peer | pair | Gegenstelle | par |
| settings | réglages | Einstellungen | ajustes |
| hidden ports | ports masqués | verborgene Ports | puertos ocultos |

5. Spot-check list (reviewer verifies these render sensibly and keep placeholders):
   `settings.auto.connect.curlToken`, `settings.editor.hint`,
   `settings.auto.activity.lastUsed`, `hosts.dockerHint`, `detail.expiresIn` family,
   `settings.fields.card_scale.help`, `settings.fields.text_scale.help`.

---

### Task 1: Anti-drift tooling

**Files:**
- Modify: `tests/test_i18n.py`
- Create: `scripts/locale-scaffold.py`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Produces:
  - Tests `test_locale_values_keep_english_placeholders` and `test_no_orphan_locale_keys`
    (both green on current tree).
  - Script CLI: `python scripts/locale-scaffold.py [--check] [--untranslated]`
    (runnable via `.venv/bin/python`). Exit 0 clean / 1 missing keys under `--check`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i18n.py`:

```python
import re


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Leaf-key prefixes whose keys are assembled dynamically in frontend/backend code
# (choice.<value>, settings.fields.<FieldSpec.key>, …) and therefore never appear
# as literals in sources.
_DYNAMIC_PREFIXES = (
    "choice.",
    "settings.fields.",
    "settings.groups.",
    "localeName.",
    "localeNative.",
    "settings.editor.vars.",
    "settings.source.",
)


def _flatten(obj, prefix=""):
    out = {}
    assert isinstance(obj, dict)
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


def _referenced_keys():
    roots = []
    frontend = ROOT / "frontend"
    for pattern in ("*.html", "js/*.js", "i18n.js", "test/*.mjs"):
        roots.extend(frontend.glob(pattern))
    blob = ""
    for path in roots:
        blob += path.read_text(encoding="utf-8")
    refs = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria)?="([a-zA-Z0-9_.]+)"', blob))
    refs |= set(re.findall(r"""\bt\(\s*['"]([a-zA-Z0-9_.]+)['"]""", blob))
    tx_prefixes = set(re.findall(r"""\btx\(\s*['"]([a-zA-Z0-9_.]+)['"]""", blob))
    return refs, tx_prefixes


def test_locale_values_keep_english_placeholders():
    trees = {code: json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
             for code in CODES}
    english = _flatten(trees["en"])
    expected = {key: set(_PLACEHOLDER_RE.findall(str(value))) for key, value in english.items()}
    for code in CODES[1:]:
        flat = _flatten(trees[code])
        for key, want in expected.items():
            got = set(_PLACEHOLDER_RE.findall(str(flat[key])))
            assert got == want, f"{code}:{key} placeholders {sorted(got)} != en {sorted(want)}"


def test_no_orphan_locale_keys():
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    refs, tx_prefixes = _referenced_keys()

    def covered(key):
        if any(key.startswith(p) for p in _DYNAMIC_PREFIXES):
            return True
        if key in refs:
            return True
        return any(key.startswith(p) for p in tx_prefixes)

    orphans = sorted(key for key in _flatten(english) if not covered(key))
    assert orphans == [], orphans
```

- [ ] **Step 2: Run them — expect PASS**

Run: `.venv/bin/python -m pytest tests/test_i18n.py -q`
Expected: all pass. These are characterization guards; they fail only when drift exists.
If either fails on the current tree, that IS a found defect: fix the data (locale file or
dead key removal) as part of this task and note it in the report.

- [ ] **Step 3: Implement `scripts/locale-scaffold.py`**

```python
#!/usr/bin/env python3
"""Sync locale files against en.json.

Adds every key that exists only in en.json to the other locale files, copying
the English value so files stay valid; translators replace the copies after.
Never deletes anything.

  python scripts/locale-scaffold.py                 # add missing keys
  python scripts/locale-scaffold.py --check         # exit 1 if anything missing
  python scripts/locale-scaffold.py --untranslated  # list copies still == en
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "locales"
SOURCE = "en"


def load(code: str):
    text = (LOCALES / f"{code}.json").read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=collections.OrderedDict)


def flatten(obj, prefix=""):
    out = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def insert(tree, dotted, value):
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def prune_empty(node):
    changed = False
    for key in list(node):
        if isinstance(node[key], dict):
            changed |= prune_empty(node[key])
            if not node[key]:
                del node[key]
                changed = True
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--untranslated", action="store_true")
    args = ap.parse_args()

    english = flatten(load(SOURCE))
    status = 0
    for path in sorted(LOCALES.glob("*.json")):
        code = path.stem
        if code == SOURCE:
            continue
        raw = path.read_text(encoding="utf-8")
        tree = load(code)
        flat = flatten(tree)
        missing = [k for k in english if k not in flat]
        extra = [k for k in flat if k not in english]
        if args.untranslated:
            same = sorted(k for k in english
                          if k in flat and flat[k] == english[k] and english[k].strip())
            print(f"{code}: {len(same)} untranslated-looking keys")
            for key in same:
                print(f"  {key}")
            continue
        if extra:
            print(f"{code}: WARNING {len(extra)} keys absent from en.json (kept as-is)")
        for key in missing:
            insert(tree, key, english[key])
        while prune_empty(tree):
            pass
        if missing and not args.check:
            path.write_text(json.dumps(tree, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"{code}: {'MISSING ' if missing else 'ok '}"
              f"({len(missing)} missing, {len(extra)} extra)")
        if missing and args.check:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Exercise the script**

Run: `.venv/bin/python scripts/locale-scaffold.py --check`
Expected: exit 0, four `ok (0 missing, 0 extra)` lines.
Then prove detection works by temporarily removing a key (e.g. delete `"copy"` from
zh-CN.json's `settings.auto.connect`), re-run `--check` (expect exit 1 naming zh-CN),
restore the key via scaffold, and confirm `git diff` shows only reordering-free content
and the suite is green again. Report both outputs.

- [ ] **Step 5: CONTRIBUTING.md section**

Add before the existing "Tests" or environment section, matching surrounding tone:

```markdown
## Adding UI copy

Edit `frontend/locales/en.json`, run `.venv/bin/python scripts/locale-scaffold.py`
to copy the new keys into the other locale files, translate the copied values, done —
`tests/test_i18n.py` enforces key parity, non-empty values, placeholder tokens, and
rejects orphaned keys nobody references. `--untranslated` lists suspicious
still-equal-to-English values per locale.
```

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
node --test "frontend/test/*.test.mjs"
git add tests/test_i18n.py scripts/locale-scaffold.py CONTRIBUTING.md
git commit -m "Add locale drift tooling: placeholder parity, orphan keys, scaffold script"
```

---

### Task 2: French locale

**Files:**
- Create: `frontend/locales/fr.json`
- Modify: `tests/test_i18n.py` (`CODES = ("en", "fr", "zh-CN", "zh-TW", "ja")` — position mirrors UI picker order after en)
- Modify: every existing locale file's `localeNative` (+ `"fr": "Français"`) and
  `localeName` (+ `"fr": "French"`) maps — identity across files is enforced.
- Create: `.superpowers/sdd/task-fr-report.md` (report)

**Interfaces:** Consumes Task 1 checks (they now cover fr automatically). Produces a
complete fr tree; later tasks follow the identical recipe.

- [ ] **Step 1: Generate the translation**

Translate every value in `frontend/locales/en.json` into French following the shared
rules + glossary. Key order mirrors en byte-for-byte. Keep `localeNative` /
`localeName` maps identical in structure to en's (all six codes once Task 4 lands;
while only fr exists, include the fr entry alongside existing four).

- [ ] **Step 2: Wire CODES + shared name maps**

Apply the file changes listed above. The scaffold script may be used to bootstrap
fr.json structure from en, then values replaced — allowed, but the committed file must
contain zero untranslated leftovers flagged by review sampling.

- [ ] **Step 3: Verify**

```bash
.venv/bin/python scripts/locale-scaffold.py --check   # expect ok everywhere
.venv/bin/python -m pytest tests/test_i18n.py -q      # parity now spans fr
node --test "frontend/test/*.test.mjs"                # untouched
```

Self-review pass: read your own fr.json top-to-bottom for register consistency
(vous), glossary adherence, and placeholder integrity before committing.

- [ ] **Step 4: Commit**

```bash
git add frontend/locales tests/test_i18n.py
git commit -m "Add French locale"
```

---

### Task 3: German locale

Identical recipe to Task 2 with: file `de.json`, `CODES` gains `"de"` after `"fr"`,
`localeNative` + `"de": "Deutsch"`, `localeName` + `"de": "German"`.
Commit: `git commit -m "Add German locale"`.

---

### Task 4: Spanish locale

Identical recipe to Task 2 with: file `es.json`, `CODES` becomes
`("en", "fr", "de", "es", "zh-CN", "zh-TW", "ja")`, `localeNative` +
`"es": "Español"`, `localeName` + `"es": "Spanish"`.
Commit: `git commit -m "Add Spanish locale"`.

---

### Task 5: Runtime wiring

**Files:**
- Modify: `backend/settings.py` (locale FieldSpec choices)
- Modify: `frontend/i18n.js` (`SUPPORTED`)
- Modify: `README.md`, `README.zh-CN.md` (languages mention)
- Modify: `CHANGELOG.md` (Unreleased bullet)

- [ ] **Step 1: Failing backend test**

Append to `tests/test_settings.py`:

```python
def test_locale_choice_accepts_new_languages(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCALE", "fr")
    values, _ = app_settings.resolve()
    assert values["locale"] == "fr"
    monkeypatch.setenv("LOCALE", "xx-YY")
    with pytest.raises(Exception):
        app_settings.apply_patch({"locale": "xx-YY"})
```

(Adjust the negative half to whatever assertion style neighbours use — the point is:
fr accepted, garbage rejected.) Run → expect FAIL (fr rejected today).

- [ ] **Step 2: Implement**

backend/settings.py locale choices become:

```python
        choices=("auto", "en", "fr", "de", "es", "zh-CN", "zh-TW", "ja"),
```

frontend/i18n.js:

```js
  var SUPPORTED = ['en', 'fr', 'de', 'es', 'zh-CN', 'zh-TW', 'ja'];
```

README.md languages line (where the four languages are listed) becomes "English,
Français, Deutsch, Español, 简体中文, 繁體中文, 日本語"; README.zh-CN.md likewise.

CHANGELOG Unreleased gets:

```markdown
- Localization: three new interface languages — Français, Deutsch, Español — alongside
  tooling that keeps every locale in lockstep (placeholder parity and unused-key tests,
  plus `scripts/locale-scaffold.py` for adding copy).
```

- [ ] **Step 3: Gates + commit**

Full pytest + node + ruff green, then
`git commit -m "Wire fr/de/es into locale choices and browser matching"`.

---

### Task 6: Existing-locales audit + close-out

**Files:**
- Possibly modify: `frontend/locales/{en,zh-CN,zh-TW,ja}.json` (defect fixes only)
- Modify: `.superpowers/sdd/progress.md` (ledger)

- [ ] **Step 1: Mechanical sweeps**

```bash
.venv/bin/python scripts/locale-scaffold.py --untranslated
```

Review the report: flag genuine mistranslations hiding behind English-identical values
vs legitimate matches (proper nouns). Then grep each existing locale for broken HTML
entities (`&lt;` without `;`), doubled spaces, and trailing-period mismatches vs en.

- [ ] **Step 2: Defect pass over zh-CN / zh-TW / ja / en**

Read the four files fully. Fix only real defects (wrong term, truncated sentence,
inconsistent terminology vs glossary history in CHANGELOG). Record debatable items in
the ledger instead of churning. If nothing is wrong, say so explicitly in the report —
a clean audit is a valid outcome.

- [ ] **Step 3: Final gates + ledger**

ruff + full pytest + node suite green. Update `.superpowers/sdd/progress.md` with
audit verdict and any deferred items.

- [ ] **Step 4: Commit (only if fixes were made)**

`git commit -m "Fix localization defects found in the pre-existing locales audit"`
