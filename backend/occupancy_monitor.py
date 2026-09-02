"""Background occupancy scanning and change publication.

The monitor is the single writer of occupancy snapshots. HTTP handlers read
the last completed snapshot and may reclassify it for a requested range or
visibility; they do not run Docker, listen-table, or Compose scans.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable

from . import degradations, history, webhooks


def _interval(values: dict) -> float:
    return max(1.0, min(float(values.get("refresh_ms", 5000)) / 1000.0, 30.0))


class OccupancyMonitor:
    """Own scanning, snapshot caching, observation, and change signals."""

    def __init__(
        self,
        *,
        values: Callable[[], dict],
        scan_key: Callable[[dict], tuple],
        state_key: Callable[[], tuple],
        build: Callable[[dict], dict],
        load_state: Callable[[], tuple[list[dict], list[int]]],
        classify: Callable[..., dict],
    ) -> None:
        self._values = values
        self._scan_key = scan_key
        self._state_key = state_key
        self._build = build
        self._load_state = load_state
        self._classify = classify
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._latest: dict | None = None
        self._fingerprint = ""
        self._sequence = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refresh_event: asyncio.Event | None = None
        self._change_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._refresh_event = asyncio.Event()
        self._change_event = asyncio.Event()
        await asyncio.to_thread(self.refresh)
        self._task = asyncio.create_task(self._run(), name="occupancy-monitor")

    async def stop(self) -> None:
        self._stopping = True
        self.wake()
        if self._task is not None:
            await self._task
        self._task = None
        self._refresh_event = None
        self._change_event = None
        self._loop = None

    async def _run(self) -> None:
        while not self._stopping:
            values = self._values()
            interval = _interval(values)
            wake = self._refresh_event
            if wake is None:
                return
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval)
                wake.clear()
            except TimeoutError:
                pass
            if self._stopping:
                return
            try:
                await asyncio.to_thread(self.refresh)
            except Exception:
                degradations.report("monitor", "scan", "occupancy refresh failed")

    def wake(self) -> None:
        loop = self._loop
        wake = self._refresh_event
        if loop is not None and wake is not None:
            loop.call_soon_threadsafe(wake.set)

    def refresh(self) -> dict:
        """Run one complete scan. Production calls this from the monitor task."""
        with self._refresh_lock:
            values = self._values()
            snap = self._build(values)
            snap["key"] = self._scan_key(values)
            snap["user_state"] = self._load_state()
            snap["state_key"] = self._state_key()
            snap["at"] = time.monotonic()
            snap["packed"] = {}
            self._accept(snap, values)
            return snap

    def state_changed(self) -> None:
        """Refresh stored manual/hidden state without rerunning host scanners."""
        with self._refresh_lock:
            with self._lock:
                previous = self._latest
            if previous is None:
                self.wake()
                return
            values = self._values()
            snap = dict(previous)
            snap["user_state"] = self._load_state()
            snap["state_key"] = self._state_key()
            snap["packed"] = {}
            if snap.get("key") != self._scan_key(values):
                snap["stale"] = True
                self.wake()
            else:
                snap.pop("stale", None)
            self._accept(snap, values)

    def _accept(self, snap: dict, values: dict) -> None:
        full = self._classify(snap, values, 1, 65535)
        rows = full["ports"]
        webhooks.observe(rows)
        compose_scan = snap["compose_scan"]
        if not compose_scan.incomplete and not compose_scan.truncated:
            try:
                history.record(rows)
            except sqlite3.Error:
                degradations.report("history", "history.db", "occupancy history write failed")
        fingerprint_body = {
            "ports": rows,
            "summary": full["summary"],
            "compose_truncated": compose_scan.truncated,
            "compose_incomplete": compose_scan.incomplete,
            "scan_key": snap.get("key"),
            "state_key": snap.get("state_key"),
        }
        digest = hashlib.sha256(json.dumps(
            fingerprint_body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")).hexdigest()
        changed = False
        with self._lock:
            if digest != self._fingerprint:
                self._fingerprint = digest
                self._sequence += 1
                changed = True
            self._latest = snap
        if changed:
            self._signal_waiters()

    def _signal_waiters(self) -> None:
        loop = self._loop
        wake = self._change_event
        if loop is not None and wake is not None:
            loop.call_soon_threadsafe(wake.set)

    def latest(self, values: dict | None = None) -> dict:
        """Return the latest completed snapshot, bootstrapping only outside lifespan."""
        values = values or self._values()
        wanted_key = self._scan_key(values)
        with self._lock:
            snap = self._latest
            running = self._task is not None
        if snap is None or (snap.get("key") != wanted_key and not running):
            return self.refresh()
        if not running and snap.get("state_key") != self._state_key():
            self.state_changed()
            with self._lock:
                snap = self._latest or snap
        if snap.get("key") != wanted_key:
            self.wake()
            stale = dict(snap)
            stale["stale"] = True
            return stale
        age = time.monotonic() - float(snap.get("at", 0.0))
        interval = _interval(values)
        if age > max(4.0, interval * 2.0):
            stale = dict(snap)
            stale["stale"] = True
            return stale
        return snap

    def sequence(self) -> int:
        with self._lock:
            return self._sequence

    async def wait_for_change(self, after: int, timeout: float = 15.0) -> tuple[int, bool]:
        wake = self._change_event
        if wake is None:
            await asyncio.sleep(min(timeout, 0.5))
            return self.sequence(), self.sequence() > after
        while self.sequence() <= after:
            wake.clear()
            if self.sequence() > after:
                break
            try:
                await asyncio.wait_for(wake.wait(), timeout=timeout)
            except TimeoutError:
                return self.sequence(), False
        return self.sequence(), True

    def reset(self) -> None:
        """Test hook: discard snapshots and observation fingerprints."""
        with self._lock:
            self._latest = None
            self._fingerprint = ""
            self._sequence = 0

    def packed(self, values: dict, start: int, end: int,
               show_hidden: bool, hidden_locked: bool) -> tuple[dict, str, str]:
        """Classify and serialize a range from the latest completed scan."""
        snap = self.latest(values)
        key = (start, end, show_hidden, hidden_locked)
        with self._lock:
            cached = snap["packed"].get(key)
        if cached is not None and not snap.get("stale"):
            return cached
        result = self._classify(snap, values, start, end, show_hidden, hidden_locked)
        result["summary"].update({
            "compose_truncated": snap["compose_scan"].truncated,
            "compose_incomplete": snap["compose_scan"].incomplete,
            "compose_files": snap["compose_scan"].files_scanned,
        })
        if snap.get("stale"):
            result["summary"]["stale"] = True
        body = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] + '"'
        packed = (result, body, etag)
        with self._lock:
            if self._latest is snap and not snap.get("stale"):
                # Bound arbitrary client-supplied ranges within each snapshot.
                if len(snap["packed"]) >= 128:
                    snap["packed"].pop(next(iter(snap["packed"])))
                snap["packed"][key] = packed
        return packed
