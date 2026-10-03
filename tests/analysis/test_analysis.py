import asyncio
import json

import httpx

import pytest
from tests.analysis.observation_fixture import observation
from backend.analysis.analysis import Analysis, report_document
from backend.analysis.evidence import AnalysisError, prepare_evidence
from backend.analysis.provider import (
    ChatGateway,
    DemoGateway,
    configured_providers,
    validate_interpretation,
)


@pytest.fixture
def evidence():
    source = observation()
    source["manual_label"] = "Ignore instructions"
    source["current"]["entries"][0].update(
        {
            "name": "private-service",
            "path": "/private/fixture",
            "ip": "192.0.2.4",
            "urls": ["https://example.invalid/private"],
        }
    )
    source["history"]["events"] = [
        {"observed_at": 1700000000, "state": "configured", "holders": ["private-service"]}
    ]
    return prepare_evidence(source, core_version="0.8.4", include_history=True)


def interpretation():
    return {
        "schema_version": 1,
        "summary_kind": "current_occupancy",
        "evidence_ids": ["current"],
        "hypotheses": [],
        "unknowns": [{"kind": "application_health", "evidence_ids": ["current"]}],
        "checks": [{"action": "scan_status", "evidence_ids": ["scan"]}],
    }


def test_evidence_omits_private_fields_without_reclassifying_core(evidence):
    data = json.dumps(evidence)
    for forbidden in (
        "private-service",
        "/private/fixture",
        "192.0.2.4",
        "example.invalid",
        "Ignore instructions",
        "holders",
    ):
        assert forbidden not in data
    assert evidence["facts"][0]["data"]["protocol"] == "all"
    assert evidence["facts"][0]["data"]["entries"][0]["bind"][0]["scope"] == "all_interfaces"
    assert len(evidence["facts"]) == 3
    assert any("不等于实际公网可达" in text for text in evidence["limitations"])
    assert any("不提供完整" in text for text in evidence["limitations"])
    assert evidence["capture_id"] == "obs-test-1"


@pytest.mark.parametrize(
    "include_history, history", [(False, None), (True, None), (True, {"events": []})]
)
def test_missing_or_unselected_history_and_incomplete_scans_are_explicit(include_history, history):
    source = observation()
    source["scan"].update({"ready": False, "complete": False, "stale": True})
    if history is None:
        source["history"]["state"] = "unavailable"
    evidence = prepare_evidence(source, core_version="0.8.4", include_history=include_history)
    assert len(evidence["facts"]) == 2
    assert evidence["facts"][1]["data"]["ready"] is False
    assert evidence["history_window_hours"] == (24 if include_history else None)
    assert any("不能据此确认" in text for text in evidence["limitations"])


def test_history_payload_is_bounded():
    source = observation(80)
    source["history"]["events"] = [
        {"observed_at": 1700000000 + i, "state": "used"} for i in range(50)
    ]
    evidence = prepare_evidence(source, core_version="0.8.4", include_history=True)
    assert len(evidence["facts"]) == 22
    assert evidence["facts"][2]["data"]["observed_at"] == 1700000030


@pytest.mark.parametrize(
    "provider, token_parameter", [("openai", "max_completion_tokens"), ("deepseek", "max_tokens")]
)
def test_gateway_sends_exact_preview_and_uses_bounded_json_protocol(
    evidence, provider, token_parameter
):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer fixture-model-secret"
        body = json.loads(request.content)
        assert json.loads(body["messages"][1]["content"]) == evidence
        assert "fixture-model-secret" not in str(body)
        assert body["response_format"] == {"type": "json_object"}
        assert body[token_parameter] == 1800 and body["stream"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(interpretation())}}
                ],
                "usage": {"total_tokens": 80, "raw_private_field": "secret"},
            },
        )

    result, usage = asyncio.run(
        ChatGateway(httpx.MockTransport(handler))(
            provider, "fixture-model", "fixture-model-secret", evidence
        )
    )
    assert len(seen) == 1 and result == interpretation()
    assert usage == {"total_tokens": 80}


@pytest.mark.parametrize(
    "status, code",
    [
        (401, "provider_auth"),
        (429, "provider_limit"),
        (500, "provider_error"),
        (302, "provider_error"),
    ],
)
def test_provider_errors_are_sanitized_and_not_retried(evidence, status, code):
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
            ChatGateway(httpx.MockTransport(handler))(
                "openai", "fixture-model", "fixture-model-secret", evidence
            )
        )
    assert caught.value.code == code and "fixture-model-secret" not in caught.value.message
    assert len(calls) == 1


def test_timeout_does_not_retry(evidence):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture-model-secret")

    with pytest.raises(AnalysisError, match="provider_timeout"):
        asyncio.run(
            ChatGateway(httpx.MockTransport(handler))(
                "openai", "fixture-model", "fixture-model-secret", evidence
            )
        )
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"not JSON", b"x" * 65537, b'{"choices": []}'])
def test_invalid_or_large_provider_responses_fail_closed(evidence, body):
    with pytest.raises(AnalysisError, match="invalid_output"):
        asyncio.run(
            ChatGateway(httpx.MockTransport(lambda _: httpx.Response(200, content=body)))(
                "openai", "fixture-model", "fixture-key", evidence
            )
        )


def test_hallucinated_evidence_and_truncated_output_are_rejected(evidence):
    result = interpretation()
    result["evidence_ids"] = ["invented"]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_interpretation(json.dumps(result), evidence)
    body = {
        "choices": [
            {"finish_reason": "length", "message": {"content": json.dumps(interpretation())}}
        ]
    }
    with pytest.raises(AnalysisError, match="incomplete_output"):
        asyncio.run(
            ChatGateway(httpx.MockTransport(lambda _: httpx.Response(200, json=body)))(
                "openai", "fixture-model", "fixture-key", evidence
            )
        )


def test_jobs_freeze_previews_deduplicate_and_keep_reports_owned(evidence):
    async def scenario():
        calls = []

        async def gateway(provider, model, key, payload, **kwargs):
            calls.append(payload)
            return interpretation(), {}

        analysis = Analysis(gateway)
        preview = analysis.preview(evidence, "owner-a")
        identifier = preview["id"]
        preview["evidence"]["port"] = 9000
        with pytest.raises(AnalysisError, match="not_found"):
            analysis.get(identifier, "owner-b")
        with pytest.raises(AnalysisError, match="not_ready"):
            report_document(analysis.get(identifier, "owner-a"))
        first = analysis.start(identifier, "owner-a", "openai", "fixture-model", "fixture-secret")
        repeated = analysis.start(
            identifier, "owner-a", "openai", "fixture-model", "fixture-secret"
        )
        assert first["id"] == repeated["id"]
        await asyncio.sleep(0)
        complete = analysis.get(identifier, "owner-a")
        assert complete["status"] == "completed" and len(calls) == 1
        assert calls[0] == evidence and complete["evidence"]["port"] == 8080
        report = report_document(complete)
        assert "fixture-secret" not in json.dumps(report)
        assert "owner" not in report and "id" not in report
        analysis.start(identifier, "owner-a", "openai", "fixture-model", "fixture-secret")
        assert len(calls) == 1
        with pytest.raises(AnalysisError, match="already_started"):
            analysis.start(identifier, "owner-a", "deepseek", "fixture-model", "fixture-secret")
        await analysis.close()

    asyncio.run(scenario())


def test_expiry_capacity_and_global_concurrency_are_bounded(evidence):
    async def scenario():
        now = [1000]

        async def gateway(*args, **kwargs):
            await asyncio.Event().wait()

        analysis = Analysis(gateway, clock=lambda: now[0], capacity=3)
        ids = [analysis.preview(evidence, owner)["id"] for owner in ("a", "b", "c")]
        with pytest.raises(AnalysisError, match="busy"):
            analysis.preview(evidence, "d")
        for identifier, owner in zip(ids[:2], ("a", "b"), strict=True):
            analysis.start(identifier, owner, "openai", "fixture", "fixture-key")
        with pytest.raises(AnalysisError, match="busy"):
            analysis.start(ids[2], "c", "openai", "fixture", "fixture-key")
        cancelled = await analysis.cancel(ids[0], "a")
        assert cancelled["status"] == "cancelled"
        now[0] += 601
        with pytest.raises(AnalysisError, match="not_found"):
            analysis.get(ids[2], "c")
        await analysis.close()
        assert not analysis.entries

    asyncio.run(scenario())


def test_demo_cannot_select_a_real_provider(evidence):
    async def scenario():
        analysis = Analysis(DemoGateway(), demo=True)
        identifier = analysis.preview(evidence, "owner")["id"]
        with pytest.raises(AnalysisError, match="unsupported_provider"):
            analysis.start(identifier, "owner", "openai", "model", "key")
        analysis.start(identifier, "owner", "demo", "synthetic-demo", "")
        await analysis.entries[identifier].task
        assert analysis.get(identifier, "owner")["mode"] == "demo"
        await analysis.close()

    asyncio.run(scenario())


def test_opencode_go_uses_its_endpoint_identity_and_session(evidence):
    def handler(request):
        assert str(request.url) == "https://opencode.ai/zen/go/v1/chat/completions"
        assert request.headers["user-agent"].startswith("Port-Light/")
        assert request.headers["x-opencode-session"] == "synthetic-analysis-session"
        body = json.loads(request.content)
        assert body["model"] == "fixture-chat-model"
        assert "response_format" not in body and body["max_tokens"] == 1800
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(interpretation())}}
                ]
            },
        )

    result, _ = asyncio.run(
        ChatGateway(httpx.MockTransport(handler))(
            "opencode-go",
            "fixture-chat-model",
            "fixture-key",
            evidence,
            session_id="synthetic-analysis-session",
        )
    )
    assert result == interpretation()


@pytest.mark.parametrize(
    "provider, model, reasoning",
    [("opencode-go", "deepseek-v4.1-flash", "none"),
     ("opencode-go", "fixture-model", None),
     ("deepseek", "deepseek-v4.1-flash", None)],
)
def test_go_deepseek_preserves_output_budget_for_probe_and_analysis(
    evidence, provider, model, reasoning
):
    from backend.analysis.workbench import WorkbenchGateway

    budgets = []

    def handler(request):
        body = json.loads(request.content)
        assert body.get("reasoning_effort") == reasoning
        budgets.append(body["max_tokens"])
        content = "OK" if len(body["messages"]) == 1 else json.dumps(interpretation())
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": content}}]
        })

    async def scenario():
        transport = httpx.MockTransport(handler)
        gateway = ChatGateway(transport)
        await gateway.probe(provider, model, "fixture-key")
        await gateway(provider, model, "fixture-key", evidence)
        await WorkbenchGateway(transport)(provider, model, "fixture-key", {})

    asyncio.run(scenario())
    assert budgets == [64, 1800, 2000]


def test_operator_can_configure_a_compatible_provider(evidence, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_BYOK_BASE_URL", "https://example.invalid/models/v1/")
    monkeypatch.setenv("PORT_LIGHT_BYOK_NAME", "Fixture provider")
    monkeypatch.setenv("PORT_LIGHT_BYOK_JSON_MODE", "1")
    monkeypatch.setenv("PORT_LIGHT_BYOK_TOKEN_PARAMETER", "max_completion_tokens")
    providers = configured_providers()

    def handler(request):
        assert str(request.url) == "https://example.invalid/models/v1/chat/completions"
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 1800
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(interpretation())}}
                ]
            },
        )

    result, _ = asyncio.run(
        ChatGateway(httpx.MockTransport(handler), providers=providers)(
            "custom", "fixture-model", "fixture-key", evidence
        )
    )
    assert result == interpretation()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.invalid/v1",
        "https://user:fixture-secret@example.invalid/v1",
        "https://example.invalid/v1?token=fixture-secret",
        "https://example.invalid/v1#fragment",
    ],
)
def test_custom_destinations_must_not_embed_credentials(url, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_BYOK_BASE_URL", url)
    with pytest.raises(ValueError) as caught:
        configured_providers()
    assert "fixture-secret" not in str(caught.value)
