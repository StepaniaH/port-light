#!/usr/bin/env python3
"""Minimal Model Context Protocol (MCP) stdio server for Port-Light.

Exposes Port-Light's occupancy data as agent-callable tools:

    doctor           connection and scan diagnostics
    suggest_ports    plan free ports (legacy reservation options supported)
    reserve_ports    reserve expiring ports with privately saved credentials
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
import sys

from port_light_client import PortLightClient, PortLightError, __version__
from port_light_client.client import create_client
from port_light_client.state import ReservationStore

SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-06-18", "2025-11-25")
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]
MAX_MESSAGE_BYTES = 1024 * 1024
SERVER_INFO = {"name": "port-light", "version": __version__}
INSTRUCTIONS = (
    "Port-Light observes the host selected by PORT_LIGHT_URL, which may differ "
    "from the machine running this agent. Use doctor to verify connectivity and "
    "scan readiness. Check existing project ports before changing them. Use "
    "suggest_ports for planning and reserve_ports before starting a new service. "
    "reserve_ports defaults to a one-hour lease and saves release credentials "
    "privately; release_port only needs the port in the same client state directory. "
    "After an uncertain reservation failure, retry identical arguments with the "
    "same URL and state directory to recover, rather than creating a new request. "
    "Release your reservation after stopping or abandoning the service. "
    "Reservations do not bind OS sockets. A failed or stale scan is not evidence "
    "that a port is free. scope=all checks peers but reserves only on this server."
)

TOOLS = [
    {
        "name": "suggest_ports",
        "description": (
            "Plan free network ports on the selected host, skipping anything that is "
            "listening, published by Docker, declared in Compose, or reserved. "
            "With reserve=true the returned ports are claimed as configured so "
            "later calls skip them until released or expired. Save each returned "
            "reservation token for release_port. Prefer reserve_ports for new "
            "reservations with automatic expiry. This does not bind an OS socket."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 64,
                          "description": "How many ports are needed"},
                "start": {"type": "integer", "minimum": 1, "maximum": 65535},
                "end": {"type": "integer", "minimum": 1, "maximum": 65535},
                "rule": {"type": "string", "description": "Named allocation range on this server"},
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
        "description": "Recent degraded-scan events. Use doctor for current readiness; "
                       "an empty event log does not establish scanner health.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "release_port",
        "description": "Release your reservation after stopping or abandoning the service. "
                       "Uses the privately saved token for this server and port. "
                       "Supply token only for a reservation from another client. "
                       "Never deletes manual entries or stops a process.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "token": {"type": "string", "minLength": 1},
            },
            "required": ["port"],
        },
    },
]

# Keep the legacy suggest tool available while making new reservations explicit.
TOOLS.extend([
    {
        "name": "doctor",
        "description": "Check connection, server compatibility and current scan readiness "
                       "without reserving ports. Run after setup or an occupancy error.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reserve_ports",
        "description": "Atomically reserve the full requested count on the selected host. "
                       "Defaults to a one-hour lease. Saves tokens privately; "
                       "release_port needs only the port. Retry identical arguments "
                       "after an uncertain failure to recover the original claim. "
                       "Use a project/service label. Does not bind OS sockets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                key: ({**value, "default": 3600} if key == "ttl" else value)
                for key, value in TOOLS[0]["inputSchema"]["properties"].items()
                if key != "reserve"
            },
        },
    },
])
for tool in TOOLS:
    tool["inputSchema"]["additionalProperties"] = False
    tool["annotations"] = {
        "readOnlyHint": tool["name"] not in {"suggest_ports", "reserve_ports", "release_port"},
        "destructiveHint": tool["name"] == "release_port",
        "openWorldHint": True,
    }


def validate_arguments(name: str, args: object) -> None:
    """Validate our flat tool schemas before any HTTP request or state mutation."""
    schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
    if not isinstance(args, dict):
        raise PortLightError("invalid_request", "arguments must be an object")
    if set(args) - schema["properties"].keys():
        raise PortLightError("invalid_request", "unknown tool argument")
    for key in schema.get("required", []):
        if key not in args:
            raise PortLightError("invalid_request", f"{key} is required")
    types = {"integer": int, "boolean": bool, "string": str}
    for key, value in args.items():
        spec = schema["properties"][key]
        if type(value) is not types[spec["type"]]:
            raise PortLightError("invalid_request", f"{key} must be {spec['type']}")
        if ("minimum" in spec and value < spec["minimum"]
                or "maximum" in spec and value > spec["maximum"]
                or "enum" in spec and value not in spec["enum"]
                or "minLength" in spec and len(value) < spec["minLength"]):
            raise PortLightError("invalid_request", f"{key} is outside its allowed values")


def client() -> PortLightClient:
    return create_client(os.environ)


def run_tool(name: str, args: dict) -> dict:
    if name not in {tool["name"] for tool in TOOLS}:
        raise KeyError(name)
    validate_arguments(name, args)
    port_light = client()
    if name == "doctor":
        return port_light.doctor()

    if name == "reserve_ports":
        parameters = {"count": 1, "start": None, "end": None, "label": "",
                      "ttl": 3600, "scope": "self", **args}
        store = ReservationStore()
        store.ensure_writable()
        with store.pending_request(port_light.base_url, parameters) as key:
            result = port_light.reserve_ports(**parameters, request_key=key)
            for reservation in result.get("reservations", []):
                store.save(port_light.base_url, reservation)
        return {
            **result,
            "reservations": [
                {key: value for key, value in row.items() if key != "token"}
                for row in result.get("reservations", [])
            ],
            "tokens_saved": True,
        }

    if name == "suggest_ports":
        parameters = dict(
            count=int(args.get("count", 1)),
            start=int(args["start"]) if args.get("start") is not None else None,
            end=int(args["end"]) if args.get("end") is not None else None,
            reserve=bool(args.get("reserve")),
            ttl=int(args["ttl"]) if args.get("ttl") is not None else None,
            scope=str(args.get("scope", "self")),
            label=str(args.get("label", "")),
        )
        if args.get("rule") is not None:
            parameters["rule"] = str(args["rule"])
        if parameters["reserve"] or parameters["ttl"] is not None:
            store = ReservationStore()
            store.ensure_writable()
            with store.pending_request(port_light.base_url, parameters) as key:
                result = port_light.suggest_ports(**parameters, request_key=key)
                for reservation in result.get("reservations", []):
                    store.save(port_light.base_url, reservation)
                return result
        return port_light.suggest_ports(**parameters)

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
        port = args["port"]
        store = ReservationStore()
        token = args.get("token") or store.load(port_light.base_url, port)
        if not token:
            raise PortLightError(
                "reservation_token_missing",
                "No saved reservation token for this server and port. Use the same "
                "URL and state directory as the reservation, or supply its token.",
            )
        result = port_light.release_port(port, token)
        try:
            if store.load(port_light.base_url, port) == token:
                store.delete(port_light.base_url, port)
        except PortLightError:
            return {**result, "remote_released": True,
                    "warning": "Reservation released; local token cleanup failed."}
        return result

    raise KeyError(name)


def rpc_error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class McpSession:
    """One stdio connection, including the initialization handshake."""

    def __init__(self) -> None:
        self.protocol_version: str | None = None
        self.ready = False

    def handle_request(self, msg: object) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return rpc_error(None, -32600, "Expected a JSON-RPC 2.0 object")
        has_id = "id" in msg
        msg_id = msg.get("id")
        if has_id and type(msg_id) not in (str, int):
            return rpc_error(None, -32600, "Request id must be a string or integer")
        method = msg.get("method")
        if not isinstance(method, str) or not method:
            # This server sends no requests, so unsolicited responses need no reply.
            if has_id and ("result" in msg or "error" in msg):
                return None
            return rpc_error(msg_id, -32600, "Request method must be a non-empty string")
        if "result" in msg or "error" in msg:
            return rpc_error(msg_id, -32600, "Request must not contain a response") if has_id else None
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return rpc_error(msg_id, -32602, "params must be an object") if has_id else None
        if not has_id:
            if method == "notifications/initialized" and self.protocol_version:
                self.ready = True
            # Never execute a tool carried in a notification, including mutations.
            return None
        if method == "initialize":
            if self.protocol_version:
                return rpc_error(msg_id, -32600, "This connection is already initialized")
            version = params.get("protocolVersion")
            info = params.get("clientInfo")
            if (not isinstance(version, str) or not version
                    or not isinstance(params.get("capabilities"), dict)
                    or not isinstance(info, dict)
                    or any(not isinstance(info.get(key), str) or not info[key] for key in ("name", "version"))):
                return rpc_error(msg_id, -32602, "initialize requires protocolVersion, capabilities and clientInfo")
            self.protocol_version = version if version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if not self.ready:
            return rpc_error(msg_id, -32002, "Complete initialize and notifications/initialized before using tools")
        if method == "tools/list":
            # Annotations entered the protocol in March 2025.
            tools = TOOLS if self.protocol_version != "2024-11-05" else [
                {key: value for key, value in tool.items() if key != "annotations"} for tool in TOOLS
            ]
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in {tool["name"] for tool in TOOLS}:
                return rpc_error(msg_id, -32602, "Unknown tool name")
            try:
                data = run_tool(name, params.get("arguments", {}))
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(data)}]}}
            except PortLightError as exc:
                return {"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": json.dumps({"code": exc.code, "message": str(exc)})}],
                    "isError": True,
                }}
            except Exception:  # noqa: BLE001 — isolate failures without leaking credentials
                return {"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": "Port-Light MCP request failed unexpectedly"}],
                    "isError": True,
                }}
        return rpc_error(msg_id, -32601, "Unknown method")


def _reject_nonfinite(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def serve(stdin, stdout) -> int:
    """Read bounded UTF-8 frames; recover at the next newline after bad input."""
    session = McpSession()
    while raw := stdin.readline(MAX_MESSAGE_BYTES + 1):
        if len(raw) > MAX_MESSAGE_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = stdin.readline(MAX_MESSAGE_BYTES + 1)
            reply = rpc_error(None, -32600, "MCP message exceeds 1 MiB")
        elif not raw.strip():
            continue
        else:
            try:
                msg = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
            except (UnicodeError, ValueError, RecursionError):
                reply = rpc_error(None, -32700, "Invalid UTF-8 JSON message")
            else:
                reply = session.handle_request(msg)
        if reply is not None:
            stdout.write((json.dumps(reply, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8"))
            stdout.flush()
    return 0


def main() -> int:
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    except BrokenPipeError:
        # The host closed its side of the stdio transport.
        return 0


if __name__ == "__main__":
    sys.exit(main())
