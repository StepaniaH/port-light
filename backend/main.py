"""Port-Light backend — FastAPI app."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import degradations, hosts, webhooks, port_store
from . import settings as app_settings
from .auth import (
    auth_configured,
    basic_auth_middleware,
    hidden_ports_withheld,
    hidden_unlock_configured,
    request_may_see_hidden,
)
from .classification import classify, free_port_payload
from .compose_scanner import scan_compose_tree
from .docker_scanner import docker_available, scan_containers
from .known_ports import get_known_port
from .port_scanner import (
    host_listen_trusted,
    listen_scan_source,
    scan_listening_ports,
)

VERSION = "0.6.0"

_log_level = os.environ.get("PORT_LIGHT_LOG_LEVEL", "").strip().upper()
if not logging.getLogger("port-light").handlers and not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, _log_level, logging.WARNING) if _log_level else logging.WARNING,
    )

app = FastAPI(title="Port-Light", version=VERSION)


@app.exception_handler(port_store.StoreWriteError)
def _store_write_error(_request: Request, exc: port_store.StoreWriteError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})

_OCC_TTL = 2.0
_STALE_SERVE_AFTER = 4.0
_occ_lock = threading.Lock()
_occ_wait = threading.Condition(_occ_lock)
_occ_snap: dict | None = None
_occ_building = False


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/static/js/"):
        # ES module chunks: revalidate so an upgrade never mixes generations.
        response.headers.setdefault("Cache-Control", "no-cache")
    elif request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


app.middleware("http")(basic_auth_middleware)
app.middleware("http")(security_headers_middleware)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class ManualPortCreate(BaseModel):
    port: int = Field(ge=1, le=65535)
    label: str = ""
    machine: str = "localhost"


class ManualPortUpdate(BaseModel):
    label: str = ""
    machine: str = "localhost"


def _compose_dir() -> str:
    return os.environ.get("COMPOSE_SCAN_DIR", "/compose")


def _values() -> dict:
    values, _ = app_settings.resolve()
    return values


@app.get("/api/meta")
def meta() -> dict:
    values, _ = app_settings.resolve()
    return {
        "version": VERSION,
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "hidden_ports_withheld": hidden_ports_withheld(),
        "settings_readonly": app_settings.settings_readonly(),
        "refresh_ms": values["refresh_ms"],
        "theme": values["theme"],
        "grid_density": values["grid_density"],
    }


@app.get("/api/health")
def health() -> dict:
    compose_dir = _compose_dir()
    source = listen_scan_source()
    trusted = host_listen_trusted()
    return {
        "status": "ok",
        "version": VERSION,
        "auth_required": auth_configured(),
        "scanners": {
            "proc": trusted,
            "listen_source": source if trusted else "none",
            "docker": docker_available(),
            "compose": os.path.isdir(compose_dir),
        },
        "degradations": degradations.recent(5),
    }


def _metrics_enabled() -> bool:
    return os.environ.get("METRICS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


@app.get("/api/metrics")
def metrics() -> Response:
    """Prometheus text exposition over the current occupancy snapshot.

    Disabled unless ``METRICS_ENABLED`` is set. Aggregates only — the
    endpoint never emits ports, names, or URLs. Hidden rows are included in
    the aggregates (they are real occupancy) but never identified.
    """
    if not _metrics_enabled():
        raise HTTPException(status_code=404, detail="not found")
    values = _values()
    snap = _scan_snapshot(values)
    manuals, hidden = snap["user_state"]
    result = classify(
        snap["listening"],
        snap["containers"],
        snap["compose_scan"].ports,
        manuals,
        hidden,
        values["port_range_start"],
        values["port_range_end"],
        True,
        hidden_locked=False,
        options=values,
    )
    summary = result["summary"]
    lines = [
        "# TYPE port_light_up gauge",
        "port_light_up 1",
        "# TYPE port_light_ports gauge",
        f'port_light_ports{{status="used"}} {summary["used"]}',
        f'port_light_ports{{status="configured"}} {summary["configured"]}',
        f'port_light_ports{{status="free"}} {summary["free"]}',
        "# TYPE port_light_hidden gauge",
        f"port_light_hidden {summary['hidden']}",
        "# TYPE port_light_degradations gauge",
        f"port_light_degradations {len(degradations.recent(20))}",
        "# TYPE port_light_compose_files gauge",
        f"port_light_compose_files {snap['compose_scan'].files_scanned}",
        "# TYPE port_light_compose_incomplete gauge",
        f"port_light_compose_incomplete {1 if snap['compose_scan'].incomplete else 0}",
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def _scan_key(values: dict) -> tuple:
    return (
        os.environ.get("PORT_LIGHT_DATA_DIR", "/data"),
        _compose_dir(),
        port_store.store_generation(),
        values["compose_scan_depth"],
        values["compose_scan_max_files"],
        values["guess_urls"],
        values["url_host"],
        values["url_scheme"],
    )


def _scan_snapshot(values: dict) -> dict:
    """Reuse Docker / listen / Compose scans for a couple of seconds.

    Opening `#/port/N` otherwise re-walks the same trees the grid just polled.
    Store writes bump ``store_generation`` so a hide / rename is visible immediately.
    Concurrent polls share one in-flight scan instead of walking twice.
    When a rebuild runs longer than ``_STALE_SERVE_AFTER``, waiters get the
    last good snapshot marked ``stale`` instead of blocking indefinitely.
    """
    global _occ_snap, _occ_building
    key = _scan_key(values)
    now = time.monotonic()
    deadline = now + min(2 * _OCC_TTL, _STALE_SERVE_AFTER)
    with _occ_wait:
        snap = _occ_snap
        if snap and snap["key"] == key and now - snap["at"] < _OCC_TTL:
            return snap
        while _occ_building:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and snap is not None:
                stale = dict(snap)
                stale["stale"] = True
                return stale
            _occ_wait.wait(timeout=0.25 if remaining <= 0 else min(0.25, remaining))
            snap = _occ_snap
            now = time.monotonic()
            if snap and snap["key"] == key and now - snap["at"] < _OCC_TTL:
                return snap
        _occ_building = True
    try:
        containers = scan_containers()
        prefer: list[int] = []
        for c in containers:
            prefer.extend(c.pids or [])
        snap = {
            "at": time.monotonic(),
            "key": key,
            "containers": containers,
            "listening": scan_listening_ports(prefer_pids=prefer),
            "compose_scan": scan_compose_tree(
                _compose_dir(),
                max_depth=values["compose_scan_depth"],
                max_files=values["compose_scan_max_files"],
            ),
            "user_state": port_store.occupancy_user_state(),
            "packed": {},
        }
        with _occ_wait:
            _occ_snap = snap
            return snap
    finally:
        with _occ_wait:
            _occ_building = False
            _occ_wait.notify_all()


def _packed_occupancy(
    request: Request,
    range_start: int | None,
    range_end: int | None,
    include_hidden: bool,
) -> tuple[dict, str, str]:
    values = _values()
    start = range_start if range_start is not None else values["port_range_start"]
    end = range_end if range_end is not None else values["port_range_end"]
    if end < start:
        end = start
    may_see = request_may_see_hidden(request)
    show_hidden = bool(include_hidden and may_see)
    hidden_locked = hidden_ports_withheld() and not may_see
    snap = _scan_snapshot(values)
    stale = bool(snap.get("stale"))
    pkey = (start, end, show_hidden, hidden_locked)
    if not stale:
        # A stale copy shares the memoized results of its source snapshot;
        # those are served as-is only when they can carry the stale marker,
        # i.e. never here — stale requests always re-classify below.
        with _occ_lock:
            packed = snap.get("packed", {}).get(pkey)
            if packed is not None:
                return packed
    manuals, hidden = snap["user_state"]
    result = classify(
        snap["listening"],
        snap["containers"],
        snap["compose_scan"].ports,
        manuals,
        hidden,
        start,
        end,
        show_hidden,
        hidden_locked=hidden_locked,
        options=values,
    )
    result["summary"]["compose_truncated"] = snap["compose_scan"].truncated
    result["summary"]["compose_incomplete"] = snap["compose_scan"].incomplete
    result["summary"]["compose_files"] = snap["compose_scan"].files_scanned
    if not stale:
        webhooks.observe(result["ports"])
    if stale:
        result["summary"]["stale"] = True
        body, etag = _json_etag(result)
        return (result, body, etag)
    body, etag = _json_etag(result)
    packed = (result, body, etag)
    with _occ_lock:
        snap.setdefault("packed", {})[pkey] = packed
    return packed


def _etag_matched(header: str | None, etag: str) -> bool:
    if not header or not etag:
        return False
    want = etag.strip()
    for part in header.split(","):
        token = part.strip()
        if token[:2].lower() == "w/":
            token = token[2:].strip()
        if token == "*" or token == want:
            return True
    return False


def _json_etag(payload: dict) -> tuple[str, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return body, f'"{digest}"'


@app.get("/api/settings")
def get_settings() -> dict:
    return app_settings.snapshot()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)) -> dict:
    try:
        return app_settings.apply_patch(body)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/hosts")
def get_hosts() -> dict:
    try:
        return hosts.catalog()
    except hosts.HostsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/hosts")
def put_hosts(body: dict = Body(...)) -> dict:
    try:
        peers = hosts.replace_peers(body.get("peers") if isinstance(body, dict) else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except hosts.HostsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"local": hosts.public_local(), "peers": peers, "readonly": False}


@app.get("/api/hosts/{host_id}/health")
def get_host_health(host_id: str) -> Response:
    if host_id == hosts.LOCAL_ID:
        return JSONResponse(health())
    return _proxy_peer(host_id, "/api/health", {})


@app.get("/api/hosts/{host_id}/ports")
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


@app.get("/api/hosts/{host_id}/ports/{port}")
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


@app.get("/api/ports")
def get_ports(
    request: Request,
    range_start: int | None = Query(default=None, ge=1, le=65535),
    range_end: int | None = Query(default=None, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> Response:
    _payload, body, etag = _packed_occupancy(request, range_start, range_end, include_hidden)
    headers = {"ETag": etag}
    if _etag_matched(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@app.get("/api/ports/{port}")
def get_port(
    port: int,
    request: Request,
    include_hidden: bool = Query(default=False),
) -> dict:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    may_see = request_may_see_hidden(request)
    show_hidden = bool(include_hidden and may_see)
    hidden_locked = hidden_ports_withheld() and not may_see
    snap = _scan_snapshot(_values())
    with _occ_lock:
        packed_map = dict(snap.get("packed") or {})
    found_visibility = False
    for (_start, _end, sh, hl), packed in packed_map.items():
        if sh != show_hidden or hl != hidden_locked:
            continue
        found_visibility = True
        for row in packed[0]["ports"]:
            if row["port"] == port:
                return row
    if not found_visibility:
        payload, _body, _etag = _packed_occupancy(request, 1, 65535, include_hidden)
        for row in payload["ports"]:
            if row["port"] == port:
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
        manuals, hidden_list = snap["user_state"]
        result = classify(
            snap["listening"],
            snap["containers"],
            snap["compose_scan"].ports,
            manuals,
            hidden_list,
            1,
            65535,
            True,
            hidden_locked=False,
            options=_values(),
        )
        for row in result["ports"]:
            if row["port"] == port:
                return row
        return free_port_payload(port, hidden=True)
    return free_port_payload(port, hidden=False)


@app.get("/api/known-ports/{port}")
def known_port(port: int) -> dict:
    known = get_known_port(port)
    if not known:
        raise HTTPException(status_code=404, detail="unknown port")
    return {"port": port, **known}


@app.get("/api/manual-ports")
def list_manual_ports() -> dict:
    return {"manual_ports": port_store.get_manual_ports()}


@app.post("/api/manual-ports")
def add_manual_port(body: ManualPortCreate) -> dict:
    entry = port_store.add_manual_port(body.port, body.label, body.machine)
    return {"status": "ok", "entry": entry}


@app.patch("/api/manual-ports/{port}")
def patch_manual_port(port: int, body: ManualPortUpdate) -> dict:
    entry = port_store.update_manual_port(port, body.label, body.machine)
    if not entry:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "ok", "entry": entry}


@app.delete("/api/manual-ports/{port}")
def del_manual_port(port: int, machine: str = Query(default="localhost")) -> dict:
    removed = port_store.remove_manual_port(port, machine)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "ok"}


@app.get("/api/hidden")
def list_hidden(request: Request) -> dict:
    if hidden_ports_withheld() and not request_may_see_hidden(request):
        return {"hidden_ports": [], "locked": True}
    return {"hidden_ports": port_store.get_hidden_ports(), "locked": False}


@app.post("/api/hidden/{port}")
def hide_port(port: int) -> dict:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    added = port_store.add_hidden_port(port)
    return {"status": "ok" if added else "already_hidden"}


@app.delete("/api/hidden/{port}")
def unhide_port(port: int) -> dict:
    removed = port_store.remove_hidden_port(port)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "icon.png")


app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")




def _event_lines():
    """SSE frames: hello on connect, then ``refresh`` when occupancy may have changed."""
    last = _scan_key(_values())
    yield "retry: 3000\n\n"
    yield "event: hello\ndata: {}\n\n"
    while True:
        time.sleep(0.5)
        sig = _scan_key(_values())
        if sig != last:
            last = sig
            yield "event: refresh\ndata: {}\n\n"


@app.get("/api/events")
def events() -> Response:
    """Server-sent events: nudges open UIs the moment occupancy may have changed.

    Purely a hint — clients still pull ``GET /api/ports`` (its ETag does the
    real work).
    """
    return StreamingResponse(
        _event_lines(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/free-runs")
def free_runs(
    count: int = Query(default=1, ge=1, le=64),
    start: int | None = Query(default=None, ge=1, le=65535),
    end: int | None = Query(default=None, ge=1, le=65535),
) -> dict:
    """Largest contiguous free-port runs inside a window.

    Read-only planning aid: nothing is reserved here — use the manual-ports
    API to claim a run once chosen.
    """
    values = _values()
    lo = start if start is not None else values["port_range_start"]
    hi = end if end is not None else values["port_range_end"]
    if hi < lo:
        lo, hi = hi, lo
    snap = _scan_snapshot(values)
    manuals, hidden = snap["user_state"]
    result = classify(
        snap["listening"],
        snap["containers"],
        snap["compose_scan"].ports,
        manuals,
        hidden,
        lo,
        hi,
        True,
        hidden_locked=False,
        options=values,
    )
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
        runs.append({"start": run_start, "end": cursor - 1, "size": cursor - run_start})
    runs.sort(key=lambda r: (-r["size"], r["start"]))
    return {"count": count, "start": lo, "end": hi, "runs": runs[:10]}
