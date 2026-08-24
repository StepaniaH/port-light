from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)


def test_event_lines_push_refresh_on_store_change(monkeypatch):
    from backend import main, port_store

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    gen = main._event_lines()
    assert next(gen) == "retry: 3000\n\n"
    assert "event: hello" in next(gen)

    port_store.add_manual_port(7602, "", "localhost")
    frame = next(gen)  # detects the generation bump within one tick
    assert "event: refresh" in frame
    gen.close()


def test_events_respects_basic_auth(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    assert client.get("/api/events").status_code == 401
