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

    app.add_api_route(
        "/__preview/workbench/advance", advance, methods=["POST"], include_in_schema=False
    )
    app.add_api_route(
        "/__preview/workbench/state", fixture_state, methods=["GET"], include_in_schema=False
    )


def create_app():
    from backend import main
    _install_workbench_fixture(main, main.app)
    return main.app
