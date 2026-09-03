"""Degraded-scan reporting.

Records scanner and storage failures in process memory and the ``port-light``
logger. Recent events are also returned by the health API, including through
peer health requests. Callers supply a short reason and a scope without secrets;
the health route redacts scopes from anonymous responses when auth is configured.
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

    ``source`` names the scanner or module (for example "docker" or "history"),
    ``scope`` narrows it down (a scan-root-relative path or a component),
    and ``reason`` is a short constant string. Callers must never include
    secrets or environment values.
    """
    key = (source, scope, reason)
    now = int(time.time())
    with _lock:
        last = next((event for event in reversed(_recent)
                     if (event["source"], event["scope"], event["reason"]) == key), None)
        if last is not None:
            _recent.remove(last)
            last["ts"] = now
            _recent.append(last)
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
