"""Local port-occupancy history, stored in SQLite next to port_light.json.

Opt-in via ``HISTORY_RETENTION_DAYS`` (default 7; ``0`` disables the feature
entirely). Only state transitions are written, so a quiet host costs nothing.
Everything stays inside the data volume — nothing is exported anywhere.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_primed = False
_last_sig: dict[int, str] = {}


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
    global _conn
    if not enabled():
        return None
    if _conn is None:
        try:
            _conn = sqlite3.connect(_db_path(), check_same_thread=False)
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "ts INTEGER NOT NULL, port INTEGER NOT NULL, state TEXT NOT NULL,"
                "holders TEXT NOT NULL DEFAULT '[]')"
            )
            _conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_port_ts ON events(port, ts)"
            )
            _conn.commit()
        except sqlite3.Error:
            _conn = None
    return _conn


def reset() -> None:
    """Test hook: forget the baseline and close the handle."""
    global _conn, _primed, _last_sig
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
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
