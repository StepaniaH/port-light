from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from port_light_client import PortLightError, mcp
from port_light_client.state import ReservationStore


ROOT = pathlib.Path(__file__).resolve().parents[2]



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


def initialize(version=mcp.PROTOCOL_VERSION):
    return {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": version, "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1"},
    }}


def session():
    value = mcp.McpSession()
    value.handle_request(initialize())
    value.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return value


def call_tool(name, arguments=None):
    return session().handle_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })


def tool_data(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def test_initialize_negotiates_protocol_version():
    reply = mcp.McpSession().handle_request(initialize("2025-06-18"))
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    assert reply["result"]["serverInfo"]["name"] == "port-light"


def test_direct_script_entry_point_works_outside_repository(tmp_path):
    request = json.dumps(initialize("2024-11-05"))
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
    reply = session().handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in reply["result"]["tools"]}
    assert names == {
        "suggest_ports",
        "check_port",
        "list_occupancy",
        "port_history",
        "list_degradations",
        "release_port",
        "doctor",
        "reserve_ports",
    }


def test_notification_returns_nothing():
    assert session().handle_request(
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
    reply = session().handle_request({"jsonrpc": "2.0", "id": 6, "method": "nope"})
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


def test_doctor_uses_shared_diagnostics(fake_client, monkeypatch):
    monkeypatch.setattr(fake_client, 'doctor', lambda: {'overall': 'attention'}, raising=False)
    assert tool_data(call_tool('doctor')) == {'overall': 'attention'}


def test_reserve_defaults_to_lease_and_releases_without_exposing_tokens(fake_client, monkeypatch):
    calls = []
    def reserve(**kwargs):
        calls.append(kwargs)
        return {'ports': [8000], 'reservations': [
            {'port': 8000, 'token': 'private-token', 'expires_at': None}]}
    monkeypatch.setattr(fake_client, 'reserve_ports', reserve, raising=False)
    result = call_tool('reserve_ports', {'label': 'project/api'})
    assert 'private-token' not in json.dumps(result)
    assert tool_data(result)['tokens_saved'] is True
    assert calls[0]['ttl'] == 3600
    assert calls[0]['label'] == 'project/api'
    assert ReservationStore().load(fake_client.base_url, 8000) == 'private-token'
    assert ReservationStore().pending_requests(fake_client.base_url) == []
    assert tool_data(call_tool('release_port', {'port': 8000})) == {'released': 8000}
    assert fake_client.calls == [('release_port', 8000, 'private-token')]
    assert ReservationStore().load(fake_client.base_url, 8000) is None


def test_reserve_retry_recovers_after_local_token_write_failure(fake_client, monkeypatch):
    keys = []
    def reserve(**kwargs):
        keys.append(kwargs['request_key'])
        return {'ports': [8000], 'reservations': [
            {'port': 8000, 'token': 'private-token', 'expires_at': None}]}
    monkeypatch.setattr(fake_client, 'reserve_ports', reserve, raising=False)
    save = ReservationStore.save
    def fail(*args):
        raise PortLightError('state_write_failed', 'save failed')
    monkeypatch.setattr(ReservationStore, 'save', fail)
    assert call_tool('reserve_ports')['result']['isError'] is True
    assert len(ReservationStore().pending_requests(fake_client.base_url)) == 1
    monkeypatch.setattr(ReservationStore, 'save', save)
    assert tool_data(call_tool('reserve_ports'))['ports'] == [8000]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_release_does_not_use_another_hosts_token(fake_client):
    ReservationStore().save('http://another-host:2100', {
        'port': 8000, 'token': 'other-token', 'expires_at': None})
    assert call_tool('release_port', {'port': 8000})['result']['isError'] is True
    assert fake_client.calls == []


def test_release_failure_keeps_token_and_cleanup_failure_reports_remote_success(fake_client, monkeypatch):
    store = ReservationStore()
    store.save(fake_client.base_url, {'port': 8000, 'token': 'my-token', 'expires_at': None})
    release = fake_client.release_port
    def fail(*args):
        raise PortLightError('unreachable', 'response lost')
    monkeypatch.setattr(fake_client, 'release_port', fail)
    assert call_tool('release_port', {'port': 8000})['result']['isError'] is True
    assert store.load(fake_client.base_url, 8000) == 'my-token'
    monkeypatch.setattr(fake_client, 'release_port', release)
    monkeypatch.setattr(ReservationStore, 'delete', fail)
    result = tool_data(call_tool('release_port', {'port': 8000}))
    assert result['remote_released'] is True
    assert 'cleanup failed' in result['warning']


@pytest.mark.parametrize(('name', 'arguments'), [
    ('check_port', {}), ('check_port', {'port': True}),
    ('check_port', {'port': '8000'}), ('check_port', {'port': 65536}),
    ('reserve_ports', {'ttl': None}), ('reserve_ports', {'ttl': 0}),
    ('reserve_ports', {'reserve': False}), ('reserve_ports', []),
    ('suggest_ports', {'reserve': 'false'}), ('suggest_ports', {'scope': 'unknown'}),
])
def test_invalid_tool_arguments_do_not_contact_server(fake_client, name, arguments):
    reply = session().handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                'params': {'name': name, 'arguments': arguments}})
    assert reply['result']['isError'] is True
    assert 'invalid_request' in reply['result']['content'][0]['text']
    assert fake_client.calls == []


@pytest.mark.parametrize('version', mcp.SUPPORTED_PROTOCOL_VERSIONS)
def test_supported_versions_negotiate_and_filter_tool_metadata(version):
    value = mcp.McpSession()
    reply = value.handle_request(initialize(version))
    assert reply['result']['protocolVersion'] == version
    value.handle_request({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    tools = value.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    assert ('annotations' in tools[0]) == (version != '2024-11-05')


@pytest.mark.parametrize('version', ['2099-01-01', '2025-03-26'])
def test_unsupported_protocol_falls_back_to_a_supported_version(version):
    reply = mcp.McpSession().handle_request(initialize(version))
    assert reply['result']['protocolVersion'] == mcp.PROTOCOL_VERSION


def test_tools_require_completed_handshake_and_notifications_never_mutate(fake_client):
    value = mcp.McpSession()
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': 'suggest_ports', 'arguments': {'reserve': True}}}
    assert value.handle_request(request)['error']['code'] == -32002
    value.handle_request({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    assert value.ready is False
    value.handle_request(initialize())
    assert value.handle_request(request)['error']['code'] == -32002
    value.handle_request({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    del request['id']
    assert value.handle_request(request) is None
    assert fake_client.calls == []


@pytest.mark.parametrize('message', [
    [], None, True, 123, 'text', {}, {'jsonrpc': '1.0', 'id': 1, 'method': 'ping'},
    {'jsonrpc': '2.0', 'id': None, 'method': 'ping'},
    {'jsonrpc': '2.0', 'id': True, 'method': 'ping'},
    {'jsonrpc': '2.0', 'id': 1, 'method': []},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': []},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': {}}},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': None}},
])
def test_invalid_envelopes_return_errors_and_next_request_survives(message):
    import io
    incoming = (json.dumps(message) + '\n' + json.dumps(initialize()) + '\n').encode()
    output = io.BytesIO()
    assert mcp.serve(io.BytesIO(incoming), output) == 0
    replies = list(map(json.loads, output.getvalue().splitlines()))
    assert 'error' in replies[0]
    assert replies[1]['result']['serverInfo']['name'] == 'port-light'


@pytest.mark.parametrize('bad_line', [b'{"secret":"do-not-echo",\n', b'\xff\n', b'NaN\n', b'{"n":Infinity}\n'])
def test_invalid_json_and_utf8_are_redacted_and_recoverable(bad_line):
    import io
    output = io.BytesIO()
    mcp.serve(io.BytesIO(bad_line + json.dumps(initialize()).encode() + b'\n'), output)
    replies = list(map(json.loads, output.getvalue().splitlines()))
    assert replies[0]['error']['code'] == -32700
    assert replies[1]['result']['serverInfo']['name'] == 'port-light'
    assert b'do-not-echo' not in output.getvalue()


def test_oversized_message_is_drained_before_next_message():
    import io
    output = io.BytesIO()
    mcp.serve(io.BytesIO(b'x' * (mcp.MAX_MESSAGE_BYTES * 2) + b'\n' + json.dumps(initialize()).encode() + b'\n'), output)
    replies = list(map(json.loads, output.getvalue().splitlines()))
    assert len(replies) == 2
    assert replies[0]['error']['code'] == -32600
    assert replies[1]['result']['serverInfo']['name'] == 'port-light'


def test_internal_keyerror_is_not_reported_as_unknown_tool(fake_client, monkeypatch):
    def broken(port):
        raise KeyError('private-detail')
    monkeypatch.setattr(fake_client, 'check_port', broken)
    reply = call_tool('check_port', {'port': 8000})
    assert reply['result']['isError'] is True
    assert 'private-detail' not in json.dumps(reply)
