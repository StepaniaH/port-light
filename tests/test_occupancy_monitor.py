import asyncio
import threading

from backend import history, main, webhooks
from backend.compose_scanner import ComposeScan
from backend.port_scanner import ListeningPort


def test_background_scan_records_history_and_broadcasts_without_http(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    listeners = []
    observed = []
    allow_history = threading.Event()
    history_done = threading.Event()
    record = history.record

    def delayed_record(rows):
        changed = any(row["port"] == 42000 for row in rows)
        if changed:
            assert allow_history.wait(2)
        result = record(rows)
        if changed:
            history_done.set()
        return result

    monkeypatch.setattr(main, "scan_containers", lambda: [])
    monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: list(listeners))
    monkeypatch.setattr(main, "scan_compose_tree", lambda *a, **kw: ComposeScan())
    monkeypatch.setattr(webhooks, "observe", lambda rows: observed.append(rows))
    monkeypatch.setattr(history, "record", delayed_record)
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
            assert not history_done.is_set()
            allow_history.set()
            assert await asyncio.to_thread(history_done.wait, 2)
            assert [event["state"] for event in history.query(42000)] == ["used"]
            assert observed[-1][0]["port"] == 42000
        finally:
            allow_history.set()
            await main._monitor.stop()

    try:
        asyncio.run(check())
    finally:
        history.reset()


def test_failed_listen_scan_preserves_rows_and_observer_baselines(empty_scan, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import port_scanner, port_store

    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    observed = []
    monkeypatch.setattr(webhooks, "observe", lambda rows: observed.append(rows))
    history.reset()
    try:
        with TestClient(main.app) as client:
            monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: [
                ListeningPort(port=42000, ip="127.0.0.1", protocol="tcp")])
            empty_scan.refresh()
            monkeypatch.setattr(main, "scan_listening_ports", port_scanner.scan_listening_ports)
            monkeypatch.setattr(port_scanner, "_scan_with_host_proc", lambda **kw: None)
            monkeypatch.setattr(port_scanner, "host_listen_trusted", lambda: False)
            empty_scan.refresh()
            port_store.add_hidden_port(42002)
            payload = client.get("/api/ports").json()
            assert payload["summary"]["sources"]["listen"] == "failed"
            assert payload["summary"]["scan_complete"] is False
            assert client.get("/api/ports/42000").json()["status"] == "used"
            assert client.get("/api/ports/42001").status_code == 503
            assert client.get("/api/ports/42002?include_hidden=true").status_code == 503
            assert payload["summary"]["free"] is None
            assert {"port": 42002, "status": "unknown"} in payload["summary"]["hidden_occupancy"]
            for path in ("/api/ports/suggest?start=42000&end=42000&reserve=true", "/api/free-runs"):
                assert client.get(path).status_code == 503
            assert client.post("/api/manual-ports/batch", json={"start": 42000, "end": 42001}).status_code == 503
            assert port_store.get_manual_ports() == []
            assert [e["state"] for e in history.query(42000)] == ["used"]
            assert len(observed) == 2
            assert client.get("/api/health").json()["occupancy"]["ready"] is False
            monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: [])
            empty_scan.refresh()
            assert client.get("/api/ports/42000").json()["status"] == "free"
            assert [e["state"] for e in history.query(42000)] == ["used", "free"]
            assert len(observed) == 3
    finally:
        history.reset()


def test_disabled_scanners_are_explicit_and_not_called(empty_scan, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PORT_LIGHT_SCANNERS", "listen")
    def unexpected(*args, **kwargs):
        raise AssertionError("disabled scanner called")
    monkeypatch.setattr(main, "scan_containers", unexpected)
    monkeypatch.setattr(main, "scan_compose_tree", unexpected)
    with TestClient(main.app) as client:
        summary = client.get("/api/ports").json()["summary"]
        assert summary["scan_complete"] is True
        assert summary["sources"] == {"listen": "ok", "docker": "disabled", "compose": "disabled"}
        assert client.get("/api/ports/suggest").status_code == 200


def test_write_completes_during_scan_and_survives_publication(empty_scan, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient

    started, release = threading.Event(), threading.Event()
    def blocked(**kw):
        started.set()
        assert release.wait(5)
        return []
    with TestClient(main.app) as client:
        monkeypatch.setattr(main, "scan_listening_ports", blocked)
        empty_scan.wake()
        assert started.wait(2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                write = pool.submit(client.post, "/api/manual-ports", json={"port": 42000, "label": "during scan"})
                assert write.result(timeout=1).status_code == 200
                assert client.get("/api/ports/42000").json()["manual_label"] == "during scan"
                before = empty_scan.sequence()
            finally:
                release.set()
            # Publication may have no visible change, so await the scan task's
            # completion by waiting for its single worker slot to clear.
            async def published():
                for _ in range(100):
                    if empty_scan._job is None:
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("scan did not complete")
            client.portal.call(published)
            assert empty_scan.sequence() >= before
            assert client.get("/api/ports/42000").json()["manual_label"] == "during scan"


def test_scan_timeout_bounds_startup_workers_and_shutdown(empty_scan, monkeypatch):
    import threading
    import time
    from fastapi.testclient import TestClient
    from backend import occupancy_monitor

    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(**kw):
        calls.append(1)
        started.set()
        release.wait(5)
        return [ListeningPort(port=42000, ip="127.0.0.1", protocol="tcp")]
    monkeypatch.setattr(main, "scan_listening_ports", blocked)
    monkeypatch.setattr(occupancy_monitor, "_scan_timeout", lambda: 0.05)
    before = time.monotonic()
    try:
        with TestClient(main.app) as client:
            assert started.is_set()
            assert time.monotonic() - before < 1
            assert client.get("/api/ports").status_code == 503
            health = client.get("/api/health").json()
            assert health["occupancy"]["initialized"] is False
            assert health["occupancy"]["ready"] is False
            for _ in range(3):
                client.portal.call(empty_scan._scan_once)
            assert calls == [1]
            assert client.post("/api/manual-ports", json={"port": 42001}).status_code == 200
            job = empty_scan._job
            before = time.monotonic()
        assert time.monotonic() - before < 1
        release.set()
        job.result(timeout=1)
        assert empty_scan.status()["initialized"] is False  # late result cannot publish
        monkeypatch.setattr(main, "scan_listening_ports", lambda **kw: [])
        with TestClient(main.app) as client:
            assert client.get("/api/health").json()["occupancy"]["ready"] is True
            assert client.get("/api/ports/42000").json()["status"] == "free"
            assert client.get("/api/ports/42001").json()["status"] == "configured"
    finally:
        release.set()


def test_docker_failure_is_not_an_empty_success(empty_scan, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import docker_scanner
    from backend.docker_scanner import ContainerInfo
    from backend.models import PortMapping

    monkeypatch.setattr(main, "scan_containers", lambda: [ContainerInfo(
        name="service", status="running", image="service", ports=[
            PortMapping(host_port=42000, container_port=80, protocol="tcp", host_ip="0.0.0.0")])])
    with TestClient(main.app) as client:
        monkeypatch.setattr(docker_scanner, "_docker_client", lambda: None)
        monkeypatch.setattr(main, "scan_containers", docker_scanner.scan_containers)
        empty_scan.refresh()
        assert client.get("/api/ports/42000").json()["status"] == "used"
        assert client.get("/api/ports").json()["summary"]["sources"]["docker"] == "failed"
        assert client.get("/api/ports/suggest").status_code == 503


def test_slow_observation_does_not_block_startup_or_event_loop(empty_scan, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import time
    from fastapi.testclient import TestClient
    from backend import occupancy_monitor

    release = threading.Event()
    monkeypatch.setattr(occupancy_monitor, "_scan_timeout", lambda: 0.05)
    monkeypatch.setattr(history, "record", lambda rows: release.wait(3))
    before = time.monotonic()
    try:
        with TestClient(main.app) as client:
            assert time.monotonic() - before < 1
            job = empty_scan._job
            before = time.monotonic()
            assert client.get("/api/health").json()["occupancy"]["ready"] is False
            assert client.get("/api/ports").json()["summary"]["stale"] is True
            assert time.monotonic() - before < 0.5
            with ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    write = pool.submit(client.post, "/api/manual-ports", json={"port": 42001})
                    assert write.result(timeout=0.5).status_code == 200
                    assert client.get("/api/ports/42001").json()["status"] == "configured"
                finally:
                    release.set()
            before = time.monotonic()
        assert time.monotonic() - before < 1
    finally:
        release.set()
        job.result(timeout=1)
