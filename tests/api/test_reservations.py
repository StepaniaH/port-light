"""Reservation allocation, ownership, and failure handling."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import secrets
import json

import pytest
from fastapi.testclient import TestClient

from backend import agent_events, history, main, port_store
from backend.compose_scanner import ComposeScan


def test_reservation_request_replays_after_restart_without_rescanning(monkeypatch, tmp_path):
    headers = {"Idempotency-Key": secrets.token_urlsafe(32)}
    body = {"start": 45000, "end": 45001, "ttl": None}
    with TestClient(main.app) as client:
        first = client.post("/api/reservations", json=body, headers=headers)
        assert first.status_code == 200
        assert first.json()["ports"] == [45000]
        port_store._FILE_MEMO.clear()
        main._monitor.reset()
        monkeypatch.setattr(main, "_allocation_snapshot", lambda *a: pytest.fail("replay rescanned"))
        replay = client.post("/api/reservations", json=body, headers=headers)
        assert replay.json() == first.json()
        recovered = client.get("/api/reservations/request", headers=headers)
        assert recovered.json() == first.json()
        changed = client.post("/api/reservations", json={**body, "label": "different"}, headers=headers)
        assert changed.status_code == 409
    assert len(port_store.get_manual_ports()) == 1
    stored = (tmp_path / "port_light.json").read_text()
    assert headers["Idempotency-Key"] not in stored
    assert first.json()["reservations"][0]["token"] not in stored


def test_released_request_never_allocates_again():
    headers = {"Idempotency-Key": secrets.token_urlsafe(32)}
    with TestClient(main.app) as client:
        body = {"start": 45000, "end": 45001, "ttl": None}
        first = client.post("/api/reservations", json=body, headers=headers)
        assert first.status_code == 200
        token = first.json()["reservations"][0]["token"]
        assert client.delete("/api/reservations/45000", headers={"X-Reservation-Token": token}).status_code == 200
        assert client.post("/api/reservations", json=body, headers=headers).status_code == 409
        assert client.get("/api/reservations/request", headers=headers).status_code == 409
    assert port_store.get_manual_ports() == []


def test_legacy_get_cannot_create_reservations():
    with TestClient(main.app) as client:
        assert client.get("/api/ports/suggest?reserve=true").status_code == 405
        assert client.get("/api/ports/suggest?ttl=60").status_code == 405
    assert port_store.get_manual_ports() == []


def test_request_and_claim_are_saved_atomically(monkeypatch, tmp_path):
    port_store.add_manual_port(45010)
    before = json.loads((tmp_path / "port_light.json").read_text())
    monkeypatch.setattr(port_store, "_save", lambda data: (_ for _ in ()).throw(port_store.StoreWriteError("disk full")))
    with TestClient(main.app) as client:
        res = client.post("/api/reservations", json={"start": 45000, "end": 45000},
                          headers={"Idempotency-Key": secrets.token_urlsafe(32)})
    assert res.status_code == 500
    assert json.loads((tmp_path / "port_light.json").read_text()) == before


@pytest.mark.parametrize("declaration", [
    'ports: ["20000-24096:20000-24096"]',
    'ports: [{published: "20000-24096", target: 80}]',
    'network_mode: host\n    expose: ["20000-24096"]',
])
def test_oversized_compose_range_cannot_certify_free_ports(monkeypatch, tmp_path, declaration):
    from backend.compose_scanner import scan_compose_tree

    (tmp_path / "compose.yml").write_text("services:\n  sample:\n    " + declaration + "\n")
    monkeypatch.setenv("COMPOSE_SCAN_DIR", str(tmp_path))
    monkeypatch.setattr(main, "scan_compose_tree", scan_compose_tree)
    with TestClient(main.app) as client:
        summary = client.get("/api/ports").json()["summary"]
        assert summary["scan_complete"] is False
        assert summary["compose_incomplete"] is True
        assert summary["free"] is None
        assert client.get("/api/ports/20128").status_code == 503
        assert client.get("/api/ports/suggest?start=20128&end=20128").status_code == 503
        assert client.post("/api/manual-ports/batch", json={"start": 20128, "end": 20128}).status_code == 503
    assert port_store.get_manual_ports() == []


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    for key in ("AUTH_USER", "AUTH_PASSWORD", "AGENT_TOKEN", "HIDDEN_UNLOCK_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: ComposeScan())
    main._monitor.reset()
    history.reset()
    agent_events.reset()
    yield
    history.reset()
    agent_events.reset()


def test_concurrent_reservations_do_not_claim_the_same_port(monkeypatch):
    barrier = threading.Barrier(2)
    allocate = port_store.allocate_reservation

    def together(*args, **kwargs):
        barrier.wait(timeout=5)
        return allocate(*args, **kwargs)

    monkeypatch.setattr(port_store, "allocate_reservation", together)

    with TestClient(main.app) as client:
        def reserve(label):
            res = client.post("/api/reservations", json={"require_count": False, **{
                "start": 45000, "end": 45001, "label": label,
            }}, headers={"Idempotency-Key": secrets.token_urlsafe(32)})
            assert res.status_code == 200
            return res.json()["reserved"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(reserve, ("first", "second")))
    assert sorted(p for claim in claims for p in claim) == [45000, 45001]
    assert len(port_store.get_manual_ports()) == 2


def claim(client, **params):
    response = client.post("/api/reservations", json={"require_count": False, **{
        "start": 45000, "end": 45000, "ttl": 60, **params,
    }}, headers={"Idempotency-Key": secrets.token_urlsafe(32)})
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
        response = client.post("/api/reservations", json={"require_count": False, **{
            "start": 45000, "end": 45001, "count": 2, }}, headers={"Idempotency-Key": secrets.token_urlsafe(32)})
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
        response = client.post("/api/reservations", json={"require_count": False, **{"scope": "all"}}, headers={"Idempotency-Key": secrets.token_urlsafe(32)})
    assert response.status_code == 503
    assert port_store.get_manual_ports() == []


def test_large_valid_range_is_never_allocated(monkeypatch, tmp_path):
    from backend.compose_scanner import scan_compose_tree
    (tmp_path / 'compose.yaml').write_text('services:\n  demo:\n    ports: ["20000-20255:30000-30255"]\n')
    monkeypatch.setenv('COMPOSE_SCAN_DIR', str(tmp_path))
    monkeypatch.setattr(main, 'scan_compose_tree', scan_compose_tree)
    with TestClient(main.app) as client:
        summary = client.get('/api/ports').json()['summary']
        assert summary['scan_complete'] is True
        assert client.get('/api/ports/20255').json()['status'] == 'configured'
        assert client.get('/api/ports/suggest?start=20128&end=20256').json()['ports'] == [20256]


def test_limit_diagnostics_are_current_and_hidden_gate_safe(monkeypatch, tmp_path):
    from backend.compose_scanner import scan_compose_tree
    (tmp_path / 'compose.yaml').write_text('services:\n  demo:\n    ports: ["20000-24096:80"]\n')
    monkeypatch.setenv('COMPOSE_SCAN_DIR', str(tmp_path))
    monkeypatch.setattr(main, 'scan_compose_tree', scan_compose_tree)
    monkeypatch.setenv('HIDDEN_UNLOCK_PASSWORD', 'unlock')
    with TestClient(main.app) as client:
        assert 'compose_diagnostics' not in client.get('/api/ports').json()['summary']
        summary = client.get('/api/ports', headers={'X-Hidden-Unlock': 'unlock'}).json()['summary']
        assert summary['compose_diagnostics'] == [{'code': 'port_range', 'file': 'compose.yaml',
                                                   'range': '20000-24096', 'limit': 4096}]
