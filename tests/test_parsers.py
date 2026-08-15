from __future__ import annotations

from backend.compose_scanner import (
    expand_port_range,
    parse_port_entry,
    parse_short_port,
    scan_compose_files,
    substitute_vars,
)
from backend.docker_scanner import extract_label_urls, extract_ports, safe_http_url
from backend.port_scanner import normalize_ip, parse_proc_net_line, parse_ss_line


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


def test_normalize_ip():
    assert normalize_ip("::ffff:127.0.0.1") == "127.0.0.1"
    assert normalize_ip("*") == "0.0.0.0"


def test_ss_ipv6_and_star():
    line = "tcp   LISTEN 0  4096  [::]:443  [::]:*"
    lp = parse_ss_line(line)
    assert lp is not None
    assert lp.port == 443
    assert lp.ip == "::"
    star = parse_ss_line("tcp LISTEN 0 128 *:22 *:*")
    assert star is not None
    assert star.port == 22
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


def test_label_urls_reject_javascript():
    assert safe_http_url("javascript:alert(1)") is None
    assert safe_http_url("/relative") is None
    assert safe_http_url("http://photos.lan:2283") == "http://photos.lan:2283"
    assert extract_label_urls({"homepage.href": "javascript:alert(1)"}) == []
