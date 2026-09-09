"""Diagnostics API."""
from __future__ import annotations
import os
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from .. import agent_events, degradations, doctor, history, hosts
from .. import settings as app_settings
from ..auth import (
    auth_configuration_valid,
    auth_configured,
    hidden_ports_withheld,
    hidden_unlock_configured,
    request_may_see_hidden,
    valid_basic_header,
)
from ..occupancy_monitor import complete
from ..port_scanner import (
    listen_scan_source,
)
from fastapi import APIRouter
from types import ModuleType

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()

@router.get("/api/meta")
def meta(request: Request) -> dict:
    automation = {
        "agent_token": bool(os.environ.get("AGENT_TOKEN", "").strip()),
        "metrics": os.environ.get("METRICS_ENABLED", "").strip().lower()
                   in ("1", "true", "yes", "on"),
        "webhook": bool(os.environ.get("WEBHOOK_URL", "").strip()),
        "history_days": history.retention_days(),
        "events_stream": True,
        "suggest_peers": bool(hosts.list_public_peers()),
        "listen_port": runtime._listen_port(),
    }
    if agent_events.enabled():
        leases = runtime._active_leases(request)
        automation["agent_events"] = {
            **agent_events.summary(),
            "active_leases": len(leases),
            "lease_rows": leases,
        }
    return {
        "version": runtime.VERSION,
        "capabilities": {
            "port_rules": 1,
            "doctor": 1,
            "port_check": 1,
            "reservations": 1,
            "exact_reservations": 1,
            "idempotent_reservations": 1,
            "reservation_release": 1,
            "scope_all": 1,
        },
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "hidden_ports_withheld": hidden_ports_withheld(),
        "settings_readonly": app_settings.settings_readonly(),
        "automation": automation,
    }


@router.get("/api/health")
def health(request: Request) -> dict:
    monitor = runtime._monitor.status()
    sources = monitor["sources"]
    recent = degradations.recent(5)
    if ((auth_configured() and not valid_basic_header(request.headers.get("authorization") or ""))
            or not request_may_see_hidden(request)):
        recent = [
            {key: value for key, value in event.items() if key != "scope"}
            for event in recent
        ]
    return {
        "status": "ok" if monitor["ready"] and auth_configuration_valid() else "degraded",
        "version": runtime.VERSION,
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


@router.get("/api/doctor")
def get_doctor() -> dict:
    document = runtime._doctor_document()
    return {**document, "report": doctor.report_text(document)}


@router.get("/api/doctor/report")
def get_doctor_report() -> Response:
    return Response(
        content=doctor.report_text(runtime._doctor_document()),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="port-light-diagnostics.json"'},
    )


@router.get("/api/metrics")
def metrics() -> Response:
    """Prometheus text exposition over the current occupancy snapshot.

    Disabled unless ``METRICS_ENABLED`` is set. Aggregates only — the
    endpoint never emits ports, names, or URLs. Hidden rows are included in
    the aggregates (they are real occupancy) but never identified.
    """
    if not runtime._metrics_enabled():
        raise HTTPException(status_code=404, detail="not found")
    values = runtime._values()
    snap = runtime._monitor.latest(values)
    result = runtime._classify_snapshot(
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


@router.get("/api/events")
def events() -> Response:
    """Server-sent refresh hints for accepted occupancy snapshots."""
    return StreamingResponse(
        _event_lines(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _event_lines():
    """Broadcast a refresh hint after the monitor accepts a changed snapshot."""
    last = runtime._monitor.sequence()
    yield "retry: 3000\n\n"
    yield "event: hello\ndata: {}\n\n"
    while True:
        sequence, changed = await runtime._monitor.wait_for_change(last, timeout=15.0)
        if changed:
            last = sequence
            yield "event: refresh\ndata: {}\n\n"
        else:
            yield ": keepalive\n\n"
