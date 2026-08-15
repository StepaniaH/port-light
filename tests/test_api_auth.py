from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend import port_store


def test_health_unauthenticated(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
    assert res.headers.get("referrer-policy") == "no-referrer"
    assert "camera=()" in (res.headers.get("permissions-policy") or "")
    assert res.headers.get("cache-control") == "no-store"
    csp = res.headers.get("content-security-policy") or ""
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert res.headers.get("cross-origin-opener-policy") == "same-origin"
    assert res.headers.get("cross-origin-resource-policy") == "same-origin"
    body = res.json()
    assert body["status"] == "ok"
    assert body["auth_required"] is True
    assert "listen_source" in body["scanners"]


def test_root_requires_basic_auth(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 401
    res = client.get("/", auth=("admin", "s3cret"))
    assert res.status_code == 200


def test_include_hidden_gated(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock-me")
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    port_store.add_manual_port(8096, "jellyfin")
    port_store.add_hidden_port(8096)
    client = TestClient(app)

    locked = client.get("/api/ports", params={"include_hidden": True})
    assert locked.status_code == 200
    assert locked.json()["summary"]["hidden_locked"] is True
    assert 8096 not in [p["port"] for p in locked.json()["ports"]]

    opened = client.get(
        "/api/ports",
        params={"include_hidden": True},
        headers={"X-Hidden-Unlock": "unlock-me"},
    )
    assert opened.json()["summary"]["hidden_locked"] is False
    hidden = [p for p in opened.json()["ports"] if p["port"] == 8096]
    assert len(hidden) == 1
    assert hidden[0]["is_hidden"] is True


def test_static_assets_are_cacheable(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    client = TestClient(app)
    css = client.get("/static/style.css")
    assert css.status_code == 200
    cache = css.headers.get("cache-control", "")
    assert "max-age=31536000" in cache
    assert "immutable" in cache
    html = client.get("/")
    assert html.status_code == 200
    assert html.headers.get("cache-control") == "no-cache"


def test_ports_etag_not_modified(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    client = TestClient(app)
    first = client.get("/api/ports")
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag
    again = client.get("/api/ports", headers={"If-None-Match": etag})
    assert again.status_code == 304
    client.post("/api/manual-ports", json={"port": 4242, "label": "lab"})
    changed = client.get("/api/ports", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.json()["ports"]


def test_manual_port_roundtrip_and_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    client = TestClient(app)
    created = client.post("/api/manual-ports", json={"port": 4242, "label": "lab"})
    assert created.status_code == 200
    patched = client.patch("/api/manual-ports/4242", json={"label": "bench"})
    assert patched.status_code == 200
    assert patched.json()["entry"]["label"] == "bench"
    row = client.get("/api/ports/4242")
    assert row.status_code == 200
    assert row.json()["manual_label"] == "bench"
    known = client.get("/api/known-ports/22")
    assert known.status_code == 200
    assert known.json()["name"] == "SSH"
    free = client.get("/api/ports/1")
    assert free.status_code == 200
    assert free.json()["status"] == "free"
    assert free.json()["port"] == 1


def test_hidden_port_lookup_is_not_free(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("HIDDEN_UNLOCK_PASSWORD", raising=False)
    port_store.add_manual_port(8096, "jellyfin")
    port_store.add_hidden_port(8096)
    client = TestClient(app)
    omitted = client.get("/api/ports/8096")
    assert omitted.status_code == 404
    shown = client.get("/api/ports/8096", params={"include_hidden": True})
    assert shown.status_code == 200
    assert shown.json()["status"] == "configured"
    assert shown.json()["is_hidden"] is True
