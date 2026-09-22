import asyncio
import copy
import json

import pytest
from tests.analysis.observation_fixture import observation
from backend.analysis.analysis import Analysis, report_document
from backend.analysis.baseline import baseline
from backend.analysis.evidence import AnalysisError, prepare_evidence
from backend.analysis.interpretation import (
    INTERPRETATION_VERSION,
    RENDER_VERSION,
    demo_selection,
    render_interpretation,
    validate_interpretation,
)
from backend.analysis.provider import PROMPT, PROMPT_EXAMPLE, PROMPT_VERSION
from tests.analysis.test_analysis import interpretation

REJECTED_TEXT = [
    "服务已停止。",
    "应用已经崩溃。",
    "该服务已宕机。",
    "现在公网可达。",
    "根因是声明错误。",
    "The service is down.",
    "The application crashed.",
    "The port is publicly reachable.",
    "The root cause is a conflict.",
    "目前不能确认服务已停止。",
    "绑定范围不等于公网可达。",
    "The service might be down.",
    "The observation does not prove that the port is publicly reachable.",
    "停止服务。",
    "删除该容器。",
    "重启应用。",
    "关闭防火墙。",
    "停掉这个进程。",
    "终止系统服务。",
    "删掉当前容器以释放端口。",
    "把该容器删掉。",
    "将这个服务关闭。",
    "1) 重启应用。",
    "先核对端口，然后停止服务。",
    "当前有监听。删除该容器。",
    "Stop service.",
    "Delete this container.",
    "Restart the application.",
    "Disable the firewall.",
    "Shut down the service.",
    "Terminate the running process.",
    "Remove this configuration.",
    "Current observations are available. Restart this app.",
    "建议你停止服务。",
    "应该关闭防火墙。",
    "可通过重启应用解决。",
    "现在应该重启应用。",
    "建议手动删除容器。",
    "请帮我停止服务。",
    "Please stop the service.",
    "You should restart the app.",
    "I recommend deleting the container.",
    "Consider restarting the service.",
    "You can disable the firewall.",
    "The next step is to stop the service.",
    "可能需要重启应用。",
    "或许可以删除容器。",
    "The next step might be stopping the service.",
    "Possibly restarting the app will help.",
    "Restarting services is the next step.",
    "The operator restarted the application.",
    "The operator stops the service.",
    "The operator stopped the service.",
    "This deletes the container.",
    "This deleted the container.",
    "Consider modifying the configuration.",
    "The script modifies the configuration.",
    "The operator modified the configuration.",
    "Consider shutting down the service.",
    "Consider running the command.",
    "Consider executing this script.",
    "Consider removing these containers.",
    "Consider updating the Compose configuration.",
    "Consider terminating the running process.",
    "Consider killing the process.",
    "Consider disabling the firewall.",
    "Consider enabling this rule.",
    "Consider exposing the port.",
    "Consider rebooting the server.",
    "不要重启应用而应停止服务。",
    "不要停止服务但可以删除容器。",
    "无需确认是否重启应用。",
    "No evidence is available so restart the app.",
    "No evidence of restarting the app justifies deleting the container.",
    "Do not restart the app but stop the service.",
    "建议删除容器的记录。",
    "先重启应用的记录尚未采集。",
    "停止服务的记录尚未采集，再删除容器。",
    "不要重启应用。",
    "无需停止服务。",
    "不应删除容器。",
    "没有证据证明服务已停止。",
    "没有证据证明重启应用。",
    "停止服务的记录尚未采集。",
    "重启应用的证据不存在。",
    "Do not restart the app.",
    "Don't stop the service.",
    "You should not delete the container.",
    "Never kill the process.",
    "No evidence of restarting the service.",
    "No evidence supports deleting the container.",
    "No evidence confirms that the service stopped.",
    "当前记录到 TCP 监听与 Compose 声明。",
    "Current observations include a listener.",
    "Do not restart the application.",
    "该端口对公网开放。",
    "外网可以访问。",
    "服务不可用。",
    "应用已下线。",
    "根本原因是 Docker。",
    "问题由 Docker 造成。",
    "Docker 导致了这个问题。",
    "关闭该端口。",
    "更新 Compose 配置。",
    "杀掉进程。",
    "停止 Docker 容器。",
    "Update the Compose configuration.",
    "Stop the Docker container.",
    "尚未验证外网是否可以访问。",
    "无法确认服务不可用。",
    "不能确定应用已下线。",
    "没有证据证明问题由 Docker 造成。",
    "Docker 是否造成问题还不清楚。",
    "当前观察到 Docker 运行映射。",
    "已记录的 Compose 声明发生变化。",
    "服务需要重启。",
    "Docker 容器应停止。",
    "重启一下应用。",
    "更改防火墙设置。",
    "调整端口映射。",
    "清理容器。",
    "The service needs to be restarted.",
    "A restart of the service is recommended.",
]


def freeze(source=None, question="current_occupancy"):
    return prepare_evidence(
        source or observation(), core_version="0.8.4", include_history=True, question_type=question
    )


def select_hypothesis(kind="declaration_runtime_gap", ids=None, missing=None):
    return {
        "kind": kind,
        "evidence_ids": ids or ["current"],
        "missing_evidence": missing or ["runtime_configuration"],
    }


def validate(value, evidence=None):
    return validate_interpretation(json.dumps(value), evidence or freeze())


@pytest.mark.parametrize("text", REJECTED_TEXT)
@pytest.mark.parametrize(
    "slot",
    [
        "top_extra",
        "old_summary",
        "summary_kind",
        "hypothesis_kind",
        "hypothesis_extra",
        "unknown_kind",
        "unknown_extra",
        "missing_evidence",
        "check_action",
        "check_extra",
        "evidence_id",
    ],
)
def test_free_text_is_rejected_regardless_of_language_grammar_or_negation(text, slot):
    result = interpretation()
    result["hypotheses"] = [select_hypothesis()]
    if slot == "top_extra":
        result["text"] = text
    elif slot == "old_summary":
        result["summary"] = {"text": text, "evidence_ids": ["current"]}
    elif slot == "summary_kind":
        result["summary_kind"] = text
    elif slot == "hypothesis_kind":
        result["hypotheses"][0]["kind"] = text
    elif slot == "hypothesis_extra":
        result["hypotheses"][0]["text"] = text
    elif slot == "unknown_kind":
        result["unknowns"][0]["kind"] = text
    elif slot == "unknown_extra":
        result["unknowns"][0]["text"] = text
    elif slot == "missing_evidence":
        result["hypotheses"][0]["missing_evidence"] = [text]
    elif slot == "check_action":
        result["checks"][0]["action"] = text
    elif slot == "check_extra":
        result["checks"][0]["text"] = text
    else:
        result["evidence_ids"] = [text]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate(result)
    with pytest.raises(AnalysisError, match="invalid_output"):
        render_interpretation(result, freeze())


@pytest.mark.parametrize("slot", ["summary", "hypothesis", "unknown", "check"])
@pytest.mark.parametrize("ids", [[], ["invented"], ["current", "current"], [1], None])
def test_all_references_are_required_unique_and_owned_by_frozen_evidence(slot, ids):
    result = interpretation()
    result["hypotheses"] = [select_hypothesis()]
    target = {
        "summary": result,
        "hypothesis": result["hypotheses"][0],
        "unknown": result["unknowns"][0],
        "check": result["checks"][0],
    }[slot]
    target["evidence_ids"] = ids
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_version",
        "wrong_version",
        "bool_version",
        "float_version",
        "old_shape",
        "missing_unknowns",
        "duplicate_hypothesis",
        "duplicate_unknown",
        "duplicate_check",
        "duplicate_missing",
        "empty_missing",
        "wrong_missing",
        "too_many_hypotheses",
        "too_many_checks",
        "too_many_unknowns",
        "mismatched_summary",
        "mismatched_summary_refs",
        "mismatched_hypothesis_refs",
        "mismatched_check_refs",
        "mismatched_unknown_refs",
    ],
)
def test_closed_structure_bounds_and_category_evidence_relationships(mutation):
    result = interpretation()
    result["hypotheses"] = [select_hypothesis()]
    if mutation == "missing_version":
        del result["schema_version"]
    elif mutation == "wrong_version":
        result["schema_version"] = 2
    elif mutation == "bool_version":
        result["schema_version"] = True
    elif mutation == "float_version":
        result["schema_version"] = 1.0
    elif mutation == "old_shape":
        result = {
            "summary": {"text": "safe old prose", "evidence_ids": ["current"]},
            "hypotheses": [],
            "unknowns": ["unknown"],
            "checks": result["checks"],
        }
    elif mutation == "missing_unknowns":
        del result["unknowns"]
    elif mutation == "duplicate_hypothesis":
        result["hypotheses"] *= 2
    elif mutation == "duplicate_unknown":
        result["unknowns"] *= 2
    elif mutation == "duplicate_check":
        result["checks"] *= 2
    elif mutation == "duplicate_missing":
        result["hypotheses"][0]["missing_evidence"] *= 2
    elif mutation == "empty_missing":
        result["hypotheses"][0]["missing_evidence"] = []
    elif mutation == "wrong_missing":
        result["hypotheses"][0]["missing_evidence"] = ["network_path"]
    elif mutation == "too_many_hypotheses":
        result["hypotheses"] *= 4
    elif mutation == "too_many_checks":
        result["checks"] *= 4
    elif mutation == "too_many_unknowns":
        result["unknowns"] *= 7
    elif mutation == "mismatched_summary":
        result["summary_kind"] = "recorded_changes"
    elif mutation == "mismatched_summary_refs":
        result["evidence_ids"] = ["scan"]
    elif mutation == "mismatched_hypothesis_refs":
        result["hypotheses"][0]["evidence_ids"] = ["scan"]
    elif mutation == "mismatched_check_refs":
        result["checks"][0]["evidence_ids"] = ["current"]
    elif mutation == "mismatched_unknown_refs":
        result["unknowns"][0]["evidence_ids"] = ["scan"]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate(result)


@pytest.mark.parametrize(
    "content",
    [
        "null",
        "[]",
        "{",
        '"text"',
        7,
        '{"schema_version":1,"schema_version":1}',
        json.dumps(interpretation()).replace(
            '"kind": "application_health"',
            '"kind": "application_health", "kind": "external_reachability"',
        ),
        " " * 65537,
    ],
)
def test_invalid_or_ambiguous_json_is_rejected(content):
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_interpretation(content, freeze())


def test_prompt_example_and_demo_use_only_the_closed_contract():
    assert PROMPT_VERSION == "port-analysis.v3"
    assert "text" not in json.dumps(PROMPT_EXAMPLE)
    assert validate(PROMPT_EXAMPLE) == PROMPT_EXAMPLE
    assert validate(demo_selection(freeze()))
    assert '"additionalProperties": false' in PROMPT


def event_source(kind="bind_scope_changed"):
    source = observation()
    before = {
        "status": "configured",
        "protocols": ["tcp"],
        "bind_scope": "localhost",
        "compose_conflict": False,
    }
    after = {
        "status": "used",
        "protocols": ["tcp"],
        "bind_scope": "public",
        "compose_conflict": True,
    }
    source["events"] = [
        {
            "schema_version": 1,
            "event_id": "evt-contract",
            "observation_id": "obs-contract",
            "observed_at": 1700000090,
            "port": 8080,
            "protocol": "tcp",
            "kind": kind,
            "before": before,
            "after": after,
            "evidence_refs": [],
            "source_quality": "complete",
        }
    ]
    return source


@pytest.mark.parametrize(
    "event_kind,hypothesis,missing,expected",
    [
        ("bind_scope_changed", "binding_scope_change", "network_path", "本机类别 → 公共地址类别"),
        ("state_changed", "port_state_change", "process_identity", "配置声明 → 占用"),
        (
            "configuration_mismatch",
            "configuration_relation_change",
            "change_context",
            "冲突标记 false → true",
        ),
    ],
)
def test_event_kinds_render_exact_frozen_changes_and_tentative_local_templates(
    event_kind, hypothesis, missing, expected
):
    evidence = freeze(event_source(event_kind), "recorded_changes")
    original = copy.deepcopy(evidence)
    result = demo_selection(evidence)
    result["hypotheses"] = [select_hypothesis(hypothesis, ["evt-contract"], [missing])]
    result["unknowns"] = [
        {"kind": kind, "evidence_ids": ["evt-contract"]} for kind in ("change_cause", "event_time")
    ]
    result["checks"] = [{"action": "port_history", "evidence_ids": ["evt-contract"]}]
    view = render_interpretation(result, evidence)
    assert expected in view["summary"]["text"]
    assert "可能" in view["hypotheses"][0]["text"]
    assert view["hypotheses"][0]["missing_evidence"]
    assert "不能确定根因" in view["unknowns"][0]["text"]
    assert "观察时间" in view["unknowns"][1]["text"]
    assert view["checks"][0]["title"] == "查看已记录变化"
    assert evidence == original
    result["hypotheses"][0]["evidence_ids"] = ["current"]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate(result, evidence)


def test_current_selection_renders_facts_unknowns_and_read_only_check_purposes():
    evidence = freeze()
    original = copy.deepcopy(evidence)
    result = interpretation()
    result["hypotheses"] = [select_hypothesis()]
    result["unknowns"] = [
        {"kind": kind, "evidence_ids": ["current"]}
        for kind in ("application_health", "external_reachability", "process_identity")
    ]
    result["checks"] = [
        {"action": action, "evidence_ids": ["current"]}
        for action in ("declaration", "mapping", "binding")
    ]
    view = render_interpretation(result, evidence)
    assert view["version"] == RENDER_VERSION
    assert "8080" in view["summary"]["text"] and "未观察到对应运行映射" in view["summary"]["text"]
    assert "可能" in view["hypotheses"][0]["text"]
    assert "不能确认应用" in view["unknowns"][0]["text"]
    assert "不能确认防火墙规则或公网连通性" in view["unknowns"][1]["text"]
    assert "不包含进程身份" in view["unknowns"][2]["text"]
    assert [item["title"] for item in view["checks"]] == [
        "查看 Compose 声明",
        "核对监听与 Docker 映射",
        "核对绑定范围与地址族",
    ]
    assert all(item["purpose"] and item["changes_judgment"] for item in view["checks"])
    assert evidence == original
    result["checks"].reverse()
    reordered = render_interpretation(result, evidence)
    assert reordered["summary"] == view["summary"]
    assert reordered["checks"] == list(reversed(view["checks"]))
    assert baseline(evidence) == baseline(original)


def test_reservation_categories_require_an_actual_reservation():
    result = interpretation()
    result["hypotheses"] = [
        select_hypothesis("reservation_runtime_gap", missing=["binding_outcome"])
    ]
    result["unknowns"] = [{"kind": "reservation_guarantee", "evidence_ids": ["current"]}]
    result["checks"] = [{"action": "reservation", "evidence_ids": ["current"]}]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate(result)
    source = observation()
    source["current"]["entries"][0]["reservation"] = True
    view = render_interpretation(result, freeze(source))
    assert "不能保证实际绑定" in view["hypotheses"][0]["text"]
    assert "不能保证操作系统绑定成功" in view["unknowns"][0]["text"]
    assert view["checks"][0]["title"] == "核对预留与实际绑定"


@pytest.mark.parametrize(
    "reason", ["wrong_kind", "degraded", "missing_before", "no_event", "no_declaration"]
)
def test_inapplicable_classifications_fail_even_with_existing_evidence_ids(reason):
    source = event_source()
    result = interpretation()
    result["hypotheses"] = [
        select_hypothesis("binding_scope_change", ["evt-contract"], ["change_context"])
    ]
    if reason == "wrong_kind":
        source["events"][0]["kind"] = "state_changed"
    elif reason == "degraded":
        source["events"][0]["source_quality"] = "degraded"
    elif reason == "missing_before":
        source["events"][0]["before"]["bind_scope"] = None
    elif reason == "no_event":
        source["events"] = []
    else:
        source = observation(protocol="udp")
        result["hypotheses"] = [select_hypothesis()]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate(result, freeze(source))


def test_analysis_revalidates_gateway_results_before_completion_or_export():
    async def scenario():
        async def invalid(*_args, **_kwargs):
            result = interpretation()
            result["text"] = "The service needs to be restarted."
            return result, {}

        analysis = Analysis(invalid)
        preview = analysis.preview(freeze(), "owner")
        analysis.start(preview["id"], "owner", "openai", "synthetic", "fixture")
        await analysis.entries[preview["id"]].task
        failed = analysis.get(preview["id"], "owner")
        assert failed["status"] == "failed" and failed["error"]["code"] == "invalid_output"
        assert failed["interpretation"] is None and failed["interpretation_view"] is None
        assert failed["baseline"] == preview["baseline"]
        assert "needs to be restarted" not in json.dumps(failed)
        with pytest.raises(AnalysisError, match="not_ready"):
            report_document(failed)
        await analysis.close()

    asyncio.run(scenario())


def test_completed_result_and_export_contain_only_validated_selections_and_local_view():
    async def scenario():
        selected = interpretation()
        selected["hypotheses"] = [select_hypothesis()]
        calls = []

        async def gateway(*_args, **_kwargs):
            calls.append(1)
            return selected, {}

        analysis = Analysis(gateway)
        preview = analysis.preview(freeze(), "owner")
        assert preview["interpretation"] is None and preview["interpretation_view"] is None
        assert not calls
        analysis.start(preview["id"], "owner", "openai", "synthetic", "fixture")
        await analysis.entries[preview["id"]].task
        snapshot = analysis.get(preview["id"], "owner")
        assert snapshot["status"] == "completed" and len(calls) == 1
        assert snapshot["interpretation_version"] == INTERPRETATION_VERSION
        assert snapshot["interpretation"] == selected
        assert snapshot["interpretation_view"] == render_interpretation(
            selected, snapshot["evidence"]
        )
        assert snapshot["evidence"] == preview["evidence"]
        assert snapshot["baseline"] == preview["baseline"]
        exported = report_document(snapshot)
        assert exported["interpretation_view"] == snapshot["interpretation_view"]
        assert exported["interpretation_version"] == INTERPRETATION_VERSION
        await analysis.close()

    asyncio.run(scenario())
