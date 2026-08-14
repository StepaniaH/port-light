"""Port-Light backend — FastAPI app."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import port_store, settings as app_settings
from .auth import (
    auth_configured,
    basic_auth_middleware,
    hidden_ports_withheld,
    hidden_unlock_configured,
    request_may_see_hidden,
)
from .compose_scanner import scan_compose_files
from .docker_scanner import docker_available, scan_containers
from .known_ports import get_known_port
from .port_scanner import host_proc_available, scan_listening_ports

VERSION = "0.5.2"

app = FastAPI(title="Port-Light", version=VERSION)
app.middleware("http")(basic_auth_middleware)

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
    return {
        "status": "ok",
        "version": VERSION,
        "auth_required": auth_configured(),
        "scanners": {
            "proc": host_proc_available(),
            "docker": docker_available(),
            "compose": os.path.isdir(compose_dir),
        },
    }


def _occupancy(
    request: Request,
    range_start: int | None,
    range_end: int | None,
    include_hidden: bool,
) -> dict:
    values = _values()
    start = range_start if range_start is not None else values["port_range_start"]
    end = range_end if range_end is not None else values["port_range_end"]
    if end < start:
        end = start
    may_see = request_may_see_hidden(request)
    show_hidden = bool(include_hidden and may_see)
    return _classify(
        scan_listening_ports(),
        scan_containers(),
        scan_compose_files(
            _compose_dir(),
            max_depth=values["compose_scan_depth"],
            max_files=values["compose_scan_max_files"],
        ),
        port_store.get_manual_ports(),
        port_store.get_hidden_ports(),
        start,
        end,
        show_hidden,
        hidden_locked=hidden_ports_withheld() and not may_see,
        options=values,
    )


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


@app.get("/api/ports")
def get_ports(
    request: Request,
    range_start: int | None = Query(default=None, ge=1, le=65535),
    range_end: int | None = Query(default=None, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> dict:
    return _occupancy(request, range_start, range_end, include_hidden)


@app.get("/api/ports/{port}")
def get_port(
    port: int,
    request: Request,
    include_hidden: bool = Query(default=False),
) -> dict:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    payload = _occupancy(request, 1, 65535, include_hidden)
    for row in payload["ports"]:
        if row["port"] == port:
            return row
    if port in port_store.get_hidden_ports() and payload["summary"]["hidden_locked"]:
        raise HTTPException(status_code=404, detail="not found")
    known = get_known_port(port)
    return {
        "port": port,
        "status": "free",
        "source_type": "unknown",
        "known_service": known,
        "is_hidden": False,
        "conflict": False,
        "urls": [],
        "containers": [],
        "compose_configs": [],
    }


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
    return FileResponse(_FRONTEND_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "icon.png")


app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


def _classify(
    listening: list,
    containers: list,
    compose_ports: list,
    manual_ports: list[dict],
    hidden_ports: list[int],
    range_start: int,
    range_end: int,
    include_hidden: bool,
    hidden_locked: bool,
    options: dict | None = None,
) -> dict:
    listening_map: dict[int, dict] = {}
    inode_to_port: dict[int, int] = {}
    for lp in listening:
        if lp.inode:
            inode_to_port[lp.inode] = lp.port
        rec = listening_map.get(lp.port)
        if rec is None:
            listening_map[lp.port] = {
                "protocol": _proto_label([lp.protocol]),
                "protocols": [lp.protocol],
                "ip": lp.ip,
                "ips": [lp.ip] if lp.ip else [],
                "process": lp.process_name,
                "pid": lp.pid,
            }
            continue
        if lp.protocol and lp.protocol not in rec["protocols"]:
            rec["protocols"].append(lp.protocol)
            rec["protocol"] = _proto_label(rec["protocols"])
        if lp.ip and lp.ip not in rec["ips"]:
            rec["ips"].append(lp.ip)
        if lp.process_name and not rec["process"]:
            rec["process"] = lp.process_name
            rec["pid"] = lp.pid

    container_map: dict[int, list[dict]] = {}

    def _add_container(port: int, c, extra: dict | None = None) -> None:
        payload = {
            "name": c.name,
            "status": c.status,
            "image": c.image,
            "compose_project": c.compose_project,
            "compose_service": c.compose_service,
            "network_mode": c.network_mode,
            "urls": list(c.urls or []),
        }
        if extra:
            payload.update(extra)
        lst = container_map.setdefault(port, [])
        if any(x["name"] == c.name for x in lst):
            return
        lst.append(payload)

    for c in containers:
        for p in c.ports:
            _add_container(p["host_port"], c)
        if c.network_mode == "host" and c.socket_inodes:
            for inode in c.socket_inodes:
                port = inode_to_port.get(inode)
                if port:
                    _add_container(port, c)

    compose_map: dict[int, list[dict]] = {}
    for cp in compose_ports:
        compose_map.setdefault(cp.port, []).append({
            "project_dir": cp.project_dir,
            "service_name": cp.service_name,
            "compose_file": cp.compose_file,
            "container_port": cp.container_port,
            "protocol": cp.protocol,
        })

    manual_map: dict[int, dict] = {}
    for mp in manual_ports:
        manual_map[mp["port"]] = {
            "label": mp.get("label", ""),
            "machine": mp.get("machine", "localhost"),
        }

    all_ports = set(listening_map) | set(container_map) | set(compose_map) | set(manual_map)

    port_list: list[dict] = []
    for port in sorted(all_ports):
        if port in hidden_ports and not include_hidden:
            continue

        lp_info = listening_map.get(port)
        ctors = container_map.get(port, [])
        composes = compose_map.get(port, [])
        manual = manual_map.get(port)

        is_listening = port in listening_map
        has_running = any(c["status"] == "running" for c in ctors)
        is_manual = manual is not None

        if is_listening or has_running:
            status = "used"
        elif composes or is_manual:
            status = "configured"
        else:
            status = "free"

        if ctors:
            source_type = "docker"
        elif is_listening and not ctors:
            known = get_known_port(port)
            if known and known.get("category") == "system":
                source_type = "system"
            else:
                source_type = "host"
        elif composes:
            source_type = "docker"
        elif is_manual:
            source_type = "manual"
        else:
            source_type = "unknown"

        known = get_known_port(port)
        ips = (lp_info.get("ips") if lp_info else None) or []
        if not ips:
            ips = [lp_info["ip"] if lp_info else "0.0.0.0"]
        ip = lp_info["ip"] if lp_info else ips[0]
        urls = _collect_urls(port, ips, ctors, known, options)

        port_list.append({
            "port": port,
            "status": status,
            "source_type": source_type,
            "protocol": lp_info["protocol"] if lp_info else (composes[0].get("protocol") if composes else "tcp"),
            "ip": ip,
            "ips": ips,
            "bind_scope": _bind_scope_many(ips),
            "process": lp_info["process"] if lp_info else None,
            "pid": lp_info["pid"] if lp_info else None,
            "containers": ctors,
            "compose_configs": composes,
            "manual_label": manual["label"] if manual else None,
            "machine": manual["machine"] if manual else "localhost",
            "known_service": known,
            "is_hidden": port in hidden_ports,
            "conflict": len(composes) > 1,
            "urls": urls,
        })

    used = sum(1 for p in port_list if p["status"] == "used")
    configured = sum(1 for p in port_list if p["status"] == "configured")
    occupied = {p["port"] for p in port_list}
    free = sum(1 for n in range(range_start, range_end + 1) if n not in occupied)

    return {
        "ports": port_list,
        "summary": {
            "used": used,
            "configured": configured,
            "free": free,
            "hidden": len(hidden_ports),
            "hidden_locked": hidden_locked,
            "range_start": range_start,
            "range_end": range_end,
        },
    }


def _proto_label(protocols: list[str]) -> str:
    bases: list[str] = []
    for proto in protocols:
        base = proto.replace("6", "")
        if base not in bases:
            bases.append(base)
    return ",".join(bases) if bases else "tcp"


def _bind_scope(ip: str) -> str:
    if not ip or ip in ("0.0.0.0", "::", "*"):
        return "public"
    if ip in ("127.0.0.1", "::1", "localhost"):
        return "localhost"
    return "lan"


def _bind_scope_many(ips: list[str]) -> str:
    scopes = {_bind_scope(ip) for ip in ips or ["0.0.0.0"]}
    if "public" in scopes:
        return "public"
    if "lan" in scopes:
        return "lan"
    return "localhost"


def _collect_urls(
    port: int,
    ips: list[str],
    containers: list[dict],
    known: dict | None,
    options: dict | None = None,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for c in containers:
        for u in c.get("urls") or []:
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
    opts = options or {}
    if opts.get("guess_urls") is False:
        return urls
    scope = _bind_scope_many(ips)
    if known and known.get("is_access_port"):
        configured = (opts.get("url_host") or "").strip()
        if scope == "localhost":
            host = "127.0.0.1"
        else:
            host = configured or "localhost"
        name = (known.get("name") or "").upper()
        scheme_pref = opts.get("url_scheme") or "auto"
        if scheme_pref in ("http", "https"):
            scheme = scheme_pref
        else:
            scheme = "https" if port in (443, 8443, 9443) or "HTTPS" in name else "http"
        if port in (22, 23, 25, 53, 110, 143, 445, 3389, 5900, 1194, 51820):
            return urls
        guess = f"{scheme}://{host}:{port}"
        if guess not in seen:
            urls.append(guess)
    return urls
