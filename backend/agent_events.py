"""Usage events for the agent suggest API, stored in history.db.

One row per successful GET /api/ports/suggest call. Rows carry counts,
scope, caller-supplied label, and whether a reservation was requested —
never tokens, IPs, or host names. Retention follows HISTORY_RETENTION_DAYS
like the occupancy events table; ``0`` disables recording entirely.
"""

from __future__ import annotations

import sqlite3
import time

from .history import _db_path as _db_path
from .history import connection, db_lock, retention_days
from .history import enabled as enabled
from .history import reset as _reset_history


def reset() -> None:
    """Test hook: close the shared handle so env changes take effect."""
    _reset_history()


def record(count: int, scope: str, label: str, leased: bool) -> None:
    with db_lock():
        conn = connection()
        if conn is None:
            return
        ts = int(time.time())
        with conn:
            conn.execute(
                "INSERT INTO agent_events (ts, count, scope, label, leased)"
                " VALUES (?,?,?,?,?)",
                (ts, count, scope, label[:120], 1 if leased else 0),
            )
            conn.execute(
                "DELETE FROM agent_events WHERE ts < ?",
                (ts - retention_days() * 86400,),
            )


def _query(query: str, params: tuple = ()) -> list[tuple]:
    try:
        with db_lock():
            conn = connection()
            if conn is None:
                return []
            return conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []


def _recent_rows(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT ts, count, scope, label, leased FROM agent_events"
        " ORDER BY ts DESC, rowid DESC LIMIT ?",
        (max(1, min(int(limit), 50)),),
    ).fetchall()
    return [
        {"ts": ts, "count": c, "scope": s, "label": lab, "leased": bool(d)}
        for ts, c, s, lab, d in rows
    ]


def recent(limit: int = 10) -> list[dict]:
    try:
        with db_lock():
            conn = connection()
            if conn is None:
                return []
            return _recent_rows(conn, limit)
    except sqlite3.Error:
        return []


def total_calls() -> int:
    rows = _query("SELECT COUNT(*) FROM agent_events")
    return int(rows[0][0]) if rows else 0


def last_used_at() -> int | None:
    rows = _query("SELECT MAX(ts) FROM agent_events")
    if rows and rows[0][0]:
        return int(rows[0][0])
    return None


def summary(limit: int = 10) -> dict:
    try:
        with db_lock():
            conn = connection()
            if conn is None:
                return {"total": 0, "last_used_at": None, "recent": []}
            row = conn.execute(
                "SELECT COUNT(*), MAX(ts) FROM agent_events"
            ).fetchone()
            total = int(row[0]) if row and row[0] else 0
            last_used = int(row[1]) if row and row[1] else None
            return {
                "total": total,
                "last_used_at": last_used,
                "recent": _recent_rows(conn, limit),
            }
    except sqlite3.Error:
        return {"total": 0, "last_used_at": None, "recent": []}
