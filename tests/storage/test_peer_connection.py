import pytest

from backend import hosts
from tests.storage.test_hosts import _client


def test_peer_probe_uses_draft_without_saving_or_disclosing_credentials(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    calls = []

    def fetch(peer, path, query):
        calls.append((peer, path, query))
        return 200, {"version": "0.8.4", "capabilities": {"port_observation": 1}}, None

    monkeypatch.setattr(hosts, "fetch_peer_json", fetch)
    response = client.post(
        "/api/hosts/test",
        json={
            "url": "http://127.0.0.1:2101/api/ports",
            "username": "fixture-user",
            "password": "fixture-password",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "connected", "version": "0.8.4"}
    assert "fixture-password" not in response.text
    assert client.get("/api/hosts").json()["peers"] == []
    assert calls == [
        (
            {
                "url": "http://127.0.0.1:2101",
                "username": "fixture-user",
                "password": "fixture-password",
            },
            "/api/meta",
            {},
        )
    ]


@pytest.mark.parametrize(
    "status,data,code",
    [
        (401, None, "peer_auth"),
        (403, None, "peer_auth"),
        (504, None, "peer_timeout"),
        (502, None, "peer_unreachable"),
        (200, {"version": "0.8.4"}, "peer_incompatible"),
        (
            200,
            {"version": "not-port-light", "capabilities": {"port_observation": 1}},
            "peer_incompatible",
        ),
    ],
)
def test_peer_probe_returns_actionable_failures(
    tmp_path, monkeypatch, status, data, code
):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(hosts, "fetch_peer_json", lambda *args: (status, data, None))
    response = client.post("/api/hosts/test", json={"url": "http://127.0.0.1:2101"})
    assert response.status_code == 502
    assert response.json() == {"status": "failed", "code": code}


def test_peer_probe_reuses_saved_password_only_for_the_same_address(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    peer = client.put(
        "/api/hosts",
        json={
            "peers": [
                {
                    "name": "Fixture",
                    "url": "http://127.0.0.1:2101",
                    "username": "user",
                    "password": "fixture-password",
                }
            ]
        },
    ).json()["peers"][0]
    calls = []

    def fetch(connection, *args):
        calls.append(connection)
        return 200, {"version": "0.8.4", "capabilities": {"port_observation": 1}}, None

    monkeypatch.setattr(hosts, "fetch_peer_json", fetch)
    values = {"id": peer["id"], "url": peer["url"]}
    assert client.post("/api/hosts/test", json=values).status_code == 200
    assert calls[-1]["password"] == "fixture-password"
    values["url"] = "http://127.0.0.1:2102"
    rejected = client.post("/api/hosts/test", json=values)
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "credentials_required"
    assert len(calls) == 1
    assert (
        client.put(
            "/api/hosts", json={"peers": [{**values, "name": "Fixture"}]}
        ).status_code
        == 400
    )
    assert client.get("/api/hosts").json()["peers"][0]["url"] == peer["url"]
    assert (
        client.post("/api/hosts/test", json={**values, "clear_auth": True}).status_code
        == 200
    )
    assert calls[-1]["password"] == ""


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://169.254.169.254"},
        {"url": "http://8.8.8.8"},
        {"url": "file:///tmp"},
        {"url": "http://127.0.0.1:2101", "password": "x" * 257},
        {"url": "http://127.0.0.1:2101", "unexpected": True},
    ],
)
def test_peer_probe_rejects_invalid_forms_before_connecting(
    tmp_path, monkeypatch, body
):
    client = _client(tmp_path, monkeypatch)

    def fetch(*args):
        pytest.fail("invalid form reached peer transport")

    monkeypatch.setattr(hosts, "fetch_peer_json", fetch)
    response = client.post("/api/hosts/test", json=body)
    assert response.status_code == 422
    assert "password" not in response.text


@pytest.mark.parametrize(
    "env",
    [
        {"PORT_LIGHT_SETTINGS_SOURCE": "env"},
        {"SETTINGS_READONLY": "true"},
    ],
)
def test_peer_probe_rejected_when_hosts_are_readonly(tmp_path, monkeypatch, env):
    client = _client(tmp_path, monkeypatch, **env)

    def unexpected_fetch(*args):
        pytest.fail("read-only instance must not probe network targets")

    monkeypatch.setattr(hosts, "fetch_peer_json", unexpected_fetch)
    response = client.post("/api/hosts/test", json={"url": "http://192.0.2.10:2101"})
    assert response.status_code == 403
    assert response.json() == {"detail": "peer configuration is read-only"}


def test_peer_payload_normalizers_preserve_valid_and_reject_xss_fields():
    valid_ports = {
        "ports": [
            {
                "port": 8080,
                "status": "used",
                "source_type": "docker",
                "protocol": "tcp",
                "ip": "0.0.0.0",
                "ips": ["0.0.0.0"],
                "bind_scope": "public",
                "process": "docker-proxy",
                "pid": 42,
                "containers": [
                    {
                        "name": "web",
                        "status": "running",
                        "image": "nginx",
                        "container_port": 80,
                        "bind_ips": ["0.0.0.0"],
                    }
                ],
                "compose_configs": [
                    {
                        "project_dir": "web",
                        "project_name": "web",
                        "service_name": "web",
                        "compose_file": "web/compose.yml",
                        "container_port": 80,
                    }
                ],
                "rule_violations": [
                    {"rule": "apps", "project": "web", "service": "web", "start": 9000, "end": 9099}
                ],
                "is_hidden": False,
                "is_reservation": False,
                "conflict": False,
                "urls": ["http://localhost:8080"],
                "extra_untrusted_key": "<script>alert(1)</script>",
            }
        ],
        "summary": {
            "used": 1,
            "configured": 0,
            "free": None,
            "hidden": 0,
            "hidden_locked": False,
            "scan_complete": False,
            "sources": {"listen": "ok", "docker": "ok", "compose": "failed"},
        },
        "extra_root": {"evil": True},
    }
    normalized = hosts.normalize_peer_ports_response(valid_ports)
    assert normalized["ports"][0]["port"] == 8080
    assert normalized["ports"][0]["pid"] == 42
    assert "extra_untrusted_key" not in normalized["ports"][0]
    assert "extra_root" not in normalized
    assert normalized["summary"]["free"] is None

    single = hosts.normalize_peer_port_response(valid_ports["ports"][0], expected_port=8080)
    assert single["containers"][0]["container_port"] == 80
    assert single["compose_configs"][0]["container_port"] == 80

    health_in = {
        "status": "degraded",
        "version": "0.8.5",
        "auth_required": False,
        "scanners": {"proc": True, "listen_source": "ss", "docker": True, "compose": False},
        "occupancy": {
            "ready": False,
            "initialized": True,
            "stale": False,
            "scan_age_seconds": 1.2,
            "compose_files_scanned": 3,
            "sources": {"listen": "ok", "docker": "ok", "compose": "failed"},
        },
        "degradations": [
            {"source": "compose", "scope": f"s{i}", "reason": "bad", "ts": i}
            for i in range(30)
        ],
        "unexpected": "dropped",
    }
    norm_health = hosts.normalize_peer_health_response(health_in)
    assert "unexpected" not in norm_health
    assert len(norm_health["degradations"]) == 20
    assert norm_health["degradations"][0]["ts"] == 10

    with pytest.raises(hosts.HostsError):
        hosts.normalize_peer_port_response({"port": True, "status": "used"}, expected_port=1)
    with pytest.raises(hosts.HostsError):
        hosts.normalize_peer_health_response({"status": "ok", "occupancy": {"scan_age_seconds": float("nan")}})
