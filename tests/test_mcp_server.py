from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mcp_server", ROOT / "mcp" / "server.py")
mcp = importlib.util.module_from_spec(SPEC)
sys.modules["mcp_server"] = mcp
SPEC.loader.exec_module(mcp)


def test_initialize_negotiates_protocol_version():
    reply = mcp.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["serverInfo"]["name"] == "port-light"


def test_tools_list_exposes_all_tools():
    reply = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert {"suggest_ports", "check_port", "list_occupancy",
            "port_history", "list_degradations", "release_port"} == names


def test_notification_returns_nothing():
    assert mcp.handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_check_port_maps_to_api_and_trims(monkeypatch):
    seen = {}

    def fake_get(path):
        seen["path"] = path
        return {"port": 8080, "status": "free", "source_type": "unknown",
                "protocol": "tcp", "containers": [],
                "known_service": {"name": "HTTP Alt"}}

    monkeypatch.setattr(mcp, "api_get", fake_get)
    reply = mcp.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "check_port", "arguments": {"port": 8080}},
    })
    assert seen["path"].startswith("/api/ports/8080")
    data = json.loads(reply["result"]["content"][0]["text"])
    assert data["names"] == ["HTTP Alt"]


def test_suggest_passes_reserve_and_label(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp, "api_get", lambda path: seen.update(path=path) or {})
    from urllib.parse import quote
    mcp.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "suggest_ports",
                   "arguments": {"count": 2, "reserve": True,
                                 "label": "my preview", "start": 3000}},
    })
    assert "/api/ports/suggest?" in seen["path"]
    assert "count=2" in seen["path"]
    assert "reserve=true" in seen["path"]
    assert f"label={quote('my preview')}" in seen["path"]


def test_tool_failure_reports_iserror(monkeypatch):
    def boom(path):
        raise mcp.ApiError(502, "cannot reach Port-Light")

    monkeypatch.setattr(mcp, "api_get", boom)
    reply = mcp.handle_request({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "check_port", "arguments": {"port": 1}},
    })
    assert reply["result"]["isError"] is True


def test_unknown_method_is_protocol_error():
    reply = mcp.handle_request({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert reply["error"]["code"] == -32601


def test_suggest_passes_ttl_and_scope(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp, "api_get", lambda path: seen.update(path=path) or {})
    mcp.handle_request({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "suggest_ports",
                   "arguments": {"count": 2, "ttl": 3600, "scope": "all",
                                 "reserve": True}},
    })
    assert "ttl=3600" in seen["path"]
    assert "scope=all" in seen["path"]
    assert "reserve=true" in seen["path"]


def test_list_degradations_reads_health(monkeypatch):
    monkeypatch.setattr(mcp, "api_get",
                        lambda path: {"degradations": [{"source": "docker"}]}
                        if path == "/api/health" else (_ for _ in ()).throw(AssertionError(path)))
    reply = mcp.handle_request({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "list_degradations", "arguments": {}},
    })
    data = json.loads(reply["result"]["content"][0]["text"])
    assert data["degradations"][0]["source"] == "docker"


def test_tools_list_includes_new_tool():
    reply = mcp.handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert "list_degradations" in names
