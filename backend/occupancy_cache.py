"""Occupancy snapshot cache.

Concurrent callers share one in-flight scan; a finished snapshot is reused
for ``ttl`` seconds; when a rebuild outruns ``stale_after`` waiters get the
last good snapshot marked ``stale`` instead of blocking. Memoized
classification results hang off each snapshot keyed by range and visibility.
"""

from __future__ import annotations

import threading
import time


def pack_key(start: int, end: int, show_hidden: bool, hidden_locked: bool) -> tuple:
    return (start, end, show_hidden, hidden_locked)


class OccupancyCache:
    def __init__(self, ttl: float = 2.0, stale_after: float = 4.0) -> None:
        self.ttl = ttl
        self.stale_after = stale_after
        self._cond = threading.Condition()
        self._snap: dict | None = None
        self._building = False

    def get_or_build(self, key, build) -> dict:
        now = time.monotonic()
        deadline = now + min(2 * self.ttl, self.stale_after)
        with self._cond:
            snap = self._snap
            if snap and snap["key"] == key and now - snap["at"] < self.ttl:
                return snap
            while self._building:
                remaining = deadline - time.monotonic()
                if remaining <= 0 and snap is not None:
                    stale = dict(snap)
                    stale["stale"] = True
                    return stale
                self._cond.wait(timeout=0.25 if remaining <= 0 else min(0.25, remaining))
                snap = self._snap
                now = time.monotonic()
                if snap and snap["key"] == key and now - snap["at"] < self.ttl:
                    return snap
            self._building = True
        try:
            snap = build()
            snap["key"] = key
            snap["at"] = time.monotonic()
            snap.setdefault("packed", {})
            with self._cond:
                self._snap = snap
                return snap
        finally:
            with self._cond:
                self._building = False
                self._cond.notify_all()

    def snapshot(self) -> dict | None:
        with self._cond:
            return self._snap

    def reset(self) -> None:
        with self._cond:
            self._snap = None
            self._building = False

    def lookup_packed(self, snap: dict, key: tuple):
        with self._cond:
            return snap.get("packed", {}).get(key)

    def remember_packed(self, snap: dict, key: tuple, packed) -> None:
        with self._cond:
            snap.setdefault("packed", {})[key] = packed

    def visibility_entries(self, snap: dict, show_hidden: bool, hidden_locked: bool) -> list:
        """Memoized ``(start, end, packed)`` triples for one visibility pair."""
        with self._cond:
            packed_map = snap.get("packed") or {}
        return [
            (start, end, packed)
            for (start, end, sh, hl), packed in packed_map.items()
            if sh == show_hidden and hl == hidden_locked
        ]
