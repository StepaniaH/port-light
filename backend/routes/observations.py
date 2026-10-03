"""Stable, privacy-preserving evidence views over one occupancy snapshot."""

from __future__ import annotations

from typing import Literal
from types import ModuleType

from fastapi import APIRouter, HTTPException, Query, Request

from ..auth import request_may_see_hidden
from ..port_observations import build_port_observation

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()


@router.get("/api/observations/ports/{port}")
def port_observation(
    port: int,
    request: Request,
    protocol: Literal["tcp", "udp", "all"] = Query(default="all"),
    include_hidden: bool = Query(default=False),
) -> dict:
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port out of range")
    values = runtime._values()
    snap = runtime._monitor.latest(values)
    show_hidden = bool(include_hidden and request_may_see_hidden(request))
    try:
        return build_port_observation(
            snap=snap,
            values=values,
            port=port,
            protocol=protocol,
            show_hidden=show_hidden,
            core_version=runtime.VERSION,
            monitor_status=runtime._monitor.status(),
            classify=runtime._classify_snapshot,
            events=runtime._monitor.observation_events(port, allow_hidden=show_hidden),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="not found") from None
    except RuntimeError:
        raise HTTPException(status_code=503, detail="occupancy snapshot unavailable; retry later") from None
