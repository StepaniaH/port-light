from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tomllib

import pytest

from port_light_client import integration
from port_light_client.cli import main
from port_light_client.client import PortLightError
from port_light_client.state import ReservationStore


@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_generated_config_is_parseable_private_and_repeatable(client, tmp_path, monkeypatch):
    python = str(tmp_path / 'tool "环境"' / "bin" / "python")
    monkeypatch.setattr(integration, "launch_command", lambda: [python, "-m", "port_light_client.mcp"])
    state = tmp_path / "persistent state"
    output = io.StringIO()
    arguments = ["--url", "https://ports.example/port-light/", "mcp-config", "--client", client]
    env = {"PORT_LIGHT_AUTH": "secret-password", "PORT_LIGHT_AGENT_TOKEN": "secret-token",
           "PORT_LIGHT_STATE_DIR": str(state)}
    assert main(arguments, environ=env, stdout=output) == 0
    text = output.getvalue()
    assert "secret-password" not in text and "secret-token" not in text
    assert not state.exists()  # Generating a config creates no state or AI settings.
    config = tomllib.loads(text)["mcp_servers"] if client == "codex" else json.loads(text)["mcpServers"]
    entry = config["port-light"]
    assert entry["command"] == python
    assert entry["env"]["PORT_LIGHT_URL"] == "https://ports.example/port-light"
    assert entry["env"]["PORT_LIGHT_STATE_DIR"] == str(state)
    if client == "codex":
        assert "PORT_LIGHT_AUTH" in entry["env_vars"]
    else:
        assert entry["env"]["PORT_LIGHT_AUTH"] == "${PORT_LIGHT_AUTH:-}"
    again = io.StringIO()
    assert main(arguments, environ=env, stdout=again) == 0
    assert again.getvalue() == text


def test_launch_keeps_virtualenv_python_symlink(tmp_path, monkeypatch):
    interpreter = tmp_path / "bin" / "python"
    interpreter.parent.mkdir()
    interpreter.symlink_to("/usr/bin/python3")
    monkeypatch.setattr(integration.sys, "executable", str(interpreter))
    assert integration.launch_command()[0] == str(interpreter)


@pytest.mark.parametrize("failure,code", [
    (subprocess.TimeoutExpired("adapter", 1), "verification_timeout"),
    (OSError("secret path"), "mcp_start_failed"),
    (SimpleNamespace(returncode=1, stdout="secret output", stderr="secret error"), "mcp_invalid_response"),
])
def test_verification_failures_are_actionable_and_redacted(tmp_path, monkeypatch, failure, code):
    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(integration.subprocess, "run", run)
    store = ReservationStore(tmp_path)
    environment = integration.connection_environment("http://localhost:2100", store, {})
    with pytest.raises(PortLightError) as caught:
        integration.verify_integration(environment, {}, store)
    assert caught.value.code == code
    assert "secret" not in str(caught.value)


def test_verification_reports_agent_gate_failure_without_claiming_registration(tmp_path, monkeypatch):
    def run(*args, **kwargs):
        messages = [json.loads(line) for line in kwargs["input"].splitlines()]
        assert [row.get("params", {}).get("name") for row in messages[-2:]] == ["doctor", "suggest_ports"]
        assert messages[-1]["params"]["arguments"] == {"count": 1, "scope": "self"}
        assert kwargs["env"]["PORT_LIGHT_AUTH"] == "private-password"
        results = [
            {"protocolVersion": integration.PROTOCOL_VERSION},
            {"tools": [{"name": name} for name in ("doctor", "reserve_ports", "release_port", "suggest_ports")]},
            {"content": [{"text": json.dumps({"overall": "healthy", "context": {"version": "0.8.3"}})}]},
            {"isError": True, "content": [{"text": json.dumps({"code": "authentication_failed", "message": "Invalid agent token"})}]},
        ]
        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps({
            "jsonrpc": "2.0", "id": i, "result": result,
        }) for i, result in enumerate(results, 1)))
    monkeypatch.setattr(integration.subprocess, "run", run)
    store = ReservationStore(tmp_path)
    env = integration.connection_environment("https://ports.example", store, {})
    result = integration.verify_integration(env, {"PORT_LIGHT_AUTH": "private-password"}, store)
    assert not result["ready"]
    assert result["ai_registration"] == "not_checked"
    assert result["checks"][-1]["code"] == "authentication_failed"
    assert "private-password" not in json.dumps(result)
    assert not list((Path(store.root) / "reservations").iterdir())
