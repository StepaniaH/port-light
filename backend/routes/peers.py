"""Peers API."""
from __future__ import annotations

import re
from types import ModuleType

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import hosts
from .diagnostics import health
from .occupancy import get_port, get_ports, port_history

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()


class PeerConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(default="", pattern=r"^(?:[a-z0-9]{8,16})?$")
    url: str = Field(min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=hosts.MAX_USER)
    password: str | None = Field(default=None, max_length=hosts.MAX_PASSWORD)
    clear_auth: bool = False


@router.post("/api/hosts/test")
def test_host_connection(body: dict = Body(...)) -> Response:
    if hosts.hosts_readonly():
        raise HTTPException(status_code=403, detail="peer configuration is read-only")
    try:
        values = PeerConnectionTest.model_validate(body).model_dump(exclude_none=True)
        peer = hosts.connection_test_peer(values)
    except ValidationError:
        return JSONResponse({"status": "failed", "code": "invalid_input"}, status_code=422)
    except (hosts.HostsError, ValueError) as exc:
        code = "credentials_required" if str(exc) == "credentials_required" else "invalid_url"
        return JSONResponse({"status": "failed", "code": code}, status_code=422)
    status, data, _ = hosts.fetch_peer_json(peer, "/api/meta", {})
    if status in (401, 403):
        code = "peer_auth"
    elif status == 504:
        code = "peer_timeout"
    elif status != 200:
        code = "peer_unreachable"
    elif (not isinstance(data, dict) or not isinstance(data.get("version"), str)
          or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", data["version"])
          or len(data["version"]) > 64
          or not isinstance(data.get("capabilities"), dict)
          or data["capabilities"].get("port_observation") != 1):
        code = "peer_incompatible"
    else:
        return JSONResponse({"status": "connected", "version": data["version"]})
    return JSONResponse({"status": "failed", "code": code}, status_code=502)


@router.get("/api/hosts")
def get_hosts() -> dict:
    try:
        return hosts.catalog()
    except hosts.HostsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/hosts")
def put_hosts(body: dict = Body(...)) -> dict:
    try:
        hosts.replace_peers(body.get("peers") if isinstance(body, dict) else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except hosts.HostsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime._monitor.state_changed()
    return hosts.catalog()


def _resolve_peer(host_id: str) -> dict:
    try:
        peer = hosts.get_peer(host_id)
    except hosts.HostsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not peer:
        raise HTTPException(status_code=404, detail="unknown host")
    return peer


def _peer_response(
    status: int,
    data: dict | None,
    etag: str | None,
    *,
    not_found_ok: bool = False,
) -> Response:
    headers = {}
    if etag:
        headers["ETag"] = etag
    if status == 304:
        return Response(status_code=304, headers=headers)
    if status == 200 and data is not None:
        return JSONResponse(data, headers=headers)
    if not_found_ok and status == 404:
        raise HTTPException(status_code=404, detail="not found")
    if status in (401, 403):
        raise HTTPException(status_code=502, detail="peer authentication failed")
    raise HTTPException(status_code=502, detail="peer unreachable")


@router.get("/api/hosts/{host_id}/health")
def get_host_health(host_id: str, request: Request) -> Response:
    if host_id == hosts.LOCAL_ID:
        return JSONResponse(health(request))
    peer = _resolve_peer(host_id)
    status, data, etag = hosts.fetch_peer_json(peer, "/api/health", {}, None)
    if status == 200 and data is not None:
        try:
            normalized = hosts.normalize_peer_health_response(data)
        except hosts.HostsError as exc:
            raise HTTPException(status_code=502, detail="invalid peer response") from exc
        if normalized != data:
            etag = None
        data = normalized
        if not runtime.request_may_see_hidden(request):
            if isinstance(data.get("degradations"), list):
                data = {
                    **data,
                    "degradations": [
                        {key: value for key, value in event.items() if key != "scope"}
                        if isinstance(event, dict) else event
                        for event in data["degradations"]
                    ],
                }
            etag = None
    return _peer_response(status, data, etag)


@router.get("/api/hosts/{host_id}/ports")
def get_host_ports(
    host_id: str,
    request: Request,
    range_start: int | None = Query(default=None, ge=1, le=65535),
    range_end: int | None = Query(default=None, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> Response:
    if host_id == hosts.LOCAL_ID:
        return get_ports(request, range_start, range_end, include_hidden)
    may_see = runtime.request_may_see_hidden(request)
    allow_hidden = bool(include_hidden and may_see)
    query: dict[str, str] = {"include_hidden": "true" if allow_hidden else "false"}
    if range_start is not None:
        query["range_start"] = str(range_start)
    if range_end is not None:
        query["range_end"] = str(range_end)
    peer = _resolve_peer(host_id)
    if_none_match = request.headers.get("if-none-match") if may_see else None
    status, data, etag = hosts.fetch_peer_json(peer, "/api/ports", query, if_none_match)
    if status == 200 and data is not None:
        try:
            normalized = hosts.normalize_peer_ports_response(data)
        except hosts.HostsError as exc:
            raise HTTPException(status_code=502, detail="invalid peer response") from exc
        redacted = normalized != data
        data = normalized
        if not allow_hidden:
            filtered = [
                row for row in data["ports"]
                if not row.get("is_hidden")
            ]
            if len(filtered) != len(data["ports"]):
                redacted = True
            data = {**data, "ports": filtered}
        if not may_see:
            redacted = True
            summary = dict(data["summary"])
            prev_hidden = summary.get("hidden")
            summary["hidden"] = 0
            if isinstance(summary.get("free"), int):
                if (
                    summary.get("scan_complete") is True
                    and isinstance(summary.get("range_start"), int)
                    and isinstance(summary.get("range_end"), int)
                ):
                    r_start, r_end = summary["range_start"], summary["range_end"]
                    visible_in_range = sum(
                        1 for row in data["ports"] if r_start <= row["port"] <= r_end
                    )
                    summary["free"] = max(0, r_end - r_start + 1 - visible_in_range)
                elif isinstance(prev_hidden, int) and prev_hidden > 0:
                    summary["free"] = min(65535, summary["free"] + prev_hidden)
            summary["hidden_ports"] = []
            summary["hidden_occupancy"] = []
            summary.pop("compose_diagnostics", None)
            if runtime.hidden_ports_withheld():
                summary["hidden_locked"] = True
            data = {**data, "summary": summary}
        if redacted:
            etag = None
    return _peer_response(status, data, etag)


@router.get("/api/hosts/{host_id}/ports/{port}")
def get_host_port(
    host_id: str,
    port: int,
    request: Request,
    include_hidden: bool = Query(default=False),
) -> Response:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    if host_id == hosts.LOCAL_ID:
        return JSONResponse(get_port(port, request, include_hidden))
    may_see = runtime.request_may_see_hidden(request)
    locked = runtime.hidden_ports_withheld() and not may_see
    allow_hidden = bool(include_hidden and may_see)
    query = {"include_hidden": "true" if allow_hidden else "false"}
    peer = _resolve_peer(host_id)
    status, data, etag = hosts.fetch_peer_json(peer, f"/api/ports/{port}", query, None)
    if status == 200 and data is not None:
        try:
            normalized = hosts.normalize_peer_port_response(data, expected_port=port)
        except hosts.HostsError as exc:
            raise HTTPException(status_code=502, detail="invalid peer response") from exc
        if normalized != data:
            etag = None
        data = normalized
        if (not allow_hidden and data.get("is_hidden")) or (locked and data.get("status") == "free"):
            raise HTTPException(status_code=404, detail="not found")
    return _peer_response(status, data, etag, not_found_ok=True)


def _proxy_peer(
    host_id: str,
    path: str,
    query: dict[str, str],
    if_none_match: str | None = None,
    *,
    not_found_ok: bool = False,
) -> Response:
    peer = _resolve_peer(host_id)
    status, data, etag = hosts.fetch_peer_json(peer, path, query, if_none_match)
    return _peer_response(status, data, etag, not_found_ok=not_found_ok)


@router.get("/api/hosts/{host_id}/ports/{port}/history")
def host_port_history(host_id: str, port: int, request: Request,
                      hours: int = Query(default=24, ge=1, le=720)) -> Response:
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    if host_id == hosts.LOCAL_ID:
        return JSONResponse(port_history(port, hours, request))
    peer = _resolve_peer(host_id)
    if runtime.hidden_ports_withheld() and not runtime.request_may_see_hidden(request):
        check_status, check_data, _ = hosts.fetch_peer_json(
            peer, f"/api/ports/{port}", {"include_hidden": "false"}, None,
        )
        if check_status == 404:
            raise HTTPException(status_code=404, detail="not found")
        if check_status in (401, 403):
            raise HTTPException(status_code=502, detail="peer authentication failed")
        if check_status != 200 or not isinstance(check_data, dict):
            raise HTTPException(status_code=502, detail="peer unreachable")
        try:
            check_data = hosts.normalize_peer_port_response(check_data, expected_port=port)
        except hosts.HostsError as exc:
            raise HTTPException(status_code=502, detail="invalid peer response") from exc
        if check_data.get("is_hidden") or check_data.get("status") == "free":
            raise HTTPException(status_code=404, detail="not found")
    status, data, etag = hosts.fetch_peer_json(
        peer, f"/api/ports/{port}/history", {"hours": str(hours)}, None,
    )
    if status == 200 and data is not None:
        try:
            normalized = hosts.normalize_peer_history_response(data, expected_port=port)
        except hosts.HostsError as exc:
            raise HTTPException(status_code=502, detail="invalid peer response") from exc
        if normalized != data:
            etag = None
        data = normalized
    return _peer_response(status, data, etag, not_found_ok=True)
