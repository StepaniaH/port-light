"""Closed model selections and deterministic Chinese interpretation templates."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .baseline import baseline, check
from .evidence import AnalysisError, Identifier

INTERPRETATION_VERSION = "port-interpretation.v1"
RENDER_VERSION = "port-interpretation-zh.v1"
SummaryKind = Literal["current_occupancy", "recorded_changes"]
HypothesisKind = Literal[
    "declaration_runtime_gap",
    "binding_scope_change",
    "port_state_change",
    "configuration_relation_change",
    "reservation_runtime_gap",
]
UnknownKind = Literal[
    "application_health",
    "external_reachability",
    "process_identity",
    "change_cause",
    "event_time",
    "reservation_guarantee",
]
MissingEvidenceKind = Literal[
    "runtime_configuration",
    "application_health",
    "process_identity",
    "change_context",
    "network_path",
    "binding_outcome",
]
Action = Literal["scan_status", "mapping", "declaration", "binding", "port_history", "reservation"]

HYPOTHESES = {
    "declaration_runtime_gap": "声明与运行观察可能存在差异；这不能确认应用停止或配置无效。",
    "binding_scope_change": "已记录的绑定范围变化可能与配置或运行环境变化有关；当前证据不能确定原因。",
    "port_state_change": "端口状态变化可能与运行状态或扫描来源变化有关；记录不能确定进程身份或应用健康。",
    "configuration_relation_change": "配置关系变化可能与声明或运行映射变化有关；当前证据不能确定原因。",
    "reservation_runtime_gap": "预留与运行观察可能处于不同阶段；预留是协调记录，不能保证实际绑定。",
}
UNKNOWNS = {
    "application_health": "端口观察不能确认应用是否健康或已停止。",
    "external_reachability": "本机绑定类别不能确认防火墙规则或公网连通性。",
    "process_identity": "本次证据不包含进程身份，不能确认具体进程或占用者。",
    "change_cause": "前后记录只能说明观察发生变化，不能确定根因。",
    "event_time": "事件时间是观察时间，不能当作故障发生的准确时间。",
    "reservation_guarantee": "预留记录不能保证操作系统绑定成功或服务启动。",
}
MISSING_EVIDENCE = {
    "runtime_configuration": "与当前协议和范围对应的实际运行配置",
    "application_health": "独立的应用健康检查结果",
    "process_identity": "对应观察时刻的进程身份记录",
    "change_context": "对应时段的配置与运行环境变更记录",
    "network_path": "防火墙规则与目标网络的连通性检查结果",
    "binding_outcome": "预留之后的实际绑定结果",
}
HYPOTHESIS_MISSING = {
    "declaration_runtime_gap": {"runtime_configuration", "application_health"},
    "binding_scope_change": {"change_context", "network_path"},
    "port_state_change": {"process_identity", "application_health", "change_context"},
    "configuration_relation_change": {"runtime_configuration", "change_context"},
    "reservation_runtime_gap": {"binding_outcome", "application_health"},
}
EVENT_HYPOTHESES = {
    "binding_scope_change": "bind_scope_changed",
    "port_state_change": "state_changed",
    "configuration_relation_change": "configuration_mismatch",
}


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Referenced(ClosedModel):
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=8)


class Hypothesis(Referenced):
    kind: HypothesisKind
    missing_evidence: list[MissingEvidenceKind] = Field(min_length=1, max_length=3)


class Unknown(Referenced):
    kind: UnknownKind


class Check(Referenced):
    action: Action


class Interpretation(Referenced):
    schema_version: Literal[1]
    summary_kind: SummaryKind
    hypotheses: list[Hypothesis] = Field(max_length=3)
    unknowns: list[Unknown] = Field(min_length=1, max_length=6)
    checks: list[Check] = Field(min_length=1, max_length=3)


def unique(values):
    if len(values) != len(set(values)):
        raise ValueError("Duplicate selection")


def closed_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def validate_interpretation(content, evidence):
    try:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 65536:
            raise ValueError("Invalid output size or type")
        document = json.loads(content, object_pairs_hook=closed_object)
        if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
            raise ValueError("The schema version must be an integer")
        result = Interpretation.model_validate(document)
        ids = {fact["id"] for fact in evidence["facts"]}
        entries = evidence["observation"]["current"]["entries"]
        events = {
            event["event_id"]: event
            for event in evidence["observation"]["events"]
            if event["kind"] in EVENT_HYPOTHESES.values()
        }
        for selection in [result, *result.hypotheses, *result.unknowns, *result.checks]:
            unique(selection.evidence_ids)
            if not set(selection.evidence_ids) <= ids:
                raise ValueError("Unknown evidence reference")
        if result.summary_kind != evidence["question_type"]:
            raise ValueError("Summary does not match the selected question")
        summary_refs = {"current"} if result.summary_kind == "current_occupancy" else set(events)
        if not set(result.evidence_ids) <= summary_refs:
            raise ValueError("Summary references do not support its kind")
        unique([item.kind for item in result.hypotheses])
        unique([item.kind for item in result.unknowns])
        unique([item.action for item in result.checks])
        for item in result.hypotheses:
            unique(item.missing_evidence)
            if not set(item.missing_evidence) <= HYPOTHESIS_MISSING[item.kind]:
                raise ValueError("Missing evidence does not match the hypothesis")
            if item.kind in EVENT_HYPOTHESES:
                eligible = {
                    identifier
                    for identifier, event in events.items()
                    if event["kind"] == EVENT_HYPOTHESES[item.kind]
                    and event["source_quality"] == "complete"
                    and (
                        event["kind"] != "bind_scope_changed"
                        or (
                            event["before"]["bind_scope"] is not None
                            and event["after"]["bind_scope"] is not None
                        )
                    )
                }
            elif item.kind == "declaration_runtime_gap":
                eligible = (
                    {"current"}
                    if any(
                        entry["compose_conflict"]
                        or entry["compose_relation"] == "declared_without_live_mapping"
                        for entry in entries
                    )
                    else set()
                )
            else:
                eligible = {"current"} if any(entry["reservation"] for entry in entries) else set()
            if not set(item.evidence_ids) <= eligible:
                raise ValueError("Hypothesis is not supported by the referenced observation")
        for item in result.unknowns:
            eligible = set(events) if item.kind in {"change_cause", "event_time"} else {"current"}
            if item.kind == "reservation_guarantee" and not any(e["reservation"] for e in entries):
                eligible = set()
            if not set(item.evidence_ids) <= eligible:
                raise ValueError("Unknown category does not match its evidence")
        for item in result.checks:
            eligible = {"scan"} if item.action == "scan_status" else {"current"}
            if item.action == "port_history":
                eligible = ids - {"current"}
            if not set(item.evidence_ids) <= eligible:
                raise ValueError("Check references do not match its purpose")
        return result.model_dump()
    except (ValidationError, ValueError, TypeError, KeyError, UnicodeError):
        raise AnalysisError(
            "invalid_output", "模型返回的分类或证据引用无效，本次结果未采用。", 502
        ) from None


def render_interpretation(selection, evidence):
    """Only application-owned templates and frozen evidence become display text."""
    result = validate_interpretation(json.dumps(selection), evidence)
    rules = baseline(evidence)
    if result["summary_kind"] == "current_occupancy":
        text = f"本机端口 {evidence['port']}：" + rules["conclusion"]["text"]
    else:
        chosen = set(result["evidence_ids"])
        text = f"本机端口 {evidence['port']} 的所选变化：" + " ".join(
            item["text"] for item in rules["observed"] if set(item["evidence_ids"]) <= chosen
        )
    return {
        "version": RENDER_VERSION,
        "summary": {"text": text, "evidence_ids": result["evidence_ids"]},
        "hypotheses": [
            {
                "text": HYPOTHESES[item["kind"]],
                "evidence_ids": item["evidence_ids"],
                "missing_evidence": [MISSING_EVIDENCE[kind] for kind in item["missing_evidence"]],
            }
            for item in result["hypotheses"]
        ],
        "unknowns": [
            {"text": UNKNOWNS[item["kind"]], "evidence_ids": item["evidence_ids"]}
            for item in result["unknowns"]
        ],
        "checks": [check(item["action"], *item["evidence_ids"]) for item in result["checks"]],
    }


def demo_selection(evidence):
    """Synthetic selection for the explicit local demo, without a model call."""
    kind = evidence["question_type"]
    ids = (
        ["current"]
        if kind == "current_occupancy"
        else [
            event["event_id"]
            for event in evidence["observation"]["events"]
            if event["kind"] in EVENT_HYPOTHESES.values()
        ][:8]
    )
    hypotheses = []
    if kind == "current_occupancy" and any(
        entry["compose_conflict"] or entry["compose_relation"] == "declared_without_live_mapping"
        for entry in evidence["observation"]["current"]["entries"]
    ):
        hypotheses = [
            {
                "kind": "declaration_runtime_gap",
                "evidence_ids": ["current"],
                "missing_evidence": ["runtime_configuration"],
            }
        ]
    elif kind == "recorded_changes" and ids:
        event = next(
            event for event in evidence["observation"]["events"] if event["event_id"] == ids[0]
        )
        hypothesis = next(key for key, value in EVENT_HYPOTHESES.items() if value == event["kind"])
        hypotheses = [
            {
                "kind": hypothesis,
                "evidence_ids": [ids[0]],
                "missing_evidence": [min(HYPOTHESIS_MISSING[hypothesis])],
            }
        ]
    return {
        "schema_version": 1,
        "summary_kind": kind,
        "evidence_ids": ids,
        "hypotheses": hypotheses,
        "unknowns": [
            {"kind": "application_health", "evidence_ids": ["current"]},
            {"kind": "external_reachability", "evidence_ids": ["current"]},
        ],
        "checks": [
            {"action": item["action"], "evidence_ids": item["evidence_ids"]}
            for item in baseline(evidence)["checks"]
        ],
    }
