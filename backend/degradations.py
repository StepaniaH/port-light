"""Degraded-scan reporting.

Scanners degrade to honest-empty results in several places (Docker daemon
down, untrusted listen table, unreadable Compose file, corrupt data file).
This module records those events so "no occupancy" stays distinguishable
from "could not scan". Events are kept in process memory and mirrored to
the ``port-light`` logger; nothing leaves the machine.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

log = logging.getLogger("port-light")

_lock = threading.Lock()
_recent: deque = deque(maxlen=20)

_REPEAT_LOG_SECONDS = 60.0


def report(source: str, scope: str, reason: str) -> None:
    """Record one degraded event.

    ``source`` names the scanner ("docker", "compose", "listen", "store"),
    ``scope`` narrows it down (a scan-root-relative path or a component),
    and ``reason`` is a short constant string. Callers must never include
    secrets or environment values.
    """
    key = (source, scope, reason)
    now = int(time.time())
    with _lock:
        last = _recent[-1] if _recent else None
        if last is not None and (last["source"], last["scope"], last["reason"]) == key:
            last["ts"] = now
        else:
            last = {"source": source, "scope": scope, "reason": reason, "ts": now}
            _recent.append(last)
        should_log = (
            last.get("logged_at") is None
            or (time.monotonic() - last["logged_at"]) >= _REPEAT_LOG_SECONDS
        )
        if should_log:
            last["logged_at"] = time.monotonic()
    if should_log:
        log.warning("degraded source=%s scope=%s reason=%s", source, scope, reason)


def recent(limit: int = 5) -> list[dict]:
    """The newest events, oldest first, capped at ``limit`` entries."""
    with _lock:
        items = list(_recent)
    return [
        {k: v for k, v in event.items() if k != "logged_at"}
        for event in items[-max(0, limit):]
    ]


def reset() -> None:
    """Test hook."""
    with _lock:
        _recent.clear()
