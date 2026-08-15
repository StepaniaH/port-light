from __future__ import annotations

import os

from backend.compose_scanner import (
    expand_port_range,
    parse_expose_entry,
    parse_port_entry,
    parse_short_port,
    scan_compose_files,
    substitute_vars,
)
from backend.docker_scanner import extract_label_urls, extract_ports, safe_http_url
from backend.port_scanner import (
    ListeningPort,
    _fill_process_names,
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
    assert {p.project_dir for p in db} == {"wiki"}
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
    assert {p.project_dir for p in web} == {"web"}


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


def test_env_empty_uses_colon_default_only(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "")
    colon = substitute_vars("ports: '${WEB_PORT:-8080}:80'", {})
    assert "8080:80" in colon
    hyphen = substitute_vars("ports: '${WEB_PORT-9090}:80'", {})
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


def test_ss_ipv6_and_star():
    line = "tcp   LISTEN 0  4096  [::]:443  [::]:*"
    lp = parse_ss_line(line)
    assert lp is not None
    assert lp.port == 443
    assert lp.ip == "::"
    star = parse_ss_line("tcp LISTEN 0 128 *:22 *:*")
    assert star is not None
    assert star.port == 22
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
    assert ports[0]["host_port"] == 8096
    assert ports[0]["host_ip"] == "127.0.0.1"
    urls = extract_label_urls({
        "homepage.href": "http://photos.lan:2283",
        "traefik.http.routers.x.rule": "HostRegexp(`*.home.arpa`)",
    })
    assert "http://photos.lan:2283" in urls
    assert all("*" not in u for u in urls)


def test_env_var_without_default(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "9090")
    out = substitute_vars("ports: '${WEB_PORT}:80'", {})
    assert "9090:80" in out
    missing = substitute_vars("ports: '${NO_SUCH_PORT}:80'", {})
    assert "${NO_SUCH_PORT}:80" in missing


def test_long_syntax_and_ipv4_host():
    assert parse_short_port("127.0.0.1:8080:80")[0]["host_port"] == 8080
    assert parse_short_port("127.0.0.1:8080:80")[0]["host_ip"] == "127.0.0.1"
    v6 = parse_short_port("[::1]:8080:80")[0]
    assert v6["host_port"] == 8080
    assert v6["host_ip"] == "::1"
    long_ip = parse_port_entry({"published": 9000, "target": 80, "host_ip": "10.0.0.5"})
    assert long_ip[0]["host_ip"] == "10.0.0.5"
    slash = parse_port_entry({"published": "5353/udp", "target": 53})
    assert slash[0]["host_port"] == 5353
    assert slash[0]["protocol"] == "udp"
    assert parse_port_entry("not-a-port") == []
    assert parse_port_entry({"target": 80}) == []
    attrs = {
        "HostConfig": {
            "NetworkMode": "host",
            "PortBindings": {},
        },
        "NetworkSettings": {"Ports": {}},
        "Config": {"ExposedPorts": {"53/udp": {}, "80/tcp": {}}},
    }
    ports = extract_ports(attrs)
    mapped = {(p["host_port"], p["protocol"]) for p in ports}
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
    assert extract_label_urls({"homepage.href": "javascript:alert(1)"}) == []
