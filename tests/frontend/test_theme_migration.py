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
    degradations.reset()
    assert app_settings.migrate_theme("neon-dream") == ("system", "")
    assert degradations.recent()[-1]["reason"] == "unknown value reset"


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

