"""Port-Light backend v3 — FastAPI app with full feature set.

API endpoints:
  GET  /api/ports              → port status overview (with range, machine, filters)
  GET  /api/health             → liveness check
  GET  /                       → frontend index.html
  /static/*                    → frontend assets

  POST /api/manual-ports       → add a manual port entry
  DELETE /api/manual-ports/{port}  → remove a manual port entry
  GET  /api/hidden             → list hidden ports
  POST /api/hidden/{port}      → hide a port
  DELETE /api/hidden/{port}    → unhide a port
  GET  /api/machines           → list machines
  POST /api/machines           → add a machine
  DELETE /api/machines/{name}  → remove a machine
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .port_scanner import scan_listening_ports
from .docker_scanner import scan_containers
from .compose_scanner import scan_compose_files
from .known_ports import get_known_port
from . import port_store

app = FastAPI(title="Port-Light", version="0.3.0")

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ── Pydantic models ────────────────────────────────────────────

class ManualPortCreate(BaseModel):
    port: int
    label: str = ""
    machine: str = "localhost"

class MachineCreate(BaseModel):
    name: str
    host: str
    note: str = ""


# ── Main API ───────────────────────────────────────────────────

@app.get("/api/ports")
def get_ports(
    range_start: int = Query(default=1, ge=1, le=65535),
    range_end: int = Query(default=9999, ge=1, le=65535),
    include_hidden: bool = Query(default=False),
) -> dict:
    """Return merged port status: used / configured / free, with manual + hidden + machines."""
    compose_dir = os.environ.get("COMPOSE_SCAN_DIR", "/compose")

    listening = scan_listening_ports()
    containers = scan_containers()
    compose_ports = scan_compose_files(compose_dir)
    manual_ports = port_store.get_manual_ports()
    hidden_ports = port_store.get_hidden_ports()
    machines = port_store.get_machines()

    return _classify(
        listening, containers, compose_ports,
        manual_ports, hidden_ports, machines,
        range_start, range_end, include_hidden,
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ── Manual ports ───────────────────────────────────────────────

@app.post("/api/manual-ports")
def add_manual_port(body: ManualPortCreate) -> dict:
    entry = port_store.add_manual_port(body.port, body.label, body.machine)
    return {"status": "ok", "entry": entry}


@app.delete("/api/manual-ports/{port}")
def del_manual_port(port: int, machine: str = Query(default="localhost")) -> dict:
    removed = port_store.remove_manual_port(port, machine)
    return {"status": "ok" if removed else "not_found"}


# ── Hidden ports ───────────────────────────────────────────────

@app.get("/api/hidden")
def list_hidden() -> dict:
    return {"hidden_ports": port_store.get_hidden_ports()}


@app.post("/api/hidden/{port}")
def hide_port(port: int) -> dict:
    added = port_store.add_hidden_port(port)
    return {"status": "ok" if added else "already_hidden"}


@app.delete("/api/hidden/{port}")
def unhide_port(port: int) -> dict:
    removed = port_store.remove_hidden_port(port)
    return {"status": "ok" if removed else "not_hidden"}


# ── Machines ───────────────────────────────────────────────────

@app.get("/api/machines")
def list_machines() -> dict:
    return {"machines": port_store.get_machines()}


@app.post("/api/machines")
def add_machine(body: MachineCreate) -> dict:
    entry = port_store.add_machine(body.name, body.host, body.note)
    return {"status": "ok", "entry": entry}


@app.delete("/api/machines/{name}")
def del_machine(name: str) -> dict:
    removed = port_store.remove_machine(name)
    return {"status": "ok" if removed else "not_found"}


# ── Frontend ───────────────────────────────────────────────────

@app.get("/")
def index() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


# ── Merge logic ────────────────────────────────────────────────

def _classify(
    listening: list,
    containers: list,
    compose_ports: list,
    manual_ports: list[dict],
    hidden_ports: list[int],
    machines: list[dict],
    range_start: int,
    range_end: int,
    include_hidden: bool,
) -> dict:
    # Index listening ports
    listening_map: dict[int, dict] = {}
    for lp in listening:
        listening_map.setdefault(lp.port, {
            "protocol": lp.protocol,
            "ip": lp.ip,
            "process": lp.process_name,
            "pid": lp.pid,
        })

    # Index container ports
    container_map: dict[int, list[dict]] = {}
    for c in containers:
        for p in c.ports:
            container_map.setdefault(p["host_port"], []).append({
                "name": c.name,
                "status": c.status,
                "image": c.image,
                "compose_project": c.compose_project,
                "compose_service": c.compose_service,
            })

    # Index compose ports
    compose_map: dict[int, list[dict]] = {}
    for cp in compose_ports:
        compose_map.setdefault(cp.port, []).append({
            "project_dir": cp.project_dir,
            "service_name": cp.service_name,
            "compose_file": cp.compose_file,
            "container_port": cp.container_port,
        })

    # Index manual ports
    manual_map: dict[int, dict] = {}
    for mp in manual_ports:
        manual_map[mp["port"]] = {
            "label": mp.get("label", ""),
            "machine": mp.get("machine", "localhost"),
        }

    # Union of all interesting ports
    all_ports = set(listening_map) | set(container_map) | set(compose_map) | set(manual_map)

    port_list: list[dict] = []
    for port in sorted(all_ports):
        # Skip hidden ports unless explicitly requested
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

        # Determine source type for filtering
        if ctors:
            source_type = "docker"
        elif is_listening and not ctors:
            # Check if it's a system port
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

        is_hidden = port in hidden_ports

        port_list.append({
            "port": port,
            "status": status,
            "source_type": source_type,
            "protocol": lp_info["protocol"] if lp_info else "tcp",
            "ip": lp_info["ip"] if lp_info else "0.0.0.0",
            "process": lp_info["process"] if lp_info else None,
            "pid": lp_info["pid"] if lp_info else None,
            "containers": ctors,
            "compose_configs": composes,
            "manual_label": manual["label"] if manual else None,
            "machine": manual["machine"] if manual else "localhost",
            "known_service": known,
            "is_hidden": is_hidden,
            "conflict": len(composes) > 1,
        })

    used = sum(1 for p in port_list if p["status"] == "used")
    configured = sum(1 for p in port_list if p["status"] == "configured")
    occupied = {p["port"] for p in port_list}
    free = sum(1 for n in range(range_start, range_end + 1) if n not in occupied)
    hidden_count = len(hidden_ports)

    return {
        "ports": port_list,
        "machines": machines,
        "summary": {
            "used": used,
            "configured": configured,
            "free": free,
            "hidden": hidden_count,
            "range_start": range_start,
            "range_end": range_end,
        },
    }
