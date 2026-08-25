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
