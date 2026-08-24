from __future__ import annotations

import os

import pytest

from backend.compose_scanner import (
    ComposeWouldFail,
    _read_env_file,
    expand_port_range,
    parse_expose_entry,
    parse_port_entry,
    parse_short_port,
    scan_compose_files,
    scan_compose_tree,
    substitute_vars,
)
from backend.models import PortMapping
from backend.docker_scanner import (
    ContainerInfo,
    _attach_host_netns_sockets,
    _macvlan_ports,
    _network_is_host_netns,
    _recall_publish,
    _remember_publish,
    _resolve_container_ref,
    _traefik_service_port,
    extract_label_urls,
    extract_nginx_vhosts,
    extract_ports,
    safe_http_url,
)
from backend.port_scanner import (
    ListeningPort,
    _fill_process_names,
    descendant_pids,
    host_proc_available,
    is_host_netns_mode,
    normalize_ip,
    parse_proc_net_line,
    parse_ss_line,
)


def test_parse_short_port_basic():
    assert parse_short_port("8080:80") == [
        {"host_port": 8080, "container_port": 80, "protocol": "tcp", "host_ip": None}
    ]
    bound = parse_short_port("0.0.0.0:443:8443/tcp")[0]
    assert bound["host_port"] == 443
    assert bound["host_ip"] == "0.0.0.0"
    assert parse_short_port("53:53/udp")[0]["protocol"] == "udp"
    mapped = parse_short_port("53/udp:53")[0]
    assert mapped["host_port"] == 53
    assert mapped["protocol"] == "udp"
    assert parse_short_port("8080") == []


def test_port_range_expansion():
    assert expand_port_range("3000-3002") == [3000, 3001, 3002]
    assert len(expand_port_range("1000-2000")) == 128
    hosts = parse_short_port("6000-6002:80")
    assert [p["host_port"] for p in hosts] == [6000, 6001, 6002]
    long = parse_port_entry({"published": "9000-9001", "target": 80, "protocol": "tcp"})
    assert [p["host_port"] for p in long] == [9000, 9001]


def test_compose_include_and_depth(tmp_path):
    nested = tmp_path / "apps" / "wiki"
    nested.mkdir(parents=True)
    included = tmp_path / "shared" / "ports.yml"
    included.parent.mkdir()
    included.write_text(
        "services:\n  db:\n    ports:\n      - '5432:5432'\n",
        encoding="utf-8",
    )
    (nested / "compose.yml").write_text(
        "include:\n  - ../../shared/ports.yml\n"
        "services:\n  wiki:\n    ports:\n      - '3000-3001:80'\n",
        encoding="utf-8",
    )
    ports = scan_compose_files(str(tmp_path))
    numbers = sorted({p.port for p in ports})
    assert 5432 in numbers
    assert 3000 in numbers
    assert 3001 in numbers
    db = [p for p in ports if p.port == 5432]
    assert {p.project_dir for p in db} == {"apps/wiki"}
    assert {p.project_name for p in db} == {"wiki"}


def test_include_env_file_and_export(tmp_path):
    shared = tmp_path / "shared"
    app = tmp_path / "apps" / "web"
    shared.mkdir()
    app.mkdir(parents=True)
    (shared / "stack.env").write_text("export WEB_PORT=9080\n", encoding="utf-8")
    (shared / "ports.yml").write_text(
        "services:\n  web:\n    ports:\n      - '${WEB_PORT}:80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - path: ../../shared/ports.yml\n    env_file: ../../shared/stack.env\n",
        encoding="utf-8",
    )
    (app / ".env").write_text("export LOCAL_PORT=18080\n", encoding="utf-8")
    (app / "compose.yml").write_text(
        (app / "compose.yml").read_text(encoding="utf-8")
        + "services:\n  local:\n    ports:\n      - '${LOCAL_PORT}:80'\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(tmp_path))}
    assert 9080 in numbers
    assert 18080 in numbers
    web = [p for p in scan_compose_files(str(tmp_path)) if p.port == 9080]
    assert {p.project_dir for p in web} == {"apps/web"}


def test_top_level_env_file_and_path_mapping(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "ports.env").write_text("WEB_PORT=9100\n", encoding="utf-8")
    (app / "compose.yml").write_text(
        "env_file:\n  - path: ports.env\n"
        "services:\n  web:\n    ports:\n      - '${WEB_PORT}:80'\n",
        encoding="utf-8",
    )
    assert 9100 in {p.port for p in scan_compose_files(str(tmp_path))}

    shared = tmp_path / "shared"
    other = tmp_path / "other"
    shared.mkdir()
    other.mkdir()
    (shared / "stack.env").write_text("DB_PORT=9101\n", encoding="utf-8")
    (shared / "ports.yml").write_text(
        "services:\n  db:\n    ports:\n      - '${DB_PORT}:5432'\n",
        encoding="utf-8",
    )
    (other / "compose.yml").write_text(
        "include:\n  - path: ../shared/ports.yml\n    env_file:\n      path: ../shared/stack.env\n",
        encoding="utf-8",
    )
    assert 9101 in {p.port for p in scan_compose_files(str(tmp_path))}


def test_compose_name_and_shared_include(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ports.yml").write_text(
        "services:\n  db:\n    ports:\n      - '5432:5432'\n",
        encoding="utf-8",
    )
    for folder, name in (("wiki", "wiki-stack"), ("blog", "blog-stack")):
        d = tmp_path / folder
        d.mkdir()
        (d / "compose.yml").write_text(
            f"name: {name}\ninclude:\n  - ../shared/ports.yml\n",
            encoding="utf-8",
        )
    ports = [p for p in scan_compose_files(str(tmp_path)) if p.port == 5432]
    assert {p.project_dir for p in ports} == {"wiki", "blog"}
    assert {p.project_name for p in ports} == {"wiki-stack", "blog-stack"}


def test_include_path_list(tmp_path):
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    shared.mkdir()
    app.mkdir()
    (shared / "a.yml").write_text(
        "services:\n  a:\n    ports:\n      - '7001:80'\n",
        encoding="utf-8",
    )
    (shared / "b.yml").write_text(
        "services:\n  b:\n    ports:\n      - '7002:80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - path:\n      - ../shared/a.yml\n      - ../shared/b.yml\n",
        encoding="utf-8",
    )
    ports = [p for p in scan_compose_files(str(tmp_path)) if p.port in (7001, 7002)]
    assert {p.port for p in ports} == {7001, 7002}
    assert {p.project_dir for p in ports} == {"app"}


def test_include_path_glob(tmp_path):
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    shared.mkdir()
    app.mkdir()
    (shared / "a.yml").write_text(
        "services:\n  a:\n    ports:\n      - '7101:80'\n",
        encoding="utf-8",
    )
    (shared / "b.yml").write_text(
        "services:\n  b:\n    ports:\n      - '7102:80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - ../shared/*.yml\n",
        encoding="utf-8",
    )
    ports = {p.port for p in scan_compose_files(str(tmp_path)) if p.port in (7101, 7102)}
    assert ports == {7101, 7102}


def test_compose_extends_and_bom(tmp_path):
    common = tmp_path / "common.yml"
    common.write_text(
        "services:\n  base:\n    ports:\n      - '8080:80'\n",
        encoding="utf-8",
    )
    app = tmp_path / "app"
    app.mkdir()
    (app / "compose.yml").write_text(
        "\ufeffservices:\n"
        "  web:\n"
        "    extends:\n"
        "      file: ../common.yml\n"
        "      service: base\n"
        "    ports:\n"
        "      - '8443:443'\n"
        "  dns:\n"
        "    extends: hosttmpl\n"
        "  hosttmpl:\n"
        "    network_mode: host\n"
        "    expose:\n"
        "      - '53/udp'\n",
        encoding="utf-8",
    )
    ports = scan_compose_files(str(tmp_path))
    web = {p.port for p in ports if p.service_name == "web"}
    assert web == {8080, 8443}
    dns = [p for p in ports if p.service_name == "dns"]
    assert {p.port for p in dns} == {53}
    assert dns[0].protocol == "udp"
    assert dns[0].network_mode == "host"
    assert {p.project_dir for p in ports if p.service_name == "web"} == {"app"}


def test_ephemeral_and_invalid_host_ports():
    assert parse_short_port("0:80") == []
    assert parse_short_port("65536:80") == []
    assert parse_port_entry({"published": 0, "target": 80}) == []
    assert parse_expose_entry(0) == []
    assert parse_expose_entry("0") == []
    hosts = parse_short_port("0-2:80")
    assert [p["host_port"] for p in hosts] == [1, 2]
    attrs = {
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {
                "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "0"}],
            },
        },
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {}},
    }
    assert extract_ports(attrs) == []


def test_env_default_substitution():
    out = substitute_vars("ports: '${WEB_PORT:-8080}:80'", {})
    assert "8080:80" in out
    hyphen = substitute_vars("ports: '${NO_SUCH-9090}:80'", {})
    assert "9090:80" in hyphen


def test_env_empty_uses_colon_default_only():
    colon = substitute_vars("ports: '${WEB_PORT:-8080}:80'", {"WEB_PORT": ""})
    assert "8080:80" in colon
    hyphen = substitute_vars("ports: '${WEB_PORT-9090}:80'", {"WEB_PORT": ""})
    assert hyphen == "ports: ':80'"
    assert substitute_vars("cmd: echo $$HOME", {}) == "cmd: echo $HOME"
    assert substitute_vars("x: $${unset}", {}) == "x: ${unset}"


def test_proc_tcp_and_udp():
    tcp = (
        "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 "
        "00:00000000 00000000     0        0 12345 1 0000000000000000 00"
    )
    lp = parse_proc_net_line(tcp, "tcp")
    assert lp is not None
    assert lp.port == 80
    assert lp.ip == "0.0.0.0"
    assert lp.inode == 12345

    udp = (
        "   0: 00000000:0035 00000000:0000 07 00000000:00000000 "
        "00:00000000 00000000     0        0 99 1 0000000000000000 00"
    )
    up = parse_proc_net_line(udp, "udp")
    assert up is not None
    assert up.port == 53
    assert up.protocol == "udp"


def test_ss_lines():
    tcp = 'tcp   LISTEN 0  4096  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=1,fd=3))'
    lp = parse_ss_line(tcp)
    assert lp is not None
    assert lp.port == 22
    assert lp.process_name == "sshd"
    udp = "udp   UNCONN 0  0  0.0.0.0:53  0.0.0.0:*"
    up = parse_ss_line(udp)
    assert up is not None
    assert up.port == 53
    assert up.protocol == "udp"
    tcp4 = parse_ss_line("tcp4 LISTEN 0 128 127.0.0.1:22 0.0.0.0:*")
    assert tcp4 is not None
    assert tcp4.port == 22
    assert tcp4.ip == "127.0.0.1"


def test_normalize_ip():
    assert normalize_ip("::ffff:127.0.0.1") == "127.0.0.1"
    assert normalize_ip("*") == "0.0.0.0"
    assert normalize_ip("fe80::1%eth0") == "fe80::1"
    assert normalize_ip("::ffff:c0a8:10a") == "192.168.1.10"


def test_fill_process_names_from_proc(tmp_path):
    fd = tmp_path / "42" / "fd"
    fd.mkdir(parents=True)
    (tmp_path / "42" / "comm").write_text("sshd\n", encoding="utf-8")
    os.symlink("socket:[12345]", fd / "3")
    lp = ListeningPort(port=22, protocol="tcp", ip="0.0.0.0", inode=12345)
    _fill_process_names([lp], str(tmp_path))
    assert lp.process_name == "sshd"
    assert lp.pid == 42


def test_host_proc_available_skips_container_namespace(monkeypatch):
    monkeypatch.setattr(
        "backend.port_scanner.os.path.exists",
        lambda path: path in ("/proc/net/tcp", "/.dockerenv"),
    )
    assert host_proc_available() is False
    monkeypatch.setattr(
        "backend.port_scanner.os.path.exists",
        lambda path: path == "/host/proc/1/net/tcp",
    )
    assert host_proc_available() is True
    monkeypatch.setattr(
        "backend.port_scanner.os.path.exists",
        lambda path: path == "/proc/net/tcp",
    )
    assert host_proc_available() is True


def test_ss_ipv6_and_star():
    line = "tcp   LISTEN 0  4096  [::]:443  [::]:*"
    lp = parse_ss_line(line)
    assert lp is not None
    assert lp.port == 443
    assert lp.ip == "::"
    star = parse_ss_line("tcp LISTEN 0 128 *:22 *:*")
    assert star is not None
    assert star.port == 22
    star6 = parse_ss_line("tcp6 LISTEN 0 128 *:443 *:*")
    assert star6 is not None
    assert star6.port == 443
    assert star6.ip == "::"
    assert star6.protocol == "tcp6"
    assert parse_ss_line("tcp LISTEN 0 128 0.0.0.0:0 0.0.0.0:*") is None
    zero = (
        "   0: 00000000:0000 00000000:0000 0A 00000000:00000000 "
        "00:00000000 00000000     0        0 1 1 0000000000000000 00"
    )
    assert parse_proc_net_line(zero, "tcp") is None
    assert star.ip == "0.0.0.0"


def test_proc_tcp6():
    line = (
        "   0: 00000000000000000000000000000000:01BB "
        "00000000000000000000000000000000:0000 0A 00000000:00000000 "
        "00:00000000 00000000     0        0 7 1 0000000000000000 00"
    )
    lp = parse_proc_net_line(line, "tcp6")
    assert lp is not None
    assert lp.port == 443
    assert lp.ip == "::"
    assert lp.inode == 7


def test_extract_port_bindings_and_homepage():
    attrs = {
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {
                "8096/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8096"}],
            },
        },
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {}},
    }
    ports = extract_ports(attrs)
    assert all(isinstance(row, PortMapping) for row in ports)
    assert ports[0].host_port == 8096
    assert ports[0].host_ip == "127.0.0.1"
    v6 = extract_ports({
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {
                "80/tcp": [{"HostIp": "[::1]", "HostPort": "8080"}],
            },
        },
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {}},
    })
    assert v6[0].host_ip == "::1"
    urls = extract_label_urls({
        "homepage.href": "http://photos.lan:2283",
        "traefik.http.routers.x.rule": "HostRegexp(`*.home.arpa`)",
    })
    assert "http://photos.lan:2283" in urls
    assert all("*" not in u for u in urls)


def test_env_var_without_default():
    out = substitute_vars("ports: '${WEB_PORT}:80'", {"WEB_PORT": "9090"})
    assert "9090:80" in out
    missing = substitute_vars("ports: '${NO_SUCH_PORT}:80'", {})
    assert "${NO_SUCH_PORT}:80" in missing


def test_compose_ignores_process_environment(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "9090")
    monkeypatch.setenv("HOSTNAME", "port-light")
    assert "${WEB_PORT}:80" in substitute_vars("ports: '${WEB_PORT}:80'", {})
    assert "8080:80" in substitute_vars("ports: '${WEB_PORT:-8080}:80'", {})


def test_long_syntax_and_ipv4_host():
    assert parse_short_port("127.0.0.1:8080:80")[0]["host_port"] == 8080
    assert parse_short_port("127.0.0.1:8080:80")[0]["host_ip"] == "127.0.0.1"
    v6 = parse_short_port("[::1]:8080:80")[0]
    assert v6["host_port"] == 8080
    assert v6["host_ip"] == "::1"
    long_ip = parse_port_entry({"published": 9000, "target": 80, "host_ip": "10.0.0.5"})
    assert long_ip[0]["host_ip"] == "10.0.0.5"
    assert parse_port_entry({"published": "5353/udp", "target": 53})[0]["protocol"] == "udp"
    target_slash = parse_port_entry({"published": 5353, "target": "53/udp"})
    assert target_slash[0]["host_port"] == 5353
    assert target_slash[0]["container_port"] == 53
    assert target_slash[0]["protocol"] == "udp"
    host_udp = parse_port_entry({"target": "53/udp", "mode": "host"})
    assert host_udp[0]["host_port"] == 53
    assert host_udp[0]["protocol"] == "udp"
    assert parse_port_entry("not-a-port") == []
    assert parse_port_entry({"target": 80}) == []
    host_mode = parse_port_entry({"target": 8080, "mode": "host"})
    assert host_mode[0]["host_port"] == 8080
    assert host_mode[0]["container_port"] == 8080
    assert parse_port_entry({"published": 0, "target": 8080, "mode": "host"})[0]["host_port"] == 8080
    assert parse_port_entry({"published": "", "target": 8080, "mode": "host"})[0]["host_port"] == 8080
    proto_key = parse_port_entry({"53/udp": 53})
    assert proto_key[0]["host_port"] == 53
    assert proto_key[0]["protocol"] == "udp"
    attrs = {
        "HostConfig": {
            "NetworkMode": "host",
            "PortBindings": {},
        },
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {"53/udp": {}, "80/tcp": {}}},
    }
    ports = extract_ports(attrs)
    mapped = {(p.host_port, p.protocol) for p in ports}
    assert (53, "udp") in mapped
    assert (80, "tcp") in mapped

    urls = extract_label_urls({
        "traefik.http.routers.wiki.rule": "Host(`wiki.home.arpa`, `wiki.lan`)",
        "traefik.tcp.routers.db.rule": "HostSNI(`db.home.arpa`)",
        "caddy": "media.home.arpa",
    })
    assert "https://wiki.home.arpa" in urls
    assert "https://wiki.lan" in urls
    assert "https://db.home.arpa" in urls
    assert "https://media.home.arpa" in urls
    numbered = extract_label_urls({
        "caddy_0": "photos.home.arpa",
        "caddy_1": "files.home.arpa",
    })
    assert "https://photos.home.arpa" in numbered
    assert "https://files.home.arpa" in numbered
    disabled = extract_label_urls({
        "traefik.enable": "false",
        "traefik.http.routers.wiki.rule": "Host(`wiki.home.arpa`)",
        "homepage.href": "http://wiki.lan:3000",
    })
    assert all("wiki.home.arpa" not in u for u in disabled)
    assert "http://wiki.lan:3000" in disabled
    unraid = extract_label_urls({
        "net.unraid.docker.webui": "http://[IP]:[PORT:8096]/",
    })
    assert "http://localhost:8096" in unraid
    header = extract_label_urls({
        "traefik.http.routers.x.rule": "HostHeader(`photos.home.arpa`)",
    })
    assert "https://photos.home.arpa" in header
    caddy_dir = extract_label_urls({"caddy": "reverse_proxy localhost:8080"})
    assert caddy_dir == []
    proxied = extract_label_urls(
        {},
        ["VIRTUAL_HOST=wiki.lan,wiki.home.arpa", "LETSENCRYPT_HOST=wiki.lan"],
    )
    assert "https://wiki.lan" in proxied
    assert "https://wiki.home.arpa" in proxied
    vurls, vport = extract_nginx_vhosts(
        {},
        ["VIRTUAL_HOST=wiki.lan", "VIRTUAL_PORT=8080"],
    )
    assert vurls == ["https://wiki.lan"]
    assert vport == 8080
    defaulted, default_port = extract_nginx_vhosts({}, ["VIRTUAL_HOST=wiki.lan"])
    assert defaulted == ["https://wiki.lan"]
    assert default_port == 80
    assert extract_label_urls(
        {"VIRTUAL_HOST": "wiki.lan"},
        include_nginx=False,
    ) == []


def test_compose_override_file(tmp_path):
    app = tmp_path / "media"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n  jellyfin:\n    ports:\n      - '8096:8096'\n",
        encoding="utf-8",
    )
    (app / "compose.override.yml").write_text(
        "services:\n  jellyfin:\n    ports:\n      - '8920:8920'\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(tmp_path))}
    assert 8096 in numbers
    assert 8920 in numbers


def test_host_network_expose(tmp_path):
    app = tmp_path / "dns"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n"
        "  adguard:\n"
        "    network_mode: host\n"
        "    expose:\n"
        "      - '53/udp'\n"
        "      - 80\n"
        "  bridged:\n"
        "    expose:\n"
        "      - '9000'\n",
        encoding="utf-8",
    )
    found = {(p.port, p.protocol, p.network_mode) for p in scan_compose_files(str(tmp_path))}
    assert (53, "udp", "host") in found
    assert (80, "tcp", "host") in found
    assert all(p != 9000 for p, _, _ in found)
    assert parse_expose_entry("53/udp")[0]["host_port"] == 53
    assert parse_expose_entry("8080:80") == []
    assert parse_expose_entry(53)[0]["protocol"] == "tcp"


def test_env_file_strips_utf8_bom(tmp_path):
    app = tmp_path / "web"
    app.mkdir()
    (app / ".env").write_bytes(b"\xef\xbb\xbfexport WEB_PORT=7777\n")
    (app / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '${WEB_PORT}:80'\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(tmp_path))}
    assert 7777 in numbers


def test_label_urls_reject_javascript():
    assert safe_http_url("javascript:alert(1)") is None
    assert safe_http_url("/relative") is None
    assert safe_http_url("http://photos.lan:2283") == "http://photos.lan:2283"
    assert safe_http_url("https://nas:192.168.1.10") is None
    assert extract_label_urls({"homepage.href": "javascript:alert(1)"}) == []


def test_compose_occupancy_gaps(tmp_path):
    apps = tmp_path / "apps" / "wiki"
    other = tmp_path / "other" / "wiki"
    apps.mkdir(parents=True)
    other.mkdir(parents=True)
    (apps / "compose.yml").write_text(
        "services:\n  wiki:\n    ports:\n      - '3000:80'\n",
        encoding="utf-8",
    )
    (other / "compose.yml").write_text(
        "services:\n  wiki:\n    ports:\n      - '3000:80'\n",
        encoding="utf-8",
    )
    ports = [p for p in scan_compose_files(str(tmp_path)) if p.port == 3000]
    assert {p.project_dir for p in ports} == {"apps/wiki", "other/wiki"}

    prod = tmp_path / "stack"
    prod.mkdir()
    (prod / "compose.prod.yml").write_text(
        "services:\n  api:\n    ports:\n      - '4000:80'\n"
        "    deploy:\n      ports:\n        - published: 4001\n          target: 80\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(tmp_path / "stack"))}
    assert 4000 in numbers
    assert 4001 in numbers

    mapped = tmp_path / "map"
    mapped.mkdir()
    (mapped / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - 8080: 80\n      - '8081:80/TCP'\n",
        encoding="utf-8",
    )
    mapped_ports = {p.port: p.protocol for p in scan_compose_files(str(tmp_path / "map"))}
    assert mapped_ports[8080] == "tcp"
    assert mapped_ports[8081] == "tcp"

    sidecar = tmp_path / "ns"
    sidecar.mkdir()
    (sidecar / "compose.yml").write_text(
        "services:\n"
        "  vpn:\n    network_mode: host\n    ports:\n      - '51820:51820/udp'\n"
        "    expose:\n      - '41641/udp'\n"
        "  helper:\n    network_mode: service:vpn\n    ports:\n      - '9999:80'\n",
        encoding="utf-8",
    )
    ns_ports = {p.port for p in scan_compose_files(str(tmp_path / "ns"))}
    assert 51820 not in ns_ports
    assert 9999 not in ns_ports
    assert 41641 in ns_ports

    tagged = tmp_path / "tags"
    tagged.mkdir()
    (tagged / "compose.yml").write_text(
        "services:\n  web:\n    ports: !reset\n      - '6000:80'\n",
        encoding="utf-8",
    )
    assert 6000 in {p.port for p in scan_compose_files(str(tmp_path / "tags"))}

    bad = tmp_path / "mixed"
    good = bad / "ok"
    junk = bad / "junk"
    good.mkdir(parents=True)
    junk.mkdir()
    (junk / "compose.yml").write_bytes(b"\xff\xfe services: {}\n")
    (good / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '6100:80'\n",
        encoding="utf-8",
    )
    assert 6100 in {p.port for p in scan_compose_files(str(tmp_path / "mixed"))}


def test_required_env_and_extends_env(tmp_path):
    assert "7200:80" in substitute_vars("ports: '${MUST_PORT:?missing}:80'", {"MUST_PORT": "7200"})
    with pytest.raises(ComposeWouldFail):
        substitute_vars("ports: '${NOPE:?missing}:80'", {})
    assert substitute_vars("ports: '${NOPE:?missing}:80'", {}, required=False) == "ports: ':80'"
    assert "7300:80" in substitute_vars("ports: '${MUST_PORT?missing}:80'", {"MUST_PORT": "7300"})
    with pytest.raises(ComposeWouldFail):
        substitute_vars("ports: '${ABSENT?missing}:80'", {})
    assert substitute_vars("ports: '${EMPTY?missing}:80'", {"EMPTY": ""}) == "ports: ':80'"

    base = tmp_path / "lib"
    app = tmp_path / "app"
    base.mkdir()
    app.mkdir()
    (base / ".env").write_text("BASE_PORT=7400\n", encoding="utf-8")
    (base / "common.yml").write_text(
        "services:\n  base:\n    ports:\n      - '${BASE_PORT}:80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "services:\n  web:\n    extends:\n      file: ../lib/common.yml\n      service: base\n",
        encoding="utf-8",
    )
    assert 7400 in {p.port for p in scan_compose_files(str(tmp_path))}

    env_base = tmp_path / "lib2"
    env_app = tmp_path / "app2"
    env_base.mkdir()
    env_app.mkdir()
    (env_base / "ports.env").write_text("WEB_PORT=7411\n", encoding="utf-8")
    (env_base / "common.yml").write_text(
        "env_file: ports.env\nservices:\n  base:\n    ports:\n      - '${WEB_PORT}:80'\n",
        encoding="utf-8",
    )
    (env_app / "compose.yml").write_text(
        "services:\n  web:\n    extends:\n      file: ../lib2/common.yml\n      service: base\n",
        encoding="utf-8",
    )
    assert 7411 in {p.port for p in scan_compose_files(str(tmp_path))}


def test_scan_parses_each_compose_file_once(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "base.yml").write_text(
        "services:\n  s:\n    ports: ['7700:80']\n",
        encoding="utf-8",
    )
    appdir = tmp_path / "app"
    appdir.mkdir()
    (appdir / "compose.yml").write_text(
        "include:\n  - ../shared/base.yml\nservices:\n  web:\n    ports: ['7710:80']\n",
        encoding="utf-8",
    )
    from backend import compose_scanner as cs

    counts: dict[str, int] = {}
    real = cs._compose_doc_uncached

    def counting(filepath, *a, **kw):
        name = os.path.basename(str(filepath))
        counts[name] = counts.get(name, 0) + 1
        return real(filepath, *a, **kw)

    monkeypatch.setattr(cs, "_compose_doc_uncached", counting)
    scan = cs.scan_compose_tree(str(tmp_path))
    assert {p.port for p in scan.ports} == {7700, 7710}
    # Without the per-scan cache, base.yml was loaded for include discovery,
    # macvlan names, and service parsing — three loads for one file.
    assert counts == {"compose.yml": 1, "base.yml": 1}


def test_broken_base_compose_file_marks_incomplete(tmp_path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (good / "compose.yml").write_text(
        "services:\n  web:\n    ports: ['7610:80']\n",
        encoding="utf-8",
    )
    (bad / "compose.yml").write_text(
        "services:\n  web:\n  - ports: [oops\n",
        encoding="utf-8",
    )
    scan = scan_compose_tree(str(tmp_path))
    assert scan.incomplete is True
    assert {p.port for p in scan.ports} == {7610}


def test_compose_would_fail_skips_project_not_siblings(tmp_path):
    ok = tmp_path / "ok"
    bad = tmp_path / "bad"
    ok.mkdir()
    bad.mkdir()
    (ok / "compose.yml").write_text(
        "services:\n  web:\n    ports: ['7600:80']\n",
        encoding="utf-8",
    )
    (bad / "compose.yml").write_text(
        "services:\n"
        "  keep:\n    ports: ['7500:80']\n"
        "  web:\n    ports: ['${MUST:?missing}:80']\n",
        encoding="utf-8",
    )
    scan = scan_compose_tree(str(tmp_path))
    assert scan.incomplete is True
    assert {p.port for p in scan.ports} == {7600}

    filled = tmp_path / "filled"
    filled.mkdir()
    (filled / "ports.env").write_text("MUST=7511\n", encoding="utf-8")
    (filled / "compose.yml").write_text(
        "env_file: ports.env\n"
        "services:\n  web:\n    ports: ['${MUST:?missing}:80']\n",
        encoding="utf-8",
    )
    assert 7511 in {p.port for p in scan_compose_files(str(filled))}

    optional = tmp_path / "optional"
    optional.mkdir()
    (optional / "compose.yml").write_text(
        "env_file:\n  - path: missing.env\n    required: false\n"
        "services:\n  web:\n    ports: ['7900:80']\n",
        encoding="utf-8",
    )
    opt_scan = scan_compose_tree(str(optional))
    assert opt_scan.incomplete is False
    assert {p.port for p in opt_scan.ports} == {7900}

    required = tmp_path / "required"
    required.mkdir()
    (required / "compose.yml").write_text(
        "env_file: missing.env\n"
        "services:\n  web:\n    ports: ['7910:80']\n",
        encoding="utf-8",
    )
    req_scan = scan_compose_tree(str(required))
    assert req_scan.incomplete is True
    assert req_scan.ports == []

    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "compose.yml").write_text(
        "include:\n  - ./gone.yml\n"
        "services:\n  web:\n    ports: ['7700:80']\n",
        encoding="utf-8",
    )
    inc_scan = scan_compose_tree(str(parent))
    assert inc_scan.incomplete is True
    assert inc_scan.ports == []

    svc = tmp_path / "svcenv"
    svc.mkdir()
    (svc / "compose.yml").write_text(
        "services:\n  web:\n    env_file: gone.env\n    ports: ['7930:80']\n",
        encoding="utf-8",
    )
    svc_scan = scan_compose_tree(str(svc))
    assert svc_scan.incomplete is True
    assert svc_scan.ports == []

    nested = tmp_path / "data" / "wiki"
    nested.mkdir(parents=True)
    (nested / "compose.yml").write_text(
        "services:\n  web:\n    ports: ['7800:80']\n",
        encoding="utf-8",
    )
    assert 7800 in {p.port for p in scan_compose_files(str(tmp_path / "data"))}


def test_traefik_http_entrypoint_and_caddy_scheme():
    http_only = extract_label_urls({
        "traefik.http.routers.wiki.rule": "Host(`wiki.lan`)",
        "traefik.http.routers.wiki.entrypoints": "web",
    })
    assert "http://wiki.lan" in http_only
    assert "https://wiki.lan" not in http_only
    regexp = extract_label_urls({
        "traefik.http.routers.x.rule": "HostRegexp(`^foo\\.bar$`)",
    })
    assert regexp == []
    caddy = extract_label_urls({"caddy": "http://media.home.arpa"})
    assert "http://media.home.arpa" in caddy


def test_empty_host_proc_is_authoritative(monkeypatch):
    from backend import port_scanner as ps
    monkeypatch.setattr(ps.os.path, "exists", lambda path: path == "/host/proc/1/net/tcp")
    monkeypatch.setattr(ps, "_read_proc_net_file", lambda path, proto: [])
    monkeypatch.setattr(ps, "_fill_process_names", lambda ports, root, **kwargs: None)

    def boom():
        raise FileNotFoundError("ss")

    monkeypatch.setattr(ps, "_scan_with_ss", boom)
    monkeypatch.setattr(
        ps, "_scan_with_proc",
        lambda: [ListeningPort(port=80, protocol="tcp", ip="0.0.0.0")],
    )
    assert ps.scan_listening_ports() == []


def test_fill_process_names_prefers_known_pids(tmp_path):
    for i in range(1, 40):
        fd = tmp_path / str(i) / "fd"
        fd.mkdir(parents=True)
        (tmp_path / str(i) / "comm").write_text("other\n", encoding="utf-8")
        os.symlink("socket:[1]", fd / "3")
    fd = tmp_path / "99" / "fd"
    fd.mkdir(parents=True)
    (tmp_path / "99" / "comm").write_text("sshd\n", encoding="utf-8")
    os.symlink("socket:[12345]", fd / "3")
    lp = ListeningPort(port=22, protocol="tcp", ip="0.0.0.0", inode=12345)
    _fill_process_names([lp], str(tmp_path), budget_s=0.0, prefer_pids=[99])
    assert lp.process_name == "sshd"
    assert lp.pid == 99


def test_descendant_pids_walks_children_file(tmp_path):
    for pid, kids in ((1, "2 3"), (2, ""), (3, "4"), (4, "")):
        task = tmp_path / str(pid) / "task" / str(pid)
        task.mkdir(parents=True)
        (task / "children").write_text(kids + "\n", encoding="utf-8")
    assert descendant_pids(1, str(tmp_path)) == [1, 2, 3, 4]


def test_macvlan_includes_ipv6(monkeypatch):
    monkeypatch.setattr(
        "backend.docker_scanner._network_driver",
        lambda client, nid: "macvlan",
    )
    extra = _macvlan_ports(None, {
        "NetworkSettings": {"Networks": {"lan": {
            "NetworkID": "abc",
            "IPAddress": "",
            "GlobalIPv6Address": "fd12::10",
        }}},
        "Config": {"ExposedPorts": {"80/tcp": {}}},
    }, [])
    assert extra[0].host_ip == "fd12::10"
    assert extra[0].host_port == 80
    both = _macvlan_ports(None, {
        "NetworkSettings": {"Networks": {"lan": {
            "NetworkID": "abc",
            "IPAddress": "10.0.0.9",
            "GlobalIPv6Address": "fd12::10",
        }}},
        "Config": {"ExposedPorts": {"443/tcp": {}}},
    }, [])
    ips = {row.host_ip for row in both}
    assert ips == {"10.0.0.9", "fd12::10"}


def test_container_joiner_follows_host_netns():
    vpn = ContainerInfo(name="vpn", status="running", image="vpn", network_mode="host", container_id="abc123def456")
    helper = ContainerInfo(
        name="helper", status="running", image="helper",
        network_mode="container:vpn",
    )
    by_name = {"vpn": vpn, "helper": helper}
    by_id = {"abc123def456": vpn, "abc123def456"[:12]: vpn}
    assert _network_is_host_netns("host", by_name, by_id) is True
    assert _network_is_host_netns("container:vpn", by_name, by_id) is True
    assert _network_is_host_netns("container:abc123def456", by_name, by_id) is True
    assert _network_is_host_netns("bridge", by_name, by_id) is False
    other = ContainerInfo(name="web", status="running", image="web", network_mode="bridge", container_id="fff")
    by_name["web"] = other
    by_id["fff"] = other
    assert _network_is_host_netns("container:web", by_name, by_id) is False


def test_compose_scan_reports_file_cap(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "compose.yml").write_text(
        "services:\n  a:\n    ports: ['8001:80']\n", encoding="utf-8",
    )
    (tmp_path / "b" / "compose.yml").write_text(
        "services:\n  b:\n    ports: ['8002:80']\n", encoding="utf-8",
    )
    scan = scan_compose_tree(str(tmp_path), max_files=1)
    assert scan.truncated is True
    assert scan.files_scanned == 1
    assert len({p.port for p in scan.ports}) == 1
    full = scan_compose_tree(str(tmp_path), max_files=10)
    assert full.truncated is False
    assert {p.port for p in full.ports} == {8001, 8002}


def test_extends_reset_replaces_parent_ports(tmp_path):
    (tmp_path / "compose.yml").write_text(
        "services:\n"
        "  base:\n    ports:\n      - '8080:80'\n      - '8081:80'\n"
        "  web:\n    extends: base\n    ports: !reset\n      - '9090:80'\n",
        encoding="utf-8",
    )
    ports = scan_compose_files(str(tmp_path))
    web = {p.port for p in ports if p.service_name == "web"}
    base = {p.port for p in ports if p.service_name == "base"}
    assert web == {9090}
    assert base == {8080, 8081}


def test_missing_host_pid1_tcp_does_not_fall_through(monkeypatch):
    from backend import port_scanner as ps
    monkeypatch.setattr(ps.os.path, "exists", lambda path: path == "/host/proc")
    monkeypatch.setattr(
        ps, "_scan_with_ss",
        lambda: [ListeningPort(port=80, protocol="tcp", ip="0.0.0.0")],
    )
    monkeypatch.setattr(
        ps, "_scan_with_proc",
        lambda: [ListeningPort(port=443, protocol="tcp", ip="0.0.0.0")],
    )
    assert ps.scan_listening_ports() == []


def test_dual_stack_empty_hostport_copies_sibling():
    attrs = {
        "HostConfig": {"NetworkMode": "bridge", "PortBindings": {}},
        "NetworkSettings": {"Ports": {
            "80/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "32768"},
                {"HostIp": "::", "HostPort": ""},
            ],
        }},
        "Config": {"ExposedPorts": {}},
    }
    ports = extract_ports(attrs)
    ips = {p.host_ip for p in ports if p.host_port == 32768}
    assert ips == {"0.0.0.0", "::"}


def test_ephemeral_binding_filled_from_network_settings():
    attrs = {
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "0"}]},
        },
        "NetworkSettings": {
            "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}]},
        },
        "Config": {"ExposedPorts": {}},
    }
    assert {p.host_port for p in extract_ports(attrs)} == {32768}


def test_macvlan_secondary_and_prefixed_ipv6(monkeypatch):
    monkeypatch.setattr(
        "backend.docker_scanner._network_driver",
        lambda client, nid: "ipvlan",
    )
    extra = _macvlan_ports(None, {
        "NetworkSettings": {"Networks": {"lan": {
            "NetworkID": "abc",
            "IPAddress": "10.0.0.9",
            "IPv6Address": "fd12::10/64",
            "SecondaryIPAddresses": [{"Addr": "10.0.0.10/24"}],
        }}},
        "Config": {"ExposedPorts": {"80/tcp": {}}},
    }, [])
    ips = {row.host_ip for row in extra}
    assert ips == {"10.0.0.9", "fd12::10", "10.0.0.10"}


def test_compose_macvlan_and_ns_host(tmp_path):
    (tmp_path / "compose.yml").write_text(
        "networks:\n"
        "  lan:\n    driver: macvlan\n"
        "  backend:\n    driver: bridge\n"
        "services:\n"
        "  cam:\n"
        "    networks:\n"
        "      lan:\n        ipv4_address: 192.168.1.50\n"
        "    expose:\n      - '8080'\n"
        "    ports:\n      - '9000:80'\n"
        "  db:\n"
        "    networks:\n"
        "      backend:\n        ipv4_address: 172.18.0.10\n"
        "    expose:\n      - '5432'\n"
        "  vpn:\n"
        "    network_mode: ns:/proc/1/ns/net\n"
        "    expose:\n      - '41641/udp'\n",
        encoding="utf-8",
    )
    ports = scan_compose_files(str(tmp_path))
    cam = [p for p in ports if p.service_name == "cam"]
    assert {p.port for p in cam if p.host_ip is None} == {9000}
    lan = {(p.port, p.host_ip) for p in cam if p.host_ip}
    assert (8080, "192.168.1.50") in lan
    assert (80, "192.168.1.50") in lan
    assert not any(p.port == 5432 for p in ports)
    vpn = [p for p in ports if p.service_name == "vpn"]
    assert len(vpn) == 1
    assert vpn[0].port == 41641
    assert vpn[0].protocol == "udp"


def test_compose_macvlan_from_include_and_extends(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "networks.yml").write_text(
        "networks:\n  lan:\n    driver: ipvlan\n",
        encoding="utf-8",
    )
    (stack / "base.yml").write_text(
        "services:\n  cam:\n    expose:\n      - '80'\n",
        encoding="utf-8",
    )
    (stack / "compose.yml").write_text(
        "include:\n  - networks.yml\n"
        "services:\n"
        "  cam:\n"
        "    extends:\n"
        "      file: base.yml\n"
        "      service: cam\n"
        "    networks:\n"
        "      lan:\n        ipv4_address: 10.0.0.9\n",
        encoding="utf-8",
    )
    ports = scan_compose_files(str(stack))
    cam = [p for p in ports if p.service_name == "cam"]
    assert {(p.port, p.host_ip) for p in cam} == {(80, "10.0.0.9")}

    other = tmp_path / "other"
    other.mkdir()
    (other / "common.yml").write_text(
        "networks:\n  lan:\n    driver: macvlan\n"
        "services:\n"
        "  cam:\n    expose:\n      - '8080'\n"
        "    networks:\n      lan:\n        ipv4_address: 192.168.1.50\n",
        encoding="utf-8",
    )
    (other / "compose.yml").write_text(
        "services:\n  cam:\n    extends:\n      file: common.yml\n      service: cam\n",
        encoding="utf-8",
    )
    inherited = scan_compose_files(str(other))
    row = [p for p in inherited if p.port == 8080]
    assert any(p.host_ip == "192.168.1.50" for p in row)


def test_host_netns_mode_paths():
    assert is_host_netns_mode("host") is True
    assert is_host_netns_mode("ns:/proc/1/ns/net") is True
    assert is_host_netns_mode("ns:/host/proc/1/ns/net") is True
    assert is_host_netns_mode("ns:/var/run/docker/netns/abc") is False
    assert is_host_netns_mode("bridge") is False
    attrs = {
        "HostConfig": {"NetworkMode": "ns:/proc/1/ns/net", "PortBindings": {}},
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {"53/udp": {}}},
    }
    mapped = {(p.host_port, p.protocol) for p in extract_ports(attrs)}
    assert (53, "udp") in mapped


def test_untrusted_listen_skips_ss_and_proc(monkeypatch):
    from backend import port_scanner as ps
    monkeypatch.setattr(ps, "_scan_with_host_proc", lambda prefer_pids=None: None)
    monkeypatch.setattr(ps, "host_listen_trusted", lambda: False)
    monkeypatch.setattr(
        ps, "_scan_with_ss",
        lambda: [ListeningPort(port=80, protocol="tcp", ip="0.0.0.0")],
    )
    monkeypatch.setattr(
        ps, "_scan_with_proc",
        lambda prefer_pids=None: [ListeningPort(port=443, protocol="tcp", ip="0.0.0.0")],
    )
    assert ps.scan_listening_ports() == []


def test_scan_containers_marks_client_missing(monkeypatch):
    from backend import docker_scanner as ds
    monkeypatch.setattr(ds, "_docker_client", lambda: None)
    marked = []
    monkeypatch.setattr(ds, "_mark_available", lambda ok: marked.append(ok))
    assert ds.scan_containers() == []
    assert marked == [False]


def test_unquoted_sexagesimal_ports_stay_strings(tmp_path):
    app = tmp_path / "ssh"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n"
        "  openssh:\n"
        "    ports:\n"
        "      - 22:22\n"
        "      - 8080:22\n"
        "      - 25:25\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(tmp_path))}
    assert numbers == {22, 25, 8080}


def test_compose_multidoc_and_long_syntax_edges(tmp_path):
    app = tmp_path / "stack"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n  a:\n    ports:\n      - '8001:80'\n"
        "---\n"
        "services:\n  b:\n    ports:\n      - '8002:80'\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(tmp_path))} == {8001, 8002}
    assert parse_port_entry({"published": 8080, "host_ip": "[::1]", "target": 80})[0]["host_ip"] == "::1"
    assert parse_port_entry({"published": 8080}) == []
    assert parse_short_port("8080:80:80") == []
    assert parse_expose_entry(53.0)[0]["host_port"] == 53
    assert parse_expose_entry(53.5) == []


def test_include_project_directory_and_nested_env(tmp_path):
    project = tmp_path / "project"
    lib = tmp_path / "lib"
    app = tmp_path / "app"
    commons = tmp_path / "commons"
    for d in (project, lib, app, commons):
        d.mkdir()
    (project / "local.env").write_text("PORT=7002\n", encoding="utf-8")
    (lib / "local.env").write_text("PORT=9999\n", encoding="utf-8")
    (lib / "svc.yml").write_text(
        "env_file: local.env\nservices:\n  w:\n    ports:\n      - '${PORT}:80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - path: ../lib/svc.yml\n    project_directory: ../project\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(app))} == {7002}

    (commons / "stack.env").write_text("P=7103\n", encoding="utf-8")
    (commons / "ports.yml").write_text(
        "services:\n  nested:\n    ports:\n      - '${P}:80'\n",
        encoding="utf-8",
    )
    (commons / "stack.yml").write_text(
        "include:\n  - ports.yml\n",
        encoding="utf-8",
    )
    other = tmp_path / "other"
    other.mkdir()
    (other / "compose.yml").write_text(
        "include:\n  - path: ../commons/stack.yml\n    env_file: ../commons/stack.env\n",
        encoding="utf-8",
    )
    assert 7103 in {p.port for p in scan_compose_files(str(other))}

    (app / ".env").write_text("PORT=2222\n", encoding="utf-8")
    (commons / ".env").write_text("OTHER=9999\n", encoding="utf-8")
    (commons / "only.yml").write_text(
        "services:\n  x:\n    ports:\n      - '${PORT}:80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - path: ../commons/only.yml\n    project_directory: .\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(app))} == {2222}


def test_project_directory_reroots_nested_include_and_extends(tmp_path):
    project = tmp_path / "project"
    lib = tmp_path / "lib"
    app = tmp_path / "app"
    for d in (project, lib, app):
        d.mkdir()
    (lib / "ports.yml").write_text(
        "services:\n  decoy:\n    ports:\n      - '9991:80'\n",
        encoding="utf-8",
    )
    (lib / "base.yml").write_text(
        "services:\n  web:\n    ports:\n      - '9992:80'\n",
        encoding="utf-8",
    )
    (project / "ports.yml").write_text(
        "services:\n  nested:\n    ports:\n      - '8101:80'\n",
        encoding="utf-8",
    )
    (project / "base.yml").write_text(
        "services:\n  web:\n    ports:\n      - '8102:80'\n",
        encoding="utf-8",
    )
    (lib / "stack.yml").write_text(
        "include:\n  - ports.yml\n"
        "services:\n  ext:\n    extends:\n      file: base.yml\n      service: web\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - path: ../lib/stack.yml\n    project_directory: ../project\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(app))} == {8101, 8102}


def test_services_override_and_reset_tags(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '8080:80'\n"
        "  db:\n    ports:\n      - '5432:5432'\n",
        encoding="utf-8",
    )
    (app / "compose.override.yml").write_text(
        "services: !override\n  web:\n    ports:\n      - '9090:80'\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(app))} == {9090}

    one = tmp_path / "one"
    one.mkdir()
    (one / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '8080:80'\n      - '8081:81'\n",
        encoding="utf-8",
    )
    (one / "compose.override.yml").write_text(
        "services:\n  web: !override\n    ports:\n      - '9190:80'\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(one))} == {9190}

    tagged = tmp_path / "tagged"
    tagged.mkdir()
    (tagged / "compose.yml").write_text(
        "services: !override\n  web:\n    ports:\n      - '6060:80'\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(tagged))} == {6060}


def test_deploy_ports_overlay_and_extends(tmp_path):
    app = tmp_path / "swarm"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n  web:\n    deploy:\n      ports:\n        - published: 4001\n          target: 80\n",
        encoding="utf-8",
    )
    (app / "compose.override.yml").write_text(
        "services:\n  web:\n    deploy:\n      ports: !reset\n        - published: 5001\n          target: 80\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(app))} == {5001}

    child = tmp_path / "child"
    child.mkdir()
    (child / "compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n",
        encoding="utf-8",
    )
    (child / "compose.override.yml").write_text(
        "services:\n  web:\n    deploy:\n      ports:\n        - published: 6001\n          target: 80\n",
        encoding="utf-8",
    )
    assert 6001 in {p.port for p in scan_compose_files(str(child))}

    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "compose.yml").write_text(
        "services:\n  base:\n    deploy:\n      ports:\n        - published: 7001\n          target: 80\n"
        "  web:\n    extends: base\n    deploy:\n      ports: !override\n"
        "        - published: 7002\n          target: 80\n",
        encoding="utf-8",
    )
    ports = {p.port for p in scan_compose_files(str(ext)) if p.service_name == "web"}
    assert ports == {7002}


def test_env_comments_and_plus_interpolation(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'WEB_PORT=8080 # public\nPASS="a#b"\nHASH=\'c#d\'\n',
        encoding="utf-8",
    )
    env = _read_env_file(env_path)
    assert env["WEB_PORT"] == "8080"
    assert env["PASS"] == "a#b"
    assert env["HASH"] == "c#d"

    app = tmp_path / "app"
    app.mkdir()
    (app / ".env").write_text("WEB_PORT=8080 # public\n", encoding="utf-8")
    (app / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '${WEB_PORT}:80'\n",
        encoding="utf-8",
    )
    assert {p.port for p in scan_compose_files(str(app))} == {8080}

    assert substitute_vars("ports: '${HOST_PORT:+8080}:80'", {"HOST_PORT": "1"}) == "ports: '8080:80'"
    assert substitute_vars("ports: '${HOST_PORT:+8080}:80'", {}) == "ports: ':80'"
    assert substitute_vars("ports: '${WEB_PORT:-$FALLBACK}:80'", {"FALLBACK": "9090"}) == "ports: '9090:80'"
    assert substitute_vars("ports: '${OUTER:-${INNER}}:80'", {"INNER": "7070"}) == "ports: '7070:80'"
    assert substitute_vars("ports: '${FLAG+9090}:80'", {"FLAG": ""}) == "ports: '9090:80'"
    assert substitute_vars("ports: '${FLAG+9090}:80'", {}) == "ports: ':80'"


def test_compose_override_reset_replaces_base_ports(tmp_path):
    app = tmp_path / "media"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '8080:80'\n",
        encoding="utf-8",
    )
    (app / "compose.override.yml").write_text(
        "services:\n  web:\n    ports: !reset\n      - '9090:80'\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(tmp_path))}
    assert numbers == {9090}
    lone = tmp_path / "orphan"
    lone.mkdir()
    (lone / "compose.override.yml").write_text(
        "services:\n  web:\n    ports:\n      - '7070:80'\n",
        encoding="utf-8",
    )
    assert 7070 in {p.port for p in scan_compose_files(str(lone))}


def test_ss_service_name_and_caddy_wildcard():
    ssh = parse_ss_line("tcp LISTEN 0 128 0.0.0.0:ssh 0.0.0.0:*")
    assert ssh is not None
    assert ssh.port == 22
    assert extract_label_urls({"caddy": "*.home.arpa"}) == []
    assert extract_label_urls({"caddy": "https://*.home.arpa"}) == []
    mapped = extract_label_urls(
        {"net.unraid.docker.webui": "http://[IP]:[PORT:8096]/"},
        ports=[PortMapping(host_port=18096, host_ip="0.0.0.0", container_port=8096, protocol="tcp")],
    )
    assert "http://localhost:18096" in mapped


def test_stopped_ephemeral_keeps_last_host_port():
    running = {
        "Id": "deadbeefstopped1",
        "State": {"Status": "running"},
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}]},
        },
        "NetworkSettings": {
            "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}]},
        },
        "Config": {"ExposedPorts": {}},
    }
    assert extract_ports(running)[0].host_port == 32768
    stopped = {
        "Id": "deadbeefstopped1",
        "State": {"Status": "exited"},
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "0"}]},
        },
        "NetworkSettings": {"Ports": None},
        "Config": {"ExposedPorts": {}},
    }
    recalled = extract_ports(stopped)
    assert recalled[0].host_port == 32768


def test_host_netns_joiner_gets_expose():
    vpn = ContainerInfo(
        name="vpn", status="running", image="vpn",
        network_mode="host", container_id="abc123def456",
    )
    helper = ContainerInfo(
        name="helper", status="running", image="helper",
        network_mode="container:vpn", container_id="fff111aaa222",
        ports=[],
    )
    _attach_host_netns_sockets(
        [vpn, helper],
        {"fff111aaa222": {"8080/tcp": {}}},
    )
    assert any(p.host_port == 8080 and p.source == "expose" for p in helper.ports)


def test_included_compose_yml_is_not_its_own_stack(tmp_path):
    shared = tmp_path / "shared"
    wiki = tmp_path / "wiki"
    blog = tmp_path / "blog"
    shared.mkdir()
    wiki.mkdir()
    blog.mkdir()
    (shared / "compose.yml").write_text(
        "services:\n  db:\n    ports:\n      - '5432:5432'\n",
        encoding="utf-8",
    )
    (wiki / "compose.yml").write_text(
        "include:\n  - ../shared/compose.yml\n",
        encoding="utf-8",
    )
    (blog / "compose.yml").write_text(
        "include:\n  - ../shared/compose.yml\n",
        encoding="utf-8",
    )
    ports = [p for p in scan_compose_files(str(tmp_path)) if p.port == 5432]
    assert {p.project_dir for p in ports} == {"wiki", "blog"}


def test_external_macvlan_static_ip(tmp_path):
    app = tmp_path / "cam"
    app.mkdir()
    (app / "compose.yml").write_text(
        "networks:\n  lan:\n    external: true\n"
        "services:\n  cam:\n    networks:\n      lan:\n        ipv4_address: 192.168.1.50\n"
        "    expose:\n      - '80'\n",
        encoding="utf-8",
    )
    lan = {(p.port, p.host_ip) for p in scan_compose_files(str(tmp_path))}
    assert (80, "192.168.1.50") in lan


def test_unraid_unmapped_webui_port_is_dropped():
    assert extract_label_urls(
        {"net.unraid.docker.webui": "http://[IP]:[PORT:8096]/"},
        ports=[PortMapping(host_port=18096, host_ip="0.0.0.0", container_port=80, protocol="tcp")],
    ) == []


def test_joiner_prefix_match_requires_unique_id():
    host = ContainerInfo(
        name="vpn", status="running", image="vpn",
        network_mode="host", container_id="aaaaaaaaaaaa1111",
    )
    other = ContainerInfo(
        name="web", status="running", image="web",
        network_mode="bridge", container_id="aaaaaaabbbbb2222",
    )
    by_name = {"vpn": host, "web": other}
    by_id = {
        host.container_id: host, host.container_id[:12]: host,
        other.container_id: other, other.container_id[:12]: other,
    }
    assert _resolve_container_ref("aaaa", by_name, by_id) is None
    assert _resolve_container_ref("aaaaaaaaaaaa", by_name, by_id) is host
    assert _resolve_container_ref("aaaaaaaaaaaa11", by_name, by_id) is host
    assert _resolve_container_ref("aaaaaaabbbbb", by_name, by_id) is other
    assert _network_is_host_netns("container:aaaaaaaaaaaa11", by_name, by_id) is True
    assert _network_is_host_netns("container:aaaaaaabbbbb", by_name, by_id) is False


def test_sibling_include_macvlan_is_visible(tmp_path):
    app = tmp_path / "cam"
    app.mkdir()
    (app / "networks.yml").write_text(
        "networks:\n  lan:\n    driver: macvlan\n",
        encoding="utf-8",
    )
    (app / "cam.yml").write_text(
        "services:\n  cam:\n    networks:\n      lan:\n        ipv4_address: 192.168.1.50\n"
        "    expose:\n      - '80'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - networks.yml\n  - cam.yml\n",
        encoding="utf-8",
    )
    lan = {(p.port, p.host_ip) for p in scan_compose_files(str(tmp_path))}
    assert (80, "192.168.1.50") in lan


def test_include_path_list_overlays(tmp_path):
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    shared.mkdir()
    app.mkdir()
    (shared / "base.yml").write_text(
        "services:\n  db:\n    ports:\n      - '5432:5432'\n",
        encoding="utf-8",
    )
    (shared / "over.yml").write_text(
        "services:\n  db:\n    ports: !override\n      - '15432:5432'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - path:\n      - ../shared/base.yml\n      - ../shared/over.yml\n",
        encoding="utf-8",
    )
    ports = {p.port for p in scan_compose_files(str(tmp_path)) if p.port in (5432, 15432)}
    assert ports == {15432}


def test_include_skips_sibling_override(tmp_path):
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    shared.mkdir()
    app.mkdir()
    (shared / "compose.yml").write_text(
        "services:\n  db:\n    ports:\n      - '5432:5432'\n",
        encoding="utf-8",
    )
    (shared / "compose.override.yml").write_text(
        "services:\n  db:\n    ports:\n      - '15432:5432'\n",
        encoding="utf-8",
    )
    (app / "compose.yml").write_text(
        "include:\n  - ../shared/compose.yml\n",
        encoding="utf-8",
    )
    ports = {p.port for p in scan_compose_files(str(tmp_path)) if p.port in (5432, 15432)}
    assert ports == {5432}


def test_network_mode_reset_drops_host_expose(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "compose.yml").write_text(
        "services:\n  web:\n    network_mode: host\n    expose:\n      - '80'\n"
        "    ports:\n      - '8080:80'\n",
        encoding="utf-8",
    )
    (app / "compose.override.yml").write_text(
        "services:\n  web:\n    network_mode: !reset\n    ports:\n      - '8080:80'\n",
        encoding="utf-8",
    )
    numbers = {p.port for p in scan_compose_files(str(app))}
    assert 8080 in numbers
    assert 80 not in numbers


def test_empty_hostip_records_dual_stack():
    attrs = {
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "50000"}]},
        },
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {}},
    }
    ips = {p.host_ip for p in extract_ports(attrs) if p.host_port == 50000}
    assert ips == {"0.0.0.0", "::"}


def test_stopped_mixed_publish_keeps_ephemeral():
    running = {
        "Id": "deadbeefmixed1",
        "State": {"Status": "running"},
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {
                "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
                "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}],
            },
        },
        "NetworkSettings": {
            "Ports": {
                "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
                "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}],
            },
        },
        "Config": {"ExposedPorts": {}},
    }
    assert {p.host_port for p in extract_ports(running)} == {8080, 32768}
    stopped = {
        "Id": "deadbeefmixed1",
        "State": {"Status": "exited"},
        "HostConfig": {
            "NetworkMode": "bridge",
            "PortBindings": {
                "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
                "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "0"}],
            },
        },
        "NetworkSettings": {"Ports": None},
        "Config": {"ExposedPorts": {}},
    }
    assert {p.host_port for p in extract_ports(stopped)} == {8080, 32768}


def test_homepage_template_href_is_dropped():
    assert extract_label_urls({"homepage.href": "http://{{hostname}}:2283"}) == []
    assert extract_label_urls({"wud.href": "{{href}}"}) == []
    assert "http://photos.lan:2283" in extract_label_urls(
        {"homepage.href": "http://photos.lan:2283"},
    )


def test_traefik_label_port_follows_host_router():
    labels = {
        "traefik.http.services.metrics.loadbalancer.server.port": "8082",
        "traefik.http.services.wiki.loadbalancer.server.port": "80",
        "traefik.http.routers.wiki.rule": "Host(`wiki.lan`)",
    }
    assert _traefik_service_port(labels) == 80


def test_stopped_macvlan_uses_ipam_config(monkeypatch):
    monkeypatch.setattr(
        "backend.docker_scanner._network_driver",
        lambda client, nid: "macvlan",
    )
    extra = _macvlan_ports(None, {
        "NetworkSettings": {"Networks": {"lan": {
            "NetworkID": "abc",
            "IPAddress": "",
            "IPAMConfig": {"IPv4Address": "192.168.1.50", "IPv6Address": "fd12::10"},
        }}},
        "Config": {"ExposedPorts": {"80/tcp": {}}},
    }, [])
    ips = {row.host_ip for row in extra}
    assert ips == {"192.168.1.50", "fd12::10"}


def test_last_publish_evicts_oldest(monkeypatch):
    import backend.docker_scanner as ds
    monkeypatch.setattr(ds, "_LAST_PUBLISH", {})
    monkeypatch.setattr(ds, "_LAST_PUBLISH_MAX", 2)
    _remember_publish("aaaa", [PortMapping(host_port=1, host_ip="0.0.0.0", container_port=None, protocol="tcp")])
    _remember_publish("bbbb", [PortMapping(host_port=2, host_ip="0.0.0.0", container_port=None, protocol="tcp")])
    _remember_publish("aaaa", [PortMapping(host_port=11, host_ip="0.0.0.0", container_port=None, protocol="tcp")])
    _remember_publish("cccc", [PortMapping(host_port=3, host_ip="0.0.0.0", container_port=None, protocol="tcp")])
    assert _recall_publish("bbbb") == []
    assert _recall_publish("aaaa")[0].host_port == 11
    assert _recall_publish("cccc")[0].host_port == 3


def test_extract_port_labels_parses_and_ignores_junk():
    from backend.docker_scanner import extract_port_labels

    labels = {
        "port-light.port.8080.name": "My App UI",
        "port-light.port.8080.category": "web",
        "port-light.port.53/udp.name": "bad key",
        "port-light.port.70000.name": "out of range",
        "port-light.port.9000.name": "   ",
        "com.docker.compose.project": "wiki",
    }
    out = extract_port_labels(labels)
    assert out == {8080: {"name": "My App UI", "category": "web"}}


def test_container_label_names_feed_known_service():
    from backend.classification import classify as _classify

    host = ContainerInfo(
        name="app", status="running", image="app", network_mode="bridge",
        ports=[PortMapping(host_port=8090, host_ip="0.0.0.0", container_port=80, protocol="tcp")],
        port_labels={80: {"name": "App UI", "category": "web"}},
    )
    out = _classify([], [host], [], [], hidden_ports=[],
                    range_start=1, range_end=65535,
                    include_hidden=False, hidden_locked=False)
    row = next(p for p in out["ports"] if p["port"] == 8090)
    assert row["known_service"]["name"] == "App UI"
    assert row["known_service"]["from_label"] is True
    # label keyed by host port also matches
    host2 = ContainerInfo(
        name="app2", status="running", image="app2", network_mode="bridge",
        ports=[PortMapping(host_port=8091, host_ip="0.0.0.0", container_port=80, protocol="tcp")],
        port_labels={8091: {"name": "Host Key"}},
    )
    out2 = _classify([], [host2], [], [], hidden_ports=[],
                     range_start=1, range_end=65535,
                     include_hidden=False, hidden_locked=False)
    row2 = next(p for p in out2["ports"] if p["port"] == 8091)
    assert row2["known_service"]["name"] == "Host Key"
