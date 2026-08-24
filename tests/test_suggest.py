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

    main._occ_snap = None
    main._occ_building = False
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())


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
