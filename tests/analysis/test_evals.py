"""Offline synthetic evidence checks; no credentials or network requests."""
import json
import socket
from pathlib import Path

import pytest

from backend.analysis.baseline import baseline
from backend.analysis.evidence import AnalysisError, prepare_evidence
from backend.analysis.interpretation import demo_selection, render_interpretation, validate_interpretation

def validate_case_descriptions(case):
    for key in ("allowed_inferences", "forbidden_assertions", "next_step_goals"):
        values = case.get(key)
        assert isinstance(values, list) and 1 <= len(values) <= 8, (case.get("id"), key)
        assert all(
            isinstance(value, str) and value.strip() and len(value) <= 500 for value in values
        ), (case.get("id"), key)


def run_case(case):
    validate_case_descriptions(case)
    expected = case["expected"]
    try:
        evidence = prepare_evidence(
            case["observation"],
            core_version="0.8.4",
            include_history=case["include_history"],
            question_type=case["question_type"],
        )
    except AnalysisError as error:
        assert error.code == expected["error"], case["id"]
        return {
            "id": case["id"],
            "result": "passed",
            "model_eligible": False,
            "input_rejected": error.code,
        }
    assert expected["error"] is None, case["id"]
    rules = baseline(evidence)
    assert rules["conclusion"]["code"] == expected["baseline_code"], (
        case["id"],
        rules["conclusion"],
    )
    assert rules["ai"]["eligible"] is expected["model_eligible"], case["id"]
    assert 1 <= len(rules["checks"]) <= 3
    # Goals are explicit per scenario; one of the next useful checks must be retained.
    assert set(case["next_step_goals"]) & {check["action"] for check in rules["checks"]}, case["id"]
    for assertion in expected["facts"]:
        value = evidence
        for segment in assertion["path"]:
            value = value[segment]
        assert value == assertion["equals"], (case["id"], assertion["path"])
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert all(value not in encoded for value in expected["excluded"]), case["id"]
    assert "requires_hidden_access" not in encoded
    selection = None
    if rules["ai"]["eligible"]:
        selection = validate_interpretation(json.dumps(demo_selection(evidence)), evidence)
    if case["id"] == "A01-normal":
        return {
            "id": case["id"],
            "result": "passed",
            "model_eligible": rules["ai"]["eligible"],
            "comparison": {
                "label": "Synthetic output; not model quality evidence",
                "rules": rules,
                "test_interpretation": selection,
                "interpretation_view": render_interpretation(selection, evidence),
            },
        }
    return {"id": case["id"], "result": "passed", "model_eligible": rules["ai"]["eligible"]}


CORPUS = json.loads(Path(__file__).with_name("cases.json").read_text())


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_synthetic_evidence(case, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Synthetic evaluations must not open network connections")
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    assert run_case(case)["result"] == "passed"
