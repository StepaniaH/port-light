"""Optional outbound webhook notifications.

The owner opts in via environment variables:

- ``WEBHOOK_URL``          full http(s) target; empty disables everything
- ``WEBHOOK_SECRET``       sent as the ``X-Port-Light-Secret`` header
- ``WEBHOOK_EVENTS``       comma list: ``new_listener``, ``conflict``
- ``WEBHOOK_COOLDOWN_S``   optional per-(event, port) debounce seconds (default 0)

Delivery is fire-and-forget from a daemon thread (3s timeout, no retries).
Failures surface as degradation events. Unlike peer URLs, public targets are
allowed here — the owner deliberately chose this destination.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from urllib.parse import urlparse

from . import degradations

_lock = threading.Lock()
_seen_used: set[int] = set()
_seen_conflicts: set[int] = set()
_last_sent: dict[tuple[str, int], float] = {}
_primed = False
_DEFAULT_URLOPEN = urllib.request.urlopen


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def reset() -> None:
    """Test hook: clear observed baseline and cooldown state."""
    global _primed
    with _lock:
        _primed = False
        _seen_used.clear()
        _seen_conflicts.clear()
        _last_sent.clear()


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _events_enabled() -> set[str]:
    raw = os.environ.get("WEBHOOK_EVENTS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _cooldown_seconds() -> float:
    raw = os.environ.get("WEBHOOK_COOLDOWN_S", "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _open_webhook(req: urllib.request.Request, timeout: float = 3.0):
    if urllib.request.urlopen is not _DEFAULT_URLOPEN:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (test hook)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    return opener.open(req, timeout=timeout)


def deliver(url: str, secret: str, body: dict) -> None:
    """POST one JSON event. Runs in a caller-provided thread."""
    parsed = urlparse(url)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Port-Light-Secret"] = secret
    try:
        if parsed.scheme not in ("http", "https"):
            raise ValueError("webhook URL must be http or https")
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
        )
        with _open_webhook(req, timeout=3) as resp:
            resp.read(1024)
    except Exception:
        host = parsed.hostname or "target"
        degradations.report("webhook", str(host)[:80], "delivery failed")


def deliver_default(url: str, secret: str, body: dict) -> None:
    deliver(url, secret, body)


def observe(rows: list[dict], max_events: int = 10, deliver=None) -> None:
    """Diff the latest classified rows against the previous scan.

    Fires at most ``max_events`` posts per scan; a cold start only primes the
    baseline so restarting Port-Light never spams every known listener.
    """
    url = os.environ.get("WEBHOOK_URL", "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        # Same policy as peer URLs: credentials belong in env, not the URL.
        return
    secret = os.environ.get("WEBHOOK_SECRET", "")
    wanted = _events_enabled()
    want_new = "new_listener" in wanted
    want_conflict = "conflict" in wanted
    if not (want_new or want_conflict):
        return

    now_used = {row["port"] for row in rows if row.get("status") == "used"}
    now_conflict = {row["port"] for row in rows if row.get("conflict")}
    now = time.monotonic()
    cooldown = _cooldown_seconds()
    events: list[tuple[str, int]] = []

    global _primed, _seen_used, _seen_conflicts
    with _lock:
        if not _primed:
            _seen_used, _seen_conflicts, _primed = set(now_used), set(now_conflict), True
            return
        rising_used = now_used - _seen_used
        rising_conflicts = now_conflict - _seen_conflicts
        _seen_used = set(now_used)
        _seen_conflicts = set(now_conflict)
        candidates: list[tuple[str, int]] = []
        if want_new:
            candidates.extend(("new_listener", p) for p in sorted(rising_used))
        if want_conflict:
            candidates.extend(("conflict", p) for p in sorted(rising_conflicts))
        for key in candidates:
            last = _last_sent.get(key)
            if cooldown > 0.0 and last is not None and now - last < cooldown:
                continue
            events.append(key)
            if len(events) == max_events:
                break
        for key in events:
            _last_sent[key] = now
        if len(_last_sent) > 4096:
            cutoff = now - cooldown
            for key, ts in list(_last_sent.items()):
                if ts <= cutoff:
                    del _last_sent[key]

    deliver_fn = deliver or deliver_default

    def _send(batch: list[tuple[str, int]]) -> None:
        for event, port in batch:
            try:
                deliver_fn(url, secret, {"event": event, "port": port})
            except Exception:
                degradations.report("webhook", "target", "delivery failed")

    if events:
        threading.Thread(target=_send, args=(events,), daemon=True).start()
