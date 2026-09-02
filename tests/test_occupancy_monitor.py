import asyncio

from backend import history, main, webhooks
from backend.compose_scanner import ComposeScan
from backend.port_scanner import ListeningPort


def test_background_scan_records_history_and_broadcasts_without_http(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    listeners = []
    observed = []
    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: list(listeners))
    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: ComposeScan())
    monkeypatch.setattr(webhooks, "observe", lambda rows: observed.append(rows))
    history.reset()
    main._monitor.reset()

    async def check():
        await main._monitor.start()
        try:
            before = main._monitor.sequence()
            waiters = [asyncio.create_task(main._monitor.wait_for_change(before, 2)) for _ in range(3)]
            listeners.append(ListeningPort(port=42000, ip="127.0.0.1", protocol="tcp"))
            main._monitor.wake()
            results = await asyncio.gather(*waiters)
            assert all(changed and sequence > before for sequence, changed in results)
            assert main._monitor.latest()["listening"][0].port == 42000
            assert [event["state"] for event in history.query(42000)] == ["used"]
            assert observed[-1][0]["port"] == 42000
        finally:
            await main._monitor.stop()

    try:
        asyncio.run(check())
    finally:
        history.reset()
