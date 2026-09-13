"""Smoke-test an installed wheel and a repeated install outside the source tree."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("python", type=Path, help="Python in a disposable venv containing the wheel")
    parser.add_argument("wheel", type=Path, help="the same wheel to reinstall without network access")
    args = parser.parse_args()
    python = str(args.python.absolute())  # Keep the venv symlink.
    wheel = str(args.wheel.resolve())
    cli = str(args.python.absolute().with_name("port-light.exe" if os.name == "nt" else "port-light"))
    mcp = str(args.python.absolute().with_name("port-light-mcp.exe" if os.name == "nt" else "port-light-mcp"))
    with tempfile.TemporaryDirectory(prefix="port-light-install-check-") as directory:
        root = Path(directory)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("PORT_LIGHT_") and key not in ("PYTHONPATH", "AGENT_TOKEN")}
        environment.update(PORT_LIGHT_URL="https://ports.example.invalid/instance",
                           PORT_LIGHT_STATE_DIR=str(root / "private state"),
                           PORT_LIGHT_AUTH="test:secret-not-for-output")

        def run(command, *, input=None):
            return subprocess.run(command, input=input, text=True, capture_output=True, check=True,
                                  cwd=root, env=environment, timeout=60).stdout

        def configurations():
            codex = run([cli, "mcp-config", "--client", "codex"])
            claude = run([cli, "mcp-config", "--client", "claude-code"])
            assert "secret-not-for-output" not in codex + claude
            first = tomllib.loads(codex)["mcp_servers"]["port-light"]
            second = json.loads(claude)["mcpServers"]["port-light"]
            assert first["command"] == second["command"]
            assert Path(first["command"]).parent.samefile(Path(python).parent)
            assert first["args"] == second["args"] == ["-m", "port_light_client.mcp"]
            return codex, claude

        before_config = configurations()
        run([python, "-c", "from port_light_client.state import ReservationStore; "
             "ReservationStore().save('https://ports.example.invalid/instance', "
             "{'port': 45000, 'token': 'private-release-fixture', 'expires_at': None})"])
        before_state = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")}
        assert before_state
        run([python, "-m", "pip", "install", "--no-index", "--force-reinstall", wheel])
        assert configurations() == before_config
        assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")} == before_state
        run([python, "-c", "from port_light_client.state import ReservationStore; "
             "assert ReservationStore().load('https://ports.example.invalid/instance', 45000) "
             "== 'private-release-fixture'"])
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "installed-wheel-smoke", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        # Test both the public entry point and the command written into AI config.
        for command in ([mcp], [python, "-m", "port_light_client.mcp"]):
            output = run(command, input="".join(json.dumps(row) + "\n" for row in messages))
            replies = [json.loads(line) for line in output.splitlines()]
            assert [row["id"] for row in replies] == [1, 2]
            assert replies[0]["result"]["protocolVersion"] == "2025-11-25"
            assert {"doctor", "reserve_ports", "release_port"} <= {
                tool["name"] for tool in replies[1]["result"]["tools"]}
    print("Installed wheel: configuration, MCP handshake and state-preserving reinstall passed.")


if __name__ == "__main__":
    main()
