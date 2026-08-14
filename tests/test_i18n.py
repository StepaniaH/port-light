from __future__ import annotations

import json
from pathlib import Path


LOCALES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "locales"
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
