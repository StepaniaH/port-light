#!/usr/bin/env python3
"""Minimal Model Context Protocol (MCP) stdio server for Port-Light.

Exposes Port-Light's occupancy data as agent-callable tools:

    suggest_ports    find free ports, optionally reserving them
    check_port       status of one port on this host
    list_occupancy   compact occupancy map for a range
    port_history     recent state transitions for one port
    release_port     drop a previous reservation

Configuration (environment):

    PORT_LIGHT_URL    base URL of a Port-Light instance
                      (default http://127.0.0.1:2100)
    PORT_LIGHT_AUTH   optional "user:password" for Basic Auth
    PORT_LIGHT_AGENT_TOKEN  optional token matching the server's AGENT_TOKEN

Implements the subset of the MCP stdio transport needed for tools:
initialize, notifications/initialized, ping, tools/list, tools/call.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

# Keep the documented `python /path/to/mcp/server.py` entry point working even
# when the caller's current directory is outside the repository.
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from port_light_client import PortLightClient, PortLightError, __version__
from port_light_client.client import create_client

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "port-light", "version": __version__}

TOOLS = [
    {
        "name": "suggest_ports",
        "description": (
            "Return free network ports on the host, skipping anything that is "
            "listening, published by Docker, declared in Compose, or reserved. "
            "With reserve=true the returned ports are claimed as configured so "
            "later calls skip them until released or expired. Save each returned "
            "reservation token for release_port. This does not bind an OS socket."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 64,
                          "description": "How many ports are needed"},
                "start": {"type": "integer", "minimum": 1, "maximum": 65535},
                "end": {"type": "integer", "minimum": 1, "maximum": 65535},
                "reserve": {"type": "boolean", "default": False},
                "label": {"type": "string",
                          "description": "Label stored with reserved ports"},
                "ttl": {"type": "integer", "minimum": 60, "maximum": 604800,
                        "description": "Turn reservations into leases that "
                                       "expire after this many seconds"},
                "scope": {"type": "string", "enum": ["self", "all"],
                          "default": "self",
                          "description": "all also avoids ports occupied on peers"},
            },
        },
    },
    {
        "name": "check_port",
        "description": "Status of a single port: used, configured or free.",
        "inputSchema": {
            "type": "object",
            "properties": {"port": {"type": "integer", "minimum": 1, "maximum": 65535}},
            "required": ["port"],
        },
    },
    {
        "name": "list_occupancy",
        "description": "Compact occupancy map (port, status, name) for a range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "minimum": 1, "maximum": 65535},
                "end": {"type": "integer", "minimum": 1, "maximum": 65535},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                          "default": 200},
            },
        },
    },
    {
        "name": "port_history",
        "description": "Recent state transitions for one port (needs history enabled).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "hours": {"type": "integer", "minimum": 1, "maximum": 720,
                          "default": 24},
            },
            "required": ["port"],
        },
    },
    {
        "name": "list_degradations",
        "description": "Recent degraded-scan events (Docker unreachable, "
                       "unreadable Compose file, ...). Empty list means every "
                       "scanner is healthy.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "release_port",
        "description": "Release your reservation using its returned token. Never deletes manual entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "token": {"type": "string", "minLength": 1},
            },
            "required": ["port", "token"],
        },
    },
]


def client() -> PortLightClient:
    return create_client(os.environ)


def run_tool(name: str, args: dict) -> dict:
    port_light = client()
    if name == "suggest_ports":
        return port_light.suggest_ports(
            count=int(args.get("count", 1)),
            start=int(args["start"]) if args.get("start") is not None else None,
            end=int(args["end"]) if args.get("end") is not None else None,
            reserve=bool(args.get("reserve")),
            ttl=int(args["ttl"]) if args.get("ttl") is not None else None,
            scope=str(args.get("scope", "self")),
            label=str(args.get("label", "")),
        )

    if name == "check_port":
        return port_light.check_port(int(args["port"]))

    if name == "list_occupancy":
        return port_light.list_occupancy(
            start=int(args.get("start", 1)),
            end=int(args.get("end", 9999)),
            limit=int(args.get("limit", 200)),
        )

    if name == "port_history":
        return port_light.port_history(
            int(args["port"]),
            hours=int(args.get("hours", 24)),
        )

    if name == "list_degradations":
        return {"degradations": port_light.health().get("degradations", [])}

    if name == "release_port":
        token = str(args.get("token") or "")
        if not token:
            raise PortLightError(
                "reservation_token_missing",
                "release_port requires the reservation token returned by suggest_ports",
            )
        return port_light.release_port(int(args["port"]), token)

    raise KeyError(name)


def handle_request(msg: dict) -> dict | None:
    """Route one incoming JSON-RPC message. Returns None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        client_version = (msg.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            data = run_tool(name, args)
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text",
                                            "text": json.dumps(data)}]}}
        except KeyError:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        except PortLightError as exc:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": str(exc)}],
                               "isError": True}}
        except Exception:  # noqa: BLE001 — keep the loop alive without exposing internals
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text",
                                             "text": "Port-Light MCP request failed unexpectedly"}],
                               "isError": True}}

    if method in ("notifications/initialized", "initialized"):
        return None

    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"unknown method: {method}"}}
    return None


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": f"parse error: {exc}"}}) + "\n")
            sys.stdout.flush()
            continue
        reply = handle_request(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
