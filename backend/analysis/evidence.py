"""Freeze a selected, allowlisted public observation for analysis and reports."""

from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

EVIDENCE_VERSION = "port-observation.v2"
CORE_VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?$"
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_:.-]{1,200}$")]
Timestamp = Annotated[int, Field(ge=0, le=32503680000)]
Port = Annotated[int, Field(ge=1, le=65535)]
State = Literal["used", "configured", "free", "unknown"]
Protocol = Literal["tcp", "udp"]
Source = Literal["listen", "docker", "compose", "manual", "occupancy"]


class AnalysisError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


class Whitelist(BaseModel):
    # Unknown fields are excluded, including additions to otherwise valid core rows.
    model_config = ConfigDict(extra="ignore", strict=True)


class Bind(Whitelist):
    family: Literal["ipv4", "ipv6", "unknown"]
    scope: Literal["all_interfaces", "loopback", "specific_interface", "unknown"]
    source: Source


class Entry(Whitelist):
    protocol: Protocol
    status: State
    listening: bool
    docker_mapped: bool
    docker_live: bool
    compose_declared: bool
    compose_relation: Literal["not_declared", "declared_and_live", "declared_without_live_mapping"]
    compose_conflict: bool
    manual: bool
    reservation: bool
    bind: list[Bind] = Field(max_length=32)
    evidence_refs: list[Identifier] = Field(max_length=8)


class Current(Whitelist):
    overall_status: State
    entries: list[Entry] = Field(min_length=1, max_length=2)


class SourceState(Whitelist):
    name: Source
    state: Literal["ok", "failed", "disabled", "unknown"]
    observed_at: Timestamp | None


class Scan(Whitelist):
    ready: bool
    complete: bool
    stale: bool
    sources: list[SourceState] = Field(max_length=5)


class HistoryEvent(Whitelist):
    observed_at: Timestamp
    state: State


class History(Whitelist):
    state: Literal["available", "disabled", "unavailable"]
    resolution: Literal["port_state_only"]
    events: list[HistoryEvent] = Field(max_length=128)
    truncated: bool


class PortState(Whitelist):
    status: State
    protocols: list[Protocol] = Field(max_length=2)
    bind_scope: Literal["public", "lan", "link", "localhost"] | None
    compose_conflict: bool


class QualityState(Whitelist):
    quality: Literal["complete", "degraded"]


class Event(Whitelist):
    schema_version: Literal[1]
    event_id: Identifier
    observation_id: Identifier
    observed_at: Timestamp
    port: Port | None
    protocol: Literal["tcp", "udp", "all"]
    kind: Literal[
        "state_changed",
        "bind_scope_changed",
        "configuration_mismatch",
        "observation_degraded",
        "observation_recovered",
    ]
    before: PortState | QualityState
    after: PortState | QualityState
    evidence_refs: list[Identifier] = Field(max_length=8)
    source_quality: Literal["complete", "degraded"]


class Reference(Whitelist):
    id: Identifier
    source: Source
    observed_at: Timestamp | None
    protocol: Protocol | None = None


class Scope(Whitelist):
    kind: Literal["local_hub"]


class Identity(Whitelist):
    port: Port
    protocol: Literal["tcp", "udp", "all"]


class Observation(Whitelist):
    schema_version: Literal[1]
    capture_id: Identifier
    captured_at: Timestamp
    core_version: Annotated[str, Field(pattern=CORE_VERSION_PATTERN)]
    requires_hidden_access: bool
    scope: Scope
    identity: Identity
    scan: Scan
    current: Current
    history: History
    events: list[Event] = Field(max_length=64)
    evidence_refs: list[Reference] = Field(max_length=256)
    limitations: list[
        Literal[
            "history_port_state_only",
            "scan_incomplete",
            "scan_stale",
            "history_disabled",
            "history_unavailable",
            "event_history_disabled",
            "event_history_unavailable",
        ]
    ] = Field(max_length=8)


def prepare_evidence(
    observation, *, core_version, include_history, question_type="current_occupancy", max_records=20
):
    try:
        if question_type not in {"current_occupancy", "recorded_changes"}:
            raise ValueError("Unsupported question")
        if type(max_records) is not int or not 1 <= max_records <= 20:
            raise ValueError("Invalid record bound")
        if observation.get("scope") != {"kind": "local_hub"}:
            raise ValueError("Unsupported observation scope")
        if any(
            {"scope", "hub_id", "node_id", "host_id"} & event.keys()
            for event in observation.get("events", [])
        ):
            raise ValueError("Events cannot override local observation scope")
        document = Observation.model_validate(observation).model_dump(exclude_unset=True)
        if document["core_version"] != core_version:
            raise ValueError("Core version mismatch")
        identity = document["identity"]
        entries = document["current"]["entries"]
        expected_protocols = (
            {"tcp", "udp"} if identity["protocol"] == "all" else {identity["protocol"]}
        )
        if (
            len(entries) != len(expected_protocols)
            or {entry["protocol"] for entry in entries} != expected_protocols
        ):
            raise ValueError("Protocol identity mismatch")
        if any(event["port"] not in (None, identity["port"]) for event in document["events"]):
            raise ValueError("Event belongs to another port")
        event_ids = [event["event_id"] for event in document["events"]]
        if len(set(event_ids)) != len(event_ids) or any(
            identifier in {"current", "scan"} or identifier.startswith("history-")
            for identifier in event_ids
        ):
            raise ValueError("Ambiguous evidence identifiers")
        for event in document["events"]:
            expected = "quality" if event["kind"].startswith("observation_") else "status"
            if expected not in event["before"] or expected not in event["after"]:
                raise ValueError("Event kind and before/after shape disagree")
    except (ValidationError, ValueError, TypeError, KeyError):
        raise AnalysisError(
            "core_incompatible",
            "Port observation format is incompatible; no evidence was prepared.",
            503,
        ) from None
    captured = document["captured_at"]
    history = document["history"]
    cutoff = captured - 24 * 3600
    selected_history = [
        event for event in history["events"] if cutoff <= event["observed_at"] <= captured
    ]
    selected_events = [
        event for event in document["events"] if cutoff <= event["observed_at"] <= captured
    ]
    limitations = [
        "Status covers enabled scan sources only; disabled sources were not checked.",
        "Declared configuration is not a running service; bind scope is not external reachability.",
        "Firewall rules, remote connectivity, process logs, and health checks are not collected.",
    ]
    limitation_codes = ["sources", "declarations", "health"]
    if (
        not document["scan"]["ready"]
        or not document["scan"]["complete"]
        or document["scan"]["stale"]
    ):
        limitations.append(
            "Scan is incomplete or stale; absence of observations does not confirm a free port."
        )
        limitation_codes.append("scan")
    if include_history:
        history["truncated"] = history["truncated"] or len(selected_history) > max_records
        history["events"] = selected_history[-max_records:]
        document["events"] = selected_events[-max_records:]
        limitations.append(
            "Legacy history records port state only without protocol, holder, or bind transitions."
        )
        limitation_codes.append("history_resolution")
        if history["state"] != "available":
            limitations.append("History is disabled or unavailable; unrecorded changes are unknown.")
            limitation_codes.append("history_unavailable")
        if any(code.startswith("event_history_") for code in document["limitations"]):
            limitations.append(
                "Persistent change history is disabled or unavailable; only in-memory events may appear."
            )
            limitation_codes.append("event_history")
        if not history["events"] and not document["events"]:
            limitations.append("No history records are available; prior changes cannot be ruled out.")
            limitation_codes.append("no_history")
        if history["truncated"] or len(selected_events) > max_records:
            limitation_codes.append("truncated")
            limitations.append(
                f"Preview includes at most {max_records} state records and {max_records} events from the last 24 hours."
            )
    else:
        history["events"] = []
        history["state"] = "not_selected"
        document["events"] = []
        limitations.append("History and change events were excluded from this selection.")
        limitation_codes.append("not_selected")
    used_refs = {f"{document['capture_id']}:port:{identity['port']}"}
    for entry in entries:
        used_refs.update(entry["evidence_refs"])
    for event in document["events"]:
        used_refs.update(event["evidence_refs"])
    document["evidence_refs"] = [ref for ref in document["evidence_refs"] if ref["id"] in used_refs]
    facts = [
        {
            "id": "current",
            "source": "port_observation",
            "observed_at": captured,
            "data": {
                "port": identity["port"],
                "protocol": identity["protocol"],
                "status": document["current"]["overall_status"],
                "entries": entries,
            },
        },
        {"id": "scan", "source": "scan_health", "observed_at": captured, "data": document["scan"]},
    ]
    for index, event in enumerate(history["events"], 1):
        facts.append(
            {
                "id": f"history-{index}",
                "source": "port_history",
                "observed_at": event["observed_at"],
                "data": event,
            }
        )
    for event in document["events"]:
        facts.append(
            {
                "id": event["event_id"],
                "source": "port_event",
                "observed_at": event["observed_at"],
                "data": event,
            }
        )
    del document["requires_hidden_access"]
    return {
        "schema_version": 1,
        "evidence_version": EVIDENCE_VERSION,
        "capture_id": document["capture_id"],
        "core_version": core_version,
        "captured_at": datetime.fromtimestamp(captured, UTC).isoformat(timespec="seconds"),
        "port": identity["port"],
        "question_type": question_type,
        "history_window_hours": 24 if include_history else None,
        "history_record_limit": max_records if include_history else None,
        "history_resolution": history["resolution"],
        "observation": document,
        "facts": facts,
        "limitations": limitations,
        "limitation_codes": limitation_codes,
    }


def core_client(app, request):
    headers = {
        key: request.headers[key]
        for key in ("authorization", "x-hidden-unlock")
        if key in request.headers
    }
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers
    )


async def read_port(client, port, protocol="all"):
    response = await client.get(
        f"/api/observations/ports/{port}", params={"protocol": protocol, "include_hidden": "true"}
    )
    if response.status_code != 200:
        raise AnalysisError(
            "core_unavailable",
            "Unable to read port observation from core.",
            response.status_code,
        )
    return response.json()


async def require_port_access(app, request, port):
    async with core_client(app, request) as client:
        await read_port(client, port)


async def read_evidence(
    app,
    request,
    port,
    include_history,
    core_version,
    protocol="all",
    question_type="current_occupancy",
    max_records=20,
):
    async with core_client(app, request) as client:
        metadata = await client.get("/api/meta")
        if metadata.status_code != 200:
            raise AnalysisError(
                "core_unavailable", "Unable to read core metadata.", metadata.status_code
            )
        if metadata.json().get("capabilities", {}).get("port_observation") != 1:
            raise AnalysisError(
                "core_incompatible", "Core does not support port observations.", 503
            )
        observation = await read_port(client, port, protocol)
        if observation.get("identity") != {"port": port, "protocol": protocol}:
            raise AnalysisError(
                "core_incompatible", "Port observation identity does not match requested port.", 503
            )
        evidence = prepare_evidence(
            observation,
            core_version=core_version,
            include_history=include_history,
            question_type=question_type,
            max_records=max_records,
        )
        return evidence, observation["requires_hidden_access"]
