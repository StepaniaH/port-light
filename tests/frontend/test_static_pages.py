from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_index_loads_module_entry(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert 'src="/static/boot.js?v=' in res.text
    assert "<script>" not in res.text
    assert 'type="module" src="/static/js/app.js?v=' in res.text
    assert res.headers.get("cache-control") == "no-cache"
    csp = res.headers.get("content-security-policy") or ""
    assert "script-src 'self';" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp


def test_module_chunks_are_served_with_revalidation(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    client = TestClient(app)
    for path in (
        "/static/js/app.js",
        "/static/js/text.js",
        "/static/js/kinds.js",
        "/static/js/a11y.js",
    ):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-cache", path


def test_classic_static_assets_stay_immutable(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    client = TestClient(app)
    for path in ("/static/i18n.js", "/static/boot.js"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "immutable" in (res.headers.get("cache-control") or ""), path
