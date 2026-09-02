"""End-to-end guard: a real bound socket must show up in the occupancy API.

The rest of the suite feeds scanners fake fixtures; this file binds an actual
listener so the listen-scan -> classify -> HTTP chain stays honest. Skips on
hosts without a trusted listen table (no ss, e.g. macOS) instead of lying.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from backend import main as main_module
from backend.port_scanner import host_listen_trusted

PROBE_RANGE_START = 3000
PROBE_RANGE_END = 3999


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    main_module._occ.reset()
    return TestClient(main_module.app)


def _bind_in_probe_range() -> tuple[socket.socket, int] | None:
    """Bind a listener inside the probed range; None when everything is taken."""
    for port in range(PROBE_RANGE_START, PROBE_RANGE_END):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(4)
        except OSError:
            sock.close()
            continue
        return sock, port
    return None


def _occupancy(client: TestClient) -> dict:
    res = client.get(
        "/api/ports",
        params={
            "range_start": PROBE_RANGE_START,
            "range_end": PROBE_RANGE_END,
            "include_hidden": "false",
        },
    )
    assert res.status_code == 200
    return res.json()


def _reset_snapshot_cache() -> None:
    main_module._occ.reset()


@pytest.mark.skipif(not host_listen_trusted(), reason="no trusted listen table on this host")
def test_real_listener_appears_in_occupancy(client):
    bound = _bind_in_probe_range()
    if bound is None:
        pytest.skip("no bindable port in probe range")
    sock, port = bound
    try:
        import shutil

        from backend import port_scanner as ps

        listening = ps.scan_listening_ports()
        raw_hit = [p.asdict() if hasattr(p, "asdict") else vars(p) for p in listening if p.port == port]
        assert raw_hit, (
            f"listen scanner missed bound port {port}:"
            f" which_ss={shutil.which('ss')}"
            f" source={ps.listen_scan_source()}"
            f" trusted={ps.host_listen_trusted()}"
            f" n_listening={len(listening)}"
            f" sample={[p.port for p in listening[:10]]}"
        )
        data = _occupancy(client)
        row = next((p for p in data["ports"] if p["port"] == port), None)
        assert row is not None, f"bound listener on {port} missing from /api/ports"
        assert row["status"] == "used"
        assert data["summary"]["used"] >= 1
    finally:
        sock.close()


@pytest.mark.skipif(not host_listen_trusted(), reason="no trusted listen table on this host")
def test_listener_disappears_after_close(client):
    bound = _bind_in_probe_range()
    if bound is None:
        pytest.skip("no bindable port in probe range")
    sock, port = bound
    try:
        data = _occupancy(client)
        assert any(p["port"] == port for p in data["ports"]), (
            f"listener on {port} not visible while bound"
        )
    finally:
        sock.close()
    _reset_snapshot_cache()
    data = _occupancy(client)
    assert not any(p["port"] == port for p in data["ports"]), (
        f"closed listener on {port} still reported"
    )
