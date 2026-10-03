from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.ui_extensions import register_ui_link


@pytest.fixture(autouse=True)
def _navigation(monkeypatch, tmp_path):
    monkeypatch.setattr(app.state, "ui_links", (), raising=False)
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")


def test_core_has_no_extra_links_until_an_application_registers_them():
    client = TestClient(app)
    assert client.get("/api/meta").json()["ui_links"] == []
    labels = {"zh-CN": "工具"}
    register_ui_link(app, key="tools", path="/tools/", label="Tools", labels=labels)
    labels["zh-CN"] = "Changed by caller"
    assert client.get("/api/meta").json()["ui_links"] == [
        {"key": "tools", "path": "/tools/", "label": "Tools", "labels": {"zh-CN": "工具"}}
    ]
    with pytest.raises(ValueError):
        register_ui_link(app, key="tools", path="/other/", label="Other")


@pytest.mark.parametrize("path", [
    "https://example.invalid/", "//example.invalid/", "/%2fexample.invalid",
    "/tools/?secret=fixture", "/tools/#section", "/../outside", "/\\outside",
])
def test_registration_rejects_nonlocal_or_ambiguous_paths(path):
    with pytest.raises(ValueError):
        register_ui_link(app, key="tools", path=path, label="Tools")
    assert not app.state.ui_links


def test_workspace_registration_copies_a_versioned_local_interface():
    workspace = {"api": 1, "entry": "/tools/assets/main.js",
                 "stylesheet": "/tools/assets/main.css", "port_action": True}
    register_ui_link(app, key="tools", path="/tools/", label="Tools", workspace=workspace)
    workspace["entry"] = "/other/main.js"
    assert app.state.ui_links[0]["workspace"]["entry"] == "/tools/assets/main.js"


def test_settings_registration_copies_a_local_interface():
    settings = {"api": 1, "entry": "/tools/assets/settings.js", "stylesheet": "/tools/assets/settings.css"}
    register_ui_link(app, key="tools", path="/tools/", label="Tools", settings=settings)
    settings["entry"] = "/other/settings.js"
    assert app.state.ui_links[0]["settings"] == {
        "api": 1, "entry": "/tools/assets/settings.js", "stylesheet": "/tools/assets/settings.css",
    }


@pytest.mark.parametrize("field,value", [
    ("entry", "https://example.invalid/main.js"), ("entry", "/tools/../main.js"),
    ("entry", "/other/main.js"), ("entry", "/tools/main.js?token=fixture"),
    ("stylesheet", "/tools/main.js"), ("api", True), ("api", 2), ("port_action", "true"),
    ("revision", "unverified"),
])
def test_workspace_rejects_external_assets_and_unknown_interfaces(field, value):
    workspace = {"api": 1, "entry": "/tools/main.js", "stylesheet": "/tools/main.css", "port_action": True}
    workspace[field] = value
    with pytest.raises(ValueError):
        register_ui_link(app, key="tools", path="/tools/", label="Tools", workspace=workspace)
    assert not app.state.ui_links


@pytest.mark.parametrize("field,value", [
    ("entry", "https://example.invalid/settings.js"), ("entry", "/tools/../settings.js"),
    ("entry", "/other/settings.js"), ("entry", "/tools/settings.js?token=fixture"),
    ("stylesheet", "/tools/settings.js"), ("api", True), ("api", 2), ("revision", "unverified"),
])
def test_settings_rejects_external_assets_and_unknown_interfaces(field, value):
    settings = {"api": 1, "entry": "/tools/settings.js", "stylesheet": "/tools/settings.css"}
    settings[field] = value
    with pytest.raises(ValueError):
        register_ui_link(app, key="tools", path="/tools/", label="Tools", settings=settings)
    assert not app.state.ui_links
