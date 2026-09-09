"""Occupancy API."""
from __future__ import annotations
import sqlite3
from fastapi import HTTPException, Query, Request
from fastapi.responses import Response
from .. import degradations, history, port_store
from ..auth import (
    request_may_see_hidden,
)
from ..classification import free_port_payload
from ..known_ports import get_known_port
from ..occupancy_monitor import complete
from fastapi import APIRouter
from types import ModuleType

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()

@router.get("/api/ports")
def get_ports(
    request: Request,
    range_start: int | None = Query(default=None, ge=1, le=65535),
    range_end: int | None = Query(default=None, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> Response:
    _payload, body, etag = runtime._packed_occupancy(request, range_start, range_end, include_hidden)
    headers = {"ETag": etag}
    if runtime._etag_matched(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/api/ports/{port}")
def get_port(
    port: int,
    request: Request,
    include_hidden: bool = Query(default=False),
) -> dict:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    may_see = request_may_see_hidden(request)
    show_hidden = bool(include_hidden and may_see)
    snap = runtime._monitor.latest(runtime._values())
    payload, _body, _etag = runtime._packed_occupancy(request, port, port, include_hidden)
    for row in payload["ports"]:
        if row["port"] == port:
            if row["status"] == "unknown":
                raise HTTPException(status_code=503, detail="occupancy scan is incomplete; inspect the scan warning or run port-light doctor, repair the enabled source, then retry")
            return row
    hidden: set[int] = set()
    for raw in snap["user_state"][1]:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 65535:
            hidden.add(n)
    if port in hidden:
        if not show_hidden:
            raise HTTPException(status_code=404, detail="not found")
        result = runtime._classify_snapshot(snap, runtime._values(), 1, 65535)
        for row in result["ports"]:
            if row["port"] == port:
                return row
        if not complete(snap):
            raise HTTPException(status_code=503, detail="occupancy scan is incomplete; inspect the scan warning or run port-light doctor, repair the enabled source, then retry")
        return free_port_payload(port, hidden=True)
    if not complete(snap):
        raise HTTPException(status_code=503, detail="occupancy scan is incomplete; inspect the scan warning or run port-light doctor, repair the enabled source, then retry")
    return free_port_payload(port, hidden=False)


@router.get("/api/ports/{port}/history")
def port_history(
    port: int,
    hours: int = Query(default=24, ge=1, le=720),
    request: Request = None,
) -> dict:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    if not history.enabled():
        raise HTTPException(status_code=404, detail="not found")
    if port in port_store.get_hidden_ports() and not request_may_see_hidden(request):
        raise HTTPException(status_code=404, detail="not found")
    try:
        events = history.query(port, hours)
    except sqlite3.Error as exc:
        degradations.report("history", "history.db", "occupancy history read failed")
        raise HTTPException(status_code=503, detail="history is temporarily unavailable") from exc
    return {"port": port, "events": events}


@router.get("/api/known-ports/{port}")
def known_port(port: int) -> dict:
    known = get_known_port(port)
    if not known:
        raise HTTPException(status_code=404, detail="unknown port")
    return {"port": port, **known}
