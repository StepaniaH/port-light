from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from backend.main import app
from backend import settings as app_settings


def test_settings_field_groups_match_the_settings_panes():
    assert {spec.group for spec in app_settings.FIELDS} <= {
        "appearance", "grid", "local", "scanning", "links",
    }



def test_settings_file_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THEME_MODE", "dark")
    monkeypatch.setenv("THEME_PALETTE", "nord")
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "auto")
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    values, origins = app_settings.resolve()
    assert values["theme_mode"] == "dark"
    assert values["theme_palette"] == "nord"
    assert origins["theme_mode"] == "env"

    client = TestClient(app)
    res = client.put("/api/settings", json={"theme_mode": "light", "refresh_ms": 8000})
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["theme_mode"] == "light"
    assert body["origins"]["theme_mode"] == "file"
    assert body["values"]["refresh_ms"] == 8000


def test_settings_source_env_locks_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "env")
    monkeypatch.setenv("THEME_MODE", "dark")
    client = TestClient(app)
    locked = client.put("/api/settings", json={"theme_mode": "light"})
    assert locked.status_code == 403


def test_settings_rejects_bad_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT_LIGHT_SETTINGS_SOURCE", raising=False)
    client = TestClient(app)
    assert client.put("/api/settings", json={"theme_palette": "neon"}).status_code == 400
    palette = client.put("/api/settings", json={"theme_palette": "gruvbox"})
    assert palette.status_code == 200
    assert palette.json()["values"]["theme_palette"] == "gruvbox"
    assert client.put("/api/settings", json={"locale": "xx-YY"}).status_code == 400
    ok = client.put("/api/settings", json={"locale": "ja"})
    assert ok.status_code == 200
    assert ok.json()["values"]["locale"] == "ja"
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
    assert client.put("/api/settings", json={"url_host": "http://nas.lan"}).status_code == 400
    v6 = client.put("/api/settings", json={"url_host": "[2001:db8::10]"})
    assert v6.status_code == 200
    assert v6.json()["values"]["url_host"] == "2001:db8::10"


def test_locale_choice_accepts_new_languages(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT_LIGHT_SETTINGS_SOURCE", raising=False)
    monkeypatch.setenv("LOCALE", "fr")
    values, _ = app_settings.resolve()
    assert values["locale"] == "fr"
    client = TestClient(app)
    ok = client.put("/api/settings", json={"locale": "fr"})
    assert ok.status_code == 200
    assert ok.json()["values"]["locale"] == "fr"
    assert client.put("/api/settings", json={"locale": "xx-YY"}).status_code == 400


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


def test_get_single_port_free(tmp_path, monkeypatch, empty_scan):
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


def test_density_defaults_to_standard(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    values, _ = app_settings.resolve()
    assert values["grid_density"] == "standard"
    assert "card_scale" not in values
    assert "text_scale" not in values


def test_bind_address_card_settings_default_off_with_both_families_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    values, _ = app_settings.resolve()
    assert values["show_bind_addresses"] is False
    assert values["show_bind_ipv4"] is True
    assert values["show_bind_ipv6"] is True

    client = TestClient(app)
    saved = client.put("/api/settings", json={
        "show_bind_addresses": True,
        "show_bind_ipv4": False,
        "show_bind_ipv6": True,
    })
    assert saved.status_code == 200
    assert saved.json()["values"]["show_bind_addresses"] is True
    assert saved.json()["values"]["show_bind_ipv4"] is False
    assert saved.json()["values"]["show_bind_ipv6"] is True


def test_local_scanners_are_one_atomic_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SCANNERS", "listen,docker,compose")
    monkeypatch.delenv("PORT_LIGHT_SETTINGS_SOURCE", raising=False)
    client = TestClient(app)

    saved = client.put("/api/settings", json={"local_scanners": ["listen", "docker"]})
    assert saved.status_code == 200
    assert saved.json()["values"]["local_scanners"] == ["listen", "docker"]
    values, origins = app_settings.resolve()
    assert values["local_scanners"] == ["listen", "docker"]
    assert origins["local_scanners"] == "file"

    assert client.put("/api/settings", json={"local_scanners": []}).status_code == 400
    assert client.put("/api/settings", json={"local_scanners": ["listen", "nope"]}).status_code == 400


@pytest.mark.parametrize("selection", ["listen,dokcer", "", " , "])
def test_invalid_scanner_env_blocks_scans_but_can_be_repaired_in_settings(empty_scan, monkeypatch, selection):
    monkeypatch.setenv("PORT_LIGHT_SCANNERS", selection)
    client = TestClient(app)
    assert client.get("/api/ports").status_code == 503
    assert client.get("/api/ports/suggest").status_code == 503
    document = client.get("/api/settings")
    assert document.status_code == 200
    assert document.json()["values"]["local_scanners"] == []
    assert document.json()["origins"]["local_scanners"] == "env"
    assert document.json()["local_scanning"]["ready"] is False
    repaired = client.put("/api/settings", json={"local_scanners": ["listen"]})
    assert repaired.status_code == 200
    assert client.get("/api/ports").status_code == 200


def test_invalid_saved_scanners_do_not_fall_back_to_environment(empty_scan):
    from backend import port_store

    port_store.update_stored_settings({"local_scanners": ["dokcer"]})
    client = TestClient(app)
    assert client.get("/api/ports").status_code == 503
    assert client.get("/api/settings").json()["origins"]["local_scanners"] == "file"


def test_invalid_scanner_env_remains_readonly_in_env_mode(empty_scan, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_SCANNERS", "listen,dokcer")
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "env")
    client = TestClient(app)
    document = client.get("/api/settings").json()
    assert document["readonly"] is True
    assert document["values"]["local_scanners"] == []
    assert client.put("/api/settings", json={"local_scanners": ["listen"]}).status_code == 403


def test_local_host_name_can_be_saved_in_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_HOST_NAME", "Environment name")
    monkeypatch.delenv("PORT_LIGHT_SETTINGS_SOURCE", raising=False)
    client = TestClient(app)

    saved = client.put("/api/settings", json={"host_name": "My server"})
    assert saved.status_code == 200
    assert client.get("/api/hosts").json()["local"]["name"] == "My server"


def test_host_layout_defaults_to_waterfall_and_can_be_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT_LIGHT_HOST_LAYOUT", raising=False)
    client = TestClient(app)
    settings_doc = client.get("/api/settings").json()
    assert settings_doc["values"]["host_layout"] == "waterfall"
    layout_field = next(field for field in settings_doc["fields"] if field["key"] == "host_layout")
    assert layout_field["group"] == "appearance"
    saved = client.put("/api/settings", json={"host_layout": "tabs"})
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["values"]["host_layout"] == "tabs"
    assert client.put("/api/settings", json={"host_layout": "automatic"}).status_code == 400


def test_local_description_is_optional_bounded_and_can_be_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_HOST_DESCRIPTION", "Environment note")
    client = TestClient(app)
    assert client.get("/api/hosts").json()["local"]["description"] == "Environment note"
    description = "机" * 120
    saved = client.put("/api/settings", json={"host_description": " " + description + " "})
    assert saved.status_code == 200
    field = next(row for row in saved.json()["fields"] if row["key"] == "host_description")
    assert field["max_length"] == 120
    assert client.get("/api/hosts").json()["local"]["description"] == description
    assert client.put("/api/settings", json={"host_description": description + "x"}).status_code == 400
    assert client.put("/api/settings", json={"host_description": ""}).status_code == 200
    assert client.get("/api/hosts").json()["local"]["description"] == ""


def test_settings_document_separates_scanner_intent_from_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SCANNERS", "listen")
    client = TestClient(app)

    body = client.get("/api/settings").json()
    assert body["values"]["local_scanners"] == ["listen"]
    states = {row["id"]: row for row in body["local_scanning"]["scanners"]}
    assert states["listen"]["enabled"] is True
    assert states["docker"] == {"id": "docker", "enabled": False, "state": "disabled"}
    assert states["compose"] == {"id": "compose", "enabled": False, "state": "disabled"}


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
