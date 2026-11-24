from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend import hosts, port_store
from backend.main import app


def _client(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT_LIGHT_SETTINGS_SOURCE", raising=False)
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    monkeypatch.delenv("PORT_LIGHT_PEERS", raising=False)
    monkeypatch.delenv("PORT_LIGHT_HOST_NAME", raising=False)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return TestClient(app)


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("http://10.0.0.2:2100/api/ports", "http://10.0.0.2:2100"),
        ("http://192.168.1.8:2100/", "http://192.168.1.8:2100"),
        ("http://100.64.1.2:2100", "http://100.64.1.2:2100"),
        ("https://nas.lan:2100/foo", "https://nas.lan:2100"),
        ("http://127.0.0.1:2101", "http://127.0.0.1:2101"),
        ("http://[fd7a:115c:a1e0::1]:2100", "http://[fd7a:115c:a1e0::1]:2100"),
        ("http://[fe80::1%eth0]:2100/api/ports", "http://[fe80::1%eth0]:2100"),
        ("http://10.0.0.2", "http://10.0.0.2"),
    ],
)
def test_origin_url_strips_to_scheme_host_port(url, origin):
    assert hosts.origin_url(url) == origin


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://10.0.0.2:2100",
        "http://169.254.169.254/",
        "http://8.8.8.8:2100",
        "http://1.1.1.1",
        "http://metadata.google.internal/",
        "http://user:pass@10.0.0.2:2100",
        "not-a-url",
        "http://",
        "http://[::ffff:169.254.169.254]/",
        "http://[::ffff:169.254.169.254%eth0]/",
        "http://[2606:4700:4700::1111%eth0]:2100",
    ],
)
def test_origin_url_rejects_ssrf_and_public_ips(url):
    with pytest.raises(hosts.HostsError):
        hosts.origin_url(url)


@pytest.mark.parametrize("addresses", [
    ["8.8.8.8"], ["10.0.0.2", "8.8.8.8"], ["169.254.169.254"],
    ["::ffff:169.254.169.254"], [],
])
def test_peer_dns_rejects_any_disallowed_answer(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 2100)) for address in addresses
    ])

    def unexpected_open(*args):
        pytest.fail("disallowed address reached the HTTP client")

    monkeypatch.setattr(hosts.urllib.request, "build_opener", unexpected_open)
    assert hosts.fetch_peer_json({"url": "http://nas.lan:2100"}, "/api/ports", {}) == (502, None, None)


def test_peer_dns_failure_returns_unreachable(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("DNS unavailable")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert hosts.fetch_peer_json({"url": "http://nas.lan:2100"}, "/api/ports", {}) == (502, None, None)


def test_peer_dns_allows_private_addresses_and_keeps_hostname(monkeypatch):
    import io

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 2100)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::2", 2100, 0, 0)),
    ])

    class Opener:
        def open(self, req, timeout):
            assert req.full_url == "http://nas.lan:2100/api/ports"
            response = io.BytesIO(b'{"ports": []}')
            response.headers = {}
            return response

    monkeypatch.setattr(hosts.urllib.request, "build_opener", lambda *args: Opener())
    assert hosts.fetch_peer_json({"url": "http://nas.lan:2100"}, "/api/ports", {}) == (200, {"ports": []}, None)


def test_hosts_crud_hides_password(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, PORT_LIGHT_HOST_NAME="Studio")
    listed = client.get("/api/hosts").json()
    assert listed["local"]["id"] == "local"
    assert listed["local"]["name"] == "Studio"
    assert listed["peers"] == []
    assert listed["readonly"] is False
    assert listed["max_peers"] == hosts.MAX_PEERS

    created = client.put("/api/hosts", json={
        "peers": [{
            "name": "NAS",
            "url": "http://10.0.0.2:2100/ignored",
            "username": "admin",
            "password": "s3cret",
        }],
    })
    assert created.status_code == 200
    peer = created.json()["peers"][0]
    assert peer["name"] == "NAS"
    assert peer["url"] == "http://10.0.0.2:2100"
    assert peer["has_auth"] is True
    assert peer["username"] == "admin"
    assert "password" not in peer
    host_id = peer["id"]

    listed = client.get("/api/hosts").json()
    assert listed["peers"][0]["username"] == "admin"
    assert "password" not in listed["peers"][0]

    again = client.put("/api/hosts", json={
        "peers": [{"id": host_id, "name": "NAS", "url": "http://10.0.0.2:2100", "username": "admin"}],
    })
    assert again.status_code == 200
    assert again.json()["peers"][0]["has_auth"] is True
    assert again.json()["peers"][0]["username"] == "admin"

    stored = json.loads((tmp_path / "port_light.json").read_text(encoding="utf-8"))
    assert stored["peers"][0]["password"] == "s3cret"

    cleared = client.put("/api/hosts", json={
        "peers": [{"id": host_id, "name": "NAS", "url": "http://10.0.0.2:2100", "username": "", "password": ""}],
    })
    assert cleared.json()["peers"][0]["has_auth"] is False


def test_hosts_readonly_follows_settings_source(tmp_path, monkeypatch):
    client = _client(
        tmp_path,
        monkeypatch,
        PORT_LIGHT_SETTINGS_SOURCE="env",
        PORT_LIGHT_PEERS='[{"name":"Box","url":"http://192.168.1.9:2100"}]',
    )
    got = client.get("/api/hosts").json()
    assert got["readonly"] is True
    assert got["peers"][0]["name"] == "Box"
    locked = client.put("/api/hosts", json={"peers": []})
    assert locked.status_code == 403


def test_saved_empty_peers_does_not_fall_through_to_env(tmp_path, monkeypatch):
    client = _client(
        tmp_path,
        monkeypatch,
        PORT_LIGHT_PEERS='[{"name":"Box","url":"http://192.168.1.9:2100"}]',
    )
    client.put("/api/hosts", json={"peers": []})
    assert client.get("/api/hosts").json()["peers"] == []


def test_peer_description_roundtrip_preserves_old_clients_and_supports_clearing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    row = {"name": "NAS", "url": "http://10.0.0.2:2100"}
    created = client.put("/api/hosts", json={"peers": [row]}).json()["peers"][0]
    assert created["description"] == ""
    row["id"] = created["id"]
    description = "机" * hosts.MAX_DESCRIPTION
    saved = client.put("/api/hosts", json={"peers": [{**row, "description": " " + description + " "}]})
    assert saved.status_code == 200
    assert saved.json()["peers"][0]["description"] == description
    assert port_store.get_peers()[0]["description"] == description
    assert client.put("/api/hosts", json={"peers": [row]}).json()["peers"][0]["description"] == description
    rejected = client.put("/api/hosts", json={"peers": [{**row, "description": description + "x"}]})
    assert rejected.status_code == 400
    cleared = client.put("/api/hosts", json={"peers": [{**row, "description": ""}]})
    assert cleared.json()["peers"][0]["description"] == ""


def test_peer_description_can_be_provided_by_environment(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, PORT_LIGHT_SETTINGS_SOURCE="env",
                     PORT_LIGHT_PEERS='[{"name":"NAS","url":"http://10.0.0.2:2100","description":"Tailscale 100.64.0.12"}]')
    assert client.get("/api/hosts").json()["peers"][0]["description"] == "Tailscale 100.64.0.12"


def test_overlong_stored_peer_description_is_rejected_without_overwriting_data(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    path = tmp_path / "port_light.json"
    raw = json.dumps({"peers": [{
        "id": "peer0001", "name": "NAS", "url": "http://10.0.0.2:2100",
        "description": "x" * (hosts.MAX_DESCRIPTION + 1),
    }]})
    path.write_text(raw, encoding="utf-8")
    assert client.get("/api/hosts").status_code == 503
    assert path.read_text(encoding="utf-8") == raw


def test_put_rejects_too_many_and_bad_urls(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    allowed = [{"name": f"m{i}", "url": f"http://10.0.0.{i}:2100"}
               for i in range(1, hosts.MAX_PEERS + 1)]
    accepted = client.put("/api/hosts", json={"peers": allowed})
    assert accepted.status_code == 200
    assert accepted.json()["max_peers"] == hosts.MAX_PEERS
    assert len(accepted.json()["peers"]) == hosts.MAX_PEERS
    too_many = allowed + [{"name": "overflow", "url": "http://10.0.1.1:2100"}]
    assert client.put("/api/hosts", json={"peers": too_many}).status_code == 400
    assert client.put("/api/hosts", json={"peers": [{"name": "x", "url": "http://8.8.8.8:2100"}]}).status_code == 400
    assert client.put("/api/hosts", json={"peers": [{"name": "x", "url": "file:///tmp"}]}).status_code == 400
    assert client.put("/api/hosts", json={"peers": "nas"}).status_code == 400


def test_peer_batch_fetch_has_bounded_concurrency(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_fetch(peer, path, query, if_none_match=None):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return 200, {"peer": peer["name"]}, None

    monkeypatch.setattr(hosts, "fetch_peer_json", fake_fetch)
    peers = [{"name": f"peer-{i}"} for i in range(hosts.PEER_FETCH_CONCURRENCY * 3)]
    results = asyncio.run(hosts.fetch_peers_json(peers, "/api/ports", {}))
    assert len(results) == len(peers)
    assert peak == hosts.PEER_FETCH_CONCURRENCY


def test_local_host_ports_alias(tmp_path, monkeypatch, empty_scan):
    client = _client(tmp_path, monkeypatch)
    a = client.get("/api/ports")
    b = client.get("/api/hosts/local/ports")
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["summary"] == b.json()["summary"]
    row = client.get("/api/hosts/local/ports/2100")
    assert row.status_code == 200
    assert client.get("/api/hosts/nope/ports").status_code == 404


def test_peer_proxy_uses_mock_and_maps_errors(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    saved = client.put("/api/hosts", json={"peers": [{"name": "NAS", "url": "http://10.0.0.2:2100"}]})
    peer_id = saved.json()["peers"][0]["id"]

    def fake_fetch(peer, path, query, if_none_match=None):
        if path == "/api/health":
            return 200, {"status": "ok", "scanners": {"docker": True}}, None
        if path.startswith("/api/ports/"):
            return 404, None, None
        if if_none_match == '"abc"':
            return 304, None, '"abc"'
        return 200, {"ports": [], "summary": {"used": 0, "configured": 0, "free": 9}}, '"abc"'

    monkeypatch.setattr(hosts, "fetch_peer_json", fake_fetch)
    listed = client.get(f"/api/hosts/{peer_id}/ports", params={"range_start": 1, "range_end": 99})
    assert listed.status_code == 200
    assert listed.json()["summary"]["free"] == 9
    assert listed.headers.get("etag") == '"abc"'
    not_modified = client.get(
        f"/api/hosts/{peer_id}/ports",
        headers={"If-None-Match": '"abc"'},
    )
    assert not_modified.status_code == 304
    health = client.get(f"/api/hosts/{peer_id}/health")
    assert health.json()["scanners"]["docker"] is True
    missing = client.get(f"/api/hosts/{peer_id}/ports/4242")
    assert missing.status_code == 404

    def fake_auth(peer, path, query, if_none_match=None):
        return 401, None, None

    monkeypatch.setattr(hosts, "fetch_peer_json", fake_auth)
    denied = client.get(f"/api/hosts/{peer_id}/ports")
    assert denied.status_code == 502
    assert "auth" in denied.json()["detail"]

    def fake_down(peer, path, query, if_none_match=None):
        return 502, None, None

    monkeypatch.setattr(hosts, "fetch_peer_json", fake_down)
    assert client.get(f"/api/hosts/{peer_id}/ports").status_code == 502


def test_fetch_peer_json_treats_redirects_as_failure(monkeypatch):
    class FakeResp:
        status = 302
        headers = {"Location": "http://169.254.169.254/"}

        def read(self, n):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeOpener:
        def open(self, req, timeout=None):
            return FakeResp()

    monkeypatch.setattr(hosts.urllib.request, "build_opener", lambda *a, **k: FakeOpener())
    status, data, etag = hosts.fetch_peer_json(
        {"url": "http://10.0.0.2:2100", "username": "", "password": ""},
        "/api/ports",
        {},
    )
    assert status == 502
    assert data is None
    assert etag is None
    status, _, _ = hosts.fetch_peer_json(
        {"url": "http://10.0.0.2:2100"},
        "/etc/passwd",
        {},
    )
    assert status == 502


def test_hosts_put_unwritable_data_dir(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def boom(_data):
        raise port_store.StoreWriteError(
            "cannot write /data/port_light.json (permission denied). "
            "The data directory must be writable by this process."
        )

    monkeypatch.setattr(port_store, "_save", boom)
    res = client.put("/api/hosts", json={
        "peers": [{"name": "NAS", "url": "http://10.0.0.2:2100"}],
    })
    assert res.status_code == 500
    detail = res.json()["detail"].lower()
    assert "permission denied" in detail
    assert "writable" in detail


def test_peer_connection_pins_validated_addresses_and_preserves_host_header(monkeypatch):
    attempted: list[tuple] = []
    request_wire = bytearray()
    client_sock, server_sock = socket.socketpair()

    def serve():
        try:
            while b"\r\n\r\n" not in request_wire:
                chunk = server_sock.recv(4096)
                if not chunk:
                    break
                request_wire.extend(chunk)
            body = b'{"ports": []}'
            server_sock.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
        finally:
            server_sock.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    class PinnedSocket:
        def __init__(self):
            self._connected = False

        def settimeout(self, _timeout):
            pass

        def setsockopt(self, *_args):
            pass

        def connect(self, sockaddr):
            attempted.append(sockaddr)
            if sockaddr[0] == "192.0.2.10":
                raise ConnectionRefusedError("primary unreachable")
            self._connected = True

        def sendall(self, data):
            client_sock.sendall(data)

        def makefile(self, *args, **kwargs):
            return client_sock.makefile(*args, **kwargs)

        def close(self):
            if self._connected:
                client_sock.close()

    lookups = 0

    def fake_getaddrinfo(host, resolved_port, *_args, **_kwargs):
        nonlocal lookups
        lookups += 1
        if lookups > 1:
            raise AssertionError("HTTP connection performed a second DNS lookup")
        assert host == "peer.example.invalid"
        assert resolved_port == 2100
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 2100)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 2100)),
        ]

    try:
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: PinnedSocket())
        status, data, _ = hosts.fetch_peer_json(
            {"url": "http://peer.example.invalid:2100"}, "/api/ports", {},
        )
    finally:
        client_sock.close()
        thread.join(timeout=1)

    assert lookups == 1
    assert attempted == [("192.0.2.10", 2100), ("10.0.0.2", 2100)]
    assert status == 200
    assert data == {"ports": []}
    assert b"Host: peer.example.invalid:2100\r\n" in bytes(request_wire)


def test_peer_ipv6_link_local_zone_id_preserves_scope_in_sockaddr(monkeypatch):
    connected: list[tuple] = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def setsockopt(self, *_args):
            pass

        def connect(self, sockaddr):
            connected.append(sockaddr)
            raise ConnectionRefusedError("offline fixture")

        def close(self):
            pass

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 2100, 0, 7)),
    ])
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: FakeSocket())
    status, data, _ = hosts.fetch_peer_json(
        {"url": "http://[fe80::1%eth0]:2100"}, "/api/ports", {},
    )
    assert status == 502
    assert data is None
    assert connected == [("fe80::1%eth0", 2100, 0, 7)]


def test_peer_proxy_gates_hidden_ports_history_and_health_scopes(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, HIDDEN_UNLOCK_PASSWORD="unlock-secret")
    saved = client.put("/api/hosts", json={"peers": [{"name": "Peer", "url": "http://10.0.0.2:2100"}]})
    peer_id = saved.json()["peers"][0]["id"]
    seen_queries: list[tuple[str, dict]] = []

    def fake_fetch(peer, path, query, if_none_match=None):
        seen_queries.append((path, dict(query)))
        if path == "/api/health":
            return 200, {
                "status": "degraded",
                "degradations": [{"source": "compose", "scope": "secret/compose.yml", "reason": "invalid compose file"}],
            }, '"health-etag"'
        if path == "/api/ports":
            return 200, {
                "ports": [
                    {"port": 8080, "status": "used", "is_hidden": False},
                    {"port": 8096, "status": "used", "is_hidden": True},
                ],
                "summary": {
                    "used": 2,
                    "configured": 0,
                    "free": 100,
                    "hidden": 1,
                    "hidden_locked": False,
                    "hidden_ports": [8096],
                    "hidden_occupancy": [{"port": 8096, "status": "used"}],
                    "compose_diagnostics": [{"code": "port_range", "file": "compose.yaml"}],
                },
            }, '"ports-etag"'
        if path == "/api/ports/8096":
            if query.get("include_hidden") == "true":
                return 200, {"port": 8096, "status": "used", "is_hidden": True}, None
            return 404, None, None
        if path == "/api/ports/8080":
            return 200, {"port": 8080, "status": "used", "is_hidden": False}, None
        if path == "/api/ports/8096/history":
            return 200, {"port": 8096, "events": [{"state": "used"}]}, None
        if path == "/api/ports/8080/history":
            return 200, {"port": 8080, "events": [{"state": "used"}]}, None
        return 404, None, None

    monkeypatch.setattr(hosts, "fetch_peer_json", fake_fetch)

    locked = client.get(f"/api/hosts/{peer_id}/ports", params={"include_hidden": True})
    assert locked.status_code == 200
    assert seen_queries[-1] == ("/api/ports", {"include_hidden": "false"})
    assert [row["port"] for row in locked.json()["ports"]] == [8080]
    assert locked.json()["summary"]["hidden_locked"] is True
    assert locked.json()["summary"]["hidden_ports"] == []
    assert locked.json()["summary"]["hidden_occupancy"] == []
    assert "compose_diagnostics" not in locked.json()["summary"]

    unlocked = client.get(
        f"/api/hosts/{peer_id}/ports",
        params={"include_hidden": True},
        headers={"X-Hidden-Unlock": "unlock-secret"},
    )
    assert unlocked.status_code == 200
    assert seen_queries[-1] == ("/api/ports", {"include_hidden": "true"})
    assert [row["port"] for row in unlocked.json()["ports"]] == [8080, 8096]
    assert unlocked.json()["summary"]["hidden_ports"] == [8096]

    assert client.get(f"/api/hosts/{peer_id}/ports/8096", params={"include_hidden": True}).status_code == 404
    assert client.get(
        f"/api/hosts/{peer_id}/ports/8096",
        params={"include_hidden": True},
        headers={"X-Hidden-Unlock": "unlock-secret"},
    ).status_code == 200

    assert client.get(f"/api/hosts/{peer_id}/ports/8096/history").status_code == 404
    assert client.get(f"/api/hosts/{peer_id}/ports/8080/history").status_code == 200
    assert client.get(
        f"/api/hosts/{peer_id}/ports/8096/history",
        headers={"X-Hidden-Unlock": "unlock-secret"},
    ).status_code == 200

    anon_health = client.get(f"/api/hosts/{peer_id}/health").json()
    assert "scope" not in anon_health["degradations"][0]
    auth_health = client.get(
        f"/api/hosts/{peer_id}/health",
        headers={"X-Hidden-Unlock": "unlock-secret"},
    ).json()
    assert auth_health["degradations"][0]["scope"] == "secret/compose.yml"
