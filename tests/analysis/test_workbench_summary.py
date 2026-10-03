import pytest

from backend.analysis.evidence import AnalysisError
from backend.analysis.workbench import build_capture, validate_recommendations
from tests.analysis.test_workbench import batch_document


def observed_capture(ports=(8080,)):
    batch = batch_document(ports=ports, protocol="tcp", conflict=False)
    for row in batch["ports"]:
        row["relations"] = []
        row["current"]["overall_status"] = "used"
        row["current"]["entries"][0].update(
            status="used",
            listening=True,
            compose_declared=False,
            compose_relation="not_declared",
        )
    return build_capture(
        batch,
        identifier="observed",
        kind="triage",
        scope_requested={
            "kind": "selected_ports",
            "scope": {"kind": "selected_ports", "ports": list(ports)},
            "protocol": "tcp",
            "history_hours": 6,
        },
    )


def test_observed_port_without_rule_problems_can_be_explained_by_ai():
    capture = observed_capture()
    assert capture["problems"] == []
    assert capture["ai_preview"]["eligible"] is True
    current = [
        fact
        for fact in capture["ai_preview"]["payload"]["facts"].values()
        if fact["kind"] == "current"
    ]
    assert len(current) == 1
    assert current[0]["data"]["listening"] is True
    assert capture["summary"]["listening_count"] == 1


def test_ordinary_port_input_is_bounded_and_discloses_omitted_resources():
    capture = observed_capture(range(64512, 65536))
    preview = capture["ai_preview"]
    assert preview["eligible"] is True
    assert preview["input_bytes"] <= preview["max_input_bytes"]
    resources = preview["payload"]["resource_summary"]
    assert resources["total_count"] == 1024
    assert 1 <= resources["sent_count"] <= 32
    assert resources["omitted_count"] == 1024 - resources["sent_count"]


def test_model_evidence_omits_uncitable_source_pointers_without_changing_saved_facts():
    batch = batch_document(ports=[8080], protocol="tcp", conflict=False)
    batch["ports"][0]["current"]["entries"][0]["evidence_refs"] = ["obs-1:port:8080:tcp:compose"]
    capture = build_capture(
        batch, identifier="fixture", kind="triage",
        scope_requested={"kind": "single_port", "scope": {"kind": "single_port", "port": 8080},
                         "protocol": "tcp", "history_hours": 6},
    )
    fact_id = "current:8080:tcp"
    original = capture["facts"][fact_id]
    sent = capture["ai_preview"]["payload"]["facts"][fact_id]
    assert original["data"]["evidence_refs"] == ["obs-1:port:8080:tcp:compose"]
    assert "evidence_refs" not in sent["data"]
    assert sent["resource"] == {"port": 8080, "protocol": "tcp"}
    assert sent["data"]["compose_declared"] is True
    assert sent["data"]["compose_relation"] == original["data"]["compose_relation"]


def test_empty_capture_explains_why_ai_has_no_port_data():
    capture = observed_capture(())
    assert capture["data_status"] == "empty"
    assert capture["ai_preview"]["eligible"] is False
    assert capture["ai_preview"]["reason"] == "no_observations"


def test_model_binding_category_does_not_claim_external_reachability():
    batch = batch_document(ports=[8080], protocol="tcp")
    batch["events"] = [{
        "event_id": "obs-2:binding:8080", "observation_id": "obs-2", "port": 8080,
        "protocol": "all", "kind": "bind_scope_changed", "observed_at": 1_700_000_100,
        "before": {"status": "used", "bind_scope": "localhost"},
        "after": {"status": "used", "bind_scope": "public"}, "source_quality": "complete",
    }]
    capture = build_capture(
        batch, identifier="fixture", kind="changes",
        scope_requested={"kind": "single_port", "scope": {"kind": "single_port", "port": 8080},
                         "protocol": "tcp", "history_hours": 6},
    )
    original = capture["facts"]["event:obs-2:binding:8080"]["data"]
    sent = capture["ai_preview"]["payload"]["facts"]["event:obs-2:binding:8080"]["data"]
    assert original["after"]["bind_scope"] == "public"
    assert sent["after"]["bind_scope"] == "wildcard_or_global_address"
    assert sent["before"]["bind_scope"] == "localhost"
    assert sent["after"]["status"] == original["after"]["status"]


def test_ai_conclusion_must_cite_sent_facts():
    capture = observed_capture()
    selected = {
        "schema_version": 1,
        "conclusion": {
            "text": "TCP 8080 is listening.",
            "evidence_ids": ["current:8080:tcp"],
        },
        "recommendations": [],
    }
    assert validate_recommendations(selected, capture) == []
    selected["conclusion"]["evidence_ids"] = ["invented-fact"]
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(selected, capture)


def test_ai_cannot_complete_without_a_conclusion():
    with pytest.raises(AnalysisError, match="invalid_output"):
        validate_recommendations(
            {"schema_version": 1, "recommendations": []}, observed_capture()
        )
