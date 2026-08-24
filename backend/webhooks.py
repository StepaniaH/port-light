"""Optional outbound webhook notifications.

The owner opts in via environment variables:

- ``WEBHOOK_URL``          full http(s) target; empty disables everything
- ``WEBHOOK_SECRET``       sent as the ``X-Port-Light-Secret`` header
- ``WEBHOOK_EVENTS``       comma list: ``new_listener``, ``conflict``

Delivery is fire-and-forget from a daemon thread (3s timeout, no retries).
Failures surface as degradation events. Unlike peer URLs, public targets are
allowed here — the owner deliberately chose this destination.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request

from . import degradations

_lock = threading.Lock()
_seen_used: set[int] = set()
_seen_conflicts: set[int] = set()
_primed = False


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _events_enabled() -> set[str]:
    raw = os.environ.get("WEBHOOK_EVENTS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def deliver(url: str, secret: str, body: dict) -> None:
    """POST one JSON event. Runs in a caller-provided thread."""
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Port-Light-Secret"] = secret
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 (owner-configured)
            resp.read(1024)
    except Exception as exc:
        host = getattr(exc, "filename", "") or url.split("/")[2] if "/" in url else url
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
    if not (url.startswith("http://") or url.startswith("https://")):
        return
    secret = os.environ.get("WEBHOOK_SECRET", "")
    wanted = _events_enabled()
    want_new = "new_listener" in wanted
    want_conflict = "conflict" in wanted
    if not (want_new or want_conflict):
        return

    now_used = {row["port"] for row in rows if row.get("status") == "used"}
    now_conflict = {row["port"] for row in rows if row.get("conflict")}

    global _primed, _seen_used, _seen_conflicts
    with _lock:
        if not _primed:
            _seen_used, _seen_conflicts, _primed = now_used, now_conflict, True
            return
        new_used = now_used - _seen_used
        new_conflicts = now_conflict - _seen_conflicts
        _seen_used |= now_used
        _seen_conflicts |= now_conflict

    deliver_fn = deliver or deliver_default
    events: list[tuple[str, int]] = []
    if want_new:
        events.extend(("new_listener", p) for p in sorted(new_used))
    if want_conflict:
        events.extend(("conflict", p) for p in sorted(new_conflicts))

    def _send(batch: list[tuple[str, int]]) -> None:
        for event, port in batch[:max_events]:
            try:
                deliver_fn(url, secret, {"event": event, "port": port})
            except Exception:
                degradations.report("webhook", "target", "delivery failed")

    if events:
        threading.Thread(target=_send, args=(events,), daemon=True).start()
