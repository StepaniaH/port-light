"""Bounded, frozen multi-port occupancy observations for local consumers."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from types import ModuleType

from .. import degradations, history
from ..auth import request_may_see_hidden
from ..occupancy_monitor import SnapshotUnavailable, complete
from ..port_observations import (
    _number,
    _observation_index,
    batch_port_observation,
    known_observation_ports,
)

MAX_BATCH_PORTS = 1024
MAX_BATCH_EVENTS = 512
Port = Annotated[int, Field(ge=1, le=65535)]

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()


class BatchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KnownSelection(BatchDocument):
    kind: Literal["known"]
    # A recheck can retain members that disappeared from the current inventory
    # while adding newly known members, without combining separate snapshots.
    additional_ports: list[Port] = Field(default_factory=list, max_length=MAX_BATCH_PORTS)

    @model_validator(mode="after")
    def unique_additional_ports(self):
        if len(set(self.additional_ports)) != len(self.additional_ports):
            raise ValueError("additional_ports must be unique")
        return self


class PortsSelection(BatchDocument):
    kind: Literal["ports"]
    ports: list[Port] = Field(min_length=1, max_length=MAX_BATCH_PORTS)

    @model_validator(mode="after")
    def unique_ports(self):
        if len(set(self.ports)) != len(self.ports):
            raise ValueError("ports must be unique")
        return self


class RangeSelection(BatchDocument):
    kind: Literal["range"]
    start: int = Field(ge=1, le=65535)
    end: int = Field(ge=1, le=65535)

    @model_validator(mode="after")
    def bounded_range(self):
        if self.end < self.start:
            raise ValueError("range end must not precede start")
        if self.end - self.start + 1 > MAX_BATCH_PORTS:
            raise ValueError("range exceeds batch port limit")
        return self


Selection = Annotated[
    KnownSelection | PortsSelection | RangeSelection,
    Field(discriminator="kind"),
]


class BatchInput(BatchDocument):
    selection: Selection
    protocol: Literal["tcp", "udp", "all"] = "all"
    history_hours: int = Field(default=24, ge=1, le=720)
    event_limit: int = Field(default=MAX_BATCH_EVENTS, ge=1, le=MAX_BATCH_EVENTS)
    include_hidden: bool = False


def _scan_document(snap: dict, monitor_status: dict) -> dict:
    captured_at = snap["captured_at"]
    states = []
    for name, state in sorted(snap.get("sources", {}).items()):
        states.append({
            "name": name,
            "state": state if state in ("ok", "failed", "disabled") else "unknown",
            "observed_at": captured_at if state == "ok" else None,
        })
    return {
        "ready": bool(monitor_status.get("ready")),
        "complete": complete(snap),
        "stale": bool(snap.get("stale") or monitor_status.get("stale")),
        "sources": states,
    }


def _selection_ports(selection, snap: dict, show_hidden: bool) -> tuple[list[int], str]:
    if isinstance(selection, KnownSelection):
        return sorted(
            set(known_observation_ports(snap, show_hidden=show_hidden)) | set(selection.additional_ports)
        ), "known"
    if isinstance(selection, PortsSelection):
        return sorted(selection.ports), "ports"
    return list(range(selection.start, selection.end + 1)), "range"


def _history_events(
    *,
    ports: set[int] | None,
    input: BatchInput,
    snap: dict,
    live_events: list[dict],
    show_hidden: bool,
) -> tuple[list[dict], dict, list[str]]:
    limitations = []
    capture_id, captured_at = snap["capture_id"], snap["captured_at"]
    if not history.enabled():
        persisted, truncated, state = [], False, "disabled"
        limitations.append("event_history_disabled")
    else:
        try:
            persisted, truncated = history.query_observation_events_batch(
                ports,
                input.history_hours,
                allow_hidden=show_hidden,
                limit=input.event_limit,
                capture_id=capture_id,
                captured_at=captured_at,
            )
            state = "available"
        except sqlite3.Error:
            degradations.report("history", "history.db", "batch observation event history read failed")
            persisted, truncated, state = [], False, "unavailable"
            limitations.append("event_history_unavailable")
    selected = None if ports is None else set(ports)
    cutoff = captured_at - min(
        max(input.history_hours, 1), history.retention_days() * 24, 24 * 30
    ) * 3600
    hidden = {
        port for value in snap.get("user_state", ([], []))[1]
        if (port := _number(value)) is not None
    }

    def visible_in_current_snapshot(event: dict) -> bool:
        port = event.get("port")
        if port is None:
            return True
        if type(port) is not int or not 1 <= port <= 65535:
            return False
        if not show_hidden and port in hidden:
            return False
        return selected is None or port in selected

    by_id = {event["event_id"]: event for event in persisted}
    for event in live_events:
        if (
            type(event.get("observed_at")) is not int
            or not cutoff <= event["observed_at"] <= captured_at
            or not visible_in_current_snapshot(event)
        ):
            continue
        by_id[event["event_id"]] = event
    if not show_hidden and any(
        type(event.get("port")) is int and event["port"] in hidden
        for event in by_id.values()
    ):
        limitations.append("hidden_withheld")
    events = sorted(
        (event for event in by_id.values() if visible_in_current_snapshot(event)),
        key=lambda event: (event["observed_at"], event["event_id"]),
    )
    if len(events) > input.event_limit:
        events = events[-input.event_limit:]
        truncated = True
    if truncated:
        limitations.append("event_history_truncated")
    return events, {
        "state": state,
        "truncated": truncated,
        "window_hours": input.history_hours,
        "boundary": "capture_sequence",
        "time_resolution": "seconds",
    }, limitations


@router.post("/api/observations/batch")
def batch_observations(body: BatchInput, request: Request) -> dict:
    """Return compact facts from one monitor capture and a fenced history read."""
    values = runtime._values()
    may_see_hidden = request_may_see_hidden(request)
    show_hidden = bool(body.include_hidden and may_see_hidden)
    try:
        snap, live_events = runtime._monitor.latest_with_observation_events(
            values, allow_hidden=show_hidden
        )
    except SnapshotUnavailable as exc:
        raise HTTPException(status_code=503, detail="occupancy snapshot unavailable; retry later") from exc
    selected, selection_kind = _selection_ports(body.selection, snap, show_hidden)
    if len(selected) > MAX_BATCH_PORTS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "selection_too_large",
                "message": "selection exceeds the batch observation limit",
                "max_ports": MAX_BATCH_PORTS,
            },
        )
    history_ports = None if selection_kind == "known" else set(selected)
    events, event_coverage, limitations = _history_events(
        ports=history_ports,
        input=body,
        snap=snap,
        live_events=live_events,
        show_hidden=show_hidden,
    )
    event_ports = {event["port"] for event in events if type(event.get("port")) is int}
    effective = sorted(set(selected) | event_ports)
    if len(effective) > MAX_BATCH_PORTS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "selection_too_large",
                "message": "current and recorded event ports exceed the batch observation limit",
                "max_ports": MAX_BATCH_PORTS,
            },
        )
    index = _observation_index(snap)
    observations = []
    hidden_withheld = False
    for port in effective:
        observation = batch_port_observation(
            snap=snap, port=port, protocol=body.protocol, show_hidden=show_hidden, index=index
        )
        if observation is None:
            hidden_withheld = True
            continue
        observations.append(observation)
    scan = _scan_document(snap, runtime._monitor.status())
    if not scan["complete"]:
        limitations.append("scan_incomplete")
    if scan["stale"]:
        limitations.append("scan_stale")
    if hidden_withheld or (body.include_hidden and not show_hidden):
        limitations.append("hidden_withheld")
    return {
        "schema_version": 1,
        "scope": {"kind": "local_hub"},
        "capture_id": snap["capture_id"],
        "captured_at": snap["captured_at"],
        "core_version": runtime.VERSION,
        "selection": {"kind": selection_kind, "protocol": body.protocol},
        "coverage": {
            "requested_count": len(selected),
            "observed_count": len(observations),
            "complete": not hidden_withheld and len(observations) == len(effective),
            "limitations": sorted(set(limitations)),
        },
        "scan": scan,
        "event_coverage": event_coverage,
        "ports": observations,
        "events": events,
    }
