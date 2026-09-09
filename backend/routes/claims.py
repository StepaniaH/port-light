"""Claims API."""
from __future__ import annotations
import asyncio
import re
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, Field
from .. import port_store
from ..auth import (
    hidden_ports_withheld,
    request_may_see_hidden,
)
from fastapi import APIRouter
from types import ModuleType

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()

class ManualPortCreate(BaseModel):
    port: int = Field(ge=1, le=65535)
    label: str = ""
    machine: str = "localhost"
    ttl: int | None = Field(default=None, ge=60, le=604800)


class ManualPortBatch(BaseModel):
    start: int = Field(ge=1, le=65535)
    end: int = Field(ge=1, le=65535)
    label: str = ""


class ManualPortUpdate(BaseModel):
    label: str = ""
    machine: str = "localhost"


class ReservationCreate(BaseModel):
    model_config = {"extra": "forbid"}
    count: int = Field(default=1, ge=1, le=64)
    start: int | None = Field(default=None, ge=1, le=65535)
    end: int | None = Field(default=None, ge=1, le=65535)
    label: str = Field(default="", max_length=256)
    ttl: int | None = Field(default=None, ge=60, le=604800)
    scope: str = Field(default="self", pattern="^(self|all)$")
    require_count: bool = True
    rule: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("/api/ports/suggest")
async def suggest_ports(
    request: Request,
    count: int = Query(default=1, ge=1, le=64),
    start: int | None = Query(default=None, ge=1, le=65535),
    end: int | None = Query(default=None, ge=1, le=65535),
    reserve: bool = Query(default=False),
    label: str = Query(default=""),
    ttl: int | None = Query(default=None, ge=60, le=604800),
    scope: str = Query(default="self", pattern="^(self|all)$"),
    require_count: bool = Query(default=False),
    rule: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict:
    """Read-only planning; mutations require the recoverable POST interface."""
    runtime._require_agent_token(request)
    if reserve or ttl is not None:
        raise HTTPException(status_code=405, detail="use POST /api/reservations with an Idempotency-Key; upgrade CLI/MCP clients")
    return await runtime._suggest(count, start, end, label, ttl, scope, require_count, rule=rule)


def _reservation_key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):
        raise HTTPException(status_code=400, detail="Idempotency-Key must be a retained random URL-safe secret of 43–128 characters")
    return key


@router.post("/api/reservations")
async def create_reservation(body: ReservationCreate, request: Request) -> dict:
    runtime._require_agent_token(request)
    if body.start is not None and body.end is not None and body.start > body.end:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start")
    key = _reservation_key(request)
    parameters = body.model_dump(exclude_none=False)
    if parameters["rule"] is None:
        parameters.pop("rule")
    existing = await asyncio.to_thread(port_store.reservation_request, key, parameters)
    if existing is not None:
        return existing
    return await runtime._suggest(**parameters, request_key=key)


@router.get("/api/reservations/request")
def recover_reservation(request: Request) -> dict:
    runtime._require_agent_token(request)
    result = port_store.reservation_request(_reservation_key(request))
    if result is None:
        raise HTTPException(status_code=404, detail="reservation request not found")
    return result


@router.delete("/api/reservations/{port}")
def release_reservation(port: int, request: Request) -> dict:
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    token = request.headers.get("x-reservation-token", "")
    if not token:
        raise HTTPException(status_code=403, detail="X-Reservation-Token header required")
    if not port_store.release_reservation(port, token):
        raise HTTPException(status_code=404, detail="reservation not found")
    runtime._monitor.state_changed()
    return {"status": "ok"}


@router.get("/api/reservations")
def list_reservations(request: Request) -> dict:
    return {"reservations": list_manual_ports(request)["manual_ports"]}


@router.get("/api/manual-ports")
def list_manual_ports(request: Request) -> dict:
    hidden = set(port_store.get_hidden_ports()) if not request_may_see_hidden(request) else set()
    return {"manual_ports": [entry for entry in port_store.get_manual_ports()
                             if entry["port"] not in hidden]}


@router.post("/api/manual-ports")
def add_manual_port(body: ManualPortCreate, request: Request) -> dict:
    runtime._require_hidden_write(request, body.port)
    entry = port_store.add_manual_port(body.port, body.label, body.machine, body.ttl)
    runtime._monitor.state_changed()
    return {"status": "ok", "entry": entry}


@router.post("/api/manual-ports/batch")
def reserve_manual_range(body: ManualPortBatch) -> dict:
    if body.end < body.start or body.end - body.start >= 64:
        raise HTTPException(status_code=422, detail="select between 1 and 64 contiguous ports")
    values = runtime._values()
    snap = runtime._allocation_snapshot(values)
    picks = port_store.reserve_manual_range(
        runtime._scanned_ports(snap, values, body.start, body.end), body.start, body.end, body.label)
    runtime._monitor.state_changed()
    return {"status": "ok", "ports": picks}


@router.patch("/api/manual-ports/{port}")
def patch_manual_port(port: int, body: ManualPortUpdate, request: Request) -> dict:
    runtime._require_hidden_write(request, port)
    entry = port_store.update_manual_port(port, body.label, body.machine)
    if not entry:
        raise HTTPException(status_code=404, detail="not found")
    runtime._monitor.state_changed()
    return {"status": "ok", "entry": entry}


@router.delete("/api/manual-ports/{port}")
def del_manual_port(port: int, request: Request, machine: str = Query(default="localhost")) -> dict:
    runtime._require_hidden_write(request, port)
    removed = port_store.remove_manual_port(port, machine)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    runtime._monitor.state_changed()
    return {"status": "ok"}


@router.get("/api/hidden")
def list_hidden(request: Request) -> dict:
    if hidden_ports_withheld() and not request_may_see_hidden(request):
        return {"hidden_ports": [], "locked": True}
    return {"hidden_ports": port_store.get_hidden_ports(), "locked": False}


@router.post("/api/hidden/{port}")
def hide_port(port: int, request: Request) -> dict:
    runtime._require_hidden_write(request)
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    added = port_store.add_hidden_port(port)
    if added:
        runtime._monitor.state_changed()
    return {"status": "ok" if added else "already_hidden"}


@router.delete("/api/hidden/{port}")
def unhide_port(port: int, request: Request) -> dict:
    runtime._require_hidden_write(request)
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    removed = port_store.remove_hidden_port(port)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    runtime._monitor.state_changed()
    return {"status": "ok"}


@router.get("/api/free-runs")
def free_runs(
    count: int = Query(default=1, ge=1, le=64),
    start: int | None = Query(default=None, ge=1, le=65535),
    end: int | None = Query(default=None, ge=1, le=65535),
) -> dict:
    """Largest contiguous free-port runs inside a window.

    Read-only planning aid. POST /api/manual-ports/batch claims a selected run
    atomically after checking the latest occupancy and stored reservations.
    """
    values = runtime._values()
    lo = start if start is not None else values["port_range_start"]
    hi = end if end is not None else values["port_range_end"]
    if hi < lo:
        lo, hi = hi, lo
    snap = runtime._allocation_snapshot(values)
    _manuals, hidden = snap["user_state"]
    result = runtime._classify_snapshot(snap, values, lo, hi)
    taken = {row["port"] for row in result["ports"]}
    taken.update(hidden)
    runs: list[dict] = []
    cursor = lo
    while cursor <= hi:
        if cursor in taken:
            cursor += 1
            continue
        run_start = cursor
        while cursor <= hi and cursor not in taken:
            cursor += 1
        if cursor - run_start >= count:
            runs.append({"start": run_start, "end": cursor - 1, "size": cursor - run_start})
    runs.sort(key=lambda r: (-r["size"], r["start"]))
    return {"count": count, "start": lo, "end": hi, "runs": runs[:10]}
