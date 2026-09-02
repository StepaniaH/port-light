"""Background occupancy scanning and change publication.

The monitor is the single writer of occupancy snapshots. HTTP handlers read
the last completed snapshot and may reclassify it for a requested range or
visibility; they do not run Docker, listen-table, or Compose scans.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace

from . import degradations, history, webhooks


def _interval(values: dict) -> float:
    return max(1.0, min(float(values.get("refresh_ms", 5000)) / 1000.0, 30.0))


class SnapshotUnavailable(Exception):
    """No completed occupancy snapshot is available yet."""


def _scan_timeout() -> float:
    try:
        return max(1.0, min(60.0, float(os.environ.get("PORT_LIGHT_SCAN_TIMEOUT_S", "10"))))
    except ValueError:
        return 10.0


def complete(snap: dict) -> bool:
    return bool(snap.get("scan_complete")) and not snap.get("stale", False)


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
        self._publish_lock = threading.Lock()
        self._observation_lock = threading.Lock()
        self._pending_observations: deque[list[dict]] = deque(maxlen=128)
        self._job: Future | None = None
        self._running = False
        self._epoch = 0
        self._interval = 5.0
        self._failed = False
        self._latest: dict | None = None
        self._fingerprint = ""
        self._sequence = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refresh_event: asyncio.Event | None = None
        self._change_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._epoch += 1
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._refresh_event = asyncio.Event()
        self._change_event = asyncio.Event()
        await self._scan_once()
        self._task = asyncio.create_task(self._run(), name="occupancy-monitor")

    async def stop(self) -> None:
        self._stopping = True
        self._epoch += 1
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._running = False
        self._task = None
        self._refresh_event = None
        self._change_event = None
        self._loop = None

    async def _scan_once(self) -> None:
        # A timed-out scan may still be blocked in the OS. Keep its slot until
        # it exits, discard its late result, and never accumulate worker threads.
        if self._job is not None:
            if not self._job.done():
                self._fail()
                return
            self._job = None
        job: Future = Future()
        self._job = job
        epoch = self._epoch
        timeout = _scan_timeout()
        deadline = time.monotonic() + timeout

        def valid():
            return self._running and self._epoch == epoch and time.monotonic() < deadline

        def scan():
            try:
                values = self._values()
                key = self._scan_key(values)
                snap = self._build(values)
                snap["key"] = key
                job.set_result(self._publish_scan(snap, valid=valid))
            except Exception as exc:
                job.set_exception(exc)

        threading.Thread(target=scan, name="occupancy-scan", daemon=True).start()
        try:
            # asyncio.wait leaves the worker's future intact on cancellation.
            wrapped = asyncio.wrap_future(job)
            wrapped.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            done, _ = await asyncio.wait({wrapped}, timeout=timeout)
            if not done:
                self._fail()
                return
            self._job = None
            if wrapped.result() is None:
                self._fail()
        except Exception:
            self._fail()

    def _fail(self) -> None:
        with self._lock:
            if not self._failed:
                self._sequence += 1
                self._fingerprint = ""
            self._failed = True
        degradations.report("monitor", "scan", "occupancy refresh failed or timed out")
        self._signal_waiters()

    async def _run(self) -> None:
        while not self._stopping:
            interval = self._interval
            wake = self._refresh_event
            if wake is None:
                return
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval)
                wake.clear()
            except TimeoutError:
                pass
            await self._scan_once()

    def wake(self) -> None:
        loop = self._loop
        wake = self._refresh_event
        if loop is not None and wake is not None:
            loop.call_soon_threadsafe(wake.set)

    def refresh(self) -> dict:
        """Synchronous scan for callers outside the application lifespan."""
        values = self._values()
        key = self._scan_key(values)
        snap = self._build(values)
        snap["key"] = key
        return self._publish_scan(snap)

    def _publish_scan(self, snap: dict, valid: Callable[[], bool] | None = None) -> dict | None:
        # Only publication and stored-state updates serialize. No host scanner
        # holds this lock, and a finished scan reloads any intervening writes.
        with self._publish_lock:
            if valid is not None and not valid():
                return None
            values = self._values()
            previous = self._latest
            sources = snap.get("sources", {})
            if previous is not None and previous.get("key") == snap.get("key"):
                for source, field in (("listen", "listening"), ("docker", "containers")):
                    if sources.get(source) == "failed":
                        snap[field] = previous[field]
                if sources.get("compose") == "failed":
                    snap["compose_scan"] = replace(
                        snap["compose_scan"], ports=previous["compose_scan"].ports)
            snap["scan_complete"] = bool(sources) and all(
                state in ("ok", "disabled") for state in sources.values())
            snap["user_state"] = self._load_state()
            snap["state_key"] = self._state_key()
            snap["at"] = time.monotonic()
            snap["packed"] = {}
            if snap["key"] != self._scan_key(values):
                snap["stale"] = True
            if not self._accept(snap, values, valid=valid, scanned=True):
                return None
        self._observe_pending(valid)
        return snap

    def state_changed(self) -> None:
        """Publish committed user state without waiting for a host scan."""
        try:
            with self._publish_lock:
                previous = self._latest
                if previous is None:
                    self.wake()
                    return
                values = self._values()
                snap = dict(previous)
                snap["user_state"] = self._load_state()
                snap["state_key"] = self._state_key()
                snap["packed"] = {}
                if self._is_stale(snap, values):
                    snap["stale"] = True
                    self.wake()
                self._accept(snap, values)
            self.wake()
        except Exception:
            # The write has committed. A publication error must not discard an
            # allocation token or misreport the durable write as unsuccessful.
            self._fail()
            self.wake()

    def _accept(self, snap: dict, values: dict, *,
                valid: Callable[[], bool] | None = None, scanned: bool = False) -> bool:
        snap["max_age"] = max(4.0, _interval(values) * 2.0)
        full = self._classify(snap, values, 1, 65535)
        rows = full["ports"]
        compose_scan = snap["compose_scan"]
        fingerprint_body = {
            "ports": rows,
            "summary": full["summary"],
            "compose_truncated": compose_scan.truncated,
            "compose_incomplete": compose_scan.incomplete,
            "sources": snap.get("sources"),
            "stale": snap.get("stale", False),
            "scan_key": snap.get("key"),
            "state_key": snap.get("state_key"),
        }
        digest = hashlib.sha256(json.dumps(
            fingerprint_body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")).hexdigest()
        changed = False
        with self._lock:
            if valid is not None and not valid():
                return False
            if scanned:
                self._failed = False
            self._interval = _interval(values)
            if digest != self._fingerprint:
                self._fingerprint = digest
                self._sequence += 1
                changed = True
            self._latest = snap
        if changed:
            self._signal_waiters()
        if complete(snap):
            if len(self._pending_observations) == self._pending_observations.maxlen:
                degradations.report("monitor", "observations", "queue full; intermediate changes omitted")
            self._pending_observations.append(rows)
        return True

    def _observe_pending(self, valid: Callable[[], bool] | None = None) -> None:
        # Observation order follows publication order. Slow SQLite/webhook work
        # never holds the publication lock or delays an HTTP write response.
        with self._observation_lock:
            while valid is None or valid():
                with self._publish_lock:
                    if not self._pending_observations:
                        return
                    rows = self._pending_observations.popleft()
                webhooks.observe(rows)
                try:
                    history.record(rows)
                except sqlite3.Error:
                    degradations.report("history", "history.db", "occupancy history write failed")

    def _signal_waiters(self) -> None:
        loop = self._loop
        wake = self._change_event
        if loop is not None and wake is not None:
            loop.call_soon_threadsafe(wake.set)

    def _is_stale(self, snap: dict, values: dict) -> bool:
        return (self._failed or bool(snap.get("stale"))
                or snap.get("key") != self._scan_key(values)
                or time.monotonic() - snap["at"] > max(4.0, _interval(values) * 2.0))

    def status(self) -> dict:
        """Readiness without scanner I/O or access to possibly broken settings."""
        with self._lock:
            snap = self._latest
        age = max(0.0, time.monotonic() - snap["at"]) if snap else None
        ready = bool(snap and complete(snap) and not self._failed and age <= snap["max_age"])
        return {"ready": ready, "initialized": snap is not None,
                "scan_age_seconds": round(age, 1) if age is not None else None,
                "sources": dict(snap.get("sources", {})) if snap else {}}

    def latest(self, values: dict | None = None) -> dict:
        """Return a completed snapshot; HTTP never scans during lifespan."""
        values = values or self._values()
        with self._lock:
            snap = self._latest
        if snap is None:
            if self._running:
                raise SnapshotUnavailable("occupancy snapshot unavailable; retry later")
            return self.refresh()
        if snap.get("key") != self._scan_key(values) and not self._running:
            return self.refresh()
        if snap.get("state_key") != self._state_key():
            self.state_changed()
            snap = self._latest or snap
        if self._is_stale(snap, values):
            self.wake()
            return {**snap, "stale": True}
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
            self._pending_observations.clear()
            self._fingerprint = ""
            self._sequence = 0
            self._failed = False

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
            "scan_complete": complete(snap),
            "sources": snap.get("sources", {}),
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
