from fastapi.testclient import TestClient

from backend import main
from backend.compose_scanner import ComposePort, ComposeScan
from backend.docker_scanner import ContainerInfo
from backend.models import PortMapping
from backend.port_scanner import ListeningPort


def _batch(client, selection, **extra):
    return client.post(
        "/api/observations/batch",
        json={"selection": selection, "protocol": "all", "history_hours": 24, **extra},
    )


def test_batch_keeps_high_ports_and_scoped_relationships_private(empty_scan, monkeypatch):
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [
        ListeningPort(port=65535, protocol="tcp", ip="127.0.0.1"),
    ])
    monkeypatch.setattr(main, "scan_containers", lambda: [ContainerInfo(
        name="private-container", container_id="container-unique-id", status="running",
        image="private/image", ports=[
            PortMapping(host_port=5000, container_port=5000, protocol="tcp", host_ip="0.0.0.0"),
        ], compose_project="same-visible-label",
    )])
    monkeypatch.setattr(main, "scan_compose_tree", lambda *_a, **_kw: ComposeScan(ports=[
        ComposePort(5000, "/private/a.yml", "private-a", "api", protocol="tcp", project_name="same"),
        ComposePort(5001, "/private/a.yml", "private-a", "worker", protocol="udp", project_name="same"),
        ComposePort(5002, "/private/b.yml", "private-b", "api", protocol="tcp", project_name="same"),
    ]))
    with TestClient(main.app) as client:
        response = _batch(client, {"kind": "known"})
    assert response.status_code == 200
    body = response.json()
    assert body["coverage"]["complete"] is True
    assert {port["port"] for port in body["ports"]} == {5000, 5001, 5002, 65535}
    rows = {port["port"]: port for port in body["ports"]}
    project_5000 = next(item for item in rows[5000]["relations"] if item["kind"] == "compose_project")
    project_5001 = next(item for item in rows[5001]["relations"] if item["kind"] == "compose_project")
    project_5002 = next(item for item in rows[5002]["relations"] if item["kind"] == "compose_project")
    assert (project_5000["id"], project_5000["protocol"]) != (project_5001["id"], project_5001["protocol"])
    assert project_5000["id"] == project_5001["id"]
    assert project_5000["id"] != project_5002["id"]
    assert all(item["source"] == "compose" for item in (project_5000, project_5001, project_5002))
    assert "private" not in response.text and "same-visible-label" not in response.text


def test_batch_range_is_bounded_without_dropping_the_high_port(empty_scan):
    with TestClient(main.app) as client:
        accepted = _batch(client, {"kind": "range", "start": 64512, "end": 65535})
        rejected = _batch(client, {"kind": "range", "start": 1, "end": 1025})
        duplicate = _batch(client, {"kind": "ports", "ports": [65535, 65535]})
    assert accepted.status_code == 200
    assert accepted.json()["coverage"]["requested_count"] == 1024
    assert accepted.json()["ports"][-1]["port"] == 65535
    assert rejected.status_code == 422
    assert duplicate.status_code == 422


def test_known_batch_keeps_a_visible_recently_disappeared_port(empty_scan, monkeypatch):
    listeners = [ListeningPort(port=45000, protocol="tcp", ip="127.0.0.1")]
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: list(listeners))
    with TestClient(main.app) as client:
        assert _batch(client, {"kind": "known"}).status_code == 200
        listeners.clear()
        main._monitor.refresh()
        main._monitor._observe_pending()
        response = _batch(client, {"kind": "known"})
    assert response.status_code == 200
    body = response.json()
    assert any(event["port"] == 45000 and event["kind"] == "state_changed" for event in body["events"])
    row = next(port for port in body["ports"] if port["port"] == 45000)
    assert row["current"]["overall_status"] == "free"


def test_known_batch_atomically_includes_recheck_members(empty_scan, monkeypatch):
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: [
        ListeningPort(port=8080, protocol="tcp", ip="127.0.0.1"),
    ])
    with TestClient(main.app) as client:
        response = _batch(
            client,
            {"kind": "known", "additional_ports": [65535]},
        )
        duplicate = _batch(
            client,
            {"kind": "known", "additional_ports": [65535, 65535]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["selection"] == {"kind": "known", "protocol": "all"}
    assert body["coverage"]["requested_count"] == 2
    assert {row["port"] for row in body["ports"]} == {8080, 65535}
    assert duplicate.status_code == 422


def test_batch_hides_currently_hidden_event_ports(empty_scan, monkeypatch):
    from backend import port_store

    listeners = [ListeningPort(port=45001, protocol="tcp", ip="127.0.0.1")]
    monkeypatch.setattr(main, "scan_listening_ports", lambda **_kw: list(listeners))
    with TestClient(main.app) as client:
        assert _batch(client, {"kind": "known"}).status_code == 200
        listeners.clear()
        main._monitor.refresh()
        main._monitor._observe_pending()
        port_store.add_hidden_port(45001)
        main._monitor.state_changed()
        response = _batch(client, {"kind": "known"})
    assert response.status_code == 200
    body = response.json()
    assert all(port["port"] != 45001 for port in body["ports"])
    assert all(event["port"] != 45001 for event in body["events"])
    assert "hidden_withheld" in body["coverage"]["limitations"]
