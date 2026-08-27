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
