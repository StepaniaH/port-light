"""Usage events for the agent suggest API, stored in history.db.

One row per successful GET /api/ports/suggest call. Rows carry counts,
scope, caller-supplied label, and whether a reservation was requested —
never tokens, IPs, or host names. Retention follows HISTORY_RETENTION_DAYS
like the occupancy events table; ``0`` disables recording entirely.
"""

from __future__ import annotations

import os
import sqlite3
import threading

from .history import enabled, retention_days

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


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
                "CREATE TABLE IF NOT EXISTS agent_events ("
                "ts INTEGER NOT NULL, count INTEGER NOT NULL,"
                "scope TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',"
                "leased INTEGER NOT NULL DEFAULT 0)"
            )
            _conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_events_ts"
                " ON agent_events(ts)"
            )
            _conn.commit()
        except sqlite3.Error:
            _conn = None
    return _conn


def reset() -> None:
    """Test hook: close the handle so env changes take effect."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def record(count: int, scope: str, label: str, leased: bool) -> None:
    import time

    with _lock:
        conn = _connect()
        if conn is None:
            return
        ts = int(time.time())
        conn.execute(
            "INSERT INTO agent_events (ts, count, scope, label, leased)"
            " VALUES (?,?,?,?,?)",
            (ts, count, scope, label[:120], 1 if leased else 0),
        )
        conn.execute(
            "DELETE FROM agent_events WHERE ts < ?",
            (ts - retention_days() * 86400,),
        )
        conn.commit()


def _query(query: str, params: tuple = ()) -> list[tuple]:
    with _lock:
        conn = _connect()
        if conn is None:
            return []
        return list(conn.execute(query, params))


def recent(limit: int = 10) -> list[dict]:
    rows = _query(
        "SELECT ts, count, scope, label, leased FROM agent_events"
        " ORDER BY ts DESC, rowid DESC LIMIT ?",
        (max(1, min(int(limit), 50)),),
    )
    return [
        {"ts": ts, "count": c, "scope": s, "label": lab, "leased": bool(d)}
        for ts, c, s, lab, d in rows
    ]


def total_calls() -> int:
    rows = _query("SELECT COUNT(*) FROM agent_events")
    return int(rows[0][0]) if rows else 0


def last_used_at() -> int | None:
    rows = _query("SELECT MAX(ts) FROM agent_events")
    if rows and rows[0][0]:
        return int(rows[0][0])
    return None


def summary(limit: int = 10) -> dict:
    return {
        "total": total_calls(),
        "last_used_at": last_used_at(),
        "recent": recent(limit),
    }
