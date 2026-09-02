"""Port-Light backend — FastAPI app."""

from __future__ import annotations

import asyncio
import sqlite3
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent_events, degradations, history, hosts, port_store, themes
from . import settings as app_settings
from .auth import (
    auth_configured,
    basic_auth_middleware,
    hidden_ports_withheld,
    hidden_unlock_configured,
    request_may_see_hidden,
    valid_basic_header,
)
from .classification import classify, free_port_payload
from .compose_scanner import ComposeScan, scan_compose_tree
from .docker_scanner import scan_containers
from .known_ports import get_known_port
from .occupancy_monitor import OccupancyMonitor, SnapshotUnavailable, complete
from .scan_status import ScanUnavailable, enabled_scanners
from .port_scanner import (
    listen_scan_source,
    scan_listening_ports,
)

VERSION = "0.7.6"

_log_level = os.environ.get("PORT_LIGHT_LOG_LEVEL", "").strip().upper()
if not logging.getLogger("port-light").handlers and not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, _log_level, logging.WARNING) if _log_level else logging.WARNING,
    )

_monitor: OccupancyMonitor


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _monitor.start()
    try:
        yield
    finally:
        await _monitor.stop()


app = FastAPI(title="Port-Light", version=VERSION, lifespan=_lifespan)


@app.exception_handler(port_store.StoreReadError)
@app.exception_handler(SnapshotUnavailable)
@app.exception_handler(ScanUnavailable)
def _unavailable(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(port_store.StoreWriteError)
def _store_write_error(_request: Request, exc: port_store.StoreWriteError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(port_store.ReservationConflict)
def _reservation_conflict(_request: Request, exc: port_store.ReservationConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})

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
    ttl: int | None = Field(default=None, ge=60, le=604800)


class ManualPortBatch(BaseModel):
    start: int = Field(ge=1, le=65535)
    end: int = Field(ge=1, le=65535)
    label: str = ""


class ManualPortUpdate(BaseModel):
    label: str = ""
    machine: str = "localhost"


def _compose_dir() -> str:
    return os.environ.get("COMPOSE_SCAN_DIR", "/compose")


def _values() -> dict:
    values, _ = app_settings.resolve()
    return values


def _active_leases(request: Request) -> list[dict]:
    now = int(time.time())
    rows = []
    hidden = set(port_store.get_hidden_ports()) if not request_may_see_hidden(request) else set()
    for entry in port_store.get_manual_ports():
        if entry["port"] in hidden:
            continue
        exp = entry.get("expires_at")
        if exp and int(exp) > now:
            rows.append({"port": int(entry["port"]),
                         "label": entry.get("label") or "",
                         "expires_at": int(exp)})
            if entry.get("is_reservation"):
                rows[-1]["is_reservation"] = True
    return rows[:64]


def _listen_port() -> int | None:
    raw = os.environ.get("PORT_LIGHT_PORT", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


@app.get("/api/meta")
def meta(request: Request) -> dict:
    automation = {
        "agent_token": bool(os.environ.get("AGENT_TOKEN", "").strip()),
        "metrics": os.environ.get("METRICS_ENABLED", "").strip().lower()
                   in ("1", "true", "yes", "on"),
        "webhook": bool(os.environ.get("WEBHOOK_URL", "").strip()),
        "history_days": history.retention_days(),
        "events_stream": True,
        "suggest_peers": bool(hosts.list_public_peers()),
        "listen_port": _listen_port(),
    }
    if agent_events.enabled():
        leases = _active_leases(request)
        automation["agent_events"] = {
            **agent_events.summary(),
            "active_leases": len(leases),
            "lease_rows": leases,
        }
    return {
        "version": VERSION,
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "hidden_ports_withheld": hidden_ports_withheld(),
        "settings_readonly": app_settings.settings_readonly(),
        "automation": automation,
    }


@app.get("/api/health")
def health(request: Request) -> dict:
    monitor = _monitor.status()
    sources = monitor["sources"]
    recent = degradations.recent(5)
    if auth_configured() and not valid_basic_header(request.headers.get("authorization") or ""):
        recent = [
            {key: value for key, value in event.items() if key != "scope"}
            for event in recent
        ]
    return {
        "status": "ok" if monitor["ready"] else "degraded",
        "version": VERSION,
        "auth_required": auth_configured(),
        "occupancy": monitor,
        "scanners": {
            "proc": sources.get("listen") == "ok",
            "listen_source": listen_scan_source() if sources.get("listen") == "ok" else "none",
            "docker": sources.get("docker") == "ok",
            "compose": sources.get("compose") == "ok",
        },
        "degradations": recent,
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
    snap = _monitor.latest(values)
    result = _classify_snapshot(
        snap, values, values["port_range_start"], values["port_range_end"])
    summary = result["summary"]
    lines = [
        "# TYPE port_light_up gauge",
        "port_light_up 1",
        "# TYPE port_light_ready gauge",
        f"port_light_ready {int(complete(snap))}",
        "# TYPE port_light_ports gauge",
        f'port_light_ports{{status="used"}} {summary["used"]}',
        f'port_light_ports{{status="configured"}} {summary["configured"]}',
        f'port_light_ports{{status="free"}} {summary["free"] if complete(snap) else "NaN"}',
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
        tuple(sorted(enabled_scanners())),
        values["compose_scan_depth"],
        values["compose_scan_max_files"],
        values["guess_urls"],
        values["url_host"],
        values["url_scheme"],
    )


def _build_snapshot(values: dict) -> dict:
    enabled = enabled_scanners()
    snap = {"containers": [], "listening": [], "compose_scan": ComposeScan(), "sources": {}}
    prefer: list[int] = []
    for source, field, scan in (
        ("docker", "containers", scan_containers),
        ("listen", "listening", lambda: scan_listening_ports(prefer_pids=prefer)),
        ("compose", "compose_scan", lambda: scan_compose_tree(
            _compose_dir(), max_depth=values["compose_scan_depth"],
            max_files=values["compose_scan_max_files"])),
    ):
        if source not in enabled:
            snap["sources"][source] = "disabled"
            continue
        try:
            snap[field] = scan()
            if source == "compose" and (snap[field].incomplete or snap[field].truncated):
                raise ValueError("incomplete Compose scan")
            snap["sources"][source] = "ok"
            if source == "docker":
                for container in snap[field]:
                    prefer.extend(container.pids or [])
        except Exception:
            snap["sources"][source] = "failed"
            degradations.report(source, "scan", "occupancy source unavailable or incomplete")
    return snap


def _allocation_snapshot(values: dict) -> dict:
    snap = _monitor.latest(values)
    if not complete(snap):
        raise HTTPException(status_code=503, detail="occupancy scan is incomplete; retry later")
    return snap


def _scanned_ports(snap: dict, values: dict, lo: int, hi: int) -> set[int]:
    # Stored claims and hidden ports are re-read under the store's write lock.
    result = _classify_snapshot({**snap, "user_state": ([], [])}, values, lo, hi)
    return {row["port"] for row in result["ports"]}


def _classify_snapshot(snap: dict, values: dict, start: int, end: int,
                       show_hidden: bool = True, hidden_locked: bool = False) -> dict:
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

    if not complete(snap):
        result["summary"]["free"] = None
        for row in result["ports"] + result["summary"].get("hidden_occupancy", []):
            if row["status"] == "free":
                row["status"] = "unknown"
    return result


_monitor = OccupancyMonitor(
    values=_values,
    scan_key=_scan_key,
    state_key=lambda: (
        os.environ.get("PORT_LIGHT_DATA_DIR", "/data"),
        port_store.store_revision(),
    ),
    build=_build_snapshot,
    load_state=port_store.occupancy_user_state,
    classify=_classify_snapshot,
)


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
    return _monitor.packed(values, start, end, show_hidden, hidden_locked)


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


@app.get("/api/settings")
def get_settings() -> dict:
    body = app_settings.snapshot()
    body["custom_themes"] = themes.list_themes()
    return body


@app.put("/api/settings")
def put_settings(body: dict = Body(...)) -> dict:
    try:
        result = app_settings.apply_patch(body)
        _monitor.state_changed()
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/custom-themes")
def get_custom_themes() -> dict:
    return {"themes": themes.list_themes()}


@app.post("/api/custom-themes")
def post_custom_theme(body: dict = Body(...)) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    try:
        return themes.add_theme(body)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/custom-themes/{theme_id}")
def put_custom_theme(theme_id: str, body: dict = Body(...)) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    try:
        return themes.update_theme(theme_id, body)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/custom-themes/{theme_id}")
def delete_custom_theme(theme_id: str) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    if not themes.delete_theme(theme_id):
        raise HTTPException(status_code=404, detail="no such theme")
    current, _ = app_settings.resolve()
    if current.get("theme_palette") == "@custom:" + theme_id:
        app_settings.apply_patch({"theme_palette": ""})
    return {"removed": theme_id}


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
    _monitor.state_changed()
    return {"local": hosts.public_local(), "peers": peers, "readonly": False}


@app.get("/api/hosts/{host_id}/health")
def get_host_health(host_id: str, request: Request) -> Response:
    if host_id == hosts.LOCAL_ID:
        return JSONResponse(health(request))
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


@app.get("/api/ports/suggest")
async def suggest_ports(
    request: Request,
    count: int = Query(default=1, ge=1, le=64),
    start: int | None = Query(default=None, ge=1, le=65535),
    end: int | None = Query(default=None, ge=1, le=65535),
    reserve: bool = Query(default=False),
    label: str = Query(default=""),
    ttl: int | None = Query(default=None, ge=60, le=604800),
    scope: str = Query(default="self", pattern="^(self|all)$"),
) -> dict:
    """Suggest free ports, optionally reserving them as manual entries.

    Reserved ports turn amber (configured) on every map and are excluded
    from future suggestions. Each returned reservation carries the capability
    token required by ``DELETE /api/reservations/{n}``.
    ``ttl`` seconds turns the reservation into a lease that expires on its own.
    """
    _require_agent_token(request)
    values = _values()
    lo = start if start is not None else values["port_range_start"]
    hi = end if end is not None else values["port_range_end"]
    if hi < lo:
        lo, hi = hi, lo
    snap = _allocation_snapshot(values)
    taken = _scanned_ports(snap, values, lo, hi)
    scope_label = "self"
    if scope == "all":
        public_peers = hosts.list_public_peers()
        peers = [
            hosts.get_peer(public_peer.get("id", "")) or public_peer
            for public_peer in public_peers
        ]
        query = {
            "range_start": str(lo),
            "range_end": str(hi),
            "include_hidden": "true",
        }
        responses = await asyncio.gather(*(
            asyncio.to_thread(hosts.fetch_peer_json, peer, "/api/ports", query)
            for peer in peers
        ))
        reachable = 0
        for peer, (status, data, _etag) in zip(peers, responses, strict=True):
            summary = data.get("summary", {}) if isinstance(data, dict) else {}
            rows = data.get("ports") if isinstance(data, dict) else None
            complete = (
                status == 200 and isinstance(rows, list) and isinstance(summary, dict)
                and summary.get("scan_complete") is True
                and all(type(summary.get(key)) is bool for key in (
                    "hidden_locked", "compose_incomplete", "compose_truncated"))
                and not any(summary.get(key) for key in (
                    "stale", "hidden_locked", "compose_incomplete", "compose_truncated"))
                and all(isinstance(row, dict) and type(row.get("port")) is int
                        and 1 <= row["port"] <= 65535 for row in rows)
            )
            if complete:
                reachable += 1
                taken.update(row["port"] for row in rows)
            else:
                degradations.report(
                    "suggest", peer.get("name") or peer.get("url", ""),
                    "peer occupancy unavailable or incomplete")
                raise HTTPException(status_code=503, detail="peer occupancy unavailable or incomplete")
        scope_label = f"all:{reachable}/{len(peers)}"
    # Peer I/O may outlast the local snapshot. Revalidate it before claiming.
    values = _values()
    taken.update(_scanned_ports(_allocation_snapshot(values), values, lo, hi))
    picks, reservations = await asyncio.to_thread(
        port_store.allocate_ports, taken, lo, hi, count, label, ttl, reserve or ttl is not None)
    if reservations:
        await asyncio.to_thread(_monitor.state_changed)
    reserved = [entry["port"] for entry in reservations]
    try:
        agent_events.record(len(picks), scope_label, label, bool(reserved))
    except sqlite3.Error:
        # The reservation is already durable: still return its release token.
        degradations.report("history", "agent", "could not record allocation event")
    return {
        "ports": picks,
        "reserved": reserved,
        "failed": [],
        "reservations": reservations,
        "expires_at": reservations[0]["expires_at"] if reservations else None,
        "scope": scope_label,
        "range": {"start": lo, "end": hi},
    }


def _require_agent_token(request: Request) -> None:
    expected = os.environ.get("AGENT_TOKEN", "").strip()
    supplied = request.headers.get("x-agent-token", "")
    if expected and not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="valid X-Agent-Token header required")


@app.delete("/api/reservations/{port}")
def release_reservation(port: int, request: Request) -> dict:
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    token = request.headers.get("x-reservation-token", "")
    if not token:
        raise HTTPException(status_code=403, detail="X-Reservation-Token header required")
    if not port_store.release_reservation(port, token):
        raise HTTPException(status_code=404, detail="reservation not found")
    _monitor.state_changed()
    return {"status": "ok"}




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
    snap = _monitor.latest(_values())
    payload, _body, _etag = _packed_occupancy(request, port, port, include_hidden)
    for row in payload["ports"]:
        if row["port"] == port:
            if row["status"] == "unknown":
                raise HTTPException(status_code=503, detail="occupancy scan is incomplete; retry later")
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
        result = _classify_snapshot(snap, _values(), 1, 65535)
        for row in result["ports"]:
            if row["port"] == port:
                return row
        if not complete(snap):
            raise HTTPException(status_code=503, detail="occupancy scan is incomplete; retry later")
        return free_port_payload(port, hidden=True)
    if not complete(snap):
        raise HTTPException(status_code=503, detail="occupancy scan is incomplete; retry later")
    return free_port_payload(port, hidden=False)


@app.get("/api/ports/{port}/history")
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


@app.get("/api/hosts/{host_id}/ports/{port}/history")
def host_port_history(host_id: str, port: int, request: Request,
                      hours: int = Query(default=24, ge=1, le=720)) -> Response:
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    if host_id == "local":
        return JSONResponse(port_history(port, hours, request))
    return _proxy_peer(host_id, f"/api/ports/{port}/history", {"hours": str(hours)},
                       not_found_ok=True)


def _require_hidden_write(request: Request, port: int | None = None) -> None:
    if (port is None or port in port_store.get_hidden_ports()) and not request_may_see_hidden(request):
        raise HTTPException(status_code=403, detail="hidden ports require authorization")


@app.get("/api/known-ports/{port}")
def known_port(port: int) -> dict:
    known = get_known_port(port)
    if not known:
        raise HTTPException(status_code=404, detail="unknown port")
    return {"port": port, **known}


@app.get("/api/manual-ports")
def list_manual_ports(request: Request) -> dict:
    hidden = set(port_store.get_hidden_ports()) if not request_may_see_hidden(request) else set()
    return {"manual_ports": [entry for entry in port_store.get_manual_ports()
                             if entry["port"] not in hidden]}


@app.post("/api/manual-ports")
def add_manual_port(body: ManualPortCreate, request: Request) -> dict:
    _require_hidden_write(request, body.port)
    entry = port_store.add_manual_port(body.port, body.label, body.machine, body.ttl)
    _monitor.state_changed()
    return {"status": "ok", "entry": entry}


@app.post("/api/manual-ports/batch")
def reserve_manual_range(body: ManualPortBatch) -> dict:
    if body.end < body.start or body.end - body.start >= 64:
        raise HTTPException(status_code=422, detail="select between 1 and 64 contiguous ports")
    values = _values()
    snap = _allocation_snapshot(values)
    picks = port_store.reserve_manual_range(
        _scanned_ports(snap, values, body.start, body.end), body.start, body.end, body.label)
    _monitor.state_changed()
    return {"status": "ok", "ports": picks}


@app.patch("/api/manual-ports/{port}")
def patch_manual_port(port: int, body: ManualPortUpdate, request: Request) -> dict:
    _require_hidden_write(request, port)
    entry = port_store.update_manual_port(port, body.label, body.machine)
    if not entry:
        raise HTTPException(status_code=404, detail="not found")
    _monitor.state_changed()
    return {"status": "ok", "entry": entry}


@app.delete("/api/manual-ports/{port}")
def del_manual_port(port: int, request: Request, machine: str = Query(default="localhost")) -> dict:
    _require_hidden_write(request, port)
    removed = port_store.remove_manual_port(port, machine)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    _monitor.state_changed()
    return {"status": "ok"}


@app.get("/api/hidden")
def list_hidden(request: Request) -> dict:
    if hidden_ports_withheld() and not request_may_see_hidden(request):
        return {"hidden_ports": [], "locked": True}
    return {"hidden_ports": port_store.get_hidden_ports(), "locked": False}


@app.post("/api/hidden/{port}")
def hide_port(port: int, request: Request) -> dict:
    _require_hidden_write(request)
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    added = port_store.add_hidden_port(port)
    if added:
        _monitor.state_changed()
    return {"status": "ok" if added else "already_hidden"}


@app.delete("/api/hidden/{port}")
def unhide_port(port: int, request: Request) -> dict:
    _require_hidden_write(request)
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    removed = port_store.remove_hidden_port(port)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    _monitor.state_changed()
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




async def _event_lines():
    """Broadcast a refresh hint after the monitor accepts a changed snapshot."""
    last = _monitor.sequence()
    yield "retry: 3000\n\n"
    yield "event: hello\ndata: {}\n\n"
    while True:
        sequence, changed = await _monitor.wait_for_change(last, timeout=15.0)
        if changed:
            last = sequence
            yield "event: refresh\ndata: {}\n\n"
        else:
            yield ": keepalive\n\n"


@app.get("/api/events")
def events() -> Response:
    """Server-sent refresh hints for accepted occupancy snapshots."""
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

    Read-only planning aid. POST /api/manual-ports/batch claims a selected run
    atomically after checking the latest occupancy and stored reservations.
    """
    values = _values()
    lo = start if start is not None else values["port_range_start"]
    hi = end if end is not None else values["port_range_end"]
    if hi < lo:
        lo, hi = hi, lo
    snap = _allocation_snapshot(values)
    _manuals, hidden = snap["user_state"]
    result = _classify_snapshot(snap, values, lo, hi)
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
