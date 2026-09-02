import pytest

from backend import main, port_store
from backend.compose_scanner import ComposeScan
from backend.occupancy_monitor import OccupancyMonitor


@pytest.fixture(autouse=True)
def isolated_monitor(monkeypatch):
    """Each test owns its monitor, including any worker finishing after stop."""
    monitor = OccupancyMonitor(
        values=main._values, scan_key=main._scan_key, state_key=port_store.store_revision,
        build=main._build_snapshot, load_state=port_store.occupancy_user_state,
        classify=main._classify_snapshot,
    )
    monkeypatch.setattr(main, "_monitor", monitor)
    return monitor


@pytest.fixture
def empty_scan(monkeypatch, tmp_path, isolated_monitor):
    """An isolated store and three successful, empty scanner observations."""
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT_LIGHT_SCANNERS", "listen,docker,compose")
    for key in ("AUTH_USER", "AUTH_PASSWORD", "AGENT_TOKEN", "HIDDEN_UNLOCK_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: ComposeScan())
    return isolated_monitor
