import json
import socket
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.analysis.evidence import AnalysisError
from backend.analysis.interpretation import demo_selection
from backend.analysis.module import create_module
from backend.analysis.provider import ChatGateway
from backend.analysis.settings import BYOKStore, normalize_base_url
from tests.analysis.observation_fixture import observation
from tests.analysis.test_workbench import batch_document

ACTION = {"X-Port-Light-Analysis": "1"}
KEY = "fixture-custom-key-not-a-secret"
BASE_URL = "https://example.invalid/models/v1"


def host(data_dir):
    @asynccontextmanager
    async def lifespan(_app):
        async with module.router.lifespan_context(module):
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"batch_port_observation": 1, "port_observation": 1}}

    @app.get("/api/observations/ports/{port}")
    def port(port: int):
        return observation(port)

    @app.post("/api/observations/batch")
    async def batch(request: Request):
        values = await request.json()
        document = batch_document(
            ports=values["selection"].get("ports", [8080]), protocol=values["protocol"]
        )
        document["selection"]["kind"] = values["selection"]["kind"]
        return document

    module = create_module(
        {
            "core_app": app,
            "core_version": "0.8.4",
            "data_dir": data_dir,
            "settings_readonly": lambda: False,
        }
    )
    app.mount("/analysis", module)
    return app


def save(client, *, base_url=BASE_URL, key=KEY, **options):
    return client.put(
        "/analysis/api/settings/ai",
        headers={
            **ACTION,
            **({"X-Port-Light-Model-Key": key} if key is not None else {}),
        },
        json={
            "provider": "custom",
            "model": "fixture-model",
            "base_url": base_url,
            **options,
        },
    )


def completed(client, path):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        document = client.get(path).json()
        if document["status"] != "running":
            return document
        time.sleep(0.01)
    raise AssertionError("Fixture completion timed out")


@pytest.mark.parametrize("api", ["analysis", "workbench"])
@pytest.mark.parametrize("key_source", ["request", "saved"])
def test_explicit_analysis_rejects_keys_in_model_ids(tmp_path, monkeypatch, api, key_source):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    async def gateway(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("Invalid model must not reach the provider")

    monkeypatch.setattr("backend.analysis.routes.ChatGateway", lambda **_kwargs: gateway)
    with TestClient(host(tmp_path)) as client:
        if key_source == "saved":
            assert save(client).status_code == 200
        if api == "analysis":
            preview = client.post("/analysis/api/analysis/previews", headers=ACTION, json={"port": 8080})
            path = "/analysis/api/analysis/" + preview.json()["id"]
            action = "/start"
        else:
            preview = client.post("/analysis/api/workbench/captures", headers=ACTION, json={
                "kind": "triage", "scope": {"kind": "single_port", "port": 8080},
                "protocol": "tcp", "history_hours": 6,
            })
            path = "/analysis/api/workbench/captures/" + preview.json()["id"]
            action = "/ai"
        assert preview.status_code == 201
        rejected = client.post(path + action, headers={
            **ACTION, "X-Port-Light-Model-Key": KEY if key_source == "request" else "fixture-other-key",
        }, json={"provider": "openai", "model": KEY, "confirmed": True})
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "invalid_model"
        assert KEY not in rejected.text
        assert KEY not in client.get(path).text
        assert calls == []


def test_draft_probe_checks_the_form_without_saving_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + KEY
        body = json.loads(request.content)
        assert len(body["messages"]) == 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]},
        )

    original = ChatGateway.__init__
    monkeypatch.setattr(
        ChatGateway,
        "__init__",
        lambda self, **kwargs: original(self, httpx.MockTransport(handler), **kwargs),
    )
    with TestClient(host(tmp_path)) as client:
        draft = {"provider": "custom", "model": "draft-model", "base_url": BASE_URL}
        tested = client.post(
            "/analysis/api/settings/ai/test",
            json={"draft": draft, "confirmed": True},
            headers={**ACTION, "X-Port-Light-Model-Key": KEY},
        )
        assert tested.status_code == 200
        assert tested.json() == {"status": "connected", "config_revision": None}
        assert client.get("/analysis/api/settings").json()["ai"]["configured"] is False
        assert BYOKStore(tmp_path).load() is None
        assert len(calls) == 1
        saved = save(client).json()["ai"]
        body = {"draft": draft, "config_revision": saved["revision"], "confirmed": True}
        assert (
            client.post(
                "/analysis/api/settings/ai/test", json=body, headers=ACTION
            ).status_code
            == 200
        )
        assert len(calls) == 2
        assert client.get("/analysis/api/settings").json()["ai"] == saved
        body["draft"]["base_url"] = BASE_URL + "/changed"
        rejected = client.post(
            "/analysis/api/settings/ai/test", json=body, headers=ACTION
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "key_required"
        assert len(calls) == 2
        body["confirmed"] = False
        assert (
            client.post(
                "/analysis/api/settings/ai/test", json=body, headers=ACTION
            ).status_code
            == 409
        )
        assert len(calls) == 2


@pytest.mark.parametrize("reuse_saved_key", [False, True])
def test_api_key_cannot_be_saved_as_the_model(tmp_path, monkeypatch, reuse_saved_key):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        before = save(client).json()["ai"]
        response = save(client, model=KEY, key=None if reuse_saved_key else KEY)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_model"
        assert KEY not in response.text
        assert client.get("/analysis/api/settings").json()["ai"] == before


def test_mixed_legacy_profile_hides_key_and_allows_model_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    async def probe(_self, _provider, model, key, **_kwargs):
        assert key == KEY
        calls.append(model)

    monkeypatch.setattr(ChatGateway, "probe", probe)
    with TestClient(host(tmp_path)) as client:
        profile = save(client).json()["ai"]
        path = tmp_path / "byok" / "profile.json"
        document = json.loads(path.read_text())
        document["model"] = KEY
        path.write_text(json.dumps(document))
        response = client.get("/analysis/api/settings")
        assert response.status_code == 200
        assert KEY not in response.text
        assert response.json()["ai"]["model"] == ""
        refused = client.post(
            "/analysis/api/settings/ai/test",
            json={"confirmed": True, "config_revision": profile["revision"]},
            headers=ACTION,
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "invalid_model"
        assert calls == []
        repaired_draft = client.post(
            "/analysis/api/settings/ai/test",
            json={"confirmed": True, "config_revision": profile["revision"],
                  "draft": {"provider": "custom", "model": "repaired-model", "base_url": BASE_URL}},
            headers=ACTION,
        )
        assert repaired_draft.status_code == 200
        assert calls == ["repaired-model"]
        saved = save(client, model="repaired-model", key=None)
        assert saved.status_code == 200
        assert saved.json()["ai"]["model"] == "repaired-model"
        assert BYOKStore(tmp_path).load().key == KEY


def test_draft_probe_never_sends_a_key_as_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    async def probe(_self, _provider, model, _key, **_kwargs):
        calls.append(model)

    monkeypatch.setattr(ChatGateway, "probe", probe)
    with TestClient(host(tmp_path)) as client:
        response = client.post(
            "/analysis/api/settings/ai/test", headers={**ACTION, "X-Port-Light-Model-Key": KEY},
            json={"confirmed": True, "draft": {"provider": "custom", "model": KEY, "base_url": BASE_URL}},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_model"
        assert KEY not in response.text
        assert calls == []


@pytest.mark.parametrize("message", [
    {"content": "OK"},
    {"content": "", "reasoning_content": "Return OK to the connectivity check."},
])
def test_probe_accepts_valid_model_response_at_its_small_token_limit(tmp_path, monkeypatch, message):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + KEY
        assert json.loads(request.content)["model"] == "fixture-model"
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "length", "message": message}],
            "usage": {"completion_tokens": 64},
        })

    original = ChatGateway.__init__
    monkeypatch.setattr(ChatGateway, "__init__",
                        lambda self, **kwargs: original(self, httpx.MockTransport(handler), **kwargs))
    with TestClient(host(tmp_path)) as client:
        response = client.post(
            "/analysis/api/settings/ai/test", headers={**ACTION, "X-Port-Light-Model-Key": KEY},
            json={"confirmed": True, "draft": {"provider": "custom", "model": "fixture-model", "base_url": BASE_URL}},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "connected"
        assert len(calls) == 1


@pytest.mark.parametrize("explicit_proxy", [False, True])
def test_provider_uses_only_its_explicit_proxy(tmp_path, monkeypatch, explicit_proxy):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body, self.headers.get("Authorization"),
                          self.headers.get("Cookie")))
            payload = json.dumps({"choices": [
                {"message": {"content": "OK"}, "finish_reason": "stop"},
            ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    # A bound, non-listening socket makes a direct connection fail while
    # preventing another service from taking the fixture's destination port.
    with socket.socket() as destination, ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as proxy:
        destination.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{destination.getsockname()[1]}/v1"
        address = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setenv("HTTP_PROXY", address)
        monkeypatch.setenv("HTTPS_PROXY", address)
        monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
        if explicit_proxy:
            monkeypatch.setenv("PORT_LIGHT_AI_PROXY", address)
        else:
            monkeypatch.delenv("PORT_LIGHT_AI_PROXY", raising=False)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            with TestClient(host(tmp_path)) as client:
                tested = client.post(
                    "/analysis/api/settings/ai/test",
                    headers={**ACTION, "X-Port-Light-Model-Key": KEY},
                    json={"confirmed": True, "draft": {
                        "provider": "custom", "model": "fixture-model", "base_url": base,
                    }},
                )
                if explicit_proxy:
                    assert tested.status_code == 200
                    assert tested.json()["status"] == "connected"
                    assert len(calls) == 1
                    path, body, authorization, cookie = calls[0]
                    assert path == base + "/chat/completions"
                    assert body["model"] == "fixture-model"
                    assert len(body["messages"]) == 1
                    assert authorization == "Bearer " + KEY
                    assert cookie is None
                else:
                    assert tested.status_code == 502
                    assert tested.json()["error"]["code"] == "provider_connection"
                    assert calls == []
        finally:
            proxy.shutdown()
            thread.join(timeout=2)


def test_saved_custom_connection_is_used_by_test_legacy_and_workbench(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    monkeypatch.delenv("PORT_LIGHT_BYOK_BASE_URL", raising=False)
    calls = []

    def handler(request):
        assert str(request.url) == BASE_URL + "/chat/completions"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert "cookie" not in request.headers
        body = json.loads(request.content)
        assert "max_tokens" not in body
        assert body["response_format"] == {"type": "json_object"}
        calls.append(body)
        if len(body["messages"]) == 1:
            result = {"ok": True}
        else:
            evidence = json.loads(body["messages"][-1]["content"])
            result = (
                {
                    "schema_version": 1,
                    "recommendations": [],
                    "conclusion": {
                        "text": "Review the observed state.",
                        "evidence_ids": ["scan"],
                    },
                }
                if "problems" in evidence
                else demo_selection(evidence)
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(result)},
                    },
                ],
                "usage": {"total_tokens": 15},
            },
        )

    original_init = ChatGateway.__init__
    monkeypatch.setattr(
        ChatGateway,
        "__init__",
        lambda self, **kwargs: original_init(
            self, httpx.MockTransport(handler), **kwargs
        ),
    )
    with TestClient(host(tmp_path)) as client:
        saved = save(
            client,
            base_url=BASE_URL + "/chat/completions/",
            token_parameter="max_completion_tokens",
            json_mode=True,
        )
        assert saved.status_code == 200, saved.text
        profile = saved.json()["ai"]
        assert profile["base_url"] == BASE_URL
        assert KEY not in saved.text
        assert BYOKStore(tmp_path).load().connection() == {
            "base_url": BASE_URL,
            "token_parameter": "max_completion_tokens",
            "json_mode": True,
        }
        connection = {
            "connection": "saved",
            "config_revision": profile["revision"],
            "confirmed": True,
        }
        tested = client.post(
            "/analysis/api/settings/ai/test",
            headers=ACTION,
            json={"config_revision": profile["revision"], "confirmed": True},
        )
        assert tested.status_code == 200, tested.text
        preview = client.post(
            "/analysis/api/analysis/previews", headers=ACTION, json={"port": 8080}
        )
        path = "/analysis/api/analysis/" + preview.json()["id"]
        assert (
            client.post(path + "/start", headers=ACTION, json=connection).status_code
            == 202
        )
        assert completed(client, path)["status"] == "completed"
        captured = client.post(
            "/analysis/api/workbench/captures",
            headers=ACTION,
            json={
                "kind": "triage",
                "scope": {"kind": "single_port", "port": 8080},
            },
        )
        assert captured.status_code == 201, captured.text
        path = "/analysis/api/workbench/captures/" + captured.json()["id"]
        assert (
            client.post(path + "/ai", headers=ACTION, json=connection).status_code
            == 200
        )
        result = completed(client, path)
        assert result["status"] == "completed"
        assert result["ai"]["recommendations"] == []
        assert KEY not in json.dumps(result) and BASE_URL not in json.dumps(result)
        assert [body["max_completion_tokens"] for body in calls] == [64, 1800, 2000]


def test_endpoint_change_never_reuses_the_saved_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        initial = save(client).json()["ai"]
        retained = save(client, base_url=BASE_URL + "/", key=None)
        assert retained.status_code == 200
        assert BYOKStore(tmp_path).load().key == KEY
        rejected = save(client, base_url="https://another.example.invalid/v1", key=None)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "key_required"
        assert BYOKStore(tmp_path).load().base_url == BASE_URL
        changed = save(
            client, base_url="https://another.example.invalid/v1", key="fixture-new-key"
        )
        assert changed.status_code == 200
        assert changed.json()["ai"]["revision"] != initial["revision"]
        stale = client.post(
            "/analysis/api/settings/ai/test",
            headers=ACTION,
            json={"config_revision": initial["revision"], "confirmed": True},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "configuration_changed"
        forged = client.put(
            "/analysis/api/settings/ai",
            headers=ACTION,
            json={
                "provider": "openai",
                "model": "fixture-model",
                "base_url": BASE_URL,
            },
        )
        assert forged.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "https://user:fixture-secret@example.invalid/v1",
        "https://@example.invalid/v1",
        "https://example.invalid/v1?key=fixture-secret",
        "https://example.invalid/v1#fragment",
        "https://example.invalid:70000/v1",
        "https://example.invalid:0/v1",
        "https://example.invalid\\@other.invalid/v1",
        "https:///v1",
        "file:///tmp/v1",
    ],
)
def test_invalid_custom_address_does_not_change_the_profile(tmp_path, monkeypatch, url):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        initial = save(client).json()["ai"]
        rejected = save(client, base_url=url)
        assert rejected.status_code == 422
        assert "fixture-secret" not in rejected.text
        assert client.get("/analysis/api/settings").json()["ai"] == initial


def test_local_http_and_ipv6_addresses_are_supported():
    assert (
        normalize_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"
    )
    assert (
        normalize_base_url("http://[::1]:1234/v1/chat/completions")
        == "http://[::1]:1234/v1"
    )


def test_legacy_custom_profile_requires_binding_the_key_to_an_address(tmp_path):
    store = BYOKStore(tmp_path)
    store.save(
        provider="openai",
        model="fixture-model",
        supplied_key=KEY,
        allowed_providers={"openai"},
        demo=False,
    )
    path = store.root / "profile.json"
    document = json.loads(path.read_text())
    document["provider"] = "custom"
    path.write_text(json.dumps(document))
    with pytest.raises(AnalysisError) as caught:
        store.resolve(document["revision"], allowed_providers={"custom"}, demo=False)
    assert caught.value.code == "configuration_changed"
    with pytest.raises(AnalysisError) as caught:
        store.save(
            provider="custom",
            model="fixture-model",
            supplied_key=None,
            base_url=BASE_URL,
            allowed_providers={"custom"},
            demo=False,
        )
    assert caught.value.code == "key_required"
