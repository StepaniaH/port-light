from __future__ import annotations

from backend.compose_scanner import ComposePort
from backend.docker_scanner import ContainerInfo
from backend.main import (
    _bind_scope,
    _bind_scope_many,
    _classify,
    _collect_urls,
    _compose_conflict,
    _proto_label,
)
from backend.port_scanner import ListeningPort


def test_bind_scope():
    assert _bind_scope("0.0.0.0") == "public"
    assert _bind_scope("::") == "public"
    assert _bind_scope("*") == "public"
    assert _bind_scope("127.0.0.1") == "localhost"
    assert _bind_scope("127.0.0.2") == "localhost"
    assert _bind_scope("::1") == "localhost"
    assert _bind_scope("::ffff:127.0.0.1") == "localhost"
    assert _bind_scope("192.168.1.10") == "lan"
    assert _bind_scope_many(["127.0.0.1", "0.0.0.0"]) == "public"
    assert _bind_scope_many(["127.0.0.1", "10.0.0.5"]) == "lan"
    assert _bind_scope_many(["127.0.0.1"]) == "localhost"
    assert _bind_scope("[::1]") == "localhost"
    assert _bind_scope("[::]") == "public"


def test_compose_conflict_uses_bind_overlap():
    overlapping = [
        {"project_dir": "a", "host_ip": "127.0.0.1"},
        {"project_dir": "b", "host_ip": "0.0.0.0"},
    ]
    disjoint = [
        {"project_dir": "a", "host_ip": "127.0.0.1"},
        {"project_dir": "b", "host_ip": "10.0.0.5"},
    ]
    same_stack = [
        {"project_dir": "media", "host_ip": "127.0.0.1"},
        {"project_dir": "media", "host_ip": "0.0.0.0"},
    ]
    assert _compose_conflict(overlapping) is True
    assert _compose_conflict(disjoint) is False
    assert _compose_conflict(same_stack) is False
    tcp_udp = [
        {"project_dir": "dns", "host_ip": "0.0.0.0", "protocol": "tcp"},
        {"project_dir": "other", "host_ip": "0.0.0.0", "protocol": "udp"},
    ]
    assert _compose_conflict(tcp_udp) is False


def test_compose_keeps_distinct_host_ips():
    compose = [
        ComposePort(
            port=8080, compose_file="web/compose.yml", project_dir="web",
            service_name="app", host_ip="127.0.0.1",
        ),
        ComposePort(
            port=8080, compose_file="web/compose.yml", project_dir="web",
            service_name="app", host_ip="10.0.0.5",
        ),
        ComposePort(
            port=8080, compose_file="other/compose.yml", project_dir="other",
            service_name="app", host_ip="192.168.1.10",
        ),
    ]
    out = _classify(
        [], [], compose, [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    row = out["ports"][0]
    assert len(row["compose_configs"]) == 3
    assert row["conflict"] is False
    assert row["bind_scope"] == "lan"


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
    assert _collect_urls(5060, ["0.0.0.0"], [], {"name": "SIP", "is_access_port": True}) == []

    v6 = _collect_urls(
        8096, ["0.0.0.0"], [], known_web, {"url_host": "2001:db8::1"},
    )
    assert "http://[2001:db8::1]:8096" in v6
    lan = _collect_urls(8096, ["192.168.1.10"], [], known_web)
    assert "http://192.168.1.10:8096" in lan
    ula = _collect_urls(8096, ["fd12::10"], [], known_web)
    assert "http://[fd12::10]:8096" in ula
    still_override = _collect_urls(
        8096, ["192.168.1.10"], [], known_web, {"url_host": "nas.lan"},
    )
    assert "http://nas.lan:8096" in still_override


def test_compose_project_name_and_port_zero():
    compose = [
        ComposePort(
            port=8080, compose_file="web/compose.yml", project_dir="web",
            project_name="web-stack", service_name="app",
        ),
        ComposePort(
            port=0, compose_file="web/compose.yml", project_dir="web",
            service_name="ephemeral",
        ),
    ]
    out = _classify(
        [ListeningPort(port=0, protocol="tcp", ip="0.0.0.0")],
        [], compose, [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    ports = {row["port"] for row in out["ports"]}
    assert 0 not in ports
    assert 8080 in ports
    assert out["ports"][0]["compose_configs"][0]["project_name"] == "web-stack"


def test_classify_skips_junk_manual_rows():
    out = _classify(
        [], [], [],
        ["nope", {"port": "9090", "label": "lab"}, {"label": "missing"}],
        hidden_ports=["22", "nope", 0],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    assert [row["port"] for row in out["ports"]] == [9090]
    assert out["ports"][0]["manual_label"] == "lab"
    assert out["summary"]["hidden"] == 1


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


def test_compose_same_project_is_not_conflict():
    compose = [
        ComposePort(port=8096, compose_file="media/compose.yml", project_dir="media", service_name="jellyfin"),
        ComposePort(port=8096, compose_file="media/compose.override.yml", project_dir="media", service_name="jellyfin"),
        ComposePort(port=5432, compose_file="db/compose.yml", project_dir="db", service_name="postgres"),
        ComposePort(port=5432, compose_file="other/compose.yml", project_dir="other", service_name="db"),
    ]
    out = _classify(
        [], [], compose, [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert by_port[8096]["conflict"] is False
    assert len(by_port[8096]["compose_configs"]) == 1
    assert by_port[5432]["conflict"] is True


def test_compose_host_ip_sets_localhost_scope():
    compose = [
        ComposePort(
            port=8080, compose_file="web/compose.yml", project_dir="web",
            service_name="app", host_ip="127.0.0.1",
        ),
    ]
    out = _classify(
        [], [], compose, [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    row = out["ports"][0]
    assert row["bind_scope"] == "localhost"
    assert row["ip"] == "127.0.0.1"


def test_collect_urls_drops_javascript():
    urls = _collect_urls(
        8096, ["0.0.0.0"],
        [{"urls": ["javascript:alert(1)", "https://media.home.arpa"]}],
        {"name": "Jellyfin", "is_access_port": True},
    )
    assert "javascript:alert(1)" not in urls
    assert "https://media.home.arpa" in urls
    assert all(u.startswith(("http://", "https://")) for u in urls)


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
