from __future__ import annotations

import time

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


def test_suggest_with_ttl_creates_expiring_lease():
    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 2, "start": 7000, "end": 7010,
                             "ttl": 120, "label": "agent"})
    body = res.json()
    assert body["reserved"] == [7000, 7001]
    assert abs(body["expires_at"] - (time.time() + 120)) < 5

    stored = {m["port"]: m for m in __import__("backend.port_store", fromlist=["x"]).get_manual_ports()}
    assert stored[7000]["expires_at"] == pytest.approx(body["expires_at"], abs=2)


def test_expired_lease_disappears_and_bumps_generation(monkeypatch):
    from backend import port_store

    clock = {"now": 1_000_000}
    monkeypatch.setattr(port_store, "_now", lambda: clock["now"])

    port_store.add_manual_port(7100, "", "localhost", ttl=60)
    assert len(port_store.get_manual_ports()) == 1
    gen_before = port_store.store_generation()

    clock["now"] += 61
    assert port_store.get_manual_ports() == []
    assert port_store.store_generation() > gen_before


def test_manual_post_accepts_ttl_validation():
    client = TestClient(app)
    ok = client.post("/api/manual-ports",
                     json={"port": 7200, "label": "", "machine": "localhost", "ttl": 60})
    assert ok.status_code == 200
    bad = client.post("/api/manual-ports",
                      json={"port": 7201, "label": "", "machine": "localhost", "ttl": 10})
    assert bad.status_code == 422


def test_ttl_bounds_on_suggest():
    client = TestClient(app)
    low = client.get("/api/ports/suggest", params={"count": 1, "ttl": 30})
    assert low.status_code == 422
