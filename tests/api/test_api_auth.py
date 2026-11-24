from __future__ import annotations

import json
import base64

import pytest

from fastapi.testclient import TestClient

from backend.main import app
from backend import port_store


@pytest.mark.parametrize("credentials", [
    {"AUTH_USER": "admin"}, {"AUTH_PASSWORD": "secret"},
    {"AUTH_USER": "admin", "AUTH_PASSWORD": ""},
    {"AUTH_USER": "", "AUTH_PASSWORD": "secret"},
    {"AUTH_USER": "", "AUTH_PASSWORD": ""},
])
def test_incomplete_auth_fails_closed(monkeypatch, credentials):
    for key in ("AUTH_USER", "AUTH_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    client = TestClient(app)
    for method, path, body in (
        ("GET", "/", None), ("GET", "/api/meta", None),
        ("GET", "/openapi.json", None),
        ("POST", "/api/manual-ports", {"port": 45000}),
    ):
        response = client.request(method, path, json=body)
        assert response.status_code == 503
        assert "AUTH_USER" in response.text
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["auth_required"] is True
    assert health.json()["status"] == "degraded"


@pytest.mark.parametrize("user,password,given_user,given_password,status", [
    ("admin", "密码测试", "admin", "密码测试", 200),
    ("管理员", "密码测试", "管理员", "密码测试", 200),
    ("admin", "secret", "管理用户甲", "secret", 401),
    ("admin", "secret", "admin", "密码输入错误", 401),
])
def test_unicode_basic_auth(monkeypatch, user, password, given_user, given_password, status):
    monkeypatch.setenv("AUTH_USER", user)
    monkeypatch.setenv("AUTH_PASSWORD", password)
    token = base64.b64encode(f"{given_user}:{given_password}".encode()).decode()
    response = TestClient(app, raise_server_exceptions=False).get(
        "/", headers={"Authorization": "Basic " + token})
    assert response.status_code == status
    if status == 401:
        assert 'charset="UTF-8"' in response.headers["www-authenticate"]


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
    assert "script-src 'self';" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "frame-ancestors 'none'" in csp
    assert res.headers.get("cross-origin-opener-policy") == "same-origin"
    assert res.headers.get("cross-origin-resource-policy") == "same-origin"
    body = res.json()
    assert body["status"] in ("ok", "degraded")
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


def test_manual_port_roundtrip_and_lookup(monkeypatch, tmp_path, empty_scan):
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


def test_hidden_port_lookup_is_not_free(monkeypatch, tmp_path, empty_scan):
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


def test_hidden_free_lookup_with_include(monkeypatch, tmp_path, empty_scan):
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


def test_store_write_reclassifies_latest_snapshot_without_rescan(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._monitor.reset()
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
    updated = client.get("/api/ports")
    assert updated.status_code == 200
    assert any(row["port"] == 4242 for row in updated.json()["ports"])
    assert n["c"] == 1


def test_ports_etag_reuses_classified_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._monitor.reset()
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
    detail_classifications = n["k"]
    assert client.get("/api/ports/2100").status_code == 200
    assert n["k"] == detail_classifications


def test_http_read_does_not_wait_for_background_scan(monkeypatch, tmp_path):
    import threading
    import time

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan
    from backend.port_scanner import ListeningPort

    main._monitor.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    state = {"slow": False}
    release = threading.Event()
    started = threading.Event()

    def fake_listen(**_kw):
        if state["slow"]:
            started.set()
            release.wait(timeout=5)
        port = 2300 if state["slow"] else 2200
        return [ListeningPort(port=port, protocol="tcp", ip="0.0.0.0")]

    monkeypatch.setattr(main, "scan_listening_ports", fake_listen)

    client = TestClient(app)
    first = client.get("/api/ports")
    assert first.status_code == 200
    assert any(row["port"] == 2200 for row in first.json()["ports"])
    assert "stale" not in first.json()["summary"]

    state["slow"] = True
    refresh = threading.Thread(target=main._monitor.refresh)
    refresh.start()
    assert started.wait(timeout=2)
    before = time.monotonic()
    during = client.get("/api/ports")
    assert time.monotonic() - before < 0.5
    assert any(row["port"] == 2200 for row in during.json()["ports"])
    assert not any(row["port"] == 2300 for row in during.json()["ports"])

    release.set()
    refresh.join(timeout=5)
    fresh = client.get("/api/ports")
    assert fresh.status_code == 200
    assert any(row["port"] == 2300 for row in fresh.json()["ports"])
    assert not any(row["port"] == 2200 for row in fresh.json()["ports"])


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


def test_monitor_picks_up_store_hand_edit(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import main
    from backend.compose_scanner import ComposeScan

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan())
    main._monitor.reset()
    with TestClient(app) as client:
        assert client.get("/api/ports/45000").json()["status"] == "free"
        (tmp_path / "port_light.json").write_text(json.dumps({
            "manual_ports": [{"port": 45000}],
        }))
        main._monitor.refresh()
        assert client.get("/api/ports/45000").json()["status"] == "configured"


@pytest.mark.parametrize('auth', [False, True])
def test_health_hides_scopes_until_configured_gate_is_unlocked(monkeypatch, auth):
    from backend import degradations
    for name in ('AUTH_USER', 'AUTH_PASSWORD', 'HIDDEN_UNLOCK_PASSWORD'):
        monkeypatch.delenv(name, raising=False)
    if auth:
        monkeypatch.setenv('AUTH_USER', 'preview')
        monkeypatch.setenv('AUTH_PASSWORD', '   ')
        headers = {'Authorization': 'Basic ' + base64.b64encode(b'preview:   ').decode()}
    else:
        monkeypatch.setenv('HIDDEN_UNLOCK_PASSWORD', 'secret')
        headers = {}
    degradations.reset()
    try:
        degradations.report('compose', 'private/project/compose.yaml', 'port expansion limit')
        client = TestClient(app)
        body = client.get('/api/health', headers=headers).json()
        assert all('scope' not in row for row in body['degradations'])
        if not auth:
            opened = client.get('/api/health', headers={'X-Hidden-Unlock': 'secret'}).json()
            assert opened['degradations'][0]['scope'] == 'private/project/compose.yaml'
    finally:
        degradations.reset()


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "https://attacker.example.invalid"},
        {"Origin": "null"},
    ],
)
def test_mutating_api_endpoints_reject_cross_origin_requests(monkeypatch, tmp_path, headers):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    for name in ("AUTH_USER", "AUTH_PASSWORD", "HIDDEN_UNLOCK_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(app)
    for method, path, body in (
        ("POST", "/api/manual-ports", {"port": 42000, "label": "csrf"}),
        ("PUT", "/api/hosts", {"peers": []}),
        ("PATCH", "/api/manual-ports/42000", {"label": "csrf"}),
        ("DELETE", "/api/manual-ports/42000", None),
    ):
        res = client.request(method, path, json=body, headers=headers)
        assert res.status_code == 403
        assert res.json() == {"detail": "cross-origin requests are not allowed"}
    assert port_store.get_manual_ports() == []
    assert client.get("/api/ports", headers=headers).status_code == 200


def test_mutating_api_allows_same_origin_and_headless_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    for name in ("AUTH_USER", "AUTH_PASSWORD", "HIDDEN_UNLOCK_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(app)
    browser_ok = client.post(
        "/api/manual-ports",
        json={"port": 42010, "label": "ui"},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert browser_ok.status_code == 200
    cli_ok = client.post("/api/manual-ports", json={"port": 42011, "label": "cli"})
    assert cli_ok.status_code == 200


def test_two_stage_packed_occupancy_matches_direct_classification_without_reclassifying(
    monkeypatch, tmp_path
):
    import backend.main as main
    from backend.compose_scanner import ComposeScan
    from backend.port_scanner import ListeningPort

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock-secret")
    for name in ("AUTH_USER", "AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    port_store.add_manual_port(3000, "web")
    port_store.add_manual_port(9000, "hidden-cfg")
    port_store.add_hidden_port(8096)
    port_store.add_hidden_port(9000)
    port_store.add_hidden_port(42424)
    port_store.set_port_rules([{"name": "apps", "start": 2000, "end": 4000}])

    calls = 0
    real_classify = main.classify

    def counted_classify(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [
        ListeningPort(port=80, ip="0.0.0.0", protocol="tcp"),
        ListeningPort(port=8096, ip="127.0.0.1", protocol="tcp"),
    ])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan(
        diagnostics=[{"code": "port_range", "file": "compose.yaml", "range": "1-2", "limit": 4096}],
    ))
    monkeypatch.setattr(main, "classify", counted_classify)
    main._monitor.reset()

    client = TestClient(app)
    r1 = client.get("/api/ports", params={"range_start": 1000, "range_end": 9500})
    assert r1.status_code == 200
    assert calls == 1

    r2 = client.get(
        "/api/ports",
        params={"range_start": 1, "range_end": 50000, "include_hidden": True},
        headers={"X-Hidden-Unlock": "unlock-secret"},
    )
    assert r2.status_code == 200
    assert calls == 1

    r3 = client.get(
        "/api/ports",
        params={"range_start": 1, "range_end": 50000, "include_hidden": False},
        headers={"X-Hidden-Unlock": "unlock-secret"},
    )
    assert r3.status_code == 200
    assert calls == 1

    values = main._values()
    snap = main._monitor.latest(values)
    for response, start, end, show_hidden, hidden_locked in (
        (r1, 1000, 9500, False, True),
        (r2, 1, 50000, True, False),
        (r3, 1, 50000, False, False),
    ):
        expected = real_classify(
            snap["listening"],
            snap["containers"],
            snap["compose_scan"].ports,
            snap["user_state"][0],
            snap["user_state"][1],
            start,
            end,
            show_hidden,
            hidden_locked=hidden_locked,
            options=values,
        )
        main.port_rules.annotate(expected["ports"], port_store.get_port_rules())
        if not hidden_locked:
            expected["summary"]["compose_diagnostics"] = snap["compose_scan"].diagnostics
        expected["summary"].update({
            "scan_complete": True,
            "sources": snap["sources"],
            "compose_truncated": False,
            "compose_incomplete": False,
            "compose_files": 0,
        })
        assert response.json() == expected


def test_hidden_unlock_eliminates_existence_side_channels(monkeypatch, tmp_path, empty_scan):
    import secrets
    import backend.main as main
    from backend import history
    from backend.port_scanner import ListeningPort

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock-me")
    monkeypatch.setenv("METRICS_ENABLED", "1")
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    for name in ("AUTH_USER", "AUTH_PASSWORD", "AGENT_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [
        ListeningPort(port=8080, protocol="tcp", ip="0.0.0.0", process_name="nginx", pid=10),
        ListeningPort(port=8096, protocol="tcp", ip="127.0.0.1", process_name="secret-svc", pid=20),
    ])
    port_store.add_manual_port(8096, "secret-label")
    port_store.add_hidden_port(8096)
    history.reset()
    main._monitor.reset()

    with TestClient(app) as client:
        # 1. /api/ports summary hides per-range hidden count and does not subtract hidden from free.
        hidden_range = client.get("/api/ports", params={"range_start": 8096, "range_end": 8096})
        free_range = client.get("/api/ports", params={"range_start": 8097, "range_end": 8097})
        assert hidden_range.status_code == 200 and free_range.status_code == 200
        assert [row["port"] for row in hidden_range.json()["ports"]] == [8080]
        assert [row["port"] for row in free_range.json()["ports"]] == [8080]
        assert hidden_range.json()["summary"]["hidden"] == 0
        assert free_range.json()["summary"]["hidden"] == 0
        assert hidden_range.json()["summary"]["free"] == 1
        assert free_range.json()["summary"]["free"] == 1
        assert hidden_range.json()["summary"]["hidden_locked"] is True

        # 2. /api/ports/{port}, /history, and /api/observations/ports/{port} are indistinguishable
        # for hidden vs free ports, while visible occupied ports remain readable.
        assert client.get("/api/ports/8080").status_code == 200
        assert client.get("/api/ports/8096").status_code == 404
        assert client.get("/api/ports/8097").status_code == 404
        assert client.get("/api/ports/8080/history").status_code == 200
        assert client.get("/api/ports/8096/history").status_code == 404
        assert client.get("/api/ports/8097/history").status_code == 404
        assert client.get("/api/observations/ports/8080").status_code == 200
        assert client.get("/api/observations/ports/8096").status_code == 404
        assert client.get("/api/observations/ports/8097").status_code == 404

        # 3. /api/observations/batch returns identical coverage and empty ports for hidden vs free.
        b_hidden = client.post(
            "/api/observations/batch",
            json={"selection": {"kind": "ports", "ports": [8096]}},
        ).json()
        b_free = client.post(
            "/api/observations/batch",
            json={"selection": {"kind": "ports", "ports": [8097]}},
        ).json()
        assert b_hidden["ports"] == [] == b_free["ports"]
        assert b_hidden["events"] == [] == b_free["events"]
        assert b_hidden["coverage"] == b_free["coverage"]
        assert b_hidden["coverage"]["complete"] is False
        assert "hidden_withheld" in b_hidden["coverage"]["limitations"]

        # 4. Free-runs, batch manual writes, single manual writes, and unauthenticated allocation
        # all return uniform 403 while locked (no 403-vs-404 / 403-vs-200 oracle).
        assert client.get("/api/free-runs", params={"count": 1, "start": 8096, "end": 8097}).status_code == 403
        assert client.post("/api/manual-ports/batch", json={"start": 8097, "end": 8097}).status_code == 403
        for target_port in (8096, 8097):
            assert client.post("/api/manual-ports", json={"port": target_port, "label": "x"}).status_code == 403
            assert client.patch(f"/api/manual-ports/{target_port}", json={"label": "x"}).status_code == 403
            assert client.delete(f"/api/manual-ports/{target_port}").status_code == 403
        assert client.get("/api/ports/suggest", params={"start": 8096, "end": 8096}).status_code == 403
        assert client.post(
            "/api/reservations",
            json={"start": 8097, "end": 8097},
            headers={"Idempotency-Key": secrets.token_urlsafe(36)},
        ).status_code == 403

        # 5. /api/metrics omits hidden count while locked, restores it when unlocked.
        locked_metrics = client.get("/api/metrics").text
        assert "port_light_hidden 0\n" in locked_metrics
        unlocked_metrics = client.get("/api/metrics", headers={"X-Hidden-Unlock": "unlock-me"}).text
        assert "port_light_hidden 1\n" in unlocked_metrics

        # 6. Unlocking restores full access to free-runs, batch writes, single-port lookup, and suggest.
        hdr = {"X-Hidden-Unlock": "unlock-me"}
        runs = client.get("/api/free-runs", params={"count": 1, "start": 8096, "end": 8097}, headers=hdr)
        assert runs.status_code == 200
        assert runs.json()["runs"] == [{"start": 8097, "end": 8097, "size": 1}]
        assert client.get("/api/ports/8097", headers=hdr).json()["status"] == "free"
        assert client.get("/api/ports/8096", params={"include_hidden": True}, headers=hdr).status_code == 200
        assert client.post(
            "/api/manual-ports/batch",
            json={"start": 8097, "end": 8097, "label": "ok"},
            headers=hdr,
        ).status_code == 200

        # 7. When AGENT_TOKEN is configured, authorized agents can still call /api/ports/suggest.
        monkeypatch.setenv("AGENT_TOKEN", "agent-secret")
        assert client.get("/api/ports/suggest", params={"start": 8098, "end": 8098}).status_code == 403
        agent_ok = client.get(
            "/api/ports/suggest",
            params={"start": 8098, "end": 8098},
            headers={"X-Agent-Token": "agent-secret"},
        )
        assert agent_ok.status_code == 200
        assert agent_ok.json()["ports"] == [8098]
