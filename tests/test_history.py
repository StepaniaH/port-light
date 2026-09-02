from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _rows(*triples):
    rows = []
    for port, status, holders in triples:
        rows.append({"port": port, "status": status,
                     "containers": [{"name": n} for n in holders],
                     "manual_label": None})
    return rows


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import history

    history.reset()
    yield
    history.reset()


def test_records_transitions_and_queries(monkeypatch):
    from backend import history

    monkeypatch.setattr(history, "enabled", lambda: True)
    history.record(_rows((8080, "used", ("app",))))          # prime
    history.record(_rows((8080, "used", ("app",)),
                         (9090, "configured", ())))           # appears
    history.record(_rows((8080, "used", ("app",)),
                         (9090, "free", ())))                 # released
    events = history.query(9090, hours=24)
    assert [e["state"] for e in events] == ["configured", "free"]
    used = history.query(8080)
    assert used == []  # no transition for 8080


def test_disabled_by_retention_zero(monkeypatch):
    from backend import history

    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    assert history.enabled() is False
    assert history.record(_rows((8080, "used", ()))) == 0
    assert history.query(8080) == []


def test_history_endpoint_404_when_disabled(monkeypatch):
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    client = TestClient(app)
    assert client.get("/api/ports/8080/history").status_code == 404


def test_history_endpoint_returns_events(monkeypatch, tmp_path):
    from backend import history

    history.record(_rows((7700, "configured", ())))  # prime
    history.record(_rows((7700, "used", ("svc",))))
    client = TestClient(app)
    res = client.get("/api/ports/7700/history")
    assert res.status_code == 200
    body = res.json()
    assert body["port"] == 7700
    assert len(body["events"]) == 1
    assert body["events"][0]["holders"] == ["svc"]


def test_records_disappearance_and_reappearance():
    from backend import history

    row = {"port": 45000, "status": "used"}
    history.record([row])
    history.record([])
    history.record([row])
    assert [event["state"] for event in history.query(45000)] == ["free", "used"]


def test_hidden_history_is_withheld(monkeypatch):
    from backend import history, port_store

    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock")
    port_store.add_hidden_port(45000)
    history.record([])
    history.record([{"port": 45000, "status": "used", "manual_label": "private"}])
    with TestClient(app) as client:
        response = client.get("/api/ports/45000/history")
    assert response.status_code == 404


def test_history_uses_complete_snapshots(monkeypatch):
    from backend import history, main, port_store
    from backend.compose_scanner import ComposeScan

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan())
    main._occ.reset()
    port_store.add_manual_port(45000, "hidden service")
    port_store.add_hidden_port(45000)
    with TestClient(app) as client:
        client.get("/api/ports?include_hidden=true")
        client.get("/api/ports?range_start=1&range_end=10")
        client.get("/api/ports?include_hidden=true")
        assert history.query(45000) == []
        port_store.remove_manual_port(45000)
        client.get("/api/ports")
        port_store.add_manual_port(45000, "hidden service")
        client.get("/api/ports")
        assert [event["state"] for event in history.query(45000)] == ["free", "configured"]


def test_peer_history_routes_to_peer(monkeypatch):
    from backend import main

    monkeypatch.setattr(main.hosts, "get_peer", lambda host: {"name": host})

    def fetch(peer, path, query, etag):
        assert peer["name"] == "abcdef123456"
        assert path == "/api/ports/45000/history"
        assert query == {"hours": "12"}
        return 200, {"port": 45000, "events": [{"state": "used"}]}, None

    monkeypatch.setattr(main.hosts, "fetch_peer_json", fetch)
    with TestClient(app) as client:
        response = client.get("/api/hosts/abcdef123456/ports/45000/history?hours=12")
    assert response.status_code == 200
    assert response.json()["events"] == [{"state": "used"}]
