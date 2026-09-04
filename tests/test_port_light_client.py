from __future__ import annotations

import io
import json
import os
import urllib.error

import pytest

from port_light_client.client import (
    HttpTransport,
    PortLightClient,
    PortLightError,
    normalize_base_url,
)
from port_light_client.state import ReservationStore


class FakeTransport:
    def __init__(self, response=None):
        self.response = response or {}
        self.requests = []

    def request(self, method, path, *, headers=None):
        self.requests.append((method, path, headers))
        return dict(self.response)


def test_client_normalizes_url_and_rejects_embedded_credentials():
    assert normalize_base_url(" https://port-light.example/ ") == "https://port-light.example"
    with pytest.raises(PortLightError, match="must not contain credentials"):
        normalize_base_url("https://user:secret@port-light.example")
    with pytest.raises(PortLightError, match="query string"):
        normalize_base_url("https://port-light.example?token=secret")


def test_check_port_uses_client_interface_and_compacts_row():
    transport = FakeTransport({
        "port": 8080,
        "status": "used",
        "protocol": "tcp",
        "bind_scope": "loopback",
        "containers": [{"name": "web"}],
    })
    client = PortLightClient(transport=transport)
    assert client.check_port(8080) == {
        "port": 8080,
        "status": "used",
        "protocol": "tcp",
        "bind_scope": "loopback",
        "names": ["web"],
    }
    assert transport.requests == [
        ("GET", "/api/ports/8080?include_hidden=false", None),
    ]


def test_reserve_encodes_options_and_sends_agent_token():
    response = {
        "ports": [8000],
        "reservations": [{"port": 8000, "token": "release-me", "expires_at": 123}],
    }
    transport = FakeTransport(response)
    client = PortLightClient(agent_token="agent-secret", transport=transport)
    assert client.reserve_ports(
        start=8000,
        end=8100,
        label="preview app",
        ttl=3600,
        scope="all",
    ) == response
    method, path, headers = transport.requests[0]
    assert method == "GET"
    assert path.startswith("/api/ports/suggest?")
    assert "reserve=true" in path
    assert "label=preview+app" in path
    assert "ttl=3600" in path
    assert "scope=all" in path
    assert "require_count=true" in path
    assert headers == {"X-Agent-Token": "agent-secret"}


def test_reserve_requires_tokens_but_allows_an_explicit_partial_result():
    partial = FakeTransport({
        "ports": [8000],
        "reservations": [{"port": 8000, "token": "one", "expires_at": 123}],
    })
    client = PortLightClient(transport=partial)
    assert len(client.reserve_ports(count=2)["reservations"]) == 1

    missing = PortLightClient(transport=FakeTransport({"ports": [8000], "reservations": []}))
    with pytest.raises(PortLightError, match="one reservation token"):
        missing.reserve_ports()

    empty = PortLightClient(transport=FakeTransport({"ports": [], "reservations": []}))
    with pytest.raises(PortLightError) as caught:
        empty.reserve_ports()
    assert caught.value.code == "no_capacity"


@pytest.mark.parametrize("response", [
    {
        "ports": [8000, 8000],
        "reservations": [
            {"port": 8000, "token": "one"},
            {"port": 8000, "token": "two"},
        ],
    },
    {
        "ports": [8000],
        "reservations": [{"port": 8001, "token": "one"}],
    },
])
def test_reserve_rejects_invalid_port_token_mappings(response):
    client = PortLightClient(transport=FakeTransport(response))
    with pytest.raises(PortLightError) as caught:
        client.reserve_ports()
    assert caught.value.code == "invalid_response"


def test_doctor_drops_the_duplicate_report_string():
    client = PortLightClient(transport=FakeTransport({
        "overall": "healthy",
        "report": "large duplicate",
    }))
    assert client.doctor() == {"overall": "healthy"}


def test_capability_checks_are_cached_and_legacy_servers_are_probed():
    transport = FakeTransport({"capabilities": {"doctor": 1}})
    client = PortLightClient(transport=transport)
    client.require_capability("doctor")
    client.require_capability("doctor")
    assert len(transport.requests) == 1

    with pytest.raises(PortLightError) as caught:
        client.require_capability("reservations")
    assert caught.value.code == "unsupported_server"

    legacy = PortLightClient(transport=FakeTransport({"version": "0.8.0"}))
    legacy.require_capability("reservations")


class Response:
    def __init__(self, body=b"{}"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _size):
        return self.body


class CapturingOpener:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def test_http_transport_sends_basic_auth_and_parses_json():
    opener = CapturingOpener(Response(b'{"status":"ok"}'))
    transport = HttpTransport(
        "http://localhost:2100",
        basic_auth="user:pass",
        timeout=2.5,
        opener=opener,
    )
    assert transport.request("GET", "/api/health") == {"status": "ok"}
    request, timeout = opener.requests[0]
    assert request.get_header("Authorization") == "Basic dXNlcjpwYXNz"
    assert request.get_header("Accept") == "application/json"
    assert timeout == 2.5


def test_http_transport_rejects_invalid_timeout_and_ca_bundle(tmp_path):
    with pytest.raises(PortLightError) as caught:
        HttpTransport("http://localhost:2100", timeout=float("nan"))
    assert caught.value.code == "invalid_timeout"

    with pytest.raises(PortLightError) as caught:
        HttpTransport("https://localhost:2100", ca_file=str(tmp_path / "missing.pem"))
    assert caught.value.code == "tls_error"


def test_http_transport_preserves_safe_server_detail():
    error = urllib.error.HTTPError(
        "http://localhost:2100/api/ports/1",
        503,
        "unavailable",
        {},
        io.BytesIO(b'{"detail":"occupancy scan is incomplete; retry later"}'),
    )
    transport = HttpTransport("http://localhost:2100", opener=CapturingOpener(error=error))
    with pytest.raises(PortLightError) as caught:
        transport.request("GET", "/api/ports/1")
    assert caught.value.code == "occupancy_unavailable"
    assert caught.value.status == 503
    assert "incomplete" in str(caught.value)


def test_reservation_store_round_trip_is_private_and_server_scoped(tmp_path):
    store = ReservationStore(tmp_path / "state")
    reservation = {"port": 8123, "token": "one-time-secret", "expires_at": None}
    store.ensure_writable()
    store.save("http://nas.lan:2100/", reservation)
    assert store.load("http://nas.lan:2100", 8123) == "one-time-secret"
    files = list((tmp_path / "state").rglob("8123.json"))
    assert len(files) == 1
    if os.name != "nt":
        assert files[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(files[0].read_text())["schema_version"] == 1
    assert store.load("http://other.lan:2100", 8123) is None
    store.delete("http://nas.lan:2100", 8123)
    assert store.load("http://nas.lan:2100", 8123) is None


def test_reservation_store_drops_expired_token(tmp_path, monkeypatch):
    store = ReservationStore(tmp_path / "state")
    monkeypatch.setattr("port_light_client.state.time.time", lambda: 1000)
    store.save("http://nas.lan:2100", {
        "port": 8123,
        "token": "expired",
        "expires_at": 999,
    })
    assert store.load("http://nas.lan:2100", 8123) is None
