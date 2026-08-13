from __future__ import annotations

from backend.compose_scanner import ComposePort
from backend.docker_scanner import ContainerInfo
from backend.main import _bind_scope, _bind_scope_many, _classify, _collect_urls, _proto_label
from backend.port_scanner import ListeningPort


def test_bind_scope():
    assert _bind_scope("0.0.0.0") == "public"
    assert _bind_scope("::") == "public"
    assert _bind_scope("*") == "public"
    assert _bind_scope("127.0.0.1") == "localhost"
    assert _bind_scope("::1") == "localhost"
    assert _bind_scope("192.168.1.10") == "lan"
    assert _bind_scope_many(["127.0.0.1", "0.0.0.0"]) == "public"
    assert _bind_scope_many(["127.0.0.1", "10.0.0.5"]) == "lan"
    assert _bind_scope_many(["127.0.0.1"]) == "localhost"
    assert _bind_scope_many([]) == "public"


def test_proto_label():
    assert _proto_label(["tcp", "tcp6", "udp"]) == "tcp,udp"
    assert _proto_label([]) == "tcp"


def test_collect_urls_guesses_http_not_ssh():
    known_web = {"name": "Jellyfin", "is_access_port": True}
    urls = _collect_urls(8096, ["0.0.0.0"], [], known_web)
    assert "http://localhost:8096" in urls

    known_https = {"name": "Portainer HTTPS", "is_access_port": True}
    urls = _collect_urls(9443, ["127.0.0.1"], [], known_https)
    assert "https://127.0.0.1:9443" in urls

    ssh = {"name": "SSH", "is_access_port": True}
    assert _collect_urls(22, ["0.0.0.0"], [], ssh) == []


def test_classify_used_configured_hidden_conflict():
    listening = [
        ListeningPort(port=22, protocol="tcp", ip="0.0.0.0", process_name="sshd", inode=11),
        ListeningPort(port=53, protocol="udp", ip="127.0.0.1", inode=12),
        ListeningPort(port=8096, protocol="tcp", ip="0.0.0.0", inode=99),
    ]
    containers = [
        ContainerInfo(
            name="jellyfin",
            status="running",
            image="jellyfin/jellyfin",
            ports=[{"host_port": 8096, "host_ip": "0.0.0.0", "container_port": 8096, "protocol": "tcp"}],
            urls=["https://media.home.arpa"],
            network_mode="bridge",
        ),
        ContainerInfo(
            name="coredns",
            status="running",
            image="coredns",
            ports=[],
            network_mode="host",
            socket_inodes={12},
        ),
    ]
    compose = [
        ComposePort(port=5432, compose_file="db/compose.yml", project_dir="db", service_name="postgres"),
        ComposePort(port=5432, compose_file="other/compose.yml", project_dir="other", service_name="db"),
        ComposePort(port=8096, compose_file="media/compose.yml", project_dir="media", service_name="jellyfin"),
    ]
    out = _classify(
        listening,
        containers,
        compose,
        [{"port": 12345, "label": "lab", "machine": "localhost"}],
        hidden_ports=[12345],
        range_start=1,
        range_end=65535,
        include_hidden=False,
        hidden_locked=False,
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert 12345 not in by_port
    assert by_port[22]["status"] == "used"
    assert by_port[22]["source_type"] == "system"
    assert by_port[22]["bind_scope"] == "public"
    assert by_port[53]["status"] == "used"
    assert by_port[53]["protocol"] in ("udp", "udp6")
    assert any(c["name"] == "coredns" for c in by_port[53]["containers"])
    assert by_port[8096]["status"] == "used"
    assert "https://media.home.arpa" in by_port[8096]["urls"]
    assert by_port[5432]["status"] == "configured"
    assert by_port[5432]["conflict"] is True

    shown = _classify(
        listening, containers, compose,
        [{"port": 12345, "label": "lab", "machine": "localhost"}],
        hidden_ports=[12345],
        range_start=1, range_end=65535,
        include_hidden=True, hidden_locked=False,
    )
    hidden = [p for p in shown["ports"] if p["port"] == 12345]
    assert len(hidden) == 1
    assert hidden[0]["is_hidden"] is True
    assert hidden[0]["status"] == "configured"
    assert hidden[0]["source_type"] == "manual"


def test_meta_unauthenticated(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from fastapi.testclient import TestClient
    from backend.main import app, VERSION
    client = TestClient(app)
    res = client.get("/api/meta")
    assert res.status_code == 200
    assert res.json()["version"] == VERSION
    assert res.json()["auth_required"] is False
