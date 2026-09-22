from fastapi.testclient import TestClient

from backend import main, port_store
from backend.compose_scanner import ComposePort, ComposeScan
from backend.docker_scanner import ContainerInfo
from backend.models import PortMapping
from backend.port_scanner import ListeningPort


def test_port_observation_preserves_protocols_without_exposing_raw_details(empty_scan, monkeypatch):
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [ListeningPort(
        port=4317, protocol="tcp", ip="127.0.0.1", process_name="private-process", pid=999,
    )])
    monkeypatch.setattr(main, "scan_containers", lambda: [ContainerInfo(
        name="private-container", status="running", image="private/image", ports=[
            PortMapping(host_port=4317, container_port=4317, protocol="tcp", host_ip="0.0.0.0"),
        ],
    )])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan(ports=[ComposePort(
        port=4317, compose_file="/private/compose.yml", project_dir="private-project",
        service_name="private-service", protocol="udp", host_ip="::1",
    )]))
    with TestClient(main.app) as client:
        response = client.get("/api/observations/ports/4317")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == 1
        assert body["requires_hidden_access"] is False
        assert body["scan"]["complete"] is True
        assert body["current"]["overall_status"] == "used"
        entries = {entry["protocol"]: entry for entry in body["current"]["entries"]}
        assert entries["tcp"]["status"] == "used"
        assert entries["tcp"]["docker_mapped"] is True
        assert entries["udp"]["status"] == "configured"
        assert entries["udp"]["compose_declared"] is True
        assert {binding["scope"] for binding in entries["tcp"]["bind"]} == {
            "all_interfaces", "loopback",
        }
        assert "private" not in response.text
        assert client.get("/api/observations/ports/4317", params={"protocol": "tcp"}).json()["current"]["entries"] == [entries["tcp"]]
        assert client.get("/api/meta").json()["capabilities"]["port_observation"] == 1


def test_port_observation_withholds_hidden_ports_and_sanitizes_manual_labels(empty_scan, monkeypatch):
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock-me")
    port_store.add_manual_port(8096, "private-label")
    port_store.add_hidden_port(8096)
    with TestClient(main.app) as client:
        assert client.get("/api/observations/ports/8096", params={"include_hidden": True}).status_code == 404
        response = client.get(
            "/api/observations/ports/8096",
            params={"include_hidden": True},
            headers={"X-Hidden-Unlock": "unlock-me"},
        )
        assert response.status_code == 200
        assert response.json()["requires_hidden_access"] is True
        assert response.json()["current"]["overall_status"] == "configured"
        assert "private-label" not in response.text


def test_hidden_observation_events_do_not_reappear_after_unhide(empty_scan, monkeypatch):
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock-me")
    with TestClient(main.app) as client:
        port_store.add_manual_port(8097, "private-label")
        port_store.add_hidden_port(8097)
        main._monitor.state_changed()
        assert main._monitor.observation_events(8097, allow_hidden=False) == []
        assert main._monitor.observation_events(8097, allow_hidden=True)
        client.delete("/api/hidden/8097", headers={"X-Hidden-Unlock": "unlock-me"})
        body = client.get("/api/observations/ports/8097").json()
        assert body["events"] == []


def test_incomplete_scan_returns_facts_with_explicit_limits(empty_scan, monkeypatch):
    def unavailable():
        raise RuntimeError("scanner unavailable")

    monkeypatch.setattr(main, "scan_containers", unavailable)
    with TestClient(main.app) as client:
        response = client.get("/api/observations/ports/4317", params={"protocol": "tcp"})
        assert response.status_code == 200
        body = response.json()
        assert body["scan"]["complete"] is False
        assert body["current"]["entries"][0]["status"] == "unknown"
        assert "scan_incomplete" in body["limitations"]


def test_observation_events_keep_a_complete_baseline(empty_scan, monkeypatch):
    listening = [ListeningPort(port=4317, protocol="tcp", ip="127.0.0.1")]
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: listening)
    with TestClient(main.app) as client:
        initial = client.get("/api/observations/ports/4317").json()
        listening[:] = [ListeningPort(port=4317, protocol="tcp", ip="0.0.0.0")]
        main._monitor.refresh()
        body = client.get("/api/observations/ports/4317").json()
        event = next(event for event in body["events"] if event["kind"] == "bind_scope_changed")
        assert event["before"]["bind_scope"] == "localhost"
        assert event["after"]["bind_scope"] == "public"
        assert event["evidence_refs"] == [f"{body['capture_id']}:port:4317"]
        assert set(event["evidence_refs"]) <= {ref["id"] for ref in body["evidence_refs"]}
        assert initial["capture_id"] != body["capture_id"]


def test_observation_events_survive_in_memory_event_reset(empty_scan, monkeypatch):
    from backend import history

    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    listening = [ListeningPort(port=4318, protocol="tcp", ip="127.0.0.1")]
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: listening)
    with TestClient(main.app) as client:
        client.get("/api/observations/ports/4318")
        listening[:] = [ListeningPort(port=4318, protocol="tcp", ip="0.0.0.0")]
        main._monitor.refresh()
        main._monitor._observe_pending()
        main._monitor._observation_events.clear()
        history.reset()

        body = client.get("/api/observations/ports/4318").json()
        event = next(event for event in body["events"] if event["kind"] == "bind_scope_changed")
        assert event["evidence_refs"] == [f"history:{event['event_id']}"]
        assert event["event_id"] in {ref["id"].removeprefix("history:") for ref in body["evidence_refs"]}
