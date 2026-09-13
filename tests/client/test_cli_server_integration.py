from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
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


def test_cli_doctor_check_reserve_release_against_running_server(empty_scan, tmp_path, monkeypatch):
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

        # The CLI verifies a fresh MCP process, including the agent gate, without leases.
        code, verification, error = _invoke(url, store, "verify")
        assert code == (0 if healthy else 1)
        assert verification["ready"] is healthy
        assert verification["ai_registration"] == "not_checked"
        assert verification["target_url"] == url
        assert verification["checks"][-1]["status"] == "pass"
        assert not list((store.root / "reservations").iterdir())
        assert not (store.root / "requests").exists()
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

        # Exercise the documented stdio entry point from outside the checkout.
        def mcp_call(name, arguments=None):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments or {}}}
            process = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[2] / "mcp/server.py")],
                input="\n".join(json.dumps(message) for message in [
                    {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                        "protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1"}}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"}, request,
                ]) + "\n", text=True, capture_output=True,
                cwd=tmp_path, timeout=10, check=True,
                env={**os.environ, "PORT_LIGHT_URL": url,
                     "PORT_LIGHT_STATE_DIR": str(store.root)},
            )
            reply = json.loads(process.stdout.splitlines()[-1])["result"]
            assert not reply.get("isError"), reply
            return json.loads(reply["content"][0]["text"])

        assert mcp_call("doctor")["overall"] in {"healthy", "attention"}
        reservation = mcp_call("reserve_ports", {"start": 45000, "end": 45000, "label": "mcp/api"})
        assert reservation["ports"] == [45000]
        assert reservation["tokens_saved"] is True
        assert "token" not in reservation["reservations"][0]
        assert 3590 < reservation["reservations"][0]["expires_at"] - time.time() <= 3600
        assert client.check_port(45000)["status"] == "configured"
        # A fresh MCP process can release using the persistent state.
        assert mcp_call("release_port", {"port": 45000})["released"] == 45000
        assert client.check_port(45000)["status"] == "free"

        # Healthy diagnostics alone cannot prove the separate allocation gate.
        monkeypatch.setenv("AUTH_USER", "setup-test")
        monkeypatch.setenv("AUTH_PASSWORD", "basic-test-secret")
        monkeypatch.setenv("AGENT_TOKEN", "agent-test-secret")
        for credentials, expected_code in [
            ({}, "authentication_failed"),
            ({"PORT_LIGHT_AUTH": "setup-test:basic-test-secret"}, "authentication_failed"),
            ({"PORT_LIGHT_AUTH": "setup-test:basic-test-secret",
              "PORT_LIGHT_AGENT_TOKEN": "agent-test-secret"}, None),
        ]:
            stdout = io.StringIO()
            cli_main(["--url", url, "verify", "--json"], store=store, environ=credentials, stdout=stdout)
            report = json.loads(stdout.getvalue())
            failed = [row for row in report["checks"] if row["status"] == "fail"]
            assert [row["code"] for row in failed] == ([expected_code] * len(failed) if expected_code else [])
            assert bool(failed) is bool(expected_code)
            assert "basic-test-secret" not in stdout.getvalue()
            assert "agent-test-secret" not in stdout.getvalue()
        assert not list((store.root / "reservations").rglob("*.json"))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
