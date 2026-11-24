"""Synthetic observations and legacy report fixtures for analysis tests."""

import json

from backend.analysis.evidence import prepare_evidence
from backend.analysis.reports import owner_hash


def observation(port=8080, *, hidden=False, protocol="all", core_version="0.8.4"):
    capture = "obs-test-1"
    entries = []
    selected_protocol = protocol
    for item_protocol in ("tcp", "udp") if protocol == "all" else (protocol,):
        entries.append(
            {
                "protocol": item_protocol,
                "status": "configured" if item_protocol == "tcp" else "free",
                "listening": False,
                "docker_mapped": False,
                "docker_live": False,
                "compose_declared": item_protocol == "tcp",
                "compose_relation": "declared_without_live_mapping"
                if item_protocol == "tcp"
                else "not_declared",
                "compose_conflict": False,
                "manual": False,
                "reservation": False,
                "bind": [{"family": "ipv4", "scope": "all_interfaces", "source": "compose"}]
                if item_protocol == "tcp"
                else [],
                "evidence_refs": [f"{capture}:port:{port}:tcp:compose"]
                if item_protocol == "tcp"
                else [],
            }
        )
    return {
        "schema_version": 1,
        "capture_id": capture,
        "captured_at": 1700000100,
        "core_version": core_version,
        "requires_hidden_access": hidden,
        "scope": {"kind": "local_hub"},
        "identity": {"port": port, "protocol": selected_protocol},
        "scan": {
            "ready": True,
            "complete": True,
            "stale": False,
            "sources": [
                {"name": "compose", "state": "ok", "observed_at": 1700000100},
                {"name": "listen", "state": "disabled", "observed_at": None},
                {"name": "docker", "state": "disabled", "observed_at": None},
            ],
        },
        "current": {
            "overall_status": "configured" if selected_protocol != "udp" else "free",
            "entries": entries,
        },
        "history": {
            "state": "available",
            "resolution": "port_state_only",
            "events": [],
            "truncated": False,
        },
        "events": [],
        "evidence_refs": [
            {"id": f"{capture}:port:{port}", "source": "occupancy", "observed_at": 1700000100},
            {
                "id": f"{capture}:port:{port}:tcp:compose",
                "source": "compose",
                "protocol": "tcp",
                "observed_at": 1700000100,
            },
        ],
        "limitations": ["history_port_state_only"],
    }


def seed_legacy_report(
    store,
    owner: str,
    *,
    report_id: str = "legacy-report-1",
    source_analysis_id: str = "legacy-analysis-1",
    port: int = 8080,
    hidden: bool = False,
    created_at: int = 1700000200,
) -> dict:
    """Insert a schema-v1 legacy single-port report row directly into SQLite."""
    evidence = prepare_evidence(
        observation(port, hidden=hidden), core_version="0.8.4", include_history=True
    )
    document = {
        "id": report_id,
        "schema_version": 1,
        "created_at": created_at,
        "source_analysis_id": source_analysis_id,
        "requires_hidden_access": hidden,
        "evidence": evidence,
        "baseline": {
            "version": "port-rules.v1",
            "question_type": "current_occupancy",
            "conclusion": {"code": "declaration_without_mapping", "evidence_ids": ["current"]},
        },
        "validated_interpretation": {
            "schema_version": 1,
            "summary_kind": "current_occupancy",
            "evidence_ids": ["current"],
            "hypotheses": [
                {
                    "kind": "declaration_runtime_gap",
                    "evidence_ids": ["current"],
                    "missing_evidence": ["runtime_configuration"],
                }
            ],
            "unknowns": [{"kind": "application_health", "evidence_ids": ["current"]}],
            "checks": [{"action": "scan_status", "evidence_ids": ["scan"]}],
        },
        "versions": {
            "core": "0.8.4",
            "evidence": evidence["evidence_version"],
            "baseline": "port-rules.v1",
            "interpretation": "port-interpretation.v1",
        },
        "mode": "demo",
        "provider": "demo",
        "model": "synthetic-demo",
        "usage": {},
    }
    body = json.dumps(document, ensure_ascii=False, sort_keys=True)
    size = len(body.encode("utf-8"))
    with store.connection:
        store.connection.execute(
            "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report_id,
                owner_hash(owner),
                source_analysis_id,
                port,
                created_at,
                int(hidden),
                body,
                size,
            ),
        )
    return document
