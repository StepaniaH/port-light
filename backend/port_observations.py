"""Privacy-preserving factual port observations for local consumers."""

from __future__ import annotations

import ipaddress
import hashlib
import sqlite3
from collections.abc import Callable

from . import degradations, history
from .classification import binds_overlap, free_port_payload
from .netaddr import clean_bind_ip, proto_family_of
from .occupancy_monitor import complete

PROTOCOLS = ("tcp", "udp")
MAX_HISTORY_EVENTS = 128
MAX_OBSERVATION_EVENTS = 64
RELATION_ID_VERSION = "plr1"


def _number(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 65535 else None


def _hidden(snap: dict, port: int) -> bool:
    return port in {_number(value) for value in snap.get("user_state", ([], []))[1]}


def _manual(snap: dict, port: int) -> dict | None:
    for entry in snap.get("user_state", ([], []))[0]:
        if isinstance(entry, dict) and _number(entry.get("port")) == port:
            return entry
    return None


def _bindings(value, source: str) -> list[dict]:
    text = clean_bind_ip(value)
    if not text or text == "*":
        return [{"family": "unknown", "scope": "all_interfaces", "source": source}]
    if text == "localhost":
        return [{"family": "unknown", "scope": "loopback", "source": source}]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return [{"family": "unknown", "scope": "unknown", "source": source}]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    family = "ipv4" if address.version == 4 else "ipv6"
    if address.is_unspecified:
        scope = "all_interfaces"
    elif address.is_loopback:
        scope = "loopback"
    else:
        scope = "specific_interface"
    return [{"family": family, "scope": scope, "source": source}]


def _compose_conflict(records: list) -> bool:
    by_project: dict[str, list[str | None]] = {}
    for record in records:
        by_project.setdefault(str(getattr(record, "project_dir", "")), []).append(
            getattr(record, "host_ip", None),
        )
    groups = list(by_project.values())
    for index, left in enumerate(groups):
        for right in groups[index + 1:]:
            if any(binds_overlap(a, b) for a in left for b in right):
                return True
    return False


def _observation_index(snap: dict) -> dict:
    """Index source rows once for a bounded multi-port observation request."""
    listeners: dict[tuple[int, str], list] = {}
    containers: dict[tuple[int, str], list] = {}
    compose: dict[tuple[int, str], list] = {}
    manuals: dict[int, dict] = {}
    for row in snap.get("listening", []):
        port, protocol = _number(getattr(row, "port", None)), proto_family_of(
            getattr(row, "protocol", None)
        )
        if port is not None and protocol in PROTOCOLS:
            listeners.setdefault((port, protocol), []).append(row)
    for container in snap.get("containers", []):
        for mapping in getattr(container, "ports", []) or []:
            port, protocol = _number(getattr(mapping, "host_port", None)), proto_family_of(
                getattr(mapping, "protocol", None)
            )
            if port is not None and protocol in PROTOCOLS:
                containers.setdefault((port, protocol), []).append((container, mapping))
    for row in getattr(snap.get("compose_scan"), "ports", []):
        port, protocol = _number(getattr(row, "port", None)), proto_family_of(
            getattr(row, "protocol", None)
        )
        if port is not None and protocol in PROTOCOLS:
            compose.setdefault((port, protocol), []).append(row)
    for row in snap.get("user_state", ([], []))[0]:
        if isinstance(row, dict) and (port := _number(row.get("port"))) is not None:
            manuals[port] = row
    return {"listeners": listeners, "containers": containers, "compose": compose, "manuals": manuals}


def _protocol_entry(
    snap: dict, port: int, protocol: str, capture_id: str, index: dict | None = None
) -> tuple[dict, set[str]]:
    if index is None:
        index = _observation_index(snap)
    listeners = index["listeners"].get((port, protocol), [])
    container_matches = index["containers"].get((port, protocol), [])
    compose_matches = index["compose"].get((port, protocol), [])
    manual = index["manuals"].get(port)
    docker_mapped = any(getattr(mapping, "source", "publish") != "expose"
                        for _container, mapping in container_matches)
    docker_live = any(
        getattr(container, "status", None) in ("running", "paused", "restarting")
        and getattr(mapping, "source", "publish") != "expose"
        for container, mapping in container_matches
    )
    if listeners or docker_live:
        status = "used"
    elif container_matches or compose_matches or manual:
        status = "configured"
    elif complete(snap):
        status = "free"
    else:
        status = "unknown"
    bindings = []
    for row in listeners:
        bindings.extend(_bindings(getattr(row, "ip", None), "listen"))
    for _container, mapping in container_matches:
        bindings.extend(_bindings(getattr(mapping, "host_ip", None), "docker"))
    for row in compose_matches:
        bindings.extend(_bindings(getattr(row, "host_ip", None), "compose"))
    bindings = sorted({
        (row["family"], row["scope"], row["source"]): row for row in bindings
    }.values(), key=lambda row: (row["source"], row["family"], row["scope"]))
    sources = set()
    if listeners:
        sources.add("listen")
    if container_matches:
        sources.add("docker")
    if compose_matches:
        sources.add("compose")
    if manual:
        sources.add("manual")
    refs = [f"{capture_id}:port:{port}:{protocol}:{source}" for source in sorted(sources)]
    compose_declared = bool(compose_matches)
    if not compose_declared:
        compose_relation = "not_declared"
    elif listeners or docker_live:
        compose_relation = "declared_and_live"
    else:
        compose_relation = "declared_without_live_mapping"
    return {
        "protocol": protocol,
        "status": status,
        "listening": bool(listeners),
        "docker_mapped": docker_mapped,
        "docker_live": docker_live,
        "compose_declared": compose_declared,
        "compose_relation": compose_relation,
        "compose_conflict": _compose_conflict(compose_matches),
        "manual": bool(manual),
        "reservation": bool(manual and manual.get("is_reservation")),
        "bind": bindings,
        "evidence_refs": refs,
    }, sources


def _relation_id(kind: str, value: str) -> str:
    # The source value stays local.  This id is only a scoped join key for
    # observations from this installation; it is not a project/container name.
    digest = hashlib.sha256(
        b"port-light-observation-relation-v1\0" + kind.encode() + b"\0" + value.encode()
    ).hexdigest()[:24]
    return f"{RELATION_ID_VERSION}:{kind}:{digest}"


def _protocol_relations(index: dict, port: int, protocol: str) -> list[dict]:
    relations = {}
    for container, _mapping in index["containers"].get((port, protocol), []):
        container_id = getattr(container, "container_id", None)
        if isinstance(container_id, str) and container_id:
            relation_id = _relation_id("container", container_id)
            relations[(relation_id, protocol)] = {
                "id": relation_id,
                "kind": "container",
                "source": "docker",
                "protocol": protocol,
            }
        project = getattr(container, "compose_project", None)
        if isinstance(project, str) and project:
            # Docker reports a project label, not the Compose declaration path.
            # It must remain a source-specific relationship and never join a
            # same-named declaration discovered from a different directory.
            relation_id = _relation_id("compose_project_label", project)
            relations[(relation_id, protocol)] = {
                "id": relation_id,
                "kind": "compose_project_label",
                "source": "docker",
                "protocol": protocol,
            }
    for row in index["compose"].get((port, protocol), []):
        project_dir = getattr(row, "project_dir", None)
        if isinstance(project_dir, str) and project_dir:
            relation_id = _relation_id("compose_project_dir", project_dir)
            relations[(relation_id, protocol)] = {
                "id": relation_id,
                "kind": "compose_project",
                "source": "compose",
                "protocol": protocol,
            }
    return [relations[key] for key in sorted(relations)]


def known_observation_ports(snap: dict, *, show_hidden: bool) -> list[int]:
    """Return source-backed local port identities without enumerating free space."""
    index = _observation_index(snap)
    known = set(index["manuals"])
    known.update(port for port, _protocol in index["listeners"])
    known.update(port for port, _protocol in index["containers"])
    known.update(port for port, _protocol in index["compose"])
    if show_hidden:
        known.update(_number(value) for value in snap.get("user_state", ([], []))[1])
    else:
        hidden = {_number(value) for value in snap.get("user_state", ([], []))[1]}
        known.difference_update(port for port in hidden if port is not None)
    return sorted(port for port in known if port is not None)


def batch_port_observation(
    *,
    snap: dict,
    port: int,
    protocol: str,
    show_hidden: bool,
    index: dict,
) -> dict | None:
    """Build compact current facts using one pre-built source index."""
    if _hidden(snap, port) and not show_hidden:
        return None
    capture_id = snap.get("capture_id")
    if not isinstance(capture_id, str):
        raise RuntimeError("capture unavailable")
    protocols = PROTOCOLS if protocol == "all" else (protocol,)
    entries = []
    relations = []
    for selected in protocols:
        entry, _sources = _protocol_entry(snap, port, selected, capture_id, index)
        entries.append(entry)
        relations.extend(_protocol_relations(index, port, selected))
    status_order = {"used": 3, "configured": 2, "free": 1, "unknown": 0}
    overall = max(entries, key=lambda entry: status_order[entry["status"]])["status"]
    return {
        "port": port,
        "identity": {"port": port, "protocol": protocol},
        "requires_hidden_access": _hidden(snap, port),
        "current": {"overall_status": overall, "entries": entries},
        "relations": sorted(
            {(relation["id"], relation["protocol"]): relation for relation in relations}.values(),
            key=lambda relation: (relation["id"], relation["protocol"]),
        ),
        "evidence_refs": [f"{capture_id}:port:{port}"],
        # A selection can contain a free port; it has no relationship rows and
        # still remains an explicit current observation.
    }


def _history(port: int) -> dict:
    if not history.enabled():
        return {
            "state": "disabled",
            "resolution": "port_state_only",
            "events": [],
            "truncated": False,
        }
    try:
        events = history.query(port, history.retention_days() * 24)
    except sqlite3.Error:
        degradations.report("history", "history.db", "port observation history read failed")
        return {
            "state": "unavailable",
            "resolution": "port_state_only",
            "events": [],
            "truncated": False,
        }
    trimmed = events[-MAX_HISTORY_EVENTS:]
    return {
        "state": "available",
        "resolution": "port_state_only",
        "events": [
            {"observed_at": event["ts"], "state": event["state"]}
            for event in trimmed
            if event.get("state") in ("used", "configured", "free")
        ],
        "truncated": len(events) > len(trimmed),
    }


def _persisted_events(port: int, show_hidden: bool) -> tuple[list[dict], str]:
    if not history.enabled():
        return [], "disabled"
    try:
        return history.query_observation_events(
            port, history.retention_days() * 24, allow_hidden=show_hidden,
        ), "available"
    except sqlite3.Error:
        degradations.report("history", "history.db", "port observation event history read failed")
        return [], "unavailable"


def _recent_events(port: int, show_hidden: bool, live_events: list[dict]) -> tuple[list[dict], str]:
    persisted, state = _persisted_events(port, show_hidden)
    by_id = {
        event["event_id"]: event
        for event in persisted
        if isinstance(event.get("event_id"), str)
    }
    for event in live_events:
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            by_id[event_id] = event

    def sort_key(event: dict) -> tuple[int, str]:
        observed_at = event.get("observed_at")
        event_id = event.get("event_id")
        return (
            observed_at if type(observed_at) is int else 0,
            event_id if isinstance(event_id, str) else "",
        )

    recent = sorted(by_id.values(), key=sort_key)[-MAX_OBSERVATION_EVENTS:]
    return recent, state


def build_port_observation(
    *,
    snap: dict,
    values: dict,
    port: int,
    protocol: str,
    show_hidden: bool,
    core_version: str,
    monitor_status: dict,
    classify: Callable[..., dict],
    events: list[dict],
) -> dict:
    """Build one immutable-looking view from one monitor snapshot."""
    requires_hidden_access = _hidden(snap, port)
    if requires_hidden_access and not show_hidden:
        raise LookupError("hidden")
    captured_at = snap.get("captured_at")
    capture_id = snap.get("capture_id")
    if not isinstance(captured_at, int) or not isinstance(capture_id, str):
        raise RuntimeError("capture unavailable")
    row = next(iter(classify(snap, values, port, port, show_hidden).get("ports", [])), None)
    if row is None:
        row = free_port_payload(port, hidden=False)
        if not complete(snap):
            row["status"] = "unknown"
    protocols = PROTOCOLS if protocol == "all" else (protocol,)
    entries = []
    for selected in protocols:
        entry, _ = _protocol_entry(snap, port, selected, capture_id)
        entries.append(entry)
    recent_events, event_history_state = _recent_events(port, show_hidden, events)
    refs = [{
        "id": f"{capture_id}:port:{port}",
        "source": "occupancy",
        "observed_at": captured_at,
    }]
    for selected, entry in zip(protocols, entries):
        for source in sorted({ref.rsplit(":", 1)[1] for ref in entry["evidence_refs"]}):
            refs.append({
                "id": f"{capture_id}:port:{port}:{selected}:{source}",
                "source": source,
                "protocol": selected,
                "observed_at": captured_at,
            })
    known_refs = {ref["id"] for ref in refs}
    for event in recent_events:
        for ref in event.get("evidence_refs", []):
            if ref not in known_refs:
                refs.append({
                    "id": ref,
                    "source": "occupancy",
                    "observed_at": event.get("observed_at"),
                })
                known_refs.add(ref)
    source_states = []
    for name, state in sorted(snap.get("sources", {}).items()):
        source_states.append({
            "name": name,
            "state": state if state in ("ok", "failed", "disabled") else "unknown",
            "observed_at": captured_at if state == "ok" else None,
        })
    stale = bool(snap.get("stale") or monitor_status.get("stale"))
    scan_complete = complete(snap)
    limitations = ["history_port_state_only"]
    if not scan_complete:
        limitations.append("scan_incomplete")
    if stale:
        limitations.append("scan_stale")
    history_document = _history(port)
    if history_document["state"] != "available":
        limitations.append("history_" + history_document["state"])
    if event_history_state != "available":
        limitations.append("event_history_" + event_history_state)
    return {
        "schema_version": 1,
        "capture_id": capture_id,
        "captured_at": captured_at,
        "core_version": core_version,
        "scope": {"kind": "local_hub"},
        "identity": {"port": port, "protocol": protocol},
        "requires_hidden_access": requires_hidden_access,
        "scan": {
            "ready": bool(monitor_status.get("ready")),
            "complete": scan_complete,
            "stale": stale,
            "sources": source_states,
        },
        "current": {
            "overall_status": row.get("status", "unknown"),
            "entries": entries,
        },
        "history": history_document,
        "events": recent_events,
        "evidence_refs": refs,
        "limitations": limitations,
    }
