"""Port-Light backend — FastAPI app."""

from __future__ import annotations

import sys
import asyncio
import sqlite3
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from port_light_client import __version__

from . import port_rules
from . import agent_events, degradations, doctor, hosts, port_store, themes
from . import settings as app_settings
from .auth import (
    auth_configured,
    basic_auth_middleware,
    hidden_ports_withheld,
    hidden_unlock_configured,
    request_may_see_hidden,
)
from .classification import classify
from .compose_scanner import ComposeScan, scan_compose_tree
from .docker_scanner import HAS_DOCKER, scan_containers
from .occupancy_monitor import OccupancyMonitor, SnapshotUnavailable, complete
from .scan_status import SCANNER_NAMES, ScanUnavailable, enabled_scanners
from .port_scanner import (
    host_listen_trusted,
    listen_scan_source,
    scan_listening_ports,
)

from .routes import configuration, claims, occupancy, peers, diagnostics
from .routes.diagnostics import _event_lines as _event_lines

VERSION = __version__

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


def _path_access(path: Path) -> tuple[bool, bool]:
    """Return existence/readability without exposing the path in diagnostics."""
    try:
        return path.is_dir(), path.is_dir() and os.access(path, os.R_OK | os.X_OK)
    except OSError:
        return False, False


def _data_dir_writable(path: Path) -> bool:
    try:
        if path.exists():
            return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    except OSError:
        return False


def _doctor_document() -> dict:
    values = _values()
    monitor = _monitor.status()
    compose_exists, compose_readable = _path_access(Path(_compose_dir()))
    data_dir = Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data"))
    docker_host = bool(os.environ.get("DOCKER_HOST", "").strip())
    docker_socket = Path("/var/run/docker.sock")
    if docker_host:
        docker_transport = "configured"
    elif docker_socket.exists():
        docker_transport = "socket_readable" if os.access(docker_socket, os.R_OK | os.W_OK) else "socket_denied"
    else:
        docker_transport = "socket_missing"
    return doctor.build_diagnostics({
        "version": VERSION,
        "settings_source": app_settings.settings_source(),
        "settings_readonly": app_settings.settings_readonly(),
        "data_dir_writable": _data_dir_writable(data_dir),
        "enabled_scanners": list(values["local_scanners"]),
        "monitor": monitor,
        "listen_source": listen_scan_source(),
        "listen_trusted": host_listen_trusted(),
        "docker_library_available": HAS_DOCKER,
        "docker_transport": docker_transport,
        "compose_root_available": compose_exists,
        "compose_root_readable": compose_readable,
        "peer_count": len(hosts.list_public_peers()),
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "degradations": degradations.recent(10),
    })


def _metrics_enabled() -> bool:
    return os.environ.get("METRICS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _scan_key(values: dict) -> tuple:
    return (
        os.environ.get("PORT_LIGHT_DATA_DIR", "/data"),
        _compose_dir(),
        tuple(sorted(enabled_scanners(values["local_scanners"]))),
        values["compose_scan_depth"],
        values["compose_scan_max_files"],
        tuple(values["compose_scan_exclude_dirs"]),
        values["guess_urls"],
        values["url_host"],
        values["url_scheme"],
    )


def _build_snapshot(values: dict) -> dict:
    enabled = enabled_scanners(values["local_scanners"])
    snap = {"containers": [], "listening": [], "compose_scan": ComposeScan(), "sources": {}}
    prefer: list[int] = []
    for source, field, scan in (
        ("docker", "containers", scan_containers),
        ("listen", "listening", lambda: scan_listening_ports(prefer_pids=prefer)),
        ("compose", "compose_scan", lambda: scan_compose_tree(
            _compose_dir(), max_depth=values["compose_scan_depth"],
            max_files=values["compose_scan_max_files"],
            exclude_dirs=values["compose_scan_exclude_dirs"])),
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
        raise HTTPException(status_code=503, detail="occupancy scan is incomplete; inspect the scan warning or run port-light doctor, repair the enabled source, then retry")
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

    port_rules.annotate(result["ports"], port_store.get_port_rules())
    if not hidden_locked:
        result["summary"]["compose_diagnostics"] = snap["compose_scan"].diagnostics
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


def _settings_document(body: dict | None = None) -> dict:
    body = body or app_settings.snapshot()
    body["custom_themes"] = themes.list_themes()
    monitor = _monitor.status()
    # Invalid selections resolve to [] so this page remains available for repair.
    # Scanning and allocation still validate through enabled_scanners.
    enabled = set(body["values"]["local_scanners"])
    scanners = []
    for name in SCANNER_NAMES:
        if name not in enabled:
            state = "disabled"
        else:
            observed = monitor["sources"].get(name)
            state = observed if observed in ("ok", "failed") else "checking"
        row = {"id": name, "enabled": name in enabled, "state": state}
        if name == "listen" and state == "ok":
            row["via"] = listen_scan_source()
        scanners.append(row)
    body["local_scanning"] = {
        "ready": bool(enabled) and monitor["ready"],
        "initialized": monitor["initialized"],
        "scan_age_seconds": monitor["scan_age_seconds"],
        "scanners": scanners,
    }
    return body


async def _suggest(count: int, start: int | None, end: int | None, label: str,
                   ttl: int | None, scope: str, require_count: bool,
                   request_key: str | None = None, rule: str | None = None) -> dict:
    values = _values()
    lo = start if start is not None else values["port_range_start"]
    hi = end if end is not None else values["port_range_end"]
    if hi < lo:
        lo, hi = hi, lo
    if rule is not None:
        try:
            lo, hi = port_rules.allocation_range(port_store.get_port_rules(), rule, start, end)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        responses = await hosts.fetch_peers_json(peers, "/api/ports", query)
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
    if request_key is not None:
        parameters = {"count": count, "start": start, "end": end, "label": label,
                      "ttl": ttl, "scope": scope, "require_count": require_count}
        if rule is not None:
            parameters["rule"] = rule
        result, created = await asyncio.to_thread(
            port_store.allocate_reservation, taken, lo, hi, parameters, request_key, scope_label)
        if not created:
            return result
        picks, reservations = result["ports"], result["reservations"]
    else:
        picks, reservations = await asyncio.to_thread(
            port_store.allocate_ports, taken, lo, hi, count, label, None, False, require_count)
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


def _require_hidden_write(request: Request, port: int | None = None) -> None:
    if (port is None or port in port_store.get_hidden_ports()) and not request_may_see_hidden(request):
        raise HTTPException(status_code=403, detail="hidden ports require authorization")


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





# Register static allocation paths before the parameterized /api/ports/{port}.
for domain in (configuration, claims, occupancy, peers, diagnostics):
    domain.runtime = sys.modules[__name__]
    app.include_router(domain.router)
