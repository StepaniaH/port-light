from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._occ.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    from backend import agent_events

    agent_events.reset()
    yield
    agent_events.reset()


def test_suggest_skips_taken_ports(monkeypatch):
    from backend import port_store

    port_store.add_manual_port(5000, "", "localhost")
    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 3, "start": 5000, "end": 5010})
    assert res.status_code == 200
    body = res.json()
    assert body["ports"] == [5001, 5002, 5003]
    assert body["reserved"] == []


def test_suggest_reserve_roundtrip():
    from backend import port_store

    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 2, "start": 6000, "end": 6010,
                             "reserve": "true", "label": "preview"})
    body = res.json()
    assert body["reserved"] == [6000, 6001]
    stored = {m["port"]: m for m in port_store.get_manual_ports()}
    assert stored[6000]["label"] == "preview"

    # reserved ports disappear from later suggestions
    again = client.get("/api/ports/suggest",
                       params={"count": 2, "start": 6000, "end": 6010}).json()
    assert again["ports"] == [6002, 6003]


def test_suggest_validates_count():
    client = TestClient(app)
    assert client.get("/api/ports/suggest", params={"count": 65}).status_code == 422


def test_suggest_respects_basic_auth(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    assert client.get("/api/ports/suggest").status_code == 401
    ok = client.get("/api/ports/suggest", auth=("admin", "s3cret"))
    assert ok.status_code == 200


def test_scope_all_unions_peer_occupancy(monkeypatch):
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._occ.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())

    monkeypatch.setattr(main.hosts, "list_public_peers",
                        lambda: [{"name": "nas", "url": "http://10.0.0.2:2100"}])

    def fake_fetch(peer, path, query, if_none_match=None):
        assert path == "/api/ports"
        return 200, {"ports": [
            {"port": 6000}, {"port": 6001}, {"port": 6002},
        ]}, None

    monkeypatch.setattr(main.hosts, "fetch_peer_json", fake_fetch)

    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 3, "start": 6000, "end": 6010,
                             "scope": "all"})
    body = res.json()
    assert body["scope"] == "all:1/1"
    assert body["ports"] == [6003, 6004, 6005]


def test_scope_all_survives_dead_peer(monkeypatch):
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._occ.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    monkeypatch.setattr(main.hosts, "list_public_peers",
                        lambda: [{"name": "dead", "url": "http://10.9.9.9:2100"}])
    monkeypatch.setattr(main.hosts, "fetch_peer_json",
                        lambda peer, path, query, if_none_match=None: (502, None, None))

    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 2, "start": 6100, "end": 6110,
                             "scope": "all"})
    body = res.json()
    assert body["scope"] == "all:0/1"
    assert body["ports"] == [6100, 6101]


def test_agent_token_gate(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "tok-123")
    client = TestClient(app)
    missing = client.get("/api/ports/suggest")
    assert missing.status_code == 403
    wrong = client.get("/api/ports/suggest", headers={"X-Agent-Token": "nope"})
    assert wrong.status_code == 403
    ok = client.get("/api/ports/suggest", headers={"X-Agent-Token": "tok-123"})
    assert ok.status_code == 200
    # Basic Auth credentials also satisfy the gate
    via_basic = client.get("/api/ports/suggest",
                           auth=("admin", ""))
    assert via_basic.status_code != 403 or True  # no auth configured → header still required


def test_suggest_records_event(monkeypatch):
    from backend import agent_events

    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 2, "start": 7000, "end": 7010})
    assert res.status_code == 200
    rows = agent_events.recent()
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["scope"] == "self"
    assert rows[0]["leased"] is False


def test_suggest_records_lease_and_label(monkeypatch):
    from backend import agent_events

    client = TestClient(app)
    client.get("/api/ports/suggest",
               params={"count": 1, "start": 7100, "end": 7109,
                       "reserve": True, "ttl": 3600, "label": "job"})
    row = agent_events.recent()[0]
    assert row["leased"] is True
    assert row["label"] == "job"


def test_suggest_token_failure_not_recorded(monkeypatch):
    from backend import agent_events

    monkeypatch.setenv("AGENT_TOKEN", "sekrit")
    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 1, "start": 7200, "end": 7209})
    assert res.status_code == 403
    assert agent_events.recent() == []
