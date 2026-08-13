"""Port-Light backend — FastAPI app.

API:
  GET  /api/ports         merged occupancy map
  GET  /api/health        liveness + scanner presence (no secrets)
  GET  /api/meta          version and auth/unlock flags
  GET  /                  frontend
  POST /api/manual-ports
  DELETE /api/manual-ports/{port}
  GET  /api/hidden        hidden port numbers (gated when secrets are set)
  POST /api/hidden/{port}
  DELETE /api/hidden/{port}
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import port_store
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

VERSION = "0.5.0"

app = FastAPI(title="Port-Light", version=VERSION)
app.middleware("http")(basic_auth_middleware)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class ManualPortCreate(BaseModel):
    port: int
    label: str = ""
    machine: str = "localhost"


def _compose_dir() -> str:
    return os.environ.get("COMPOSE_SCAN_DIR", "/compose")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "version": VERSION,
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "hidden_ports_withheld": hidden_ports_withheld(),
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


@app.get("/api/ports")
def get_ports(
    request: Request,
    range_start: int = Query(default=1, ge=1, le=65535),
    range_end: int = Query(default=9999, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> dict:
    may_see = request_may_see_hidden(request)
    show_hidden = bool(include_hidden and may_see)

    listening = scan_listening_ports()
    containers = scan_containers()
    compose_ports = scan_compose_files(_compose_dir())
    manual_ports = port_store.get_manual_ports()
    hidden_ports = port_store.get_hidden_ports()

    return _classify(
        listening,
        containers,
        compose_ports,
        manual_ports,
        hidden_ports,
        range_start,
        range_end,
        show_hidden,
        hidden_locked=hidden_ports_withheld() and not may_see,
    )


@app.post("/api/manual-ports")
def add_manual_port(body: ManualPortCreate) -> dict:
    entry = port_store.add_manual_port(body.port, body.label, body.machine)
    return {"status": "ok", "entry": entry}


@app.delete("/api/manual-ports/{port}")
def del_manual_port(port: int, machine: str = Query(default="localhost")) -> dict:
    removed = port_store.remove_manual_port(port, machine)
    return {"status": "ok" if removed else "not_found"}


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
    return {"status": "ok" if removed else "not_hidden"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "index.html")


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
) -> dict:
    listening_map: dict[int, dict] = {}
    for lp in listening:
        listening_map.setdefault(lp.port, {
            "protocol": lp.protocol,
            "ip": lp.ip,
            "ips": [lp.ip] if lp.ip else [],
            "process": lp.process_name,
            "pid": lp.pid,
        })
        existing = listening_map[lp.port]
        if lp.ip and lp.ip not in existing.get("ips", []):
            existing.setdefault("ips", []).append(lp.ip)

    container_map: dict[int, list[dict]] = {}
    for c in containers:
        for p in c.ports:
            container_map.setdefault(p["host_port"], []).append({
                "name": c.name,
                "status": c.status,
                "image": c.image,
                "compose_project": c.compose_project,
                "compose_service": c.compose_service,
                "network_mode": getattr(c, "network_mode", None),
                "urls": getattr(c, "urls", None) or [],
            })

    compose_map: dict[int, list[dict]] = {}
    for cp in compose_ports:
        compose_map.setdefault(cp.port, []).append({
            "project_dir": cp.project_dir,
            "service_name": cp.service_name,
            "compose_file": cp.compose_file,
            "container_port": cp.container_port,
            "protocol": getattr(cp, "protocol", "tcp"),
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
        ip = lp_info["ip"] if lp_info else "0.0.0.0"
        urls = _collect_urls(port, ip, ctors, known)

        port_list.append({
            "port": port,
            "status": status,
            "source_type": source_type,
            "protocol": lp_info["protocol"] if lp_info else "tcp",
            "ip": ip,
            "ips": (lp_info.get("ips") if lp_info else None) or [ip],
            "bind_scope": _bind_scope(ip),
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


def _bind_scope(ip: str) -> str:
    if not ip or ip in ("0.0.0.0", "::", "*"):
        return "public"
    if ip in ("127.0.0.1", "::1", "localhost"):
        return "localhost"
    return "lan"


def _collect_urls(port: int, ip: str, containers: list[dict], known: dict | None) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for c in containers:
        for u in c.get("urls") or []:
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
    if known and known.get("is_access_port"):
        host = "127.0.0.1" if _bind_scope(ip) == "localhost" else "localhost"
        scheme = "https" if port in (443, 8443, 9443) or (known.get("name") or "").upper().find("HTTPS") >= 0 else "http"
        if port in (22, 23, 25, 53, 110, 143, 445, 3389, 5900, 1194, 51820):
            return urls
        guess = f"{scheme}://{host}:{port}"
        if guess not in seen:
            urls.append(guess)
    return urls
