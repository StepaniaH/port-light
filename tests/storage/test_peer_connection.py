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


def test_saved_peer_can_be_tested_in_readonly_settings(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "env")
    monkeypatch.setattr(
        hosts,
        "fetch_peer_json",
        lambda *args: (
            200,
            {"version": "0.8.4", "capabilities": {"port_observation": 1}},
            None,
        ),
    )
    assert (
        client.post(
            "/api/hosts/test", json={"url": "http://127.0.0.1:2101"}
        ).status_code
        == 200
    )
