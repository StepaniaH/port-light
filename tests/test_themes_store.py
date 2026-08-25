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
