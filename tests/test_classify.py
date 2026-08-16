from __future__ import annotations

from backend.compose_scanner import ComposePort
from backend.docker_scanner import ContainerInfo
from backend.main import (
    _bind_scope,
    _bind_scope_many,
    _classify,
    _collect_urls,
    _compose_conflict,
    _etag_matched,
    _json_etag,
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
    assert _bind_scope("8.8.8.8") == "public"
    assert _bind_scope("169.254.1.1") == "link"
    assert _bind_scope("172.17.0.2") == "link"
    assert _bind_scope_many(["8.8.8.8", "127.0.0.1"]) == "public"


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
    tcp_sctp = [
        {"project_dir": "dns", "host_ip": "0.0.0.0", "protocol": "tcp"},
        {"project_dir": "other", "host_ip": "0.0.0.0", "protocol": "sctp"},
    ]
    assert _compose_conflict(tcp_sctp) is False


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
    assert _collect_urls(41641, ["0.0.0.0"], [], {"name": "Tailscale", "is_access_port": True}) == []
    assert _collect_urls(21, ["0.0.0.0"], [], {"name": "FTP", "is_access_port": True}) == []
    assert _collect_urls(25565, ["0.0.0.0"], [], {"name": "Minecraft", "is_access_port": True}) == []

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
    wan = _collect_urls(8096, ["8.8.8.8"], [], known_web)
    assert "http://localhost:8096" in wan
    assert "http://8.8.8.8:8096" not in wan


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


def test_exited_container_keeps_bind_ips():
    containers = [
        ContainerInfo(
            name="wiki",
            status="exited",
            image="wiki",
            ports=[
                {"host_port": 8080, "host_ip": "127.0.0.1", "container_port": 80, "protocol": "tcp"},
                {"host_port": 8080, "host_ip": "10.0.0.5", "container_port": 80, "protocol": "tcp"},
            ],
        ),
        ContainerInfo(
            name="dns",
            status="exited",
            image="coredns",
            ports=[{"host_port": 53, "host_ip": "0.0.0.0", "container_port": 53, "protocol": "udp"}],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    by_port = {p["port"]: p for p in out["ports"]}
    wiki = by_port[8080]
    assert wiki["status"] == "configured"
    assert wiki["bind_scope"] == "lan"
    assert set(wiki["containers"][0]["bind_ips"]) == {"127.0.0.1", "10.0.0.5"}
    dns = by_port[53]
    assert dns["status"] == "configured"
    assert dns["protocol"] == "udp"


def test_container_tcp_and_udp_same_port():
    containers = [
        ContainerInfo(
            name="dns",
            status="exited",
            image="coredns",
            ports=[
                {"host_port": 53, "host_ip": "0.0.0.0", "container_port": 53, "protocol": "udp"},
                {"host_port": 53, "host_ip": "0.0.0.0", "container_port": 53, "protocol": "tcp"},
            ],
        ),
    ]
    row = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )["ports"][0]
    assert set(row["protocol"].split(",")) == {"tcp", "udp"}


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


def test_summary_counts_respect_range():
    listening = [
        ListeningPort(port=22, protocol="tcp", ip="0.0.0.0", process_name="sshd"),
        ListeningPort(port=8096, protocol="tcp", ip="0.0.0.0"),
    ]
    out = _classify(
        listening, [], [], [], hidden_ports=[],
        range_start=8000, range_end=9000,
        include_hidden=False, hidden_locked=False,
    )
    assert {p["port"] for p in out["ports"]} == {22, 8096}
    assert out["summary"]["used"] == 1
    assert out["summary"]["configured"] == 0
    assert out["summary"]["free"] == 9000 - 8000 + 1 - 1


def test_summary_free_on_full_port_space():
    out = _classify(
        [ListeningPort(port=22, protocol="tcp", ip="0.0.0.0", process_name="sshd")],
        [], [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    assert out["summary"]["used"] == 1
    assert out["summary"]["free"] == 65534


def test_hidden_used_port_is_not_free():
    out = _classify(
        [ListeningPort(port=8096, protocol="tcp", ip="0.0.0.0")],
        [], [], [], hidden_ports=[8096],
        range_start=8096, range_end=8096,
        include_hidden=False, hidden_locked=False,
    )
    assert out["ports"] == []
    assert out["summary"]["used"] == 0
    assert out["summary"]["free"] == 0
    assert out["summary"]["hidden"] == 1


def test_hidden_outside_range_is_omitted_from_legend():
    out = _classify(
        [ListeningPort(port=22, protocol="tcp", ip="0.0.0.0")],
        [], [], [], hidden_ports=[22],
        range_start=8000, range_end=9000,
        include_hidden=False, hidden_locked=False,
    )
    assert out["summary"]["hidden"] == 0
    assert out["summary"]["used"] == 0


def test_paused_container_is_used():
    containers = [
        ContainerInfo(
            name="wiki",
            status="paused",
            image="wiki",
            ports=[{"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"}],
        ),
        ContainerInfo(
            name="api",
            status="restarting",
            image="api",
            ports=[{"host_port": 3000, "host_ip": "0.0.0.0", "container_port": 3000, "protocol": "tcp"}],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert by_port[8080]["status"] == "used"
    assert by_port[3000]["status"] == "used"


def test_nginx_vhost_follows_virtual_port():
    containers = [
        ContainerInfo(
            name="wiki",
            status="running",
            image="wiki",
            ports=[
                {"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"},
                {"host_port": 9000, "host_ip": "0.0.0.0", "container_port": 9000, "protocol": "tcp"},
            ],
            vhost_urls=["https://wiki.lan"],
            vhost_port=80,
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://wiki.lan" in by_port[8080]["urls"]
    assert "https://wiki.lan" not in by_port[9000]["urls"]

    lone = [
        ContainerInfo(
            name="app",
            status="running",
            image="app",
            ports=[{"host_port": 9000, "host_ip": "0.0.0.0", "container_port": 9000, "protocol": "tcp"}],
            vhost_urls=["https://app.lan"],
            vhost_port=80,
        ),
    ]
    out = _classify(
        [], lone, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    assert "https://app.lan" in out["ports"][0]["urls"]


def test_host_network_expose_is_configured_until_listen():
    containers = [
        ContainerInfo(
            name="app",
            status="running",
            image="app",
            network_mode="host",
            ports=[{
                "host_port": 8080, "host_ip": "0.0.0.0",
                "container_port": 8080, "protocol": "tcp", "source": "expose",
            }],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    assert out["ports"][0]["status"] == "configured"
    listening = [ListeningPort(port=8080, protocol="tcp", ip="0.0.0.0")]
    used = _classify(
        listening, containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    assert used["ports"][0]["status"] == "used"


def test_label_urls_follow_web_mapping():
    containers = [
        ContainerInfo(
            name="wiki",
            status="running",
            image="wiki",
            urls=["https://wiki.lan"],
            label_port=80,
            ports=[
                {"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"},
                {"host_port": 9000, "host_ip": "0.0.0.0", "container_port": 9000, "protocol": "tcp"},
            ],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://wiki.lan" in by_port[8080]["urls"]
    assert "https://wiki.lan" not in by_port[9000]["urls"]


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
    assert len(by_port[8096]["compose_configs"]) == 2
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


def test_listen_tcp_keeps_published_udp():
    listening = [ListeningPort(port=53, protocol="tcp", ip="0.0.0.0")]
    containers = [
        ContainerInfo(
            name="dns",
            status="running",
            image="coredns",
            ports=[{"host_port": 53, "host_ip": "0.0.0.0", "container_port": 53, "protocol": "udp"}],
        ),
    ]
    compose = [
        ComposePort(
            port=53, compose_file="dns/compose.yml", project_dir="dns",
            service_name="coredns", protocol="udp",
        ),
    ]
    row = _classify(
        listening, containers, compose, [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )["ports"][0]
    assert set(row["protocol"].split(",")) == {"tcp", "udp"}


def test_host_network_child_pid_is_attributed():
    containers = [
        ContainerInfo(
            name="dns",
            status="running",
            image="coredns",
            ports=[],
            network_mode="host",
            pid=10,
            pids={10, 11},
        ),
    ]
    listening = [ListeningPort(port=53, protocol="udp", ip="0.0.0.0", pid=11, process_name="coredns")]
    row = _classify(
        listening, containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )["ports"][0]
    assert row["status"] == "used"
    assert any(c["name"] == "dns" for c in row["containers"])


def test_container_netns_joiner_inodes():
    containers = [
        ContainerInfo(
            name="helper",
            status="running",
            image="helper",
            ports=[],
            network_mode="container:vpn",
            pid=2,
            pids={2},
            socket_inodes={12},
        ),
    ]
    listening = [ListeningPort(port=51820, protocol="udp", ip="0.0.0.0", inode=12)]
    row = _classify(
        listening, containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )["ports"][0]
    assert any(c["name"] == "helper" for c in row["containers"])


def test_hidden_ports_listed_when_unlocked():
    out = _classify(
        [ListeningPort(port=8096, protocol="tcp", ip="0.0.0.0")],
        [], [], [], hidden_ports=[8096, 22],
        range_start=8000, range_end=9000,
        include_hidden=False, hidden_locked=False,
    )
    assert out["summary"]["hidden_ports"] == [22, 8096]
    occ = {row["port"]: row["status"] for row in out["summary"]["hidden_occupancy"]}
    assert occ == {22: "free", 8096: "used"}
    locked = _classify(
        [ListeningPort(port=8096, protocol="tcp", ip="0.0.0.0")],
        [], [], [], hidden_ports=[8096],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=True,
    )
    assert locked["summary"]["hidden_ports"] == []
    assert locked["summary"]["hidden_occupancy"] == []


def test_listen_ips_union_docker_bind_scope():
    listening = [ListeningPort(port=8080, protocol="tcp", ip="127.0.0.1")]
    containers = [
        ContainerInfo(
            name="wiki",
            status="running",
            image="wiki",
            ports=[{"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"}],
        ),
    ]
    row = _classify(
        listening, containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )["ports"][0]
    assert set(row["ips"]) >= {"127.0.0.1", "0.0.0.0"}
    assert row["bind_scope"] == "public"


def test_label_urls_do_not_paint_sidecar_ports():
    containers = [
        ContainerInfo(
            name="app",
            status="running",
            image="app",
            urls=["https://app.lan"],
            ports=[
                {"host_port": 3000, "host_ip": "0.0.0.0", "container_port": 3000, "protocol": "tcp"},
                {"host_port": 5432, "host_ip": "0.0.0.0", "container_port": 5432, "protocol": "tcp"},
            ],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://app.lan" in by_port[3000]["urls"]
    assert "https://app.lan" not in by_port[5432]["urls"]


def test_vhost_mismatch_does_not_paint_sidecars():
    containers = [
        ContainerInfo(
            name="app",
            status="running",
            image="app",
            vhost_urls=["https://app.lan"],
            vhost_port=3000,
            ports=[
                {"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"},
                {"host_port": 5432, "host_ip": "0.0.0.0", "container_port": 5432, "protocol": "tcp"},
            ],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://app.lan" in by_port[8080]["urls"]
    assert "https://app.lan" not in by_port[5432]["urls"]


def test_unmatched_label_port_falls_back_to_web_mapping():
    containers = [
        ContainerInfo(
            name="wiki",
            status="running",
            image="wiki",
            urls=["https://wiki.lan"],
            label_port=3000,
            ports=[
                {"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"},
                {"host_port": 9000, "host_ip": "0.0.0.0", "container_port": 9000, "protocol": "tcp"},
            ],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://wiki.lan" in by_port[8080]["urls"]
    assert "https://wiki.lan" not in by_port[9000]["urls"]


def test_inode_only_host_net_urls_stay_on_web_port():
    containers = [
        ContainerInfo(
            name="proxy",
            status="running",
            image="traefik",
            urls=["https://app.lan"],
            ports=[],
            network_mode="host",
            socket_inodes={1, 2},
        ),
    ]
    listening = [
        ListeningPort(port=80, protocol="tcp", ip="0.0.0.0", inode=1),
        ListeningPort(port=5432, protocol="tcp", ip="0.0.0.0", inode=2),
    ]
    out = _classify(
        listening, containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://app.lan" in by_port[80]["urls"]
    assert "https://app.lan" not in by_port[5432]["urls"]


def test_bridge_container_pid_does_not_steal_host_listen():
    containers = [
        ContainerInfo(
            name="jellyfin",
            status="running",
            image="jellyfin",
            network_mode="bridge",
            pid=100,
            ports=[{
                "host_port": 8096, "host_ip": "0.0.0.0",
                "container_port": 8096, "protocol": "tcp",
            }],
        ),
    ]
    listening = [
        ListeningPort(port=22, protocol="tcp", ip="0.0.0.0", pid=100, process_name="sshd"),
    ]
    out = _classify(
        listening, containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert all(c["name"] != "jellyfin" for c in by_port[22].get("containers") or [])
    assert any(c["name"] == "jellyfin" for c in by_port[8096]["containers"])


def test_traefik_metrics_port_does_not_take_wiki_url():
    from backend.docker_scanner import _traefik_service_port
    labels = {
        "traefik.http.services.metrics.loadbalancer.server.port": "8082",
        "traefik.http.services.wiki.loadbalancer.server.port": "80",
        "traefik.http.routers.wiki.rule": "Host(`wiki.lan`)",
    }
    containers = [
        ContainerInfo(
            name="wiki",
            status="running",
            image="wiki",
            urls=["https://wiki.lan"],
            label_port=_traefik_service_port(labels),
            ports=[
                {"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"},
                {"host_port": 8082, "host_ip": "0.0.0.0", "container_port": 8082, "protocol": "tcp"},
            ],
        ),
    ]
    out = _classify(
        [], containers, [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
        options={"guess_urls": False},
    )
    by_port = {p["port"]: p for p in out["ports"]}
    assert "https://wiki.lan" in by_port[8080]["urls"]
    assert "https://wiki.lan" not in by_port[8082]["urls"]


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


def test_hidden_free_port_emitted_when_included():
    shown = _classify(
        [], [], [], [], hidden_ports=[42424],
        range_start=1, range_end=65535,
        include_hidden=True, hidden_locked=False,
    )
    row = next(p for p in shown["ports"] if p["port"] == 42424)
    assert row["status"] == "free"
    assert row["is_hidden"] is True
    assert row["bind_scope"] is None
    omitted = _classify(
        [ListeningPort(port=8096, protocol="tcp", ip="0.0.0.0")],
        [], [], [], hidden_ports=[8096, 42424],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    assert 42424 not in {p["port"] for p in omitted["ports"]}
    assert 8096 not in {p["port"] for p in omitted["ports"]}
    occ = {row["port"]: row["status"] for row in omitted["summary"]["hidden_occupancy"]}
    assert occ[42424] == "free"
    assert occ[8096] == "used"


def test_classify_container_order_is_stable():
    a = ContainerInfo(
        name="zeta", status="running", image="z",
        ports=[{"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"}],
    )
    b = ContainerInfo(
        name="alpha", status="running", image="a",
        ports=[{"host_port": 8080, "host_ip": "0.0.0.0", "container_port": 80, "protocol": "tcp"}],
    )
    r1 = _classify(
        [], [a, b], [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    r2 = _classify(
        [], [b, a], [], [], hidden_ports=[],
        range_start=1, range_end=65535,
        include_hidden=False, hidden_locked=False,
    )
    names1 = [c["name"] for c in r1["ports"][0]["containers"]]
    names2 = [c["name"] for c in r2["ports"][0]["containers"]]
    assert names1 == names2 == ["alpha", "zeta"]
    assert _json_etag(r1)[1] == _json_etag(r2)[1]


def test_etag_matched_weak_and_list():
    etag = '"abc123"'
    assert _etag_matched(etag, etag) is True
    assert _etag_matched('W/"abc123"', etag) is True
    assert _etag_matched('"nope", "abc123"', etag) is True
    assert _etag_matched("*", etag) is True
    assert _etag_matched('"nope"', etag) is False
    assert _etag_matched(None, etag) is False
