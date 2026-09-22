"""Local port-occupancy history, stored in SQLite next to port_light.json.

Controlled by ``HISTORY_RETENTION_DAYS`` (default 7; ``0`` disables history).
State transitions and sanitized observation evidence are written locally.
Configured hubs can read state history through the authenticated peer API.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_primed = False
_last_sig: dict[int, str] = {}
_EVENT_KINDS = frozenset({
    "state_changed", "bind_scope_changed", "configuration_mismatch",
    "observation_degraded", "observation_recovered",
})
_EVENT_STATES = frozenset({"used", "configured", "free", "unknown"})
_EVENT_SCOPES = frozenset({"public", "lan", "link", "localhost"})
_CAPTURE_ID = re.compile(r"obs-[0-9a-f]{12}-[1-9][0-9]*")
_CAPTURE_PARTS = re.compile(r"obs-([0-9a-f]{12})-([1-9][0-9]*)")
_BATCH_EVENT_PORTS = 900


def retention_days() -> int:
    try:
        return max(0, int(os.environ.get("HISTORY_RETENTION_DAYS", "7")))
    except ValueError:
        return 7


def enabled() -> bool:
    return retention_days() > 0


def _db_path() -> str:
    data_dir = os.environ.get("PORT_LIGHT_DATA_DIR", "/data")
    return os.path.join(data_dir, "history.db")


def _connect() -> sqlite3.Connection | None:
    global _conn, _conn_path, _primed, _last_sig
    if not enabled():
        return None
    path = _db_path()
    if _conn is not None and _conn_path != path:
        _conn.close()
        _conn = None
        _conn_path = None
        _primed = False
        _last_sig = {}
    if _conn is None:
        conn = None
        try:
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "ts INTEGER NOT NULL, port INTEGER NOT NULL, state TEXT NOT NULL,"
                "holders TEXT NOT NULL DEFAULT '[]')"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_port_ts ON events(port, ts)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS observation_events ("
                "event_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, "
                "observed_at INTEGER NOT NULL, port INTEGER, kind TEXT NOT NULL, "
                "before_json TEXT NOT NULL, after_json TEXT NOT NULL, "
                "source_quality TEXT NOT NULL, requires_hidden_access INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observation_events_port_ts "
                "ON observation_events(port, observed_at)"
            )
            conn.commit()
        except sqlite3.Error:
            if conn is not None:
                conn.close()
            raise
        _conn = conn
        _conn_path = path
    return _conn


def reset() -> None:
    """Test hook: forget the baseline and close the handle."""
    global _conn, _conn_path, _primed, _last_sig
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _conn_path = None
        _primed = False
        _last_sig = {}


def _holders(row: dict) -> str:
    names = [c.get("name") for c in (row.get("containers") or []) if c.get("name")]
    label = row.get("manual_label")
    if label:
        names.append(label)
    return json.dumps(names[:8], ensure_ascii=False)


def record(rows: list[dict]) -> int:
    """Write one event per port whose status changed since the previous scan."""
    if not enabled():
        return 0
    now_sig = {row["port"]: row["status"] for row in rows}
    global _primed, _last_sig
    written = 0
    with _lock:
        conn = _connect()
        if conn is None:
            return 0
        if not _primed:
            _last_sig = now_sig
            _primed = True
            return 0
        changed = [
            {"port": p, "status": now_sig.get(p, "free")}
            for p in sorted(now_sig.keys() | _last_sig.keys())
            if _last_sig.get(p, "free") != now_sig.get(p, "free")
        ]
        if not changed:
            return 0
        ts = int(time.time())
        holders_by_port = {row["port"]: _holders(row) for row in rows}
        with conn:
            for ch in changed:
                port = ch["port"]
                conn.execute(
                    "INSERT INTO events (ts, port, state, holders) VALUES (?,?,?,?)",
                    (ts, port, ch["status"], holders_by_port.get(port) or "[]"),
                )
                written += 1
            cutoff = ts - retention_days() * 86400
            conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        _last_sig = now_sig
    return written


def query(port: int, hours: int = 24) -> list[dict]:
    with _lock:
        conn = _connect()
        if conn is None:
            return []
        cutoff = int(time.time()) - min(max(hours, 1), retention_days() * 24, 24 * 30) * 3600
        rows = conn.execute(
            "SELECT ts, state, holders FROM events WHERE port=? AND ts>=? ORDER BY ts, rowid",
            (port, cutoff),
        ).fetchall()
    out = []
    for ts, state, holders in rows:
        try:
            names = json.loads(holders)
        except json.JSONDecodeError:
            names = []
        out.append({"ts": ts, "state": state, "holders": names})
    return out


def _event_side(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    if set(value) == {"quality"} and value["quality"] in ("complete", "degraded"):
        return {"quality": value["quality"]}
    if set(value) != {"status", "protocols", "bind_scope", "compose_conflict"}:
        return None
    protocols = value["protocols"]
    if (value["status"] not in _EVENT_STATES or not isinstance(protocols, (list, tuple))
            or any(protocol not in ("tcp", "udp") for protocol in protocols)
            or len(set(protocols)) != len(protocols)
            or tuple(protocols) != tuple(sorted(protocols))
            or value["bind_scope"] not in _EVENT_SCOPES | {None}
            or type(value["compose_conflict"]) is not bool):
        return None
    return {
        "status": value["status"],
        "protocols": list(protocols),
        "bind_scope": value["bind_scope"],
        "compose_conflict": value["compose_conflict"],
    }


def _event_document(event: dict) -> tuple | None:
    event_id = event.get("event_id")
    observation_id = event.get("observation_id")
    observed_at = event.get("observed_at")
    port = event.get("port")
    kind = event.get("kind")
    before = _event_side(event.get("before"))
    after = _event_side(event.get("after"))
    source_quality = event.get("source_quality")
    requires_hidden = event.get("requires_hidden_access")
    if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 256
            or not isinstance(observation_id, str) or not 1 <= len(observation_id) <= 256
            or not _CAPTURE_ID.fullmatch(observation_id)
            or type(observed_at) is not int or observed_at < 0
            or (port is not None and (type(port) is not int or not 1 <= port <= 65535))
            or kind not in _EVENT_KINDS or before is None or after is None
            or source_quality not in ("complete", "degraded")
            or type(requires_hidden) is not bool):
        return None
    suffix = "global" if port is None else str(port)
    if event_id != f"{observation_id}:{kind}:{suffix}":
        return None
    if kind == "observation_degraded":
        if (port is not None or before != {"quality": "complete"}
                or after != {"quality": "degraded"} or source_quality != "degraded"):
            return None
    elif kind == "observation_recovered":
        if (port is not None or before != {"quality": "degraded"}
                or after != {"quality": "complete"} or source_quality != "complete"):
            return None
    elif port is None or "quality" in before or "quality" in after or source_quality != "complete":
        return None
    return (
        event_id, observation_id, observed_at, port, kind,
        json.dumps(before, separators=(",", ":"), sort_keys=True),
        json.dumps(after, separators=(",", ":"), sort_keys=True),
        source_quality, int(requires_hidden),
    )


def record_observation_events(events: list[dict]) -> int:
    """Persist bounded, sanitized deterministic observation events idempotently."""
    if not enabled() or not events:
        return 0
    documents = [document for event in events if (document := _event_document(event)) is not None]
    if not documents:
        return 0
    with _lock:
        conn = _connect()
        if conn is None:
            return 0
        with conn:
            written = 0
            for document in documents:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO observation_events "
                    "(event_id, observation_id, observed_at, port, kind, before_json, after_json, "
                    "source_quality, requires_hidden_access) VALUES (?,?,?,?,?,?,?,?,?)",
                    document,
                )
                written += cursor.rowcount
            cutoff = int(time.time()) - retention_days() * 86400
            conn.execute("DELETE FROM observation_events WHERE observed_at < ?", (cutoff,))
            return written


def query_observation_events(port: int, hours: int = 24, *, allow_hidden: bool = False) -> list[dict]:
    """Read recent sanitized events without exposing records gated as hidden."""
    if not enabled():
        return []
    cutoff = int(time.time()) - min(
        max(hours, 1), retention_days() * 24, 24 * 30,
    ) * 3600
    with _lock:
        conn = _connect()
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT event_id, observation_id, observed_at, port, kind, before_json, after_json, source_quality, "
            "requires_hidden_access "
            "FROM observation_events WHERE (port=? OR port IS NULL) AND observed_at>=? "
            "AND (requires_hidden_access=0 OR ?) ORDER BY observed_at DESC, rowid DESC LIMIT 128",
            (port, cutoff, int(allow_hidden)),
        ).fetchall()
    result = []
    for (event_id, observation_id, observed_at, event_port, kind, before_raw, after_raw,
         source_quality, requires_hidden) in reversed(rows):
        try:
            before = _event_side(json.loads(before_raw))
            after = _event_side(json.loads(after_raw))
        except (TypeError, json.JSONDecodeError):
            continue
        candidate = {
            "event_id": event_id,
            "observation_id": observation_id,
            "observed_at": observed_at,
            "port": event_port,
            "kind": kind,
            "before": before,
            "after": after,
            "source_quality": source_quality,
            "requires_hidden_access": bool(requires_hidden) if requires_hidden in (0, 1) else None,
        }
        if _event_document(candidate) is None:
            continue
        result.append({
            "schema_version": 1,
            "event_id": event_id,
            "observation_id": observation_id,
            "observed_at": observed_at,
            "port": event_port,
            "protocol": "all",
            "kind": kind,
            "before": before,
            "after": after,
            "evidence_refs": [f"history:{event_id}"],
            "source_quality": source_quality,
        })
    return result


def _capture_parts(value: str) -> tuple[str, int] | None:
    """Return the monitor namespace and sequence encoded in a capture id."""
    matched = _CAPTURE_PARTS.fullmatch(value)
    if matched is None:
        return None
    return matched.group(1), int(matched.group(2))


def _event_precedes_capture(event: dict, capture_id: str, captured_at: int) -> bool:
    """Keep a history read on the frozen side of a monitor capture boundary.

    Capture ids are strictly ordered while a monitor process is running.  Older
    monitor processes have unrelated namespaces, so their second-resolution
    timestamp is only safe when it is strictly before this capture.
    """
    event_parts = _capture_parts(str(event.get("observation_id") or ""))
    capture_parts = _capture_parts(capture_id)
    if event_parts is not None and capture_parts is not None and event_parts[0] == capture_parts[0]:
        return event_parts[1] <= capture_parts[1]
    return type(event.get("observed_at")) is int and event["observed_at"] < captured_at


def _event_order(event: dict) -> tuple:
    """Order same-second captures by the monitor's numeric sequence."""
    parts = _capture_parts(str(event.get("observation_id") or ""))
    if parts is None:
        namespace, sequence = "", 0
    else:
        namespace, sequence = parts
    return event["observed_at"], namespace, sequence, event["event_id"]


def _public_observation_event(row: tuple) -> dict | None:
    (
        event_id, observation_id, observed_at, event_port, kind, before_raw, after_raw,
        source_quality, requires_hidden,
    ) = row
    try:
        before = _event_side(json.loads(before_raw))
        after = _event_side(json.loads(after_raw))
    except (TypeError, json.JSONDecodeError):
        return None
    candidate = {
        "event_id": event_id,
        "observation_id": observation_id,
        "observed_at": observed_at,
        "port": event_port,
        "kind": kind,
        "before": before,
        "after": after,
        "source_quality": source_quality,
        "requires_hidden_access": bool(requires_hidden) if requires_hidden in (0, 1) else None,
    }
    if _event_document(candidate) is None:
        return None
    return {
        "schema_version": 1,
        "event_id": event_id,
        "observation_id": observation_id,
        "observed_at": observed_at,
        "port": event_port,
        "protocol": "all",
        "kind": kind,
        "before": before,
        "after": after,
        "evidence_refs": [f"history:{event_id}"],
        "source_quality": source_quality,
    }


def query_observation_events_batch(
    ports: set[int] | None,
    hours: int = 24,
    *,
    allow_hidden: bool = False,
    limit: int = 512,
    capture_id: str,
    captured_at: int,
) -> tuple[list[dict], bool]:
    """Read bounded events for many ports without one query per port.

    ``ports=None`` means all visible event ports in the requested window.  A
    global quality event is returned once regardless of selection.  The
    capture fence prevents a later observation from entering a response that
    already froze its current occupancy facts.
    """
    if not enabled():
        return [], False
    if type(limit) is not int or not 1 <= limit <= 1024:
        raise ValueError("invalid event limit")
    if type(captured_at) is not int or _capture_parts(capture_id) is None:
        raise ValueError("invalid capture boundary")
    selected = None
    if ports is not None:
        selected = sorted({port for port in ports if type(port) is int and 1 <= port <= 65535})
    cutoff = captured_at - min(max(hours, 1), retention_days() * 24, 24 * 30) * 3600
    fields = (
        "event_id, observation_id, observed_at, port, kind, before_json, after_json, "
        "source_quality, requires_hidden_access"
    )
    rows = []
    query_truncated = False
    with _lock:
        conn = _connect()
        if conn is None:
            return [], False
        chunks = [selected[index:index + _BATCH_EVENT_PORTS]
                  for index in range(0, len(selected), _BATCH_EVENT_PORTS)] if selected else [[]]
        for chunk in chunks:
            if selected is None:
                clause, params = "", []
            elif not chunk:
                clause, params = " AND port IS NULL", []
            else:
                placeholders = ",".join("?" for _ in chunk)
                clause = f" AND (port IS NULL OR port IN ({placeholders}))"
                params = chunk
            chunk_rows = conn.execute(
                "SELECT " + fields + " FROM observation_events "
                "WHERE observed_at>=? AND (requires_hidden_access=0 OR ?)" + clause + " "
                "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
                [cutoff, int(allow_hidden), *params, limit + 1],
            ).fetchall()
            query_truncated = query_truncated or len(chunk_rows) > limit
            rows.extend(chunk_rows)
    by_id = {}
    for row in rows:
        event = _public_observation_event(row)
        if event is None or not _event_precedes_capture(event, capture_id, captured_at):
            continue
        by_id[event["event_id"]] = event
    ordered = sorted(by_id.values(), key=_event_order, reverse=True)
    truncated = len(ordered) > limit or query_truncated
    # The query is newest-first so retain the newest complete set, then return
    # chronological order like the single-port history API.
    return list(reversed(ordered[:limit])), truncated
