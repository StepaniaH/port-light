"""Build a diagnostics document from an allowlisted, already-observed fact set.

The report deliberately accepts only aggregate booleans, counts, enum-like
states, and constant failure reasons. It never receives names, URLs, ports,
paths, credentials, environment values, or degradation scopes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

_STATUSES = ("pass", "warning", "fail", "info")
_SAFE_SOURCES = frozenset({
    "compose", "docker", "history", "listen", "monitor", "settings",
    "store", "suggest", "themes", "webhook",
})
_SAFE_REASONS = frozenset({
    "corrupt file quarantined",
    "could not record allocation event",
    "data file unreadable or invalid",
    "delivery failed",
    "directory unavailable",
    "directory unreadable",
    "invalid compose file",
    "list failed",
    "occupancy history read failed",
    "occupancy history write failed",
    "occupancy refresh failed or timed out",
    "occupancy source unavailable or incomplete",
    "peer occupancy unavailable or incomplete",
    "queue full; intermediate changes omitted",
    "scan failed",
    "unknown value reset",
    "unreachable",
    "untrusted listen table",
})


def _check(check_id: str, status: str, detail: str, **evidence: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "detail": detail,
        "evidence": evidence,
    }


def build_diagnostics(facts: dict[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    """Return the public, sanitized Doctor document."""
    monitor = facts["monitor"]
    enabled = set(facts["enabled_scanners"])
    sources = monitor.get("sources") or {}
    checks: list[dict[str, Any]] = []

    if facts["settings_readonly"]:
        checks.append(_check(
            "settings_store", "info", "readonly",
            source=facts["settings_source"], writable=False,
        ))
    else:
        writable = bool(facts["data_dir_writable"])
        checks.append(_check(
            "settings_store", "pass" if writable else "fail",
            "writable" if writable else "not_writable",
            source=facts["settings_source"], writable=writable,
        ))

    if not monitor.get("initialized"):
        checks.append(_check("snapshot", "warning", "not_initialized", ready=False))
    elif monitor.get("stale"):
        checks.append(_check(
            "snapshot", "warning", "stale", ready=False,
            age_seconds=monitor.get("scan_age_seconds"),
        ))
    else:
        ready = bool(monitor.get("ready"))
        checks.append(_check(
            "snapshot", "pass" if ready else "fail",
            "ready" if ready else "incomplete", ready=ready,
            age_seconds=monitor.get("scan_age_seconds"),
        ))

    for scanner in ("listen", "docker", "compose"):
        if scanner not in enabled:
            checks.append(_check(scanner, "info", "disabled", enabled=False))
            continue
        observed = sources.get(scanner, "checking")
        status = "pass" if observed == "ok" else "fail" if observed == "failed" else "warning"
        detail = observed if observed in ("ok", "failed") else "checking"
        evidence: dict[str, Any] = {"enabled": True, "observed": observed}
        if scanner == "listen":
            evidence.update(source=facts["listen_source"], trusted=bool(facts["listen_trusted"]))
            if observed == "ok" and not facts["listen_trusted"]:
                status, detail = "warning", "untrusted"
        elif scanner == "docker":
            evidence.update(
                library_available=bool(facts["docker_library_available"]),
                transport=facts["docker_transport"],
            )
        else:
            evidence.update(
                root_available=bool(facts["compose_root_available"]),
                root_readable=bool(facts["compose_root_readable"]),
                files_scanned=int(monitor.get("compose_files_scanned") or 0),
                incomplete=bool(monitor.get("compose_incomplete")),
                truncated=bool(monitor.get("compose_truncated")),
            )
            if evidence["incomplete"] or evidence["truncated"]:
                status, detail = "fail", "incomplete"
        checks.append(_check(scanner, status, detail, **evidence))

    events = []
    for event in facts.get("degradations", []):
        source = str(event.get("source") or "")
        reason = str(event.get("reason") or "")
        if not source and not reason:
            continue
        events.append({
            "source": source if source in _SAFE_SOURCES else "unknown",
            "reason": reason if reason in _SAFE_REASONS else "redacted",
        })
    checks.append(_check(
        "degradations", "warning" if events else "pass",
        "recent" if events else "none", count=len(events), events=events,
    ))

    counts = {status: sum(1 for row in checks if row["status"] == status) for status in _STATUSES}
    for row in checks:
        if row["status"] in ("warning", "fail"):
            row["remediation"] = row["id"]
    overall = "attention" if counts["fail"] or counts["warning"] else "healthy"
    return {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": overall,
        "counts": counts,
        "context": {
            "version": facts["version"],
            "settings_source": facts["settings_source"],
            "settings_readonly": bool(facts["settings_readonly"]),
            "peer_count": int(facts["peer_count"]),
            "auth_required": bool(facts["auth_required"]),
            "hidden_unlock_required": bool(facts["hidden_unlock_required"]),
        },
        "checks": checks,
    }


def report_text(document: dict[str, Any]) -> str:
    """Serialize the already-sanitized document for copying or download."""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
