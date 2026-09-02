from __future__ import annotations

import asyncio
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
    from backend.compose_scanner import ComposeScan

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan())
    main._monitor.reset()
    main._monitor.refresh()
    async def check():
        gen = main._event_lines()
        assert await anext(gen) == "retry: 3000\n\n"
        assert "event: hello" in await anext(gen)
        port_store.add_manual_port(7602, "", "localhost")
        main._monitor.state_changed()
        assert "event: refresh" in await asyncio.wait_for(anext(gen), timeout=2)
        await gen.aclose()

    asyncio.run(check())


def test_idle_stream_is_cancellable():
    from backend import main

    async def check():
        streams = [main._event_lines() for _ in range(50)]
        for gen in streams:
            await anext(gen)
            await anext(gen)
        pending = [asyncio.create_task(anext(gen)) for gen in streams]
        await asyncio.sleep(0.01)
        for task in pending:
            task.cancel()
        results = await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 1)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        for gen in streams:
            await gen.aclose()

    asyncio.run(check())


def test_events_respects_basic_auth(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    client = TestClient(app)
    assert client.get("/api/events").status_code == 401
