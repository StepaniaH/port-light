"""Client configuration and read-only checks for a local AI integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

from . import __version__
from .client import PortLightError
from .mcp import PROTOCOL_VERSION
from .state import ReservationStore

SECRET_VARIABLES = ("PORT_LIGHT_AUTH", "PORT_LIGHT_AGENT_TOKEN", "AGENT_TOKEN")


def launch_command() -> list[str]:
    # Resolving a venv's Python symlink would bypass its installed packages.
    python = os.path.abspath(sys.executable)
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "mcp" / "server.py"
    if wrapper.is_file() and ((root / "pyproject.toml").is_file() or (root / "backend" / "main.py").is_file()):
        return [python, str(wrapper)]
    return [python, "-m", "port_light_client.mcp"]


def connection_environment(
    url: str, store: ReservationStore, environ: Mapping[str, str],
    *, timeout: float | None = None, ca_file: str | None = None,
) -> dict[str, str]:
    values = {
        "PORT_LIGHT_URL": url,
        "PORT_LIGHT_STATE_DIR": str(Path(store.root).expanduser().absolute()),
        "PORT_LIGHT_TIMEOUT": str(timeout if timeout is not None else environ.get("PORT_LIGHT_TIMEOUT", "5")),
    }
    ca = ca_file if ca_file is not None else environ.get("PORT_LIGHT_CA_FILE")
    if ca:
        values["PORT_LIGHT_CA_FILE"] = str(Path(ca).expanduser().absolute())
    return values


def mcp_configuration(client: str, environment: dict[str, str]) -> dict[str, Any]:
    command, *args = launch_command()
    server = {"command": command, "args": args, "env": environment}
    if client == "codex":
        lines = ["[mcp_servers.port-light]", f"command = {json.dumps(command, ensure_ascii=False)}",
                 f"args = {json.dumps(args, ensure_ascii=False)}",
                 f"env_vars = {json.dumps(SECRET_VARIABLES)}", "", "[mcp_servers.port-light.env]"]
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in environment.items())
        return {"client": client, "format": "toml", "config": "\n".join(lines) + "\n"}
    if client == "claude-code":
        server["env"] = {**environment, **{key: "${" + key + ":-}" for key in SECRET_VARIABLES}}
        return {"client": client, "format": "json",
                "config": json.dumps({"mcpServers": {"port-light": server}}, indent=2, ensure_ascii=False) + "\n"}
    raise PortLightError("invalid_request", "supported AI clients: codex, claude-code")


def verify_integration(environment: dict[str, str], environ: Mapping[str, str], store: ReservationStore) -> dict:
    """Exercise the actual adapter, without reserving ports or registering an AI tool."""
    store.ensure_writable()
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "port-light-verify", "version": __version__},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "doctor", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "suggest_ports", "arguments": {"count": 1, "scope": "self"},
        }},
    ]
    try:
        result = subprocess.run(
            launch_command(), input="".join(json.dumps(row) + "\n" for row in messages),
            capture_output=True, text=True, encoding="utf-8",
            env={**environ, **environment}, timeout=4 * float(environment["PORT_LIGHT_TIMEOUT"]) + 10,
        )
    except subprocess.TimeoutExpired as exc:
        raise PortLightError("verification_timeout", "MCP verification timed out; check the target URL and timeout") from exc
    except (OSError, UnicodeError) as exc:
        raise PortLightError("mcp_start_failed", "Could not launch the installed MCP adapter; repair the client installation") from exc
    try:
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        if result.returncode or [row["id"] for row in rows] != [1, 2, 3, 4]:
            raise ValueError("Unexpected MCP replies")
        if any(row["jsonrpc"] != "2.0" or "error" in row for row in rows):
            raise ValueError("MCP protocol error")
        if rows[0]["result"]["protocolVersion"] != PROTOCOL_VERSION:
            raise ValueError("Unexpected protocol version")
        names = {tool["name"] for tool in rows[1]["result"]["tools"]}
        if not {"doctor", "reserve_ports", "release_port", "suggest_ports"} <= names:
            raise ValueError("Missing tools")
        checks = [{"id": "mcp", "status": "pass", "detail": "Adapter started; handshake and tool discovery passed"},
                  {"id": "state", "status": "pass", "detail": "Reservation state directory is writable"}]
        server_version = None
        for row, name in zip(rows[2:], ("doctor", "agent_access"), strict=True):
            reply = row["result"]
            data = json.loads(reply["content"][0]["text"])
            if reply.get("isError"):
                checks.append({"id": name, "status": "fail", "code": data["code"], "detail": data["message"]})
            elif name == "doctor":
                server_version = data["context"]["version"]
                healthy = data["overall"] == "healthy"
                checks.append({"id": name, "status": "pass" if healthy else "warning",
                               "detail": "Instance diagnostics are healthy" if healthy else
                               "Instance needs attention; run port-light doctor for causes"})
            else:
                checks.append({"id": name, "status": "pass", "detail": "Read-only suggestion request succeeded; no ports reserved"})
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        # Never print subprocess stderr or malformed tool output: either may contain secrets.
        raise PortLightError("mcp_invalid_response", "The adapter returned invalid MCP output; repair the client installation") from exc
    return {
        "target_url": environment["PORT_LIGHT_URL"], "client_version": __version__,
        "server_version": server_version, "checks": checks,
        "ready": all(check["status"] == "pass" for check in checks),
        "ai_registration": "not_checked",
        "next_step": "Reload the AI tool, inspect its MCP status, and call doctor there to verify that it loaded this connection",
    }
