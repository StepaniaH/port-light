from __future__ import annotations

import http.client
import io
import json
import os
import ssl
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
        "idempotent_reservations": 1,
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

    def request(self, method, path, *, headers=None, json=None):
        self.requests.append((method, path, headers))
        self.body = json
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
        "conflict": False,
        "rule_violations": [],
    }
    assert transport.requests == [
        ("GET", "/api/meta", None),
        ("GET", "/api/ports/8080?include_hidden=false", None),
    ]


def test_compact_port_collects_deduplicated_holders_before_known_service_fallback():
    transport = FakeTransport({
        "port": 5432,
        "status": "used",
        "protocol": "tcp",
        "bind_scope": "public",
        "containers": [{"name": "db-primary"}, {"name": ""}, {"name": "db-primary"}],
        "process": "postgres",
        "manual_label": "team-db",
        "compose_configs": [
            {"service_name": "postgres"},
            {"service_name": "db-replica"},
        ],
        "known_service": {"name": "PostgreSQL"},
        "conflict": 1,
        "rule_violations": [
            {"rule": "database-tier", "project": "billing"},
            {"rule": ""},
            "ignored",
        ],
    })
    client = PortLightClient(transport=transport)
    assert client.check_port(5432) == {
        "port": 5432,
        "status": "used",
        "protocol": "tcp",
        "bind_scope": "public",
        "names": ["db-primary", "postgres", "team-db", "db-replica"],
        "conflict": True,
        "rule_violations": ["database-tier"],
    }

    fallback_client = PortLightClient(transport=FakeTransport({
        "port": 5432,
        "status": "free",
        "protocol": "tcp",
        "bind_scope": None,
        "containers": [],
        "process": "",
        "manual_label": None,
        "compose_configs": [{"project_dir": "/srv/compose"}],
        "known_service": {"name": "PostgreSQL"},
    }))
    assert fallback_client.check_port(5432)["names"] == ["PostgreSQL"]


def test_list_occupancy_validates_all_rows_and_filters_to_requested_range():
    transport = FakeTransport({
        "summary": {"used": 1, "configured": 1, "free": 998},
        "ports": [
            {
                "port": 22,
                "status": "used",
                "protocol": "tcp",
                "bind_scope": "public",
                "process": "sshd",
            },
            {
                "port": 8080,
                "status": "used",
                "protocol": "tcp",
                "bind_scope": "loopback",
                "manual_label": "preview/api",
                "conflict": True,
            },
            {
                "port": 8443,
                "status": "unknown",
                "protocol": "tcp",
                "bind_scope": None,
                "compose_configs": [{"service_name": "gateway"}],
                "rule_violations": [{"rule": "ingress"}],
            },
            {
                "port": 19000,
                "status": "configured",
                "protocol": "tcp",
                "bind_scope": "loopback",
                "manual_label": "outside-range",
            },
        ],
    })
    client = PortLightClient(transport=transport)
    result = client.list_occupancy(8000, 8999)
    assert result == {
        "summary": {"used": 1, "configured": 1, "free": 998},
        "ports": [
            {
                "port": 8080,
                "status": "used",
                "protocol": "tcp",
                "bind_scope": "loopback",
                "names": ["preview/api"],
                "conflict": True,
                "rule_violations": [],
            },
            {
                "port": 8443,
                "status": "unknown",
                "protocol": "tcp",
                "bind_scope": None,
                "names": ["gateway"],
                "conflict": False,
                "rule_violations": ["ingress"],
            },
        ],
    }
    assert transport.requests == [
        ("GET", "/api/ports?range_start=8000&range_end=8999&include_hidden=false", None),
    ]


@pytest.mark.parametrize(("start", "end"), [
    (0, 100),
    (1, 65536),
    (5000, 4999),
    (True, 100),
    (1, False),
    ("1", 100),
])
def test_list_occupancy_rejects_invalid_ranges_before_request(start, end):
    transport = FakeTransport()
    client = PortLightClient(transport=transport)
    with pytest.raises(PortLightError) as caught:
        client.list_occupancy(start, end)
    assert caught.value.code == "invalid_request"
    assert transport.requests == []


def test_list_occupancy_rejects_corrupt_out_of_range_or_duplicate_rows():
    corrupt_outside = PortLightClient(transport=FakeTransport({
        "summary": {},
        "ports": [
            {"port": 22, "status": "used", "protocol": "", "bind_scope": "public"},
            {"port": 8080, "status": "used", "protocol": "tcp", "bind_scope": "loopback"},
        ],
    }))
    with pytest.raises(PortLightError) as caught:
        corrupt_outside.list_occupancy(8000, 8999)
    assert caught.value.code == "invalid_response"

    duplicate_rows = PortLightClient(transport=FakeTransport({
        "summary": {},
        "ports": [
            {"port": 22, "status": "used", "protocol": "tcp", "bind_scope": "public"},
            {"port": 22, "status": "configured", "protocol": "tcp", "bind_scope": "public"},
        ],
    }))
    with pytest.raises(PortLightError) as caught:
        duplicate_rows.list_occupancy(8000, 8999)
    assert caught.value.code == "invalid_response"


def test_list_rules_validates_and_normalizes_server_rules():
    transport = FakeTransport({
        "rules": [
            {"name": "frontend", "start": 3000, "end": 3999, "projects": ["web", "docs"]},
            {"name": "metrics", "start": 9100, "end": 9100, "projects": []},
        ],
    })
    client = PortLightClient(transport=transport)
    assert client.list_rules() == {
        "rules": [
            {"name": "frontend", "start": 3000, "end": 3999, "projects": ["web", "docs"]},
            {"name": "metrics", "start": 9100, "end": 9100, "projects": []},
        ],
    }
    assert transport.requests == [("GET", "/api/port-rules", None)]


@pytest.mark.parametrize("payload", [
    {},
    {"rules": "invalid"},
    {"rules": [{"name": "", "start": 3000, "end": 3999, "projects": []}]},
    {"rules": [{"name": "bad", "start": True, "end": 3999, "projects": []}]},
    {"rules": [{"name": "bad", "start": 4000, "end": 3999, "projects": []}]},
    {"rules": [{"name": "bad", "start": 3000, "end": 70000, "projects": []}]},
    {"rules": [{"name": "bad", "start": 3000, "end": 3999, "projects": [123]}]},
])
def test_list_rules_rejects_malformed_payloads(payload):
    client = PortLightClient(transport=FakeTransport(payload))
    with pytest.raises(PortLightError) as caught:
        client.list_rules()
    assert caught.value.code == "invalid_response"


def test_reserve_encodes_options_and_sends_agent_token():
    response = suggestion_response(
        reservations=[{"port": 8000, "token": "release-me", "expires_at": 123}],
        start=8000,
        end=8100,
        scope="all:0/0",
    )
    transport = FakeTransport(response)
    client = PortLightClient(agent_token="agent-secret", transport=transport)
    assert client.reserve_ports(request_key="k" * 43,
        start=8000,
        end=8100,
        label="preview app",
        ttl=3600,
        scope="all",
    ) == response
    assert transport.requests[0] == ("GET", "/api/meta", None)
    method, path, headers = transport.requests[1]
    assert method == "POST"
    assert path == "/api/reservations"
    assert transport.body == dict(count=1, start=8000, end=8100, label="preview app",
                                  ttl=3600, scope="all", require_count=True)
    assert headers == {"X-Agent-Token": "agent-secret", "Idempotency-Key": "k" * 43}


def test_reserve_requires_tokens_but_preserves_a_partial_result_for_recovery():
    partial = FakeTransport(suggestion_response(
        reservations=[{"port": 8000, "token": "one", "expires_at": 123}],
    ))
    client = PortLightClient(transport=partial)
    assert len(client.reserve_ports(request_key="k" * 43, count=2)["reservations"]) == 1

    missing = PortLightClient(transport=FakeTransport(suggestion_response()))
    with pytest.raises(PortLightError, match="one reservation token"):
        missing.reserve_ports(request_key="k" * 43, )

    empty = PortLightClient(transport=FakeTransport(suggestion_response(ports=[])))
    with pytest.raises(PortLightError) as caught:
        empty.reserve_ports(request_key="k" * 43, )
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
        client.reserve_ports(request_key="k" * 43, )
    assert caught.value.code == "invalid_response"


def test_reserve_rejects_non_string_tokens():
    client = PortLightClient(transport=FakeTransport(suggestion_response(
        reservations=[{"port": 8000, "token": 123}],
    )))
    with pytest.raises(PortLightError) as caught:
        client.reserve_ports(request_key="k" * 43, )
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
        client.reserve_ports(request_key="k" * 43, )
    assert caught.value.code == "unsupported_server"
    assert all("/api/ports/suggest" not in path for _, path, _ in transport.requests)

    legacy_transport = FakeTransport(DOCTOR_RESPONSE, meta_response={})
    legacy = PortLightClient(transport=legacy_transport)
    assert legacy.doctor()["overall"] == "healthy"
    with pytest.raises(PortLightError) as caught:
        legacy.reserve_ports(request_key="k" * 43, )
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
    {"port": 8080, "status": "unknown", "protocol": "tcp", "bind_scope": None},
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


@pytest.mark.parametrize(('status', 'server_code', 'expected'), [
    (503, 'authentication_misconfigured', 'authentication_misconfigured'),
    (503, '', 'occupancy_unavailable'),
    (405, '', 'upgrade_required'),
])
def test_transport_distinguishes_actionable_failures(status, server_code, expected):
    error = urllib.error.HTTPError('http://localhost:2100/api/meta', status, 'failed', {},
                                  io.BytesIO(json.dumps({'code': server_code, 'detail': 'repair required'}).encode()))
    transport = HttpTransport('http://localhost:2100', opener=CapturingOpener(error=error))
    with pytest.raises(PortLightError) as caught:
        transport.request('GET', '/api/meta')
    assert caught.value.code == expected


def test_rule_capability_is_required_before_any_allocation():
    transport = FakeTransport()
    client = PortLightClient(transport=transport)
    with pytest.raises(PortLightError):
        client.reserve_ports(rule='development', request_key='k' * 43)
    assert all(path == '/api/meta' for _, path, _ in transport.requests)


def test_named_rule_reaches_post_body():
    response = suggestion_response(ports=[28000], start=28000, end=28010,
                                   reservations=[{'port': 28000, 'token': 'release-me', 'expires_at': 123}])
    transport = FakeTransport(response, meta_response={'capabilities': {**CAPABILITIES, 'port_rules': 1}})
    client = PortLightClient(transport=transport)
    client.reserve_ports(rule='development', request_key='k' * 43)
    assert transport.body['rule'] == 'development'
    assert transport.requests[-1][0:2] == ('POST', '/api/reservations')


def test_http_transport_ignores_environment_proxies(monkeypatch):
    import urllib.request

    observed = []

    class OKResponse(Response):
        code = 200
        msg = "OK"

        def info(self):
            return {}

    class CapturingHTTPHandler(urllib.request.BaseHandler):
        def http_open(self, req):
            observed.append((req.host, getattr(req, "_tunnel_host", None), req.get_header("Authorization")))
            return OKResponse(b'{"status":"ok"}')

    class CapturingHTTPSHandler(urllib.request.BaseHandler):
        def __init__(self, context=None):
            self.context = context

        def https_open(self, req):
            observed.append((req.host, getattr(req, "_tunnel_host", None), req.get_header("Authorization")))
            return OKResponse(b'{"status":"ok"}')

    monkeypatch.setattr(urllib.request, "HTTPHandler", CapturingHTTPHandler)
    monkeypatch.setattr(urllib.request, "HTTPSHandler", CapturingHTTPSHandler)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://192.0.2.10:3128")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")

    for scheme in ("http", "https"):
        transport = HttpTransport(f"{scheme}://192.0.2.50:2100", basic_auth="operator:secret")
        assert transport.request("GET", "/api/health") == {"status": "ok"}

    assert observed == [
        ("192.0.2.50:2100", None, "Basic b3BlcmF0b3I6c2VjcmV0"),
        ("192.0.2.50:2100", None, "Basic b3BlcmF0b3I6c2VjcmV0"),
    ]


@pytest.mark.parametrize("code", [
    "no_capacity",
    "invalid_request",
    "authentication_failed",
    "authentication_misconfigured",
    "unsupported_server",
    "upgrade_required",
    "forbidden",
    "settings_readonly",
])
def test_pending_request_unlinks_journal_on_non_committing_rejection(tmp_path, code):
    store = ReservationStore(tmp_path / "state")
    url = "https://ports.example.invalid"
    params = {"count": 1, "scope": "self"}
    with pytest.raises(PortLightError):
        with store.pending_request(url, params):
            raise PortLightError(code, "server rejected request")
    assert store.pending_requests(url) == []
    if os.name != "nt":
        assert ((tmp_path / "state" / "requests").stat().st_mode & 0o777) == 0o700
    assert len(list((tmp_path / "state" / "requests").glob("*.lock"))) == 1


@pytest.mark.parametrize("code", [
    "unreachable",
    "timeout",
    "occupancy_unavailable",
    "server_error",
])
def test_pending_request_preserves_journal_on_uncertain_failure(tmp_path, code):
    store = ReservationStore(tmp_path / "state")
    url = "https://ports.example.invalid"
    params = {"count": 1, "scope": "self"}
    seen_keys = []
    with pytest.raises(PortLightError):
        with store.pending_request(url, params) as key:
            seen_keys.append(key)
            raise PortLightError(code, "uncertain outcome")
    assert len(store.pending_requests(url)) == 1
    with store.pending_request(url, params) as retry_key:
        seen_keys.append(retry_key)
    assert seen_keys[0] == seen_keys[1]
    assert store.pending_requests(url) == []


def test_pending_request_serializes_concurrent_identical_callers(tmp_path):
    store = ReservationStore(tmp_path / "state")
    url = "https://ports.example.invalid"
    params = {"count": 1, "scope": "self"}
    with store.pending_request(url, params):
        with pytest.raises(PortLightError) as caught:
            with store.pending_request(url, params):
                pass
        assert caught.value.code == "reservation_in_progress"


def test_recovery_request_cleans_up_unrecorded_pending_request_on_not_found(tmp_path):
    store = ReservationStore(tmp_path / "state")
    url = "https://ports.example.invalid"
    params = {"count": 1, "scope": "self"}
    first_key = None
    with pytest.raises(PortLightError):
        with store.pending_request(url, params) as key:
            first_key = key
            raise PortLightError("unreachable", "network drop before server received request")
    pending = store.pending_requests(url)
    assert len(pending) == 1
    request_id = pending[0]["id"]

    with pytest.raises(PortLightError) as transient:
        with store.recovery_request(url, request_id):
            raise PortLightError("unreachable", "still offline")
    assert transient.value.code == "unreachable"
    assert len(store.pending_requests(url)) == 1

    with pytest.raises(PortLightError) as missing:
        with store.recovery_request(url, request_id):
            raise PortLightError("not_found", "server never recorded request")
    assert missing.value.code == "not_found"
    assert store.pending_requests(url) == []

    with store.pending_request(url, params) as fresh_key:
        assert fresh_key != first_key


def test_normalize_base_url_lowercases_host_supports_ipv6_and_rejects_bad_ports():
    assert normalize_base_url("HTTPS://Port-Light.Example:2100/SubPath/") == "https://port-light.example:2100/SubPath"
    assert normalize_base_url("HTTP://[2001:DB8::1]:2100/") == "http://[2001:db8::1]:2100"
    assert normalize_base_url("http://[::1]/") == "http://[::1]"
    for bad in (
        "http://port-light.example:0",
        "http://port-light.example:70000",
        "http://port-light.example:abc",
        "http://[::1",
        "http://bad host:2100",
        "http://port-light.example:2100\r\nX-Injected: 1",
    ):
        with pytest.raises(PortLightError) as caught:
            normalize_base_url(bad)
        assert caught.value.code == "invalid_url"


@pytest.mark.parametrize(("raised", "expected_code"), [
    (http.client.IncompleteRead(b"partial"), "invalid_response"),
    (http.client.BadStatusLine("garbage"), "invalid_response"),
    (http.client.LineTooLong("header"), "invalid_response"),
    (http.client.InvalidURL("bad url"), "invalid_url"),
    (http.client.RemoteDisconnected("closed"), "unreachable"),
    (ssl.SSLError(1, "WRONG_VERSION_NUMBER"), "tls_error"),
    (urllib.error.URLError(ssl.SSLCertVerificationError(1, "verify failed")), "tls_error"),
    (TimeoutError("timed out"), "timeout"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
    (ValueError("Invalid header value"), "invalid_request"),
])
def test_http_transport_classifies_http_ssl_timeout_and_value_errors(raised, expected_code):
    transport = HttpTransport("https://ports.example", opener=CapturingOpener(error=raised))
    with pytest.raises(PortLightError) as caught:
        transport.request("GET", "/api/health")
    assert caught.value.code == expected_code


def test_http_transport_handles_read_incomplete_and_rejects_invalid_headers():
    class TruncatedResponse(Response):
        def read(self, _size):
            raise http.client.IncompleteRead(b"{")

    transport = HttpTransport("http://localhost:2100", opener=CapturingOpener(TruncatedResponse()))
    with pytest.raises(PortLightError) as caught:
        transport.request("GET", "/api/health")
    assert caught.value.code == "invalid_response"

    clean_transport = HttpTransport("http://localhost:2100", opener=CapturingOpener())
    for bad_value in ("secret-token\r\nX-Injected: 1", "secret-token-\u2603", "secret-token\x00"):
        with pytest.raises(PortLightError) as caught:
            clean_transport.request("GET", "/api/health", headers={"X-Agent-Token": bad_value})
        assert caught.value.code == "invalid_request"
        assert "secret-token" not in str(caught.value)


def test_http_transport_closes_http_error_and_guards_body_read():
    class TrackingBody(io.BytesIO):
        def __init__(self, body=b"", *, read_error=None):
            super().__init__(body)
            self.read_error = read_error
            self.was_closed = False

        def read(self, size=-1):
            if self.read_error is not None:
                raise self.read_error
            return super().read(size)

        def close(self):
            self.was_closed = True
            super().close()

    broken_body = TrackingBody(b"", read_error=http.client.IncompleteRead(b"partial"))
    broken_error = urllib.error.HTTPError("http://localhost:2100/api/health", 502, "bad gateway", {}, broken_body)
    transport = HttpTransport("http://localhost:2100", opener=CapturingOpener(error=broken_error))
    with pytest.raises(PortLightError) as caught:
        transport.request("GET", "/api/health")
    assert caught.value.code == "server_error"
    assert caught.value.status == 502
    assert broken_body.was_closed is True

    ok_body = TrackingBody(b'{"detail":"reservation request has no active ports"}')
    ok_error = urllib.error.HTTPError("http://localhost:2100/api/reservations/request", 409, "conflict", {}, ok_body)
    transport = HttpTransport("http://localhost:2100", opener=CapturingOpener(error=ok_error))
    with pytest.raises(PortLightError) as caught:
        transport.request("GET", "/api/reservations/request")
    assert caught.value.code == "conflict"
    assert caught.value.status == 409
    assert ok_body.was_closed is True


def test_recovery_request_clears_stale_pending_journal_on_conflict(tmp_path):
    store = ReservationStore(tmp_path / "state")
    url = "https://ports.example.invalid"
    params = {"count": 1, "scope": "self"}
    first_key = None
    with pytest.raises(PortLightError):
        with store.pending_request(url, params) as key:
            first_key = key
            raise PortLightError("unreachable", "response lost")
    request_id = store.pending_requests(url)[0]["id"]

    with pytest.raises(PortLightError) as conflict:
        with store.recovery_request(url, request_id):
            raise PortLightError(
                "conflict",
                "reservation request has no active ports; no new ports were allocated",
                status=409,
            )
    assert conflict.value.code == "conflict"
    assert conflict.value.status == 409
    assert "cleared" in str(conflict.value).lower()
    assert store.pending_requests(url) == []

    with store.pending_request(url, params) as fresh_key:
        assert fresh_key != first_key


def test_discard_pending_request_removes_journal_and_validates_ownership(tmp_path):
    store = ReservationStore(tmp_path / "state")
    url = "https://ports.example.invalid"
    params = {"count": 1, "scope": "self"}
    with pytest.raises(PortLightError):
        with store.pending_request(url, params):
            raise PortLightError("unreachable", "response lost")
    request_id = store.pending_requests(url)[0]["id"]

    with pytest.raises(PortLightError) as wrong_server:
        store.discard_pending_request("https://other.example.invalid", request_id)
    assert wrong_server.value.code == "state_read_failed"
    assert len(store.pending_requests(url)) == 1

    with pytest.raises(PortLightError) as bad_id:
        store.discard_pending_request(url, "not-a-valid-id")
    assert bad_id.value.code == "invalid_request"

    missing_id = "0" * 64
    with pytest.raises(PortLightError) as missing:
        store.discard_pending_request(url, missing_id)
    assert missing.value.code == "not_found"
    assert not (tmp_path / "state" / "requests" / f"{missing_id}.lock").exists()

    with pytest.raises(PortLightError):
        with store.pending_request(url, params):
            with pytest.raises(PortLightError) as locked:
                store.discard_pending_request(url, request_id)
            assert locked.value.code == "reservation_in_progress"
            raise PortLightError("unreachable", "keep journal")

    discarded = store.discard_pending_request("HTTPS://PORTS.EXAMPLE.INVALID/", request_id)
    assert discarded["id"] == request_id
    assert discarded["parameters"] == params
    assert "key" not in discarded
    assert store.pending_requests(url) == []
