from __future__ import annotations

import json

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


def test_health_reports_recent_degradations(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import degradations

    degradations.reset()
    try:
        degradations.report("docker", "daemon", "unreachable")
        client = TestClient(app)
        res = client.get("/api/health")
        assert res.status_code == 200
        events = res.json()["degradations"]
        assert len(events) == 1
        assert events[0]["source"] == "docker"
        assert events[0]["scope"] == "daemon"
    finally:
        degradations.reset()


def test_health_redacts_degradation_scope_from_anonymous_callers(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    from backend import degradations

    degradations.reset()
    try:
        degradations.report("compose", "private/project/compose.yml", "invalid compose file")
        client = TestClient(app)
        anonymous = client.get("/api/health")
        assert "scope" not in anonymous.json()["degradations"][0]
        authenticated = client.get("/api/health", auth=("admin", "s3cret"))
        assert authenticated.json()["degradations"][0]["scope"] == "private/project/compose.yml"
    finally:
        degradations.reset()


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
    weak = client.get("/api/ports", headers={"If-None-Match": "W/" + etag})
    assert weak.status_code == 304
    listed = client.get("/api/ports", headers={"If-None-Match": etag + ', "nope"'})
    assert listed.status_code == 304
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


def test_hidden_free_lookup_with_include(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("HIDDEN_UNLOCK_PASSWORD", raising=False)
    port_store.add_hidden_port(42424)
    client = TestClient(app)
    omitted = client.get("/api/ports/42424")
    assert omitted.status_code == 404
    shown = client.get("/api/ports/42424", params={"include_hidden": True})
    assert shown.status_code == 200
    assert shown.json()["status"] == "free"
    assert shown.json()["is_hidden"] is True
    listed = client.get("/api/ports", params={"include_hidden": True})
    assert 42424 in [p["port"] for p in listed.json()["ports"]]
    occ = listed.json()["summary"]["hidden_occupancy"]
    assert {"port": 42424, "status": "free"} in occ


def test_occupancy_scan_snapshot_reused_until_store_write(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._occ.reset()
    n = {"c": 0}

    def fake_containers():
        n["c"] += 1
        return []

    monkeypatch.setattr(main, "scan_containers", fake_containers)
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    client = TestClient(app)
    assert client.get("/api/ports").status_code == 200
    assert client.get("/api/ports/2100").status_code == 200
    assert n["c"] == 1
    client.post("/api/manual-ports", json={"port": 4242, "label": "lab"})
    assert client.get("/api/ports").status_code == 200
    assert n["c"] == 2


def test_ports_etag_reuses_classified_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._occ.reset()
    n = {"k": 0}
    real = main.classify

    def wrapped(*args, **kwargs):
        n["k"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    monkeypatch.setattr(main, "classify", wrapped)
    client = TestClient(app)
    first = client.get("/api/ports")
    assert first.status_code == 200
    first_classifications = n["k"]
    etag = first.headers.get("etag")
    again = client.get("/api/ports", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert n["k"] == first_classifications
    row = client.get("/api/ports/2100")
    assert row.status_code == 200
    assert n["k"] == first_classifications


def test_slow_rebuild_serves_previous_snapshot(monkeypatch, tmp_path):
    import threading
    import time

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan
    from backend.port_scanner import ListeningPort

    main._occ.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    monkeypatch.setattr(main._occ, "stale_after", 0.2)

    state = {"slow": False}
    release = threading.Event()

    def fake_listen(**_kw):
        if state["slow"]:
            release.wait(timeout=5)
        return [ListeningPort(port=2200, protocol="tcp", ip="0.0.0.0")]

    monkeypatch.setattr(main, "scan_listening_ports", fake_listen)

    client = TestClient(app)
    first = client.get("/api/ports")
    assert first.status_code == 200
    assert any(row["port"] == 2200 for row in first.json()["ports"])
    assert "stale" not in first.json()["summary"]

    state["slow"] = True
    main._occ.snapshot()["at"] -= main._occ.ttl + 0.05
    builder_started = threading.Event()
    results = []

    def builder():
        builder_started.set()
        results.append(client.get("/api/ports"))

    tb = threading.Thread(target=builder)
    tb.start()
    assert builder_started.wait(timeout=2)
    time.sleep(0.1)

    waiter_started = threading.Event()
    def waiter():
        waiter_started.set()
        results.append(client.get("/api/ports"))

    tw = threading.Thread(target=waiter)
    tw.start()
    assert waiter_started.wait(timeout=2)

    stale_body = None
    # Only the blocked-out waiter is served inside this window; the builder
    # stays parked in the fake scanner until `release` below.
    for _ in range(40):
        time.sleep(0.05)
        if len(results) >= 1 and results[-1].json()["summary"].get("stale") is True:
            stale_body = results[-1].json()
            break
    assert stale_body is not None, "waiter was not served within the stale deadline"
    assert any(row["port"] == 2200 for row in stale_body["ports"])

    release.set()
    tb.join(timeout=5)
    tw.join(timeout=5)
    fresh = client.get("/api/ports")
    assert fresh.status_code == 200
    assert "stale" not in fresh.json()["summary"]
    assert any(row["port"] == 2200 for row in fresh.json()["ports"])


def test_concurrent_polls_share_one_scan(monkeypatch, tmp_path):
    import threading
    import time

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._occ.reset()
    n = {"c": 0}
    started = threading.Event()
    release = threading.Event()

    def fake_containers():
        n["c"] += 1
        started.set()
        release.wait(timeout=2)
        time.sleep(0.05)
        return []

    monkeypatch.setattr(main, "scan_containers", fake_containers)
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    client = TestClient(app)
    results = []

    def worker():
        results.append(client.get("/api/ports").status_code)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    assert started.wait(timeout=2)
    t2.start()
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert results == [200, 200]
    assert n["c"] == 1


def test_hidden_manual_writes_and_lists_require_unlock(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock")
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import agent_events, port_store

    agent_events.reset()
    port_store.add_manual_port(45000, "private", ttl=60)
    port_store.add_hidden_port(45000)
    try:
        with TestClient(app) as client:
            assert client.get("/api/manual-ports").json()["manual_ports"] == []
            assert client.get("/api/meta").json()["automation"]["agent_events"]["lease_rows"] == []
            assert client.patch("/api/manual-ports/45000", json={"label": "x"}).status_code == 403
            assert client.delete("/api/manual-ports/45000").status_code == 403
            assert client.post("/api/manual-ports", json={"port": 45000}).status_code == 403
            assert client.post("/api/hidden/45001").status_code == 403
            response = client.delete(
                "/api/hidden/45000", headers={"X-Hidden-Unlock": "unlock"})
            assert response.status_code == 200
    finally:
        agent_events.reset()


def test_store_hand_edit_invalidates_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import main
    from backend.compose_scanner import ComposeScan

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan())
    main._occ.reset()
    with TestClient(app) as client:
        assert client.get("/api/ports/45000").json()["status"] == "free"
        (tmp_path / "port_light.json").write_text(json.dumps({
            "manual_ports": [{"port": 45000}],
        }))
        assert client.get("/api/ports/45000").json()["status"] == "configured"
