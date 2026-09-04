from __future__ import annotations

import io
import json
import socket
import threading
import time

import uvicorn

from backend import main as backend_main
from port_light_client import PortLightClient
from port_light_client.cli import main as cli_main
from port_light_client.state import ReservationStore


def _invoke(url, store, *args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cli_main(
        ["--url", url, "--json", *args],
        store=store,
        environ={},
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=stderr,
    )
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


def test_cli_doctor_check_reserve_release_against_running_server(empty_scan, tmp_path):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    config = uvicorn.Config(backend_main.app, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    store = ReservationStore(tmp_path / "cli-state")
    try:
        code, result, error = _invoke(url, store, "doctor")
        healthy = result["doctor"]["overall"] == "healthy"
        assert code == (0 if healthy else 1)
        assert result["ok"] is healthy
        assert error == ""

        code, result, _ = _invoke(url, store, "check", "45000")
        assert code == 0
        assert result["port"]["status"] == "free"

        code, result, _ = _invoke(
            url,
            store,
            "reserve",
            "--start",
            "45000",
            "--end",
            "45000",
            "--ttl",
            "10m",
        )
        assert code == 0
        assert result["ports"] == [45000]
        assert result["tokens_saved"] is True
        token = store.load(url, 45000)
        assert token

        client = PortLightClient(url)
        assert client.check_port(45000)["status"] == "configured"

        code, result, _ = _invoke(url, store, "release", "45000")
        assert code == 0
        assert result["released"] == 45000
        assert store.load(url, 45000) is None
        assert client.check_port(45000)["status"] == "free"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
