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
from collections import OrderedDict, deque

log = logging.getLogger("port-light")

_lock = threading.Lock()
_recent: deque = deque(maxlen=20)
_logged: OrderedDict[tuple[str, str, str], float] = OrderedDict()

_REPEAT_LOG_SECONDS = 60.0
_MAX_LOG_KEYS = 8192


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
        tick = time.monotonic()
        last = next((event for event in reversed(_recent)
                     if (event["source"], event["scope"], event["reason"]) == key), None)
        if last is not None:
            _recent.remove(last)
            last["ts"] = now
            _recent.append(last)
        else:
            last = {"source": source, "scope": scope, "reason": reason, "ts": now}
            _recent.append(last)
        # Log retention is independent of the short health-event history.
        # Entries are ordered by log time; expire them before admitting new keys.
        while _logged and tick - next(iter(_logged.values())) >= _REPEAT_LOG_SECONDS:
            _logged.popitem(last=False)
        # Bound memory and log volume even when every failure has a new scope.
        should_log = key not in _logged and len(_logged) < _MAX_LOG_KEYS
        if should_log:
            _logged[key] = tick
    if should_log:
        log.warning("degraded source=%s scope=%s reason=%s", source, scope, reason)


def recent(limit: int = 5) -> list[dict]:
    """The newest events, oldest first, capped at ``limit`` entries."""
    with _lock:
        return [dict(event) for event in list(_recent)[-limit:]] if limit > 0 else []


def reset() -> None:
    """Test hook."""
    with _lock:
        _recent.clear()
        _logged.clear()
