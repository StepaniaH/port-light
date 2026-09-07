from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from port_light_client import PortLightError
from port_light_client.cli import _client_from_environment, main, parse_duration


DOCTOR = {
    "overall": "healthy",
    "counts": {"pass": 7, "warning": 0, "fail": 0, "info": 0},
    "context": {"version": "0.8.0"},
    "checks": [],
}


class FakeClient:
    base_url = "http://nas.lan:2100"

    def __init__(self):
        self.calls = []
        self.doctor_result = DOCTOR
        self.check_result = {
            "port": 5432,
            "status": "free",
            "protocol": "tcp",
            "bind_scope": "unknown",
            "names": [],
        }
        self.reserve_result = {
            "ports": [8000],
            "reservations": [{"port": 8000, "token": "release-me", "expires_at": 123}],
            "scope": "self",
            "range": {"start": 1, "end": 9999},
        }
        self.meta_result = {"automation": {"suggest_peers": False}}

    def doctor(self):
        self.calls.append(("doctor",))
        return self.doctor_result

    def check_port(self, port):
        self.calls.append(("check_port", port))
        return self.check_result

    def reserve_ports(self, **kwargs):
        self.calls.append(("reserve_ports", kwargs))
        return self.reserve_result

    def release_port(self, port, token):
        self.calls.append(("release_port", port, token))
        return {"released": port}

    def meta(self):
        self.calls.append(("meta",))
        return self.meta_result


class FakeStore:
    root = "/private/state"

    def __init__(self):
        self.tokens = {}
        self.writable_checks = 0

    def ensure_writable(self):
        self.writable_checks += 1

    def save(self, base_url, reservation):
        self.tokens[(base_url, reservation["port"])] = reservation["token"]

    def load(self, base_url, port):
        return self.tokens.get((base_url, port))

    def delete(self, base_url, port):
        self.tokens.pop((base_url, port), None)


def invoke(argv, *, client=None, store=None, environ=None, stdin=""):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        argv,
        client=client or FakeClient(),
        store=store or FakeStore(),
        environ=environ or {},
        stdin=io.StringIO(stdin),
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize(("value", "seconds"), [
    ("60", 60),
    ("10m", 600),
    ("1h", 3600),
    ("0.5d", 43200),
    ("7d", 604800),
])
def test_duration_parser(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["59", "8d", "forever", "1.5s"])
def test_duration_parser_rejects_invalid_values(value):
    with pytest.raises(Exception):
        parse_duration(value)


def test_client_configuration_precedence_is_option_then_environment(monkeypatch):
    captured = {}

    def make_client(environ, **kwargs):
        captured.update(environ=environ, **kwargs)
        return object()

    monkeypatch.setattr("port_light_client.cli.create_client", make_client)
    args = SimpleNamespace(
        url="https://option.example",
        timeout=9,
        ca_file="/option/ca.pem",
    )
    _client_from_environment(args, {
        "PORT_LIGHT_URL": "https://environment.example",
        "PORT_LIGHT_TIMEOUT": "4",
        "PORT_LIGHT_CA_FILE": "/environment/ca.pem",
        "PORT_LIGHT_AUTH": "operator:secret",
        "PORT_LIGHT_AGENT_TOKEN": "specific-agent-token",
        "AGENT_TOKEN": "fallback-agent-token",
    })
    assert captured == {
        "environ": {
            "PORT_LIGHT_URL": "https://environment.example",
            "PORT_LIGHT_TIMEOUT": "4",
            "PORT_LIGHT_CA_FILE": "/environment/ca.pem",
            "PORT_LIGHT_AUTH": "operator:secret",
            "PORT_LIGHT_AGENT_TOKEN": "specific-agent-token",
            "AGENT_TOKEN": "fallback-agent-token",
        },
        "base_url": "https://option.example",
        "timeout": 9,
        "ca_file": "/option/ca.pem",
    }


def test_client_configuration_uses_environment_then_defaults(monkeypatch):
    captured = []

    def make_client(environ, **kwargs):
        captured.append({"environ": environ, **kwargs})
        return object()

    monkeypatch.setattr("port_light_client.cli.create_client", make_client)
    _client_from_environment(SimpleNamespace(), {"AGENT_TOKEN": "fallback"})
    assert captured == [{
        "environ": {"AGENT_TOKEN": "fallback"},
        "base_url": None,
        "timeout": None,
        "ca_file": None,
    }]


def test_doctor_human_and_json_exit_on_health():
    code, output, error = invoke(["doctor"])
    assert code == 0
    assert output.startswith("Port-Light 0.8.0: healthy")
    assert error == ""

    client = FakeClient()
    client.doctor_result = {
        **DOCTOR,
        "overall": "attention",
        "counts": {"pass": 5, "warning": 1, "fail": 1, "info": 0},
        "checks": [
            {"id": "docker", "status": "fail", "detail": "access_failed"},
        ],
    }
    code, output, _ = invoke(["doctor", "--json"], client=client)
    payload = json.loads(output)
    assert code == 1
    assert payload["ok"] is False
    assert payload["doctor"]["overall"] == "attention"


def test_check_is_an_assertion_that_the_port_is_free():
    code, output, _ = invoke(["check", "5432"])
    assert code == 0
    assert "Port 5432 is free" in output

    client = FakeClient()
    client.check_result = {**client.check_result, "status": "used", "names": ["postgres"]}
    code, output, _ = invoke(["--json", "check", "5432"], client=client)
    payload = json.loads(output)
    assert code == 1
    assert payload["ok"] is False
    assert payload["port"]["names"] == ["postgres"]


def test_reserve_defaults_to_one_hour_self_scope_and_saves_token():
    client = FakeClient()
    store = FakeStore()
    code, output, error = invoke(
        ["reserve", "--start", "8000", "--label", "preview"],
        client=client,
        store=store,
    )
    assert code == 0
    assert "Reserved 1 port with scope self" in output
    assert "release-me" not in output
    assert error == ""
    assert store.writable_checks == 1
    assert store.tokens[(client.base_url, 8000)] == "release-me"
    call = next(call for call in client.calls if call[0] == "reserve_ports")
    assert call[1]["ttl"] == 3600
    assert call[1]["scope"] == "self"


def test_reserve_json_supports_no_expiry_and_stateless_tokens():
    client = FakeClient()
    store = FakeStore()
    code, output, _ = invoke(
        ["reserve", "--json", "--no-expiry", "--no-save", "--scope", "all"],
        client=client,
        store=store,
    )
    payload = json.loads(output)
    assert code == 0
    assert payload["reservations"][0]["token"] == "release-me"
    assert payload["tokens_saved"] is False
    assert store.writable_checks == 0
    call = next(call for call in client.calls if call[0] == "reserve_ports")
    assert call[1]["ttl"] is None
    assert call[1]["scope"] == "all"


def test_reserve_default_scope_warns_when_peers_exist():
    client = FakeClient()
    client.meta_result = {"automation": {"suggest_peers": True}}
    code, _, error = invoke(["reserve"], client=client)
    assert code == 0
    assert "use --scope all" in error

    client.calls.clear()
    code, _, error = invoke(["reserve", "--scope", "self"], client=client)
    assert code == 0
    assert error == ""
    assert ("meta",) not in client.calls


def test_reserve_scope_environment_is_explicit_and_validated():
    client = FakeClient()
    code, _, error = invoke(
        ["reserve"],
        client=client,
        environ={"PORT_LIGHT_SCOPE": "all"},
    )
    assert code == 0
    assert error == ""
    call = next(call for call in client.calls if call[0] == "reserve_ports")
    assert call[1]["scope"] == "all"

    code, _, error = invoke(
        ["reserve"],
        client=FakeClient(),
        environ={"PORT_LIGHT_SCOPE": "fleet"},
    )
    assert code == 2
    assert "must be self or all" in error


def test_partial_reservation_is_preserved_but_returns_operational_failure():
    client = FakeClient()
    code, output, error = invoke(["reserve", "--count", "2"], client=client)
    assert code == 3
    assert "Reserved 1 port" in output
    assert "only 1 of 2" in error


def test_no_save_requires_json_before_contacting_server():
    client = FakeClient()
    code, _, error = invoke(["reserve", "--no-save"], client=client)
    assert code == 2
    assert "requires --json" in error
    assert client.calls == []


def test_argument_errors_are_json_when_requested_and_never_contact_server():
    client = FakeClient()
    code, output, error = invoke(
        ["reserve", "--json", "--ttl", "30s"],
        client=client,
    )
    payload = json.loads(output)
    assert code == 2
    assert error == ""
    assert payload == {
        "schema_version": 1,
        "ok": False,
        "command": "reserve",
        "error": {
            "code": "invalid_arguments",
            "message": "argument --ttl: must be between 60 seconds and 7 days",
        },
    }
    assert client.calls == []


def test_long_options_are_not_silently_abbreviated():
    code, output, error = invoke(["--js", "doctor"])
    assert code == 2
    assert output == ""
    assert "unrecognized arguments: --js" in error


def test_release_prefers_environment_then_deletes_local_record():
    client = FakeClient()
    store = FakeStore()
    store.tokens[(client.base_url, 8000)] = "stored-token"
    code, output, _ = invoke(
        ["release", "8000"],
        client=client,
        store=store,
        environ={"PORT_LIGHT_RESERVATION_TOKEN": "environment-token"},
    )
    assert code == 0
    assert "Released reservation" in output
    assert ("release_port", 8000, "environment-token") in client.calls
    assert store.tokens == {}


def test_release_can_read_token_from_stdin():
    client = FakeClient()
    code, output, _ = invoke(
        ["release", "8000", "--token-stdin", "--json"],
        client=client,
        stdin="stdin-token\n",
    )
    assert code == 0
    assert json.loads(output)["released"] == 8000
    assert ("release_port", 8000, "stdin-token") in client.calls


def test_release_reports_remote_success_when_local_cleanup_fails():
    client = FakeClient()
    store = FakeStore()
    store.tokens[(client.base_url, 8000)] = "stored-token"

    def fail(*_):
        raise PortLightError("state_write_failed", "read-only state")

    store.delete = fail
    code, output, error = invoke(
        ["release", "8000", "--json"],
        client=client,
        store=store,
    )
    payload = json.loads(output)
    assert code == 3
    assert error == ""
    assert payload["ok"] is False
    assert payload["released"] == 8000
    assert payload["remote_released"] is True


def test_errors_use_stable_json_and_exit_category():
    client = FakeClient()

    def unavailable(_port):
        raise PortLightError(
            "occupancy_unavailable",
            "occupancy scan is incomplete; retry later",
            status=503,
        )

    client.check_port = unavailable
    code, output, error = invoke(["check", "5432", "--json"], client=client)
    payload = json.loads(output)
    assert code == 3
    assert error == ""
    assert payload["schema_version"] == 1
    assert payload["error"] == {
        "code": "occupancy_unavailable",
        "message": "occupancy scan is incomplete; retry later",
        "status": 503,
    }


def test_unexpected_failures_keep_json_stable_without_leaking_details():
    client = FakeClient()

    def broken(_port):
        raise RuntimeError("sensitive implementation detail")

    client.check_port = broken
    code, output, error = invoke(["check", "5432", "--json"], client=client)
    payload = json.loads(output)
    assert code == 3
    assert error == ""
    assert payload["error"]["code"] == "internal_error"
    assert "sensitive" not in output


def test_storage_failure_after_remote_reservation_returns_recovery_tokens():
    client = FakeClient()
    store = FakeStore()

    def fail(*_):
        raise PortLightError("state_write_failed", "disk full")

    store.save = fail
    code, output, error = invoke(["reserve"], client=client, store=store)
    payload = json.loads(output)
    assert code == 3
    assert payload["recovery_required"] is True
    assert payload["reservations"][0]["token"] == "release-me"
    assert "save the recovery JSON" in error

    code, output, error = invoke(["reserve", "--json"], client=client, store=store)
    assert code == 3
    assert json.loads(output)["recovery_required"] is True
    assert error == ""
