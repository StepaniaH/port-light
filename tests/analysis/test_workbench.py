import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from backend.analysis.evidence import AnalysisError
from backend.analysis.module import create_module
from backend.analysis.workbench import (
    build_capture,
    compare_captures,
    validate_recommendations,
)

ACTION = {"X-Port-Light-Analysis": "1"}


def batch_document(
    *, ports, capture_id="obs-test-1", protocol="all", compose_state="ok", conflict=True
):
    entries = []
    for port in ports:
        selected = ("tcp", "udp") if protocol == "all" else (protocol,)
        current_entries = []
        relations = []
        for item_protocol in selected:
            compose = item_protocol == "tcp"
            current_entries.append(
                {
                    "protocol": item_protocol,
                    "status": "configured" if compose else "free",
                    "listening": False,
                    "docker_mapped": False,
                    "docker_live": False,
                    "compose_declared": compose,
                    "compose_relation": "declared_without_live_mapping"
                    if compose
                    else "not_declared",
                    "compose_conflict": conflict and compose,
                    "manual": False,
                    "reservation": False,
                    "bind": [],
                    "evidence_refs": [],
                }
            )
            if compose:
                relations.append(
                    {
                        "id": "plr1:compose_project:fixture",
                        "kind": "compose_project",
                        "source": "compose",
                        "protocol": item_protocol,
                    }
                )
        entries.append(
            {
                "port": port,
                "identity": {"port": port, "protocol": protocol},
                "requires_hidden_access": False,
                "current": {"overall_status": "configured", "entries": current_entries},
                "relations": relations,
                "evidence_refs": [],
            }
        )
    return {
        "schema_version": 1,
        "scope": {"kind": "local_hub"},
        "capture_id": capture_id,
        "captured_at": 1_700_000_100,
        "core_version": "0.8.4",
        "selection": {"kind": "ports", "protocol": protocol},
        "coverage": {
            "requested_count": len(ports),
            "observed_count": len(ports),
            "complete": True,
            "limitations": [],
        },
        "scan": {
            "ready": True,
            "complete": True,
            "stale": False,
            "sources": [
                {"name": "compose", "state": compose_state, "observed_at": 1_700_000_100},
                {"name": "listen", "state": "ok", "observed_at": 1_700_000_100},
                {"name": "docker", "state": "ok", "observed_at": 1_700_000_100},
            ],
        },
        "event_coverage": {
            "state": "available",
            "truncated": False,
            "window_hours": 6,
            "boundary": "capture_sequence",
            "time_resolution": "seconds",
        },
        "ports": entries,
        "events": [],
    }


def host(tmp_path, state):
    @asynccontextmanager
    async def lifespan(_app):
        async with module.router.lifespan_context(module):
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"batch_port_observation": 1}}

    @app.post("/api/observations/batch")
    async def batch(request: Request):
        body = await request.json()
        selection, protocol = body["selection"], body["protocol"]
        if selection["kind"] == "known":
            ports = [] if state["hidden"] else list(state["ports"])
        elif selection["kind"] == "ports":
            ports = [port for port in selection["ports"] if not state["hidden"]]
        else:
            ports = list(range(selection["start"], selection["end"] + 1))
        document = batch_document(
            ports=ports,
            capture_id=state["capture_id"],
            protocol=protocol,
            compose_state=state["compose_state"],
            conflict=state["conflict"],
        )
        document["selection"] = {"kind": selection["kind"], "protocol": protocol}
        document["coverage"]["requested_count"] = (
            len(state["ports"]) if selection["kind"] == "known" else len(ports)
        )
        document["coverage"]["observed_count"] = len(ports)
        if state["hidden"]:
            document["coverage"].update(complete=False, limitations=["hidden_withheld"])
        return document

    module = create_module(
        {
            "module_api": 1,
            "core_app": app,
            "core_version": "0.8.4",
            "data_dir": tmp_path,
        }
    )
    app.mount("/analysis", module)
    return app


def test_workbench_capture_save_ai_revision_recheck_and_access(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    state = {
        "ports": [8080, 65535],
        "capture_id": "obs-test-1",
        "hidden": False,
        "compose_state": "ok",
        "conflict": True,
    }
    with TestClient(host(tmp_path, state)) as client:
        created = client.post(
            "/analysis/api/workbench/captures",
            json={
                "kind": "triage",
                "scope": {"kind": "all_known"},
                "protocol": "all",
                "history_hours": 6,
            },
            headers=ACTION,
        )
        assert created.status_code == 201
        capture = created.json()
        assert capture["scope_requested"] == {
            "kind": "all_known",
            "scope": {"kind": "all_known"},
            "protocol": "all",
            "history_hours": 6,
        }
        assert {item["port"] for item in capture["resources"]} == {8080, 65535}
        assert capture["ai_preview"]["input_bytes"] <= capture["ai_preview"]["max_input_bytes"]

        baseline = client.post(
            "/analysis/api/workbench/reports",
            json={"capture_id": capture["id"]},
            headers=ACTION,
        )
        assert baseline.status_code == 201
        assert baseline.json()["result_revision"] == "rules-v1"
        assert (
            client.post(
                "/analysis/api/workbench/captures/" + capture["id"] + "/ai",
                json={"provider": "demo", "model": "synthetic-demo", "confirmed": False},
                headers=ACTION,
            ).status_code
            == 409
        )
        started = client.post(
            "/analysis/api/workbench/captures/" + capture["id"] + "/ai",
            json={"provider": "demo", "model": "synthetic-demo", "confirmed": True},
            headers=ACTION,
        )
        assert started.status_code == 200
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = client.get("/analysis/api/workbench/captures/" + capture["id"]).json()
            if current["status"] != "running":
                break
            time.sleep(0.01)
        assert current["ai"]["status"] == "completed"
        enhanced = client.post(
            "/analysis/api/workbench/reports",
            json={"capture_id": capture["id"]},
            headers=ACTION,
        )
        assert enhanced.status_code == 201
        assert enhanced.json()["id"] != baseline.json()["id"]
        assert enhanced.json()["result_revision"].startswith("ai-")

        state["capture_id"] = "obs-test-2"
        recheck = client.post(
            "/analysis/api/workbench/rechecks",
            json={"report_id": baseline.json()["id"]},
            headers=ACTION,
        )
        assert recheck.status_code == 201
        assert recheck.json()["comparison"]["persisting"]

        state["hidden"] = True
        assert (
            client.get("/analysis/api/workbench/reports/" + baseline.json()["id"]).status_code
            == 404
        )
        assert client.get("/analysis/api/workbench/reports").json() == {
            "reports": [],
            "next_cursor": None,
        }


def test_workbench_keeps_high_protocol_resources_and_refuses_forged_model_references():
    batch = batch_document(ports=list(range(64512, 65536)), protocol="all")
    capture = build_capture(
        batch,
        identifier="fixture-capture",
        kind="triage",
        scope_requested={
            "kind": "port_range",
            "scope": {"kind": "port_range", "start": 64512, "end": 65535},
            "protocol": "all",
            "history_hours": 6,
        },
    )
    assert len(capture["resources"]) == 2048
    assert {item["protocol"] for item in capture["resources"] if item["port"] == 65535} == {
        "tcp",
        "udp",
    }
    bad = {
        "schema_version": 1,
        "recommendations": [
            {
                "problem_id": "invented",
                "evidence_ids": ["scan"],
                "action": "scan_status",
                "relation": "source_quality",
            }
        ],
    }
    try:
        validate_recommendations(json.dumps(bad), capture)
    except AnalysisError as error:
        assert error.code == "invalid_output"
    else:
        raise AssertionError("forged model reference was accepted")


def test_recheck_requires_compose_observation_and_never_resolves_same_capture():
    scope = {
        "kind": "single_port",
        "scope": {"kind": "single_port", "port": 65535},
        "protocol": "tcp",
        "history_hours": 6,
    }
    first = build_capture(
        batch_document(ports=[65535], protocol="tcp", compose_state="ok", conflict=True),
        identifier="first",
        kind="triage",
        scope_requested=scope,
    )
    disabled = build_capture(
        batch_document(
            ports=[65535],
            capture_id="obs-test-2",
            protocol="tcp",
            compose_state="disabled",
            conflict=False,
        ),
        identifier="second",
        kind="triage",
        scope_requested=scope,
    )
    comparison = compare_captures(first, disabled, source_report_id="report")
    assert comparison["not_observed"] == []
    assert comparison["cannot_compare"][0]["reasons"] == ["source_disabled"]
    same = compare_captures(first, first, source_report_id="report")
    assert {item["reasons"][0] for item in same["cannot_compare"]} == {"no_new_observation"}


def test_overlapping_compose_declarations_do_not_repeat_project_mapping_gaps():
    scope = {
        "kind": "selected_ports",
        "scope": {"kind": "selected_ports", "ports": [8081, 65535]},
        "protocol": "tcp",
        "history_hours": 6,
    }
    project_a = {
        "id": "plr1:compose_project:fixture-a",
        "kind": "compose_project",
        "source": "compose",
        "protocol": "tcp",
    }
    project_b = {**project_a, "id": "plr1:compose_project:fixture-b"}
    conflict_only = batch_document(ports=[65535], protocol="tcp", conflict=True)
    conflict_only["ports"][0]["relations"] = [project_a, project_b]
    conflict_capture = build_capture(
        conflict_only,
        identifier="conflict-only",
        kind="triage",
        scope_requested={
            **scope,
            "scope": {"kind": "selected_ports", "ports": [65535]},
        },
    )
    assert [item["kind"] for item in conflict_capture["problems"]] == [
        "overlapping_compose_bindings",
        "project_declaration_without_live_mapping",
        "project_declaration_without_live_mapping",
    ]
    assert [item["kind"] for item in conflict_capture["priority_queue"]] == [
        "overlapping_compose_bindings"
    ]
    assert (
        conflict_capture["ai_preview"]["payload"]["problems"][0]["id"]
        == conflict_capture["priority_queue"][0]["id"]
    )

    mixed = batch_document(ports=[65535, 8081], protocol="tcp", conflict=True)
    by_port = {row["port"]: row for row in mixed["ports"]}
    by_port[65535]["relations"] = [project_a, project_b]
    by_port[8081]["relations"] = [project_a]
    by_port[8081]["current"]["entries"][0]["compose_conflict"] = False
    mixed_capture = build_capture(mixed, identifier="mixed", kind="triage", scope_requested=scope)
    gaps = [
        item
        for item in mixed_capture["problems"]
        if item["kind"] == "project_declaration_without_live_mapping"
    ]
    assert len(gaps) == 2
    gap = next(item for item in gaps if len(item["resources"]) == 2)
    assert gap["resources"] == [
        {"port": 8081, "protocol": "tcp"},
        {"port": 65535, "protocol": "tcp"},
    ]
    assert gap["relation"]["group"] == {
        "observed_members": 2,
        "affected_members": 2,
        "complete": False,
    }
    assert [item["kind"] for item in mixed_capture["priority_queue"]] == [
        "overlapping_compose_bindings",
        "project_declaration_without_live_mapping",
    ]
