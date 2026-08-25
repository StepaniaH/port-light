from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    monkeypatch.delenv("AGENT_TOKEN", raising=False)
    from backend import agent_events, history

    history.reset()
    agent_events.reset()
    yield
    history.reset()
    agent_events.reset()


def test_meta_includes_agent_events_block():
    from backend import agent_events

    agent_events.record(3, "self", "preview", True)
    body = TestClient(app).get("/api/meta").json()
    ev = body["automation"]["agent_events"]
    assert ev["total"] == 1
    assert ev["active_leases"] == 0
    assert ev["recent"][0]["label"] == "preview"
    assert ev["lease_rows"] == []


def test_meta_omits_block_when_history_disabled(monkeypatch):
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    from backend import agent_events

    agent_events.reset()
    agent_events.record(1, "self", "", False)
    body = TestClient(app).get("/api/meta").json()
    assert "agent_events" not in body["automation"]


def test_active_leases_listed_with_rows():
    from backend import port_store

    port_store.add_manual_port(6000, "leased", "localhost", 3600)
    port_store.add_manual_port(6001, "manual", "localhost")
    body = TestClient(app).get("/api/meta").json()
    ev = body["automation"]["agent_events"]
    assert ev["active_leases"] == 1
    assert ev["lease_rows"] == [
        {"port": 6000, "label": "leased",
         "expires_at": ev["lease_rows"][0]["expires_at"]}]
    assert isinstance(ev["lease_rows"][0]["expires_at"], int)
