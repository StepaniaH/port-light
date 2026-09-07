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
    create_client,
    normalize_base_url,
)
from port_light_client.state import ReservationStore


CAPABILITIES = {
    "doctor": 1,
    "port_check": 1,
    "reservations": 1,
    "exact_reservations": 1,
    "reservation_release": 1,
    "scope_all": 1,
}

DOCTOR_RESPONSE = {
    "schema_version": 1,
    "overall": "healthy",
    "counts": {"pass": 1, "warning": 0, "fail": 0, "info": 0},
    "context": {"version": "0.8.1"},
    "checks": [{"id": "snapshot", "status": "pass", "detail": "current"}],
}


def suggestion_response(
    *,
    ports=None,
    reservations=None,
    start=1,
    end=9999,
    scope="self",
):
    return {
        "ports": ports if ports is not None else [8000],
        "reservations": reservations if reservations is not None else [],
        "range": {"start": start, "end": end},
        "scope": scope,
    }


class FakeTransport:
    def __init__(self, response=None, *, meta_response=None):
        self.response = response or {}
        self.meta_response = (
            {"capabilities": CAPABILITIES}
            if meta_response is None else meta_response
        )
        self.requests = []

    def request(self, method, path, *, headers=None):
        self.requests.append((method, path, headers))
        if path == "/api/meta":
            return dict(self.meta_response)
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
        ("GET", "/api/meta", None),
        ("GET", "/api/ports/8080?include_hidden=false", None),
    ]


def test_reserve_encodes_options_and_sends_agent_token():
    response = suggestion_response(
        reservations=[{"port": 8000, "token": "release-me", "expires_at": 123}],
        start=8000,
        end=8100,
        scope="all:0/0",
    )
    transport = FakeTransport(response)
    client = PortLightClient(agent_token="agent-secret", transport=transport)
    assert client.reserve_ports(
        start=8000,
        end=8100,
        label="preview app",
        ttl=3600,
        scope="all",
    ) == response
    assert transport.requests[0] == ("GET", "/api/meta", None)
    method, path, headers = transport.requests[1]
    assert method == "GET"
    assert path.startswith("/api/ports/suggest?")
    assert "reserve=true" in path
    assert "label=preview+app" in path
    assert "ttl=3600" in path
    assert "scope=all" in path
    assert "require_count=true" in path
    assert headers == {"X-Agent-Token": "agent-secret"}


def test_reserve_requires_tokens_but_preserves_a_partial_result_for_recovery():
    partial = FakeTransport(suggestion_response(
        reservations=[{"port": 8000, "token": "one", "expires_at": 123}],
    ))
    client = PortLightClient(transport=partial)
    assert len(client.reserve_ports(count=2)["reservations"]) == 1

    missing = PortLightClient(transport=FakeTransport(suggestion_response()))
    with pytest.raises(PortLightError, match="one reservation token"):
        missing.reserve_ports()

    empty = PortLightClient(transport=FakeTransport(suggestion_response(ports=[])))
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
    {
        "ports": [1],
        "reservations": [{"port": True, "token": "one"}],
    },
    {
        "ports": [8000],
        "reservations": [{"port": 8000, "token": "one", "expires_at": "later"}],
    },
])
def test_reserve_rejects_invalid_port_token_mappings(response):
    client = PortLightClient(transport=FakeTransport(suggestion_response(**response)))
    with pytest.raises(PortLightError) as caught:
        client.reserve_ports()
    assert caught.value.code == "invalid_response"


def test_reserve_rejects_non_string_tokens():
    client = PortLightClient(transport=FakeTransport(suggestion_response(
        reservations=[{"port": 8000, "token": 123}],
    )))
    with pytest.raises(PortLightError) as caught:
        client.reserve_ports()
    assert caught.value.code == "unsupported_server"


def test_doctor_drops_the_duplicate_report_string():
    client = PortLightClient(transport=FakeTransport({
        **DOCTOR_RESPONSE,
        "report": "large duplicate",
    }))
    assert client.doctor() == DOCTOR_RESPONSE


def test_capability_checks_are_cached_and_legacy_servers_are_probed():
    transport = FakeTransport(
        DOCTOR_RESPONSE,
        meta_response={"capabilities": {"doctor": 1}},
    )
    client = PortLightClient(transport=transport)
    client.doctor()
    client.doctor()
    assert transport.requests == [
        ("GET", "/api/meta", None),
        ("GET", "/api/doctor", None),
        ("GET", "/api/doctor", None),
    ]

    with pytest.raises(PortLightError) as caught:
        client.reserve_ports()
    assert caught.value.code == "unsupported_server"
    assert all("/api/ports/suggest" not in path for _, path, _ in transport.requests)

    legacy_transport = FakeTransport(DOCTOR_RESPONSE, meta_response={})
    legacy = PortLightClient(transport=legacy_transport)
    assert legacy.doctor()["overall"] == "healthy"
    with pytest.raises(PortLightError) as caught:
        legacy.reserve_ports()
    assert caught.value.code == "unsupported_server"
    assert legacy_transport.requests[0] == ("GET", "/api/meta", None)
    assert all("/api/ports/suggest" not in path for _, path, _ in legacy_transport.requests)


def test_capability_versions_must_be_integers():
    client = PortLightClient(transport=FakeTransport(
        DOCTOR_RESPONSE,
        meta_response={"capabilities": {"doctor": "1"}},
    ))
    with pytest.raises(PortLightError) as caught:
        client.doctor()
    assert caught.value.code == "unsupported_server"


@pytest.mark.parametrize("response", [
    {},
    {"port": 9999, "status": "free"},
    {"port": 8080, "status": "unknown"},
])
def test_check_rejects_untrustworthy_port_responses(response):
    client = PortLightClient(transport=FakeTransport(response))
    with pytest.raises(PortLightError) as caught:
        client.check_port(8080)
    assert caught.value.code == "invalid_response"


def test_doctor_rejects_untrustworthy_response():
    client = PortLightClient(transport=FakeTransport({"overall": "healthy"}))
    with pytest.raises(PortLightError) as caught:
        client.doctor()
    assert caught.value.code == "invalid_response"


def test_suggestion_requires_the_actual_scope_and_range():
    client = PortLightClient(transport=FakeTransport(suggestion_response(scope="self")))
    with pytest.raises(PortLightError) as caught:
        client.suggest_ports(scope="all")
    assert caught.value.code == "invalid_response"


def test_shared_client_factory_applies_environment_and_overrides(monkeypatch):
    captured = {}

    def make_client(url, **kwargs):
        captured.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr("port_light_client.client.PortLightClient", make_client)
    created = create_client(
        {
            "PORT_LIGHT_URL": "https://environment.example",
            "PORT_LIGHT_TIMEOUT": "4",
            "PORT_LIGHT_CA_FILE": "/environment/ca.pem",
            "PORT_LIGHT_AUTH": "operator:secret",
            "PORT_LIGHT_AGENT_TOKEN": "specific-agent-token",
            "AGENT_TOKEN": "fallback-agent-token",
        },
        base_url="https://option.example",
        timeout=9,
        ca_file="/option/ca.pem",
    )
    assert created is not None
    assert captured == {
        "url": "https://option.example",
        "timeout": 9.0,
        "ca_file": "/option/ca.pem",
        "basic_auth": "operator:secret",
        "agent_token": "specific-agent-token",
    }


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
    assert list((tmp_path / "state").rglob("8123.json")) == []


def test_reservation_store_normalizes_directory_creation_failure(tmp_path, monkeypatch):
    store = ReservationStore(tmp_path / "state")

    def fail(*_args, **_kwargs):
        raise PermissionError("private path detail")

    monkeypatch.setattr("port_light_client.state.Path.mkdir", fail)
    with pytest.raises(PortLightError) as caught:
        store.save("http://nas.lan:2100", {"port": 8123, "token": "secret"})
    assert caught.value.code == "state_write_failed"
    assert str(tmp_path) not in str(caught.value)
