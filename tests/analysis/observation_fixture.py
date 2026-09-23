"""Synthetic observations for analysis tests."""


def observation(port=8080, *, hidden=False, protocol="all"):
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
        "core_version": "0.8.4",
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
