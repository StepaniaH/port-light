"""Offline synthetic evidence checks; no credentials or network requests."""
import json
import socket
from pathlib import Path

import pytest

from backend.analysis.baseline import baseline
from backend.analysis.evidence import AnalysisError, prepare_evidence
from backend.analysis.interpretation import demo_selection, validate_interpretation


def validate_case_goals(case):
    goals = case.get("next_step_goals")
    assert isinstance(goals, list) and goals and all(isinstance(goal, str) for goal in goals)


def run_case(case):
    validate_case_goals(case)
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
        return
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
    if rules["ai"]["eligible"]:
        validate_interpretation(json.dumps(demo_selection(evidence)), evidence)


CORPUS = json.loads(Path(__file__).with_name("cases.json").read_text())


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_synthetic_evidence(case, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Synthetic evaluations must not open network connections")
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    run_case(case)
