from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend import settings as app_settings


def test_settings_field_groups_match_the_settings_panes():
    assert {spec.group for spec in app_settings.FIELDS} <= {
        "appearance", "grid", "scanning", "links",
    }



def test_settings_file_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THEME", "dark")
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "auto")
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    values, origins = app_settings.resolve()
    assert values["theme"] == "dark"
    assert origins["theme"] == "env"

    client = TestClient(app)
    res = client.put("/api/settings", json={"theme": "light", "refresh_ms": 8000})
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["theme"] == "light"
    assert body["origins"]["theme"] == "file"
    assert body["values"]["refresh_ms"] == 8000


def test_settings_source_env_locks_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "env")
    monkeypatch.setenv("THEME", "dark")
    client = TestClient(app)
    locked = client.put("/api/settings", json={"theme": "light"})
    assert locked.status_code == 403
    got = client.get("/api/settings").json()
    assert got["readonly"] is True
    assert got["values"]["theme"] == "dark"


def test_settings_rejects_bad_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT_LIGHT_SETTINGS_SOURCE", raising=False)
    client = TestClient(app)
    assert client.put("/api/settings", json={"theme": "neon"}).status_code == 400
    palette = client.put("/api/settings", json={"theme": "gruvbox"})
    assert palette.status_code == 200
    assert palette.json()["values"]["theme"] == "gruvbox"
    assert client.put("/api/settings", json={"locale": "fr"}).status_code == 400
    ok = client.put("/api/settings", json={"locale": "ja"})
    assert ok.status_code == 200
    assert ok.json()["values"]["locale"] == "ja"
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
    assert client.put("/api/settings", json={"url_host": "http://nas.lan"}).status_code == 400
    v6 = client.put("/api/settings", json={"url_host": "[2001:db8::10]"})
    assert v6.status_code == 200
    assert v6.json()["values"]["url_host"] == "2001:db8::10"


def test_manual_list_patch_and_known(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    created = client.post("/api/manual-ports", json={"port": 4242, "label": "lab"})
    assert created.status_code == 200
    listed = client.get("/api/manual-ports").json()["manual_ports"]
    assert listed[0]["label"] == "lab"
    patched = client.patch("/api/manual-ports/4242", json={"label": "lab 2"})
    assert patched.json()["entry"]["label"] == "lab 2"
    missing = client.delete("/api/manual-ports/9999")
    assert missing.status_code == 404
    known = client.get("/api/known-ports/2100")
    assert known.status_code == 200
    assert known.json()["name"] == "Port-Light"
    assert client.get("/api/known-ports/1").status_code == 404


def test_get_single_port_free(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    row = client.get("/api/ports/2100")
    assert row.status_code == 200
    assert row.json()["port"] == 2100


def test_favicon():
    client = TestClient(app)
    res = client.get("/favicon.ico")
    assert res.status_code == 200


def test_index_is_not_cached():
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-cache"


def test_collect_urls_uses_url_host():
    from backend.classification import collect_urls as _collect_urls
    known = {"name": "Jellyfin", "is_access_port": True}
    urls = _collect_urls(8096, ["0.0.0.0"], [], known, {"guess_urls": True, "url_host": "nas.lan", "url_scheme": "https"})
    assert "https://nas.lan:8096" in urls
    off = _collect_urls(8096, ["0.0.0.0"], [], known, {"guess_urls": False})
    assert off == []
