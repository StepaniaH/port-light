from __future__ import annotations

import json
import re
from pathlib import Path

from backend.settings import FIELDS


ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "frontend" / "locales"
CODES = ("en", "zh-CN", "zh-TW", "ja")


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
            if choice == "":
                continue  # empty palette = built-in option, translated via settings.theme.builtin
            _lookup(english, f"choice.{choice}")


def test_markup_i18n_keys_exist_in_english():
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria)?="([a-zA-Z0-9_.]+)"', html + js))
    keys |= set(re.findall(r"""\bt\(\s*['"]([a-zA-Z0-9_.]+)['"]""", js))
    keys = {key for key in keys if key and not key.endswith('.')}
    assert "filter.udp" in keys
    assert "filter.localhost" in keys
    for key in sorted(keys):
        _lookup(english, key)
    for prefix in sorted(set(re.findall(r"""\btx\(\s*['"]([a-zA-Z0-9_.]+)['"]""", js))):
        assert isinstance(english.get(prefix), dict), prefix


def test_cache_bust_matches_index():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "i18n.js").read_text(encoding="utf-8")
    match = re.search(r"CACHE_BUST = '(\d+)'", js)
    assert match
    version = match.group(1)
    assert f"i18n.js?v={version}" in html
    assert f"js/app.js?v={version}" in html
    assert f"style.css?v={version}" in html
