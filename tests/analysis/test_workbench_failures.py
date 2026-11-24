import asyncio
import json
import socket
import ssl
import threading

import httpx
import pytest

from backend.analysis.evidence import AnalysisError
from backend.analysis.provider import ChatGateway
from backend.analysis.reports import ReportStore
from backend.analysis.workbench import (
    AttemptGate,
    Workbench,
    WorkbenchDemoGateway,
    WorkbenchGateway,
    build_capture,
    compare_captures,
    validate_recommendations,
)
from tests.analysis.test_workbench import batch_document

CONCLUSION = {"text": "Review the observed state.", "evidence_ids": ["scan"]}


@pytest.mark.parametrize(
    "choice",
    [
        {"message": {}, "finish_reason": "stop"},
        {"message": {"content": ""}, "finish_reason": "stop"},
        {"message": {"content": "OK"}, "finish_reason": "content_filter"},
        {"message": {"content": ["OK"]}, "finish_reason": "stop"},
    ],
)
def test_connection_probe_requires_a_complete_text_response(choice):
    gateway = ChatGateway(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [choice]}))
    )
    with pytest.raises(AnalysisError) as caught:
        asyncio.run(gateway.probe("openai", "fixture-model", "fixture-key"))
    assert caught.value.code == "invalid_output"


@pytest.mark.parametrize("outcome", ["empty", "failed", "cancelled"])
def test_saving_an_ai_attempt_preserves_its_state_after_a_rules_report(
    tmp_path, outcome
):
    async def scenario():
        async def gateway(*args, **kwargs):
            if outcome == "failed":
                raise AnalysisError("provider_timeout", "Fixture timeout", 504)
            if outcome == "cancelled":
                await asyncio.sleep(60)
            return {
                "schema_version": 1,
                "conclusion": CONCLUSION,
                "recommendations": [],
            }, {"total_tokens": 10}

        store = ReportStore(tmp_path)
        store.open()
        workbench = Workbench(gateway, demo=True, reports=store)
        try:
            capture = workbench.create(
                batch_document(ports=[8080]),
                owner="owner",
                kind="triage",
                scope_requested=scope(),
            )
            original = store.save_workbench(capture, "owner")
            workbench.start_ai(capture["id"], "owner", "demo", "fixture-model", "")
            if outcome == "cancelled":
                await workbench.cancel(capture["id"], "owner")
            else:
                await workbench.entries[capture["id"]].task
            current = workbench.get(capture["id"], "owner")
            saved = store.save_workbench(current, "owner")
            assert saved["id"] != original["id"]
            assert saved["capture"]["ai"] == current["ai"]
            assert saved["capture"]["status"] == current["status"]
            assert store.save_workbench(current, "owner")["id"] == saved["id"]
            assert (
                store.get_workbench(original["id"], "owner")["capture"]["ai"]["status"]
                == "not_started"
            )
        finally:
            await workbench.close()
            store.close()

    asyncio.run(scenario())


def scope():
    return {
        "kind": "single_port",
        "scope": {"kind": "single_port", "port": 8080},
        "protocol": "all",
        "history_hours": 6,
    }


def test_current_condition_recheck_does_not_require_historical_events():
    previous = build_capture(
        batch_document(ports=[8080]),
        identifier="previous",
        kind="triage",
        scope_requested=scope(),
    )
    batch = batch_document(ports=[8080], conflict=False, capture_id="obs-new")
    batch["event_coverage"]["state"] = "unavailable"
    current = build_capture(
        batch,
        identifier="current",
        kind="recheck",
        scope_requested=scope(),
        problem_mode="triage",
    )
    comparison = compare_captures(previous, current, source_report_id="report")
    conflict = next(
        problem
        for problem in previous["problems"]
        if problem["kind"] == "overlapping_compose_bindings"
    )
    assert conflict["id"] in {item["problem_id"] for item in comparison["not_observed"]}
    assert comparison["cannot_compare"] == []


def test_receipt_failure_keeps_a_valid_ai_result(tmp_path, monkeypatch):
    async def scenario():
        async def gateway(*args, **kwargs):
            return {
                "schema_version": 1,
                "conclusion": CONCLUSION,
                "recommendations": [],
            }, {"total_tokens": 10}

        def fail_receipt(*args):
            raise AnalysisError(
                "report_storage_unavailable", "Fixture storage failure", 503
            )

        store = ReportStore(tmp_path)
        store.open()
        workbench = Workbench(gateway, demo=True, reports=store)
        try:
            capture = workbench.create(
                batch_document(ports=[8080]),
                owner="owner",
                kind="triage",
                scope_requested=scope(),
            )
            monkeypatch.setattr(store, "finish_workbench_job", fail_receipt)
            workbench.start_ai(capture["id"], "owner", "demo", "fixture-model", "")
            await workbench.entries[capture["id"]].task
            current = workbench.get(capture["id"], "owner")
            assert current["ai"] == {
                "status": "completed",
                "conclusion": CONCLUSION,
                "recommendations": [],
            }
            assert current["status"] == "completed"
            assert current["error"]["code"] == "receipt_unavailable"
            assert (
                store.save_workbench(current, "owner")["capture"]["ai"] == current["ai"]
            )
        finally:
            await workbench.close()
            store.close()

    asyncio.run(scenario())


def test_immediate_cancel_renews_capture_retention():
    async def scenario():
        async def gateway(*args, **kwargs):
            raise AssertionError("A cancelled task must not call the provider")

        now = [10]
        workbench = Workbench(gateway, demo=True, clock=lambda: now[0], ttl=1)
        capture = workbench.create(
            batch_document(ports=[8080]),
            owner="owner",
            kind="triage",
            scope_requested=scope(),
        )
        now[0] = 10.9
        workbench.start_ai(capture["id"], "owner", "demo", "fixture-model", "")
        await workbench.cancel(capture["id"], "owner")
        now[0] = 11.1
        assert workbench.get(capture["id"], "owner")["status"] == "cancelled"
        await workbench.close()

    asyncio.run(scenario())


def test_ai_must_check_degraded_coverage_before_interpreting_state():
    batch = batch_document(ports=[8080])
    batch["scan"]["stale"] = True
    capture = build_capture(
        batch, identifier="capture", kind="triage", scope_requested=scope()
    )
    problems = capture["ai_preview"]["payload"]["problems"]
    checks = [
        {
            "problem_id": problem["id"],
            "evidence_ids": problem["evidence_ids"][:1],
            "action": problem["first_check"]["action"],
            "relation": problem["relation"]["kind"],
        }
        for problem in problems
    ]
    assert (
        validate_recommendations(
            {"schema_version": 1, "conclusion": CONCLUSION, "recommendations": checks},
            capture,
        )
        == checks
    )
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(
            {
                "schema_version": 1,
                "conclusion": CONCLUSION,
                "recommendations": checks[1:],
            },
            capture,
        )
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(
            {
                "schema_version": 1,
                "conclusion": CONCLUSION,
                "recommendations": list(reversed(checks)),
            },
            capture,
        )


def test_model_cannot_repeat_one_problem_for_both_checks():
    capture = build_capture(
        batch_document(ports=[8080]), identifier="fixture", kind="triage",
        scope_requested=scope(),
    )
    problem = capture["ai_preview"]["payload"]["problems"][0]
    recommendation = {
        "problem_id": problem["id"], "evidence_ids": problem["evidence_ids"],
        "action": problem["first_check"]["action"], "relation": problem["relation"]["kind"],
    }
    result = {"schema_version": 1, "conclusion": CONCLUSION,
              "recommendations": [recommendation]}
    assert validate_recommendations(result, capture) == [recommendation]
    result["recommendations"].append({**recommendation, "action": problem["confirm"]["action"]})
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(result, capture)


def test_model_check_uses_the_captured_relation_without_reclassifying_it():
    capture = build_capture(
        batch_document(ports=[8080], conflict=False), identifier="fixture", kind="triage",
        scope_requested=scope(),
    )
    problem = capture["ai_preview"]["payload"]["problems"][0]
    recommendation = {
        "problem_id": problem["id"], "evidence_ids": problem["evidence_ids"],
        "action": problem["first_check"]["action"],
    }
    result = {"schema_version": 2, "conclusion": CONCLUSION,
              "recommendations": [recommendation]}
    assert validate_recommendations(result, capture) == [
        {**recommendation, "relation": "same_compose_project"}
    ]
    recommendation["relation"] = "independent"
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(result, capture)
    result["schema_version"] = 1
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(result, capture)


def test_slow_provider_stream_has_a_total_timeout():
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            await asyncio.sleep(60)

    gateway = ChatGateway(
        httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowStream()))
    )
    with pytest.raises(AnalysisError, match="provider_timeout"):
        asyncio.run(gateway._post("openai", "fixture-key", {}, timeout=0.01))


@pytest.mark.parametrize(
    "provider, token_parameter, expect_store",
    [("openai", "max_completion_tokens", False), ("deepseek", "max_tokens", None)],
)
def test_workbench_gateway_sends_exact_payload_and_bounded_json_protocol(
    provider, token_parameter, expect_store
):
    payload = {"facts": {"scan": {}}, "problems": []}
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer fixture-model-secret"
        body = json.loads(request.content)
        assert json.loads(body["messages"][1]["content"]) == payload
        assert "fixture-model-secret" not in str(body)
        assert body["response_format"] == {"type": "json_object"}
        assert body[token_parameter] == 2000 and body["stream"] is False
        assert body.get("store") is expect_store
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "schema_version": 2,
                                    "conclusion": CONCLUSION,
                                    "recommendations": [],
                                }
                            )
                        },
                    }
                ],
                "usage": {"total_tokens": 80, "raw_private_field": "secret"},
            },
        )

    content, usage = asyncio.run(
        WorkbenchGateway(httpx.MockTransport(handler))(
            provider, "fixture-model", "fixture-model-secret", payload
        )
    )
    assert len(seen) == 1
    assert json.loads(content)["conclusion"] == CONCLUSION
    assert usage == {"total_tokens": 80}


@pytest.mark.parametrize(
    "status, code",
    [
        (401, "provider_auth"),
        (403, "provider_auth"),
        (429, "provider_limit"),
        (500, "provider_error"),
        (302, "provider_error"),
    ],
)
def test_provider_errors_are_sanitized_and_not_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": "fixture-model-secret"},
            headers={"location": "https://example.invalid/redirect"},
        )

    with pytest.raises(AnalysisError) as caught:
        asyncio.run(
            WorkbenchGateway(httpx.MockTransport(handler))(
                "openai", "fixture-model", "fixture-model-secret", {}
            )
        )
    assert caught.value.code == code and "fixture-model-secret" not in caught.value.message
    assert len(calls) == 1


def test_read_timeout_and_truncated_or_malformed_outputs_fail_closed():
    calls = []

    def timeout_handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture-model-secret")

    with pytest.raises(AnalysisError, match="provider_timeout"):
        asyncio.run(
            WorkbenchGateway(httpx.MockTransport(timeout_handler))(
                "openai", "fixture-model", "fixture-model-secret", {}
            )
        )
    assert len(calls) == 1

    for raw in (b"not JSON", b"x" * 65537, b'{"choices": []}'):
        with pytest.raises(AnalysisError, match="invalid_output"):
            asyncio.run(
                WorkbenchGateway(httpx.MockTransport(lambda _: httpx.Response(200, content=raw)))(
                    "openai", "fixture-model", "fixture-key", {}
                )
            )

    truncated = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "content": json.dumps(
                        {"schema_version": 2, "conclusion": CONCLUSION, "recommendations": []}
                    )
                },
            }
        ]
    }
    with pytest.raises(AnalysisError, match="incomplete_output"):
        asyncio.run(
            WorkbenchGateway(httpx.MockTransport(lambda _: httpx.Response(200, json=truncated)))(
                "openai", "fixture-model", "fixture-key", {}
            )
        )


@pytest.mark.parametrize(
    "provider, model, reasoning",
    [
        ("opencode-go", "deepseek-v4.1-flash", "none"),
        ("opencode-go", "fixture-model", None),
        ("deepseek", "deepseek-v4.1-flash", None),
    ],
)
def test_opencode_go_headers_and_deepseek_reasoning_budget(provider, model, reasoning):
    budgets = []

    def handler(request):
        if provider == "opencode-go":
            assert str(request.url) == "https://opencode.ai/zen/go/v1/chat/completions"
            assert request.headers["user-agent"].startswith("Port-Light/")
            assert request.headers["x-opencode-session"] == "synthetic-workbench-session"
        body = json.loads(request.content)
        assert body.get("reasoning_effort") == reasoning
        if provider == "opencode-go":
            assert "response_format" not in body
        budgets.append(body["max_tokens"])
        content = (
            "OK"
            if len(body["messages"]) == 1
            else json.dumps({"schema_version": 2, "conclusion": CONCLUSION, "recommendations": []})
        )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    async def scenario():
        transport = httpx.MockTransport(handler)
        await ChatGateway(transport).probe(
            provider, model, "fixture-key", session_id="synthetic-workbench-session"
        )
        await WorkbenchGateway(transport)(
            provider, model, "fixture-key", {}, session_id="synthetic-workbench-session"
        )

    asyncio.run(scenario())
    assert budgets == [64, 2000]


def test_capture_pool_evicts_oldest_non_running_entry_and_only_busies_when_all_running():
    async def scenario():
        now = [1000.0]

        async def blocking_gateway(*_args, **_kwargs):
            await asyncio.Event().wait()

        gate = AttemptGate(capacity=4)
        workbench = Workbench(
            blocking_gateway, attempt_gate=gate, clock=lambda: now[0], capacity=3
        )
        c1 = workbench.create(
            batch_document(ports=[8001]), owner="owner-1", kind="triage", scope_requested=scope()
        )
        now[0] += 1
        c2 = workbench.create(
            batch_document(ports=[8002]), owner="owner-2", kind="triage", scope_requested=scope()
        )
        now[0] += 1
        c3 = workbench.create(
            batch_document(ports=[8003]), owner="owner-3", kind="triage", scope_requested=scope()
        )

        # Start c1 so it is actively running; c2 is now the oldest non-running entry.
        workbench.start_ai(c1["id"], "owner-1", "openai", "fixture-model", "fixture-key")
        now[0] += 1
        c4 = workbench.create(
            batch_document(ports=[8004]), owner="owner-4", kind="triage", scope_requested=scope()
        )
        with pytest.raises(AnalysisError, match="not_found"):
            workbench.get(c2["id"], "owner-2")
        assert workbench.get(c1["id"], "owner-1")["status"] == "running"
        assert workbench.get(c3["id"], "owner-3")["status"] == "ready"
        assert workbench.get(c4["id"], "owner-4")["status"] == "ready"

        # When all 3 slots are actively running, creating another capture raises 429 busy.
        workbench.start_ai(c3["id"], "owner-3", "openai", "fixture-model", "fixture-key")
        workbench.start_ai(c4["id"], "owner-4", "openai", "fixture-model", "fixture-key")
        with pytest.raises(AnalysisError, match="busy") as caught:
            workbench.create(
                batch_document(ports=[8005]),
                owner="owner-5",
                kind="triage",
                scope_requested=scope(),
            )
        assert caught.value.status == 429

        # Once one running capture is cancelled, its non-running slot is LRU-evictable.
        await workbench.cancel(c3["id"], "owner-3")
        now[0] += 1
        c5 = workbench.create(
            batch_document(ports=[8005]), owner="owner-5", kind="triage", scope_requested=scope()
        )
        assert workbench.get(c5["id"], "owner-5")["status"] == "ready"
        with pytest.raises(AnalysisError, match="not_found"):
            workbench.get(c3["id"], "owner-3")
        await workbench.close()

    asyncio.run(scenario())


def test_demo_and_concurrency_gates_enforce_provider_and_session_limits():
    async def scenario():
        demo = Workbench(WorkbenchDemoGateway(), demo=True)
        capture = demo.create(
            batch_document(ports=[8080]), owner="owner", kind="triage", scope_requested=scope()
        )
        with pytest.raises(AnalysisError, match="unsupported_provider"):
            demo.start_ai(capture["id"], "owner", "openai", "model", "key")
        demo.start_ai(capture["id"], "owner", "demo", "synthetic-demo", "")
        # Duplicate submission with identical parameters is idempotent while running.
        assert (
            demo.start_ai(capture["id"], "owner", "demo", "synthetic-demo", "")["id"]
            == capture["id"]
        )
        with pytest.raises(AnalysisError, match="already_started"):
            demo.start_ai(capture["id"], "owner", "demo", "other-model", "")
        await demo.entries[capture["id"]].task
        await demo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url, addresses",
    [
        ("http://imds.example.invalid/v1", ["169.254.169.254"]),
        ("http://imds6.example.invalid/v1", ["fd00:ec2::254"]),
        ("http://alibaba.example.invalid/v1", ["100.100.100.200"]),
        ("http://hub.example.invalid:2100/v1", ["127.0.0.1"]),
        ("http://mixed.example.invalid/v1", ["192.0.2.10", "fe80::1"]),
    ],
)
def test_gateway_blocks_dns_ssrf_and_self_loopback_before_opening_socket(
    monkeypatch, url, addresses
):
    monkeypatch.delenv("PORT_LIGHT_AI_PROXY", raising=False)
    monkeypatch.delenv("PORT_LIGHT_PORT", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port, *_a, **_kw: [
            (
                socket.AF_INET6 if ":" in addr else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (addr, port, 0, 0) if ":" in addr else (addr, port),
            )
            for addr in addresses
        ],
    )

    real_socket = socket.socket

    def unexpected_socket(af=socket.AF_INET, *args, **kwargs):
        if af in (socket.AF_INET, socket.AF_INET6):
            pytest.fail("disallowed DNS target opened a socket")
        return real_socket(af, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", unexpected_socket)
    gateway = ChatGateway(
        providers={
            "custom": {
                "name": "Custom",
                "token_parameter": "max_tokens",
                "json_mode": False,
            }
        }
    )
    with pytest.raises(AnalysisError) as caught:
        asyncio.run(
            gateway.probe(
                "custom",
                "fixture-model",
                "fixture-key",
                connection={
                    "base_url": url,
                    "token_parameter": "max_tokens",
                    "json_mode": False,
                },
            )
        )
    assert caught.value.status == 422
    assert caught.value.code == "invalid_base_url"


def test_gateway_pins_validated_addresses_without_second_dns_lookup(monkeypatch):
    monkeypatch.delenv("PORT_LIGHT_AI_PROXY", raising=False)
    monkeypatch.delenv("PORT_LIGHT_PORT", raising=False)
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
            header_end = request_wire.index(b"\r\n\r\n") + 4
            content_length = 0
            for line in request_wire[:header_end].decode("latin1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
            while len(request_wire) - header_end < content_length:
                chunk = server_sock.recv(4096)
                if not chunk:
                    break
                request_wire.extend(chunk)
            body = json.dumps(
                {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
            ).encode("utf-8")
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
            raise AssertionError("provider performed a second DNS lookup")
        assert host == "llm.example.invalid"
        assert resolved_port == 8081
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 8081)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 8081)),
        ]

    gateway = ChatGateway(
        providers={
            "custom": {
                "name": "Custom",
                "token_parameter": "max_tokens",
                "json_mode": False,
            }
        }
    )
    real_socket = socket.socket

    def fake_socket(af=socket.AF_INET, *args, **kwargs):
        if af in (socket.AF_INET, socket.AF_INET6):
            return PinnedSocket()
        return real_socket(af, *args, **kwargs)

    try:
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(socket, "socket", fake_socket)
        asyncio.run(
            gateway.probe(
                "custom",
                "fixture-model",
                "fixture-key",
                connection={
                    "base_url": "http://llm.example.invalid:8081/v1",
                    "token_parameter": "max_tokens",
                    "json_mode": False,
                },
            )
        )
    finally:
        client_sock.close()
        thread.join(timeout=1)

    assert lookups == 1
    assert attempted == [("192.0.2.10", 8081), ("10.0.0.2", 8081)]
    wire = bytes(request_wire)
    assert b"POST /v1/chat/completions HTTP/1.1\r\n" in wire
    assert b"Host: llm.example.invalid:8081\r\n" in wire
    assert b"Authorization: Bearer fixture-key\r\n" in wire


def test_gateway_https_pins_validated_ip_and_preserves_sni_hostname(monkeypatch):
    monkeypatch.delenv("PORT_LIGHT_AI_PROXY", raising=False)
    monkeypatch.delenv("PORT_LIGHT_PORT", raising=False)
    connected: list[tuple] = []
    sni_hosts: list[str] = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def setsockopt(self, *_args):
            pass

        def connect(self, sockaddr):
            connected.append(sockaddr)

        def close(self):
            pass

    class FakeContext:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, _sock, *, server_hostname=None):
            sni_hosts.append(server_hostname)
            raise ssl.SSLCertVerificationError("synthetic TLS mismatch")

    real_socket = socket.socket

    def fake_socket(af=socket.AF_INET, *args, **kwargs):
        if af in (socket.AF_INET, socket.AF_INET6):
            return FakeSocket()
        return real_socket(af, *args, **kwargs)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port, *_a, **_kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.20", port))
        ],
    )
    monkeypatch.setattr(socket, "socket", fake_socket)
    monkeypatch.setattr(ssl, "create_default_context", lambda: FakeContext())
    gateway = ChatGateway(
        providers={
            "custom": {
                "name": "Custom",
                "token_parameter": "max_tokens",
                "json_mode": False,
            }
        }
    )
    with pytest.raises(AnalysisError) as caught:
        asyncio.run(
            gateway.probe(
                "custom",
                "fixture-model",
                "fixture-key",
                connection={
                    "base_url": "https://llm.example.invalid/v1",
                    "token_parameter": "max_tokens",
                    "json_mode": False,
                },
            )
        )
    assert caught.value.code == "provider_connection"
    assert connected == [("192.0.2.20", 443)]
    assert sni_hosts == ["llm.example.invalid"]
