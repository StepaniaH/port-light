from __future__ import annotations

import json

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
    ],
)
def test_origin_url_rejects_ssrf_and_public_ips(url):
    with pytest.raises(hosts.HostsError):
        hosts.origin_url(url)


def test_hosts_crud_hides_password(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, PORT_LIGHT_HOST_NAME="Studio")
    listed = client.get("/api/hosts").json()
    assert listed["local"]["id"] == "local"
    assert listed["local"]["name"] == "Studio"
    assert listed["peers"] == []
    assert listed["readonly"] is False

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


def test_put_rejects_too_many_and_bad_urls(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    too_many = [{"name": f"m{i}", "url": f"http://10.0.0.{i}:2100"} for i in range(1, 8)]
    assert client.put("/api/hosts", json={"peers": too_many}).status_code == 400
    assert client.put("/api/hosts", json={"peers": [{"name": "x", "url": "http://8.8.8.8:2100"}]}).status_code == 400
    assert client.put("/api/hosts", json={"peers": [{"name": "x", "url": "file:///tmp"}]}).status_code == 400
    assert client.put("/api/hosts", json={"peers": "nas"}).status_code == 400


def test_local_host_ports_alias(tmp_path, monkeypatch):
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
