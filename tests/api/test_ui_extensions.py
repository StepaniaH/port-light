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
