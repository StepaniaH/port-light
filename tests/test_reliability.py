"""Regressions for allocation and observed occupancy semantics."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from backend import agent_events, history, main, port_store
from backend.compose_scanner import ComposeScan


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    for key in ("AUTH_USER", "AUTH_PASSWORD", "AGENT_TOKEN", "HIDDEN_UNLOCK_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: ComposeScan())
    main._occ.reset()
    history.reset()
    agent_events.reset()
    yield
    history.reset()
    agent_events.reset()


def test_concurrent_reservations_do_not_claim_the_same_port(monkeypatch):
    barrier = threading.Barrier(2)
    scan = main._scan_snapshot

    def together(*args, **kwargs):
        result = scan(*args, **kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(main, "_scan_snapshot", together)

    def reserve(label):
        with TestClient(main.app) as client:
            res = client.get("/api/ports/suggest", params={
                "start": 45000, "end": 45001, "reserve": True, "label": label,
            })
            assert res.status_code == 200
            return res.json()["reserved"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(reserve, ("first", "second")))
    assert sorted(p for claim in claims for p in claim) == [45000, 45001]
    assert len(port_store.get_manual_ports()) == 2


def test_history_records_disappearance_and_reappearance():
    row = {"port": 45000, "status": "used"}
    history.record([row])
    history.record([])
    history.record([row])
    assert [e["state"] for e in history.query(45000)] == ["free", "used"]


def test_manual_ttl_is_persisted():
    with TestClient(main.app) as client:
        response = client.post("/api/manual-ports", json={"port": 45000, "ttl": 60})
    assert response.status_code == 200
    assert response.json()["entry"].get("expires_at") is not None


def test_hidden_write_requires_unlock(monkeypatch):
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock")
    port_store.add_hidden_port(45000)
    with TestClient(main.app) as client:
        response = client.delete("/api/hidden/45000")
    assert response.status_code == 403
    assert port_store.get_hidden_ports() == [45000]


def test_hidden_history_is_withheld(monkeypatch):
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock")
    port_store.add_hidden_port(45000)
    history.record([])
    history.record([{"port": 45000, "status": "used", "manual_label": "private"}])
    with TestClient(main.app) as client:
        response = client.get("/api/ports/45000/history")
    assert response.status_code == 404


def claim(client, **params):
    response = client.get("/api/ports/suggest", params={
        "start": 45000, "end": 45000, "ttl": 60, **params,
    })
    assert response.status_code == 200
    return response.json()["reservations"][0]


def test_only_matching_reservation_can_be_released(monkeypatch):
    monkeypatch.setattr(port_store, "_now", lambda: 1000)
    with TestClient(main.app) as client:
        first = claim(client)
        url = "/api/reservations/45000"
        assert client.delete(url).status_code == 403
        assert client.delete(url, headers={"X-Reservation-Token": "wrong"}).status_code == 409
        for method, path, body in (
            ("POST", "/api/manual-ports", {"port": 45000}),
            ("PATCH", "/api/manual-ports/45000", {"label": "overwrite"}),
            ("DELETE", "/api/manual-ports/45000", None),
        ):
            assert client.request(method, path, json=body).status_code == 409
        monkeypatch.setattr(port_store, "_now", lambda: 1061)
        second = claim(client)
        assert second["token"] != first["token"]
        assert client.delete(url, headers={"X-Reservation-Token": first["token"]}).status_code == 409
        assert client.delete(url, headers={"X-Reservation-Token": second["token"]}).status_code == 200
        assert port_store.get_manual_ports() == []


def test_release_does_not_delete_legacy_manual_entry():
    port_store.add_manual_port(45000, "manual")
    with TestClient(main.app) as client:
        response = client.delete("/api/reservations/45000", headers={"X-Reservation-Token": "anything"})
    assert response.status_code == 409
    assert port_store.get_manual_ports()[0]["label"] == "manual"


def test_reservation_secrets_are_not_disclosed(tmp_path):
    with TestClient(main.app) as client:
        reservation = claim(client)
        for path in ("/api/manual-ports", "/api/ports", "/api/meta"):
            response = client.get(path)
            assert response.status_code == 200
            assert reservation["token"] not in response.text
            assert "reservation_hash" not in response.text
    assert reservation["token"] not in (tmp_path / "port_light.json").read_text()


def test_failed_batch_write_leaves_no_phantom_reservations(monkeypatch, tmp_path):
    port_store.add_manual_port(45010, "existing")
    before = (tmp_path / "port_light.json").read_text()
    generation = port_store.store_generation()

    def fail(data):
        raise port_store.StoreWriteError("disk full")

    monkeypatch.setattr(port_store, "_save", fail)
    with TestClient(main.app) as client:
        response = client.get("/api/ports/suggest", params={
            "start": 45000, "end": 45001, "count": 2, "reserve": True,
        })
    assert response.status_code == 500
    assert (tmp_path / "port_light.json").read_text() == before
    assert port_store.store_generation() == generation
    assert [row["port"] for row in port_store.get_manual_ports()] == [45010]


def test_usage_history_failure_still_returns_durable_claim(monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(agent_events, "record", fail)
    with TestClient(main.app) as client:
        reservation = claim(client)
        assert client.delete("/api/reservations/45000", headers={
            "X-Reservation-Token": reservation["token"],
        }).status_code == 200


@pytest.mark.parametrize("summary", [
    {},
    {"hidden_locked": True}, {"stale": True}, {"compose_incomplete": True},
    {"compose_truncated": True},
])
def test_incomplete_peer_prevents_all_scope_claim(monkeypatch, summary):
    monkeypatch.setattr(main.hosts, "list_public_peers", lambda: [{"name": "peer"}])
    monkeypatch.setattr(main.hosts, "fetch_peer_json", lambda *args: (
        200, {"ports": [], "summary": summary}, None))
    with TestClient(main.app) as client:
        response = client.get("/api/ports/suggest", params={"reserve": True, "scope": "all"})
    assert response.status_code == 503
    assert port_store.get_manual_ports() == []


def test_history_is_independent_of_request_visibility_and_range():
    port_store.add_manual_port(45000, "hidden service")
    port_store.add_hidden_port(45000)
    with TestClient(main.app) as client:
        client.get("/api/ports?include_hidden=true")  # baseline
        client.get("/api/ports?range_start=1&range_end=10")
        client.get("/api/ports?include_hidden=true")
        assert history.query(45000) == []
        port_store.remove_manual_port(45000)
        client.get("/api/ports")
        port_store.add_manual_port(45000, "hidden service")
        client.get("/api/ports")
        assert [e["state"] for e in history.query(45000)] == ["free", "configured"]


def test_hidden_manual_writes_and_lists_are_gated(monkeypatch):
    port_store.add_manual_port(45000, "private", ttl=60)
    port_store.add_hidden_port(45000)
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock")
    with TestClient(main.app) as client:
        assert client.get("/api/manual-ports").json()["manual_ports"] == []
        assert client.get("/api/meta").json()["automation"]["agent_events"]["lease_rows"] == []
        assert client.patch("/api/manual-ports/45000", json={"label": "x"}).status_code == 403
        assert client.delete("/api/manual-ports/45000").status_code == 403
        assert client.post("/api/manual-ports", json={"port": 45000}).status_code == 403
        assert client.post("/api/hidden/45001").status_code == 403
        assert client.delete("/api/hidden/45000", headers={"X-Hidden-Unlock": "unlock"}).status_code == 200


def test_peer_history_routes_to_peer(monkeypatch):
    monkeypatch.setattr(main.hosts, "get_peer", lambda host: {"name": host})

    def fetch(peer, path, query, etag):
        assert peer["name"] == "abcdef123456"
        assert path == "/api/ports/45000/history"
        assert query == {"hours": "12"}
        return 200, {"port": 45000, "events": [{"state": "used"}]}, None

    monkeypatch.setattr(main.hosts, "fetch_peer_json", fetch)
    with TestClient(main.app) as client:
        response = client.get("/api/hosts/abcdef123456/ports/45000/history?hours=12")
    assert response.status_code == 200
    assert response.json()["events"] == [{"state": "used"}]


def test_store_hand_edit_invalidates_scan(tmp_path):
    with TestClient(main.app) as client:
        assert client.get("/api/ports/45000").json()["status"] == "free"
        (tmp_path / "port_light.json").write_text(json.dumps({"manual_ports": [{"port": 45000}]}))
        assert client.get("/api/ports/45000").json()["status"] == "configured"
