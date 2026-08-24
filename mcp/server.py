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

Implements the subset of the MCP stdio transport needed for tools:
initialize, notifications/initialized, ping, tools/list, tools/call.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "port-light", "version": "0.1.0"}

BASE_URL = os.environ.get("PORT_LIGHT_URL", "http://127.0.0.1:2100").rstrip("/")
BASIC_AUTH = os.environ.get("PORT_LIGHT_AUTH", "")

TOOLS = [
    {
        "name": "suggest_ports",
        "description": (
            "Return free network ports on the host, skipping anything that is "
            "listening, published by Docker, declared in Compose, or reserved. "
            "With reserve=true the returned ports are claimed as configured so "
            "later calls never hand out the same ports again."
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
        "name": "release_port",
        "description": "Remove a previous reservation for a host port.",
        "inputSchema": {
            "type": "object",
            "properties": {"port": {"type": "integer", "minimum": 1, "maximum": 65535}},
            "required": ["port"],
        },
    },
]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def api_get(path: str) -> dict:
    req = urllib.request.Request(BASE_URL + path)
    if BASIC_AUTH:
        token = base64.b64encode(BASIC_AUTH.encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, f"Port-Light returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(502, f"cannot reach Port-Light at {BASE_URL}") from exc


def api_delete(path: str) -> None:
    req = urllib.request.Request(BASE_URL + path, method="DELETE")
    if BASIC_AUTH:
        token = base64.b64encode(BASIC_AUTH.encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
        resp.read(1024)


def trim_row(row: dict) -> dict:
    names = [c.get("name") for c in row.get("containers") or [] if c.get("name")]
    known = row.get("known_service") or {}
    return {
        "port": row.get("port"),
        "status": row.get("status"),
        "protocol": row.get("protocol"),
        "bind_scope": row.get("bind_scope"),
        "names": names or ([known["name"]] if known.get("name") else []),
    }


def run_tool(name: str, args: dict) -> dict:
    if name == "suggest_ports":
        qs = [f"count={int(args.get('count', 1))}"]
        if args.get("start") is not None:
            qs.append(f"start={int(args['start'])}")
        if args.get("end") is not None:
            qs.append(f"end={int(args['end'])}")
        if args.get("reserve"):
            qs.append("reserve=true")
        label = str(args.get("label", ""))
        if label:
            from urllib.parse import quote
            qs.append("label=" + quote(label))
        return api_get("/api/ports/suggest?" + "&".join(qs))

    if name == "check_port":
        row = api_get(f"/api/ports/{int(args['port'])}?include_hidden=false")
        return trim_row(row)

    if name == "list_occupancy":
        start = int(args.get("start", 1))
        end = int(args.get("end", 9999))
        limit = int(args.get("limit", 200))
        data = api_get(f"/api/ports?range_start={start}&range_end={end}&include_hidden=false")
        rows = [trim_row(r) for r in data.get("ports", [])[:limit]]
        return {"summary": data.get("summary"), "ports": rows}

    if name == "port_history":
        hours = int(args.get("hours", 24))
        return api_get(f"/api/ports/{int(args['port'])}/history?hours={hours}")

    if name == "release_port":
        api_delete(f"/api/manual-ports/{int(args['port'])}")
        return {"released": int(args["port"])}

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
        except ApiError as exc:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": str(exc)}],
                               "isError": True}}
        except Exception as exc:  # noqa: BLE001 — report, never crash the loop
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
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
