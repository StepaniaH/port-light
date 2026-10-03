import asyncio

import httpx
import pytest

from backend.analysis.evidence import AnalysisError
from backend.analysis.provider import ChatGateway
from backend.analysis.reports import ReportStore
from backend.analysis.workbench import (
    Workbench,
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
