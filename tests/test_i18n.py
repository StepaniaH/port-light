from __future__ import annotations

import json
import re
from pathlib import Path

from backend.settings import FIELDS


ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "frontend" / "locales"
CODES = ("en", "fr", "de", "es", "zh-CN", "zh-TW", "ja")


def _keys(obj: object, prefix: str = "") -> set[str]:
    out: set[str] = set()
    assert isinstance(obj, dict), prefix or "<root>"
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out |= _keys(value, path)
        else:
            assert isinstance(value, str) and value.strip(), path
            out.add(path)
    return out


def _lookup(tree: dict, key: str) -> None:
    cur: object = tree
    for part in key.split("."):
        assert isinstance(cur, dict) and part in cur, key
        cur = cur[part]
    assert isinstance(cur, str) and cur.strip(), key


def test_locale_files_have_no_duplicate_keys():
    def no_dupes(pairs):
        keys = [key for key, _ in pairs]
        assert len(keys) == len(set(keys)), keys
        return dict(pairs)

    for code in CODES:
        text = (LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8")
        json.loads(text, object_pairs_hook=no_dupes)


def test_locale_files_share_the_english_key_tree():
    trees = {}
    for code in CODES:
        path = LOCALES_DIR / f"{code}.json"
        trees[code] = json.loads(path.read_text(encoding="utf-8"))
    english = _keys(trees["en"])
    assert english
    for code in CODES:
        assert _keys(trees[code]) == english, code


def test_locale_endonyms_are_identical_across_files():
    natives = None
    for code in CODES:
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        if natives is None:
            natives = data["localeNative"]
        else:
            assert data["localeNative"] == natives
    assert natives["en"] == "English"
    assert natives["zh-CN"] == "简体中文"
    assert natives["zh-TW"] == "繁體中文"
    assert natives["ja"] == "日本語"


def test_settings_fields_have_locale_copy():
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    for spec in FIELDS:
        _lookup(english, f"settings.fields.{spec.key}.label")
        _lookup(english, f"settings.fields.{spec.key}.help")
        _lookup(english, f"settings.groups.{spec.group}.title")
        _lookup(english, f"settings.groups.{spec.group}.blurb")
        for choice in spec.choices:
            if spec.kind == "multi_choice":
                _lookup(english, f"settings.scanners.{choice}.label")
                _lookup(english, f"settings.scanners.{choice}.help")
                continue
            if choice == "":
                continue  # empty palette = built-in option, translated via settings.theme.builtin
            _lookup(english, f"choice.{choice}")


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
    # Keys also reach t()/tx() via ternaries and key-helper arguments
    # (kvRow('settings.auto.agentToken', …), const key = c ? 'grid.empty' : …).
    # Any quoted dotted literal is an exact reference; a quoted literal ending in
    # a dot is a dynamic prefix ('status.' + p.status) like the tx( prefixes.
    refs |= set(re.findall(r"""['"]([a-z][a-zA-Z0-9_.]+)['"]""", blob))
    tx_prefixes |= set(re.findall(r"""['"]([a-z][a-zA-Z0-9_.]+\.)['"]""", blob))
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


def test_cache_bust_matches_index():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "i18n.js").read_text(encoding="utf-8")
    match = re.search(r"CACHE_BUST = '(\d+)'", js)
    assert match
    version = match.group(1)
    assert f"i18n.js?v={version}" in html
    assert f"js/app.js?v={version}" in html
    assert f"style.css?v={version}" in html


def test_scanner_guidance_is_adapted_in_every_locale():
    for code in CODES:
        tree = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        diagnostics = tree["scanner"]["diagnostics"]
        assert tree["settings"]["nav"]["occupancy"] in diagnostics["selection"], code
        assert "group_add" in tree["settings"]["scanners"]["docker"]["remediation"], code
        for command in ("docker compose up -d", "docker compose restart"):
            assert command in diagnostics["upgrade"], (code, command)
        for token in ("0.0.0.0", "::", "IPv4", "IPv6"):
            assert token in tree["detail"]["publicBindHint"], (code, token)


def test_diagnostic_sentences_are_translated_not_english_scaffolds():
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    for code in CODES[1:]:
        tree = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for key, value in english["scanner"]["diagnostics"].items():
            assert tree["scanner"]["diagnostics"][key] != value, (code, key)
