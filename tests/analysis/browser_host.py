"""Synthetic local scanner fixture for the workbench browser regression."""
import os


def _install_workbench_fixture(main, app):
    """Replace scanners with synthetic facts; retain the real application lifecycle."""

    from backend import port_store
    from backend.compose_scanner import ComposePort, ComposeScan
    from backend.docker_scanner import ContainerInfo
    from backend.models import PortMapping
    from backend.port_scanner import ListeningPort
    from backend.scan_status import ScanUnavailable
    from fastapi import HTTPException, Request

    state = {"phase": "baseline"}

    def compose_scan():
        ports = [
            ComposePort(
                port=8080,
                compose_file="/synthetic/fixture-app/compose.yml",
                project_dir="/synthetic/fixture-app",
                project_name="fixture-app",
                service_name="web",
                container_port=80,
                host_ip="127.0.0.1",
            ),
            # This is a visible control member in the same declaration
            # group.  No runtime mapping is intentional: the workbench
            # must present it as a bounded factual mismatch, not a whole
            # project failure.
            ComposePort(
                port=8081,
                compose_file="/synthetic/fixture-app/compose.yml",
                project_dir="/synthetic/fixture-app",
                project_name="fixture-app",
                service_name="worker",
                container_port=81,
                host_ip="127.0.0.1",
            ),
            # Two distinct declaration directories overlap at a high port
            # so a bounded all-known scan proves it did not inspect only
            # low-numbered resources.
            ComposePort(
                port=65535,
                compose_file="/synthetic/fixture-a/compose.yml",
                project_dir="/synthetic/fixture-a",
                project_name="fixture-a",
                service_name="edge-a",
                container_port=443,
                host_ip="0.0.0.0",
            ),
        ]
        if state["phase"] != "resolved":
            ports.append(
                ComposePort(
                    port=65535,
                    compose_file="/synthetic/fixture-b/compose.yml",
                    project_dir="/synthetic/fixture-b",
                    project_name="fixture-b",
                    service_name="edge-b",
                    container_port=443,
                    host_ip="0.0.0.0",
                )
            )
        if state["phase"] == "port_range":
            ports.extend(ComposePort(
                port=port, protocol="udp", compose_file="/synthetic/streaming/compose.yml",
                project_dir="/synthetic/streaming", project_name="streaming", service_name="worker",
                container_port=port, host_ip="0.0.0.0",
            ) for port in range(20000, 20032))
        return ComposeScan(
            ports=ports,
            files_scanned=2 if state["phase"] == "resolved" else 3,
        )

    def listen_scan(*, prefer_pids=None):
        del prefer_pids
        phase = state["phase"]
        if phase == "source_gap":
            raise ScanUnavailable("synthetic listen source unavailable")
        if phase == "runtime_gap":
            return []
        web_ip = "0.0.0.0" if phase == "changed" else "127.0.0.1"
        rows = [
            ListeningPort(
                port=8080,
                protocol="tcp",
                ip=web_ip,
                process_name="synthetic-web",
                pid=4242,
            )
        ]
        if phase == "changed":
            rows.append(
                ListeningPort(
                    port=65535,
                    protocol="tcp",
                    ip="0.0.0.0",
                    process_name="synthetic-edge",
                    pid=4243,
                )
            )
        return rows

    def container_scan():
        if state["phase"] == "runtime_gap":
            return []
        return [
            ContainerInfo(
                name="synthetic-web",
                status="running",
                image="fixture/internal",
                container_id="fixture-web-runtime-1",
                compose_project="fixture-app",
                compose_service="web",
                ports=[
                    PortMapping(
                        host_port=8080,
                        host_ip="127.0.0.1",
                        container_port=80,
                        protocol="tcp",
                    )
                ],
            )
        ]

    # Keep the application's normal snapshot builder and monitor.  These
    # global functions are resolved when a scan runs, after the fixture has
    # been selected but before the original lifespan starts the monitor.
    main.scan_compose_tree = lambda *_args, **_kwargs: compose_scan()
    main.scan_listening_ports = listen_scan
    main.scan_containers = container_scan
    os.environ["PORT_LIGHT_SCANNERS"] = "listen,docker,compose"

    async def advance(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(
                status_code=422, detail="synthetic fixture phase required"
            ) from None
        if not isinstance(body, dict) or set(body) != {"phase"}:
            raise HTTPException(status_code=422, detail="synthetic fixture phase required")
        phase = body["phase"]
        allowed = {
            "baseline",
            "changed",
            "resolved",
            "runtime_gap",
            "port_range",
            "source_gap",
            "revoke_port_access",
            "restore_port_access",
        }
        if phase not in allowed:
            raise HTTPException(status_code=422, detail="unknown synthetic fixture phase")
        if phase == "revoke_port_access":
            port_store.add_hidden_port(8081)
            main._monitor.state_changed()
        elif phase == "restore_port_access":
            port_store.remove_hidden_port(8081)
            main._monitor.state_changed()
        else:
            state["phase"] = phase
            main._monitor.refresh()
        snapshot = main._monitor.latest(main._values())
        return {
            "synthetic": True,
            "phase": phase,
            "capture_id": snapshot["capture_id"],
            "sources": snapshot.get("sources", {}),
        }

    async def fixture_state():
        snapshot = main._monitor.latest(main._values())
        return {
            "synthetic": True,
            "phase": state["phase"],
            "capture_id": snapshot["capture_id"],
            "sources": snapshot.get("sources", {}),
        }

    async def seed_legacy_report(request: Request):
        import json
        import time
        from backend.analysis.reports import owner_hash
        from backend.analysis.routes import COOKIE

        payload = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            payload = await request.json()
        session = (payload.get("session") if isinstance(payload, dict) else None) or request.cookies.get(COOKIE, "")
        if not session:
            raise HTTPException(status_code=400, detail="missing analysis session cookie")
        store = app.state.analysis.state.report_store
        report_id = "legacy-fixture-report-8080"
        created_at = int(time.time())
        document = {
            "id": report_id,
            "created_at": created_at,
            "versions": {
                "schema": 1,
                "evidence": "port-evidence.v1",
                "baseline": "port-rules.v1",
                "interpretation": "port-interpretation.v1",
            },
            "evidence": {
                "port": 8080,
                "question_type": "recorded_changes",
                "captured_at": created_at - 10,
                "observation": {
                    "captured_at": created_at - 10,
                    "current": {
                        "entries": [
                            {
                                "protocol": "tcp",
                                "status": "configured",
                                "compose_declared": True,
                                "compose_conflict": False,
                                "compose_relation": "declared_without_live_mapping",
                                "bind": [{"family": "ipv4", "scope": "loopback", "source": "compose"}],
                            }
                        ]
                    },
                    "events": [
                        {
                            "event_id": "event-1",
                            "kind": "state_changed",
                            "observed_at": created_at - 20,
                            "before": {"status": "configured"},
                            "after": {"status": "used"},
                        }
                    ],
                },
            },
            "baseline": {
                "version": "port-rules.v1",
                "conclusion": {"code": "recorded_changes"},
            },
            "validated_interpretation": {
                "schema_version": 1,
                "summary_kind": "recorded_changes",
                "evidence_ids": ["event-1"],
                "hypotheses": [
                    {
                        "kind": "declaration_runtime_gap",
                        "evidence_ids": ["current"],
                        "missing_evidence": ["runtime_configuration"],
                        "text": "UNTRUSTED",
                    }
                ],
                "checks": [{"action": "mapping", "evidence_ids": ["current"]}],
            },
            "interpretation_view": {"summary": {"text": "UNTRUSTED"}},
        }
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        with store._database(write=True) as database:
            database.execute(
                """INSERT OR REPLACE INTO reports (
                    id, owner_hash, source_analysis_id, port, created_at,
                    requires_hidden_access, body, bytes
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    report_id,
                    owner_hash(session),
                    "legacy-source-8080",
                    8080,
                    created_at,
                    encoded,
                    len(encoded.encode("utf-8")),
                ),
            )
        return {"id": report_id, "port": 8080}

    app.add_api_route(
        "/__preview/workbench/advance", advance, methods=["POST"], include_in_schema=False
    )
    app.add_api_route(
        "/__preview/workbench/state", fixture_state, methods=["GET"], include_in_schema=False
    )
    app.add_api_route(
        "/__preview/workbench/seed-legacy-report",
        seed_legacy_report,
        methods=["POST"],
        include_in_schema=False,
    )


def create_app():
    from backend import main
    _install_workbench_fixture(main, main.app)
    return main.app
