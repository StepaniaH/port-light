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

    main._monitor.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())


def _seed(port_store):
    port_store.add_manual_port(5000, "", "localhost")
    port_store.add_manual_port(5010, "", "localhost")
    port_store.add_manual_port(5011, "", "localhost")
    port_store.add_hidden_port(6000)


def test_free_runs_finds_largest_gaps(monkeypatch):
    from backend import port_store

    _seed(port_store)
    client = TestClient(app)
    res = client.get("/api/free-runs", params={"count": 4, "start": 5000, "end": 6020})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 4
    assert body["runs"][0] == {"start": 5012, "end": 5999, "size": 988}
    assert {"start": 6001, "end": 6020, "size": 20} in body["runs"]
    assert {"start": 5001, "end": 5009, "size": 9} in body["runs"]
    assert {"start": 5001, "end": 5009, "size": 9} in body["runs"]
    for r in body["runs"]:
        assert r["start"] > 5000 or r["end"] < 5000
        assert not (r["start"] <= 6000 <= r["end"])


def test_free_runs_validates_window():
    client = TestClient(app)
    assert client.get("/api/free-runs", params={"start": 200, "end": 100}).status_code == 200
    bad = client.get("/api/free-runs", params={"count": 65})
    assert bad.status_code == 422


def test_free_runs_respects_basic_auth(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    assert client.get("/api/free-runs").status_code == 401
    assert client.get("/api/free-runs", auth=("admin", "s3cret")).status_code == 200


@pytest.mark.parametrize("flag", ["incomplete", "truncated"])
def test_incomplete_scan_rejects_planning_and_batch_reservation(monkeypatch, flag):
    from backend import main, port_store
    from backend.compose_scanner import ComposeScan

    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: ComposeScan(**{flag: True}))
    with TestClient(app) as client:
        assert client.get("/api/free-runs").status_code == 503
        assert client.post("/api/manual-ports/batch", json={"start": 42000, "end": 42001}).status_code == 503
    assert port_store.get_manual_ports() == []


def test_batch_conflict_preserves_every_existing_entry():
    from backend import port_store

    with TestClient(app) as client:
        assert client.get("/api/free-runs?start=42000&end=42002&count=3").json()["runs"]
        # A different writer claims one port after the planner read.
        port_store.add_manual_port(42001, "existing")
        response = client.post("/api/manual-ports/batch", json={"start": 42000, "end": 42002, "label": "batch"})
        assert response.status_code == 409
        assert port_store.get_manual_ports() == [{"port": 42001, "label": "existing", "machine": "localhost"}]
        assert client.get("/api/free-runs?start=42000&end=42002&count=3").json()["runs"] == []
        assert client.post("/api/manual-ports/batch", json={"start": 42002, "end": 42004, "label": "batch"}).status_code == 200
        assert [e["port"] for e in port_store.get_manual_ports()] == [42001, 42002, 42003, 42004]


def test_batch_disk_failure_does_not_persist_partial_claims(monkeypatch, tmp_path):
    from backend import port_store

    port_store.add_manual_port(42010, "existing")
    before = (tmp_path / "port_light.json").read_bytes()
    def fail(*args):
        raise port_store.StoreWriteError("disk full")
    monkeypatch.setattr(port_store, "_save", fail)
    with TestClient(app) as client:
        assert client.post("/api/manual-ports/batch", json={"start": 42000, "end": 42002}).status_code == 500
    assert (tmp_path / "port_light.json").read_bytes() == before
    assert len(port_store.get_manual_ports()) == 1


def test_planner_and_agent_share_one_claim_lock(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend import port_store

    barrier = threading.Barrier(2)
    for name in ("reserve_manual_range", "allocate_ports"):
        original = getattr(port_store, name)
        def together(*args, original=original):
            barrier.wait(timeout=2)
            return original(*args)
        monkeypatch.setattr(port_store, name, together)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        batch = pool.submit(client.post, "/api/manual-ports/batch", json={"start": 42000, "end": 42001})
        agent = pool.submit(client.get, "/api/ports/suggest?start=42000&end=42001&count=2&reserve=true")
        b, a = batch.result(timeout=3), agent.result(timeout=3)
        assert a.status_code == 200
        assert (b.status_code, a.json()["reserved"]) in [(200, []), (409, [42000, 42001])]
        assert len(port_store.get_manual_ports()) == 2


@pytest.mark.parametrize("start,end", [(42000, 42064), (42001, 42000), (0, 1)])
def test_batch_rejects_invalid_ranges(start, end):
    assert TestClient(app).post("/api/manual-ports/batch", json={"start": start, "end": end}).status_code == 422
