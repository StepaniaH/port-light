from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)


def test_metrics_disabled_by_default():
    client = TestClient(app)
    assert client.get("/api/metrics").status_code == 404


def test_metrics_enabled_reports_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("METRICS_ENABLED", "1")
    import backend.main as main
    from backend.compose_scanner import ComposeScan

    main._monitor.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())

    from backend import port_store

    entry = port_store.add_manual_port(7600, "", "localhost")
    assert entry["port"] == 7600

    client = TestClient(app)
    grid = client.get("/api/ports")
    assert grid.status_code == 200
    assert grid.json()["summary"]["configured"] == 1

    res = client.get("/api/metrics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    body = res.text
    assert "port_light_up 1" in body
    assert 'port_light_ports{status="used"} 0' in body
    assert 'port_light_ports{status="configured"} 1' in body
    assert 'port_light_ports{status="free"}' in body
    assert "port_light_compose_files" in body
    assert "port_light_degradations" in body


def test_metrics_aggregates_include_hidden_rows(monkeypatch, tmp_path):
    """A hidden-but-listening port is still counted as used."""
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("METRICS_ENABLED", "1")
    import backend.main as main
    from backend.compose_scanner import ComposeScan
    from backend.port_scanner import ListeningPort

    main._monitor.reset()
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(
        main,
        "scan_listening_ports",
        lambda **_kw: [ListeningPort(port=7700, protocol="tcp", ip="0.0.0.0")],
    )
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_k: ComposeScan())
    from backend import port_store

    port_store.add_hidden_port(7700)

    client = TestClient(app)
    res = client.get("/api/metrics")
    assert res.status_code == 200
    assert 'port_light_ports{status="used"} 1' in res.text
    assert "port_light_hidden 1" in res.text


def test_metrics_respects_basic_auth(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "1")
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    assert client.get("/api/metrics").status_code == 401
    ok = client.get("/api/metrics", auth=("admin", "s3cret"))
    assert ok.status_code == 200
