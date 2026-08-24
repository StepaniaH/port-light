from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _rows(*triples):
    rows = []
    for port, status, holders in triples:
        rows.append({"port": port, "status": status,
                     "containers": [{"name": n} for n in holders],
                     "manual_label": None})
    return rows


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import history

    history.reset()
    yield
    history.reset()


def test_records_transitions_and_queries(monkeypatch):
    from backend import history

    monkeypatch.setattr(history, "enabled", lambda: True)
    history.record(_rows((8080, "used", ("app",))))          # prime
    history.record(_rows((8080, "used", ("app",)),
                         (9090, "configured", ())))           # appears
    history.record(_rows((8080, "used", ("app",)),
                         (9090, "free", ())))                 # released
    events = history.query(9090, hours=24)
    assert [e["state"] for e in events] == ["configured", "free"]
    used = history.query(8080)
    assert used == []  # no transition for 8080


def test_disabled_by_retention_zero(monkeypatch):
    from backend import history

    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    assert history.enabled() is False
    assert history.record(_rows((8080, "used", ()))) == 0
    assert history.query(8080) == []


def test_history_endpoint_404_when_disabled(monkeypatch):
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    client = TestClient(app)
    assert client.get("/api/ports/8080/history").status_code == 404


def test_history_endpoint_returns_events(monkeypatch, tmp_path):
    from backend import history

    history.record(_rows((7700, "configured", ())))  # prime
    history.record(_rows((7700, "used", ("svc",))))
    client = TestClient(app)
    res = client.get("/api/ports/7700/history")
    assert res.status_code == 200
    body = res.json()
    assert body["port"] == 7700
    assert len(body["events"]) == 1
    assert body["events"][0]["holders"] == ["svc"]
