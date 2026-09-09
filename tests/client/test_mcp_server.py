from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

from port_light_client import PortLightError


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mcp_server", ROOT / "mcp" / "server.py")
mcp = importlib.util.module_from_spec(SPEC)
sys.modules["mcp_server"] = mcp
SPEC.loader.exec_module(mcp)


class FakeClient:
    base_url = "http://localhost:2100"
    def __init__(self):
        self.calls = []

    def suggest_ports(self, **kwargs):
        self.calls.append(("suggest_ports", kwargs))
        return {"ports": [8000]}

    def check_port(self, port):
        self.calls.append(("check_port", port))
        return {
            "port": port,
            "status": "free",
            "protocol": "tcp",
            "bind_scope": "unknown",
            "names": ["HTTP Alt"],
        }

    def list_occupancy(self, **kwargs):
        self.calls.append(("list_occupancy", kwargs))
        return {"summary": {"scan_complete": True}, "ports": []}

    def port_history(self, port, *, hours):
        self.calls.append(("port_history", port, hours))
        return {"port": port, "events": []}

    def health(self):
        self.calls.append(("health",))
        return {"degradations": [{"source": "docker"}]}

    def release_port(self, port, token):
        self.calls.append(("release_port", port, token))
        return {"released": port}


@pytest.fixture
def fake_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_STATE_DIR", str(tmp_path))
    value = FakeClient()
    monkeypatch.setattr(mcp, "client", lambda: value)
    return value


def call_tool(name, arguments=None):
    return mcp.handle_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })


def tool_data(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def test_initialize_negotiates_protocol_version():
    reply = mcp.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["serverInfo"]["name"] == "port-light"


def test_direct_script_entry_point_works_outside_repository(tmp_path):
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    result = subprocess.run(
        [sys.executable, str(ROOT / "mcp" / "server.py")],
        cwd=tmp_path,
        input=f"{request}\n",
        text=True,
        capture_output=True,
        check=True,
    )
    reply = json.loads(result.stdout)
    assert reply["result"]["serverInfo"]["name"] == "port-light"


def test_client_factory_reports_invalid_timeout(monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_TIMEOUT", "later")
    with pytest.raises(PortLightError) as caught:
        mcp.client()
    assert caught.value.code == "invalid_timeout"


def test_tools_list_exposes_all_tools():
    reply = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in reply["result"]["tools"]}
    assert names == {
        "suggest_ports",
        "check_port",
        "list_occupancy",
        "port_history",
        "list_degradations",
        "release_port",
    }


def test_notification_returns_nothing():
    assert mcp.handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ) is None


def test_check_port_uses_shared_client(fake_client):
    data = tool_data(call_tool("check_port", {"port": 8080}))
    assert data["names"] == ["HTTP Alt"]
    assert fake_client.calls == [("check_port", 8080)]


def test_suggest_maps_all_options_to_shared_client(fake_client):
    data = tool_data(call_tool("suggest_ports", {
        "count": 2,
        "reserve": True,
        "label": "my preview",
        "start": 3000,
        "end": 4000,
        "ttl": 3600,
        "scope": "all",
    }))
    assert data == {"ports": [8000]}
    assert len(fake_client.calls[0][1].pop("request_key")) == 43
    assert fake_client.calls == [("suggest_ports", {
        "count": 2,
        "start": 3000,
        "end": 4000,
        "reserve": True,
        "ttl": 3600,
        "scope": "all",
        "label": "my preview",
    })]


def test_list_history_and_degradations_use_shared_client(fake_client):
    occupancy = tool_data(call_tool("list_occupancy", {
        "start": 1000,
        "end": 2000,
        "limit": 10,
    }))
    history = tool_data(call_tool("port_history", {"port": 1234, "hours": 48}))
    degradations = tool_data(call_tool("list_degradations"))
    assert occupancy["summary"]["scan_complete"] is True
    assert history == {"port": 1234, "events": []}
    assert degradations["degradations"][0]["source"] == "docker"
    assert fake_client.calls == [
        ("list_occupancy", {"start": 1000, "end": 2000, "limit": 10}),
        ("port_history", 1234, 48),
        ("health",),
    ]


def test_release_requires_and_passes_reservation_token(fake_client):
    data = tool_data(call_tool(
        "release_port",
        {"port": 45000, "token": "my-reservation"},
    ))
    assert data == {"released": 45000}
    assert fake_client.calls == [("release_port", 45000, "my-reservation")]

    reply = call_tool("release_port", {"port": 45000})
    assert reply["result"]["isError"] is True
    assert "reservation token" in reply["result"]["content"][0]["text"]


def test_tool_failure_reports_iserror(monkeypatch):
    class FailingClient(FakeClient):
        def check_port(self, port):
            raise PortLightError("unreachable", "cannot reach Port-Light")

    monkeypatch.setattr(mcp, "client", FailingClient)
    reply = call_tool("check_port", {"port": 1})
    assert reply["result"]["isError"] is True


def test_unexpected_tool_failure_does_not_expose_internal_details(monkeypatch):
    class FailingClient(FakeClient):
        def check_port(self, port):
            raise RuntimeError("private implementation path")

    monkeypatch.setattr(mcp, "client", FailingClient)
    reply = call_tool("check_port", {"port": 1})
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert text == "Port-Light MCP request failed unexpectedly"
    assert "private" not in text


def test_unknown_method_and_tool_are_protocol_errors():
    reply = mcp.handle_request({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert reply["error"]["code"] == -32601
    reply = call_tool("unknown")
    assert reply["error"]["code"] == -32602


def test_mcp_retry_preserves_pending_request_key(fake_client, monkeypatch):
    keys = []
    def uncertain(**kwargs):
        keys.append(kwargs["request_key"])
        if len(keys) == 1:
            raise PortLightError("unreachable", "response lost")
        return {"ports": [8000], "reservations": []}
    monkeypatch.setattr(fake_client, "suggest_ports", uncertain)
    args = {"reserve": True, "label": "retry"}
    assert call_tool("suggest_ports", args)["result"]["isError"] is True
    assert tool_data(call_tool("suggest_ports", args))["ports"] == [8000]
    assert keys[0] == keys[1]
