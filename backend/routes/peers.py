"""Peers API."""
from __future__ import annotations
from fastapi import Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from .. import hosts
from fastapi import APIRouter
from .diagnostics import health
from .occupancy import get_ports, get_port, port_history
from types import ModuleType
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import re

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


@router.get("/api/hosts/{host_id}/health")
def get_host_health(host_id: str, request: Request) -> Response:
    if host_id == hosts.LOCAL_ID:
        return JSONResponse(health(request))
    return _proxy_peer(host_id, "/api/health", {})


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
    query: dict[str, str] = {"include_hidden": "true" if include_hidden else "false"}
    if range_start is not None:
        query["range_start"] = str(range_start)
    if range_end is not None:
        query["range_end"] = str(range_end)
    return _proxy_peer(host_id, "/api/ports", query, request.headers.get("if-none-match"))


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
    query = {"include_hidden": "true" if include_hidden else "false"}
    return _proxy_peer(host_id, f"/api/ports/{port}", query, not_found_ok=True)


def _proxy_peer(
    host_id: str,
    path: str,
    query: dict[str, str],
    if_none_match: str | None = None,
    *,
    not_found_ok: bool = False,
) -> Response:
    try:
        peer = hosts.get_peer(host_id)
    except hosts.HostsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not peer:
        raise HTTPException(status_code=404, detail="unknown host")
    status, data, etag = hosts.fetch_peer_json(peer, path, query, if_none_match)
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


@router.get("/api/hosts/{host_id}/ports/{port}/history")
def host_port_history(host_id: str, port: int, request: Request,
                      hours: int = Query(default=24, ge=1, le=720)) -> Response:
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    if host_id == "local":
        return JSONResponse(port_history(port, hours, request))
    return _proxy_peer(host_id, f"/api/ports/{port}/history", {"hours": str(hours)},
                       not_found_ok=True)
