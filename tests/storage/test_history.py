from __future__ import annotations

import secrets
import time

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


def test_data_directory_change_restarts_history_baseline(monkeypatch, tmp_path):
    from backend import history

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(first))
    history.record(_rows((4320, "used", ())))

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(second))
    history.record([])
    assert history.query(4320) == []
    history.record(_rows((4320, "used", ())))
    assert [event["state"] for event in history.query(4320)] == ["used"]


def test_disabled_by_retention_zero(monkeypatch):
    from backend import history

    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    assert history.enabled() is False
    assert history.record(_rows((8080, "used", ()))) == 0
    assert history.query(8080) == []


def _observation_event(*, capture: int, port: int, hidden: bool = False) -> dict:
    observation_id = f"obs-0123456789ab-{capture}"
    return {
        "event_id": f"{observation_id}:state_changed:{port}",
        "observation_id": observation_id,
        "observed_at": int(time.time()),
        "port": port,
        "kind": "state_changed",
        "before": {
            "status": "configured",
            "protocols": ["tcp"],
            "bind_scope": "localhost",
            "compose_conflict": False,
        },
        "after": {
            "status": "used",
            "protocols": ["tcp"],
            "bind_scope": "public",
            "compose_conflict": False,
        },
        "source_quality": "complete",
        "requires_hidden_access": hidden,
    }


def test_observation_events_are_idempotent_sanitized_and_visibility_bound():
    from backend import history

    visible = _observation_event(capture=1, port=4317)
    hidden = _observation_event(capture=2, port=4318, hidden=True)
    invalid = {
        **_observation_event(capture=3, port=4319),
        "before": {**visible["before"], "holder": "private-service"},
    }
    assert history.record_observation_events([visible, hidden, invalid]) == 2
    assert history.record_observation_events([visible, hidden]) == 0

    assert history.query_observation_events(4318) == []
    visible_events = history.query_observation_events(4317)
    assert len(visible_events) == 1
    assert visible_events[0]["evidence_refs"] == [f"history:{visible['event_id']}"]
    assert "requires_hidden_access" not in visible_events[0]
    assert "private-service" not in str(visible_events)
    assert history.query_observation_events(4318, allow_hidden=True)[0]["event_id"] == hidden["event_id"]


def test_batch_observation_history_fences_a_future_same_second_capture(monkeypatch):
    from backend import history

    now = int(time.time())
    future = _observation_event(capture=2, port=4317)
    future["observed_at"] = now
    assert history.record_observation_events([future]) == 1
    events, truncated = history.query_observation_events_batch(
        {4317},
        1,
        capture_id="obs-0123456789ab-1",
        captured_at=now,
    )
    assert events == []
    assert truncated is False


def test_batch_observation_history_keeps_the_newest_numeric_same_second_capture():
    from backend import history

    now = int(time.time())
    earlier = _observation_event(capture=9, port=4317)
    latest = _observation_event(capture=10, port=4317)
    earlier["observed_at"] = latest["observed_at"] = now
    assert history.record_observation_events([earlier, latest]) == 2
    events, truncated = history.query_observation_events_batch(
        {4317}, 1, limit=1, capture_id="obs-0123456789ab-10", captured_at=now
    )
    assert [event["event_id"] for event in events] == [latest["event_id"]]
    assert truncated is True


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
    main._monitor.reset()
    port_store.add_manual_port(45000, "hidden service")
    port_store.add_hidden_port(45000)
    with TestClient(app) as client:
        client.get("/api/ports?include_hidden=true")
        client.get("/api/ports?range_start=1&range_end=10")
        client.get("/api/ports?include_hidden=true")
        assert history.query(45000) == []
        port_store.remove_manual_port(45000)
        main._monitor.state_changed()
        client.get("/api/ports")
        port_store.add_manual_port(45000, "hidden service")
        main._monitor.state_changed()
        client.get("/api/ports")
        main._monitor._observe_pending()
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


def test_history_database_failure_reports_degradation(monkeypatch, tmp_path):
    from backend import degradations, main
    from backend.compose_scanner import ComposeScan

    # A directory in place of the database exercises the real SQLite failure.
    (tmp_path / "history.db").mkdir()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan())
    degradations.reset()
    main._monitor.reset()
    with TestClient(app) as client:
        assert client.get("/api/ports").status_code == 200
        assert any(event["reason"] == "occupancy history write failed" for event in degradations.recent())
        assert client.get("/api/ports/8080/history").status_code == 503


@pytest.mark.parametrize("flag", ["incomplete", "truncated"])
def test_partial_scan_keeps_history_baseline_and_refuses_reservation(monkeypatch, flag):
    from backend import history, main, port_store
    from backend.compose_scanner import ComposeScan
    from backend.port_scanner import ListeningPort

    listeners = [ListeningPort(port=42000, ip="127.0.0.1", protocol="tcp")]
    scan = ComposeScan()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: list(listeners))
    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: scan)
    main._monitor.reset()
    with TestClient(app) as client:
        listeners.clear()
        setattr(scan, flag, True)
        main._monitor.refresh()
        assert history.query(42000) == []
        assert client.post("/api/reservations", json={"require_count": False, **{'start': '42000', 'end': '42000'}}, headers={"Idempotency-Key": secrets.token_urlsafe(32)}).status_code == 503
        assert port_store.get_manual_ports() == []
        setattr(scan, flag, False)
        main._monitor.refresh()
        assert [row["state"] for row in history.query(42000)] == ["free"]


def test_existing_history_database_keeps_its_events_after_upgrade(tmp_path):
    import sqlite3
    from backend import history

    now = int(time.time())
    with sqlite3.connect(tmp_path / "history.db") as database:
        database.execute("CREATE TABLE events (ts INTEGER NOT NULL, port INTEGER NOT NULL, "
                         "state TEXT NOT NULL, holders TEXT NOT NULL DEFAULT '[]')")
        database.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                         (now, 8080, "used", '["existing-app"]'))
    assert history.query(8080)[0]["state"] == "used"
    assert history.record_observation_events([_observation_event(capture=1, port=8080)]) == 1
    with sqlite3.connect(tmp_path / "history.db") as database:
        assert database.execute("SELECT * FROM events").fetchall() == [
            (now, 8080, "used", '["existing-app"]')]
        assert database.execute("SELECT COUNT(*) FROM observation_events").fetchone() == (1,)
