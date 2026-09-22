"""Regression boundaries for frozen local troubleshooting captures."""

import json
import sqlite3
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from backend.analysis.module import create_module
from backend.analysis.reports import APPLICATION_ID, ReportStore, owner_hash
from backend.analysis.routes import COOKIE
from backend.analysis.workbench import build_capture, compare_captures

ACTION = {"X-Port-Light-Analysis": "1"}
OWNER = "owner-0123456789abcdefghijklmnopqrstuv"


def batch_document(
    ports, *, capture_id="obs-boundary-1", protocol="all", conflict_ports=(), compose_state="ok"
):
    rows = []
    for port in ports:
        entries = []
        for entry_protocol in ("tcp", "udp") if protocol == "all" else (protocol,):
            conflict = entry_protocol == "tcp" and port in conflict_ports
            entries.append(
                {
                    "protocol": entry_protocol,
                    "status": "configured" if conflict else "free",
                    "listening": False,
                    "docker_mapped": False,
                    "docker_live": False,
                    "compose_declared": conflict,
                    "compose_relation": "declared_without_live_mapping"
                    if conflict
                    else "not_declared",
                    "compose_conflict": conflict,
                    "manual": False,
                    "reservation": False,
                    "bind": [],
                    "evidence_refs": [],
                }
            )
        rows.append(
            {
                "port": port,
                "identity": {"port": port, "protocol": protocol},
                "requires_hidden_access": False,
                "current": {
                    "overall_status": "configured" if port in conflict_ports else "free",
                    "entries": entries,
                },
                "relations": [],
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
        "ports": rows,
        "events": [],
    }


def scope(kind, ports, protocol="all"):
    if kind == "single_port":
        value = {"kind": kind, "port": ports[0]}
    else:
        value = {"kind": kind, "ports": ports}
    return {"kind": kind, "scope": value, "protocol": protocol, "history_hours": 6}


def capture(document, *, identifier, scope_requested, kind="triage"):
    return build_capture(
        document, identifier=identifier, kind=kind, scope_requested=scope_requested
    )


def test_same_capture_and_disabled_compose_never_become_resolved_findings():
    requested = scope("single_port", [65535], protocol="tcp")
    before = capture(
        batch_document([65535], protocol="tcp", conflict_ports={65535}),
        identifier="before",
        scope_requested=requested,
    )
    same = compare_captures(before, before, source_report_id="saved")
    assert {item["reasons"][0] for item in same["cannot_compare"]} == {"no_new_observation"}

    after = capture(
        batch_document(
            [65535],
            capture_id="obs-boundary-2",
            protocol="tcp",
            compose_state="disabled",
        ),
        identifier="after",
        scope_requested=requested,
    )
    comparison = compare_captures(before, after, source_report_id="saved")
    assert comparison["not_observed"] == []
    assert comparison["cannot_compare"][0]["reasons"] == ["source_disabled"]

    resolved = capture(
        batch_document([65535], capture_id="obs-boundary-3", protocol="tcp"),
        identifier="resolved",
        scope_requested=requested,
    )
    comparison = compare_captures(before, resolved, source_report_id="saved")
    item = comparison["not_observed"][0]
    assert item["after_evidence_ids"] == ["current:65535:tcp"]
    assert item["before_evidence_ids"]
    assert set(item["after_evidence_ids"]) <= set(resolved["facts"])


def test_not_observed_keeps_frozen_current_group_and_scan_evidence():
    requested = scope("selected_ports", [8080, 8081], protocol="tcp")
    relation = {
        "id": "plr1:compose_project:compare",
        "kind": "compose_project",
        "source": "compose",
        "protocol": "tcp",
    }
    before_document = batch_document([8080, 8081], protocol="tcp")
    for row in before_document["ports"]:
        row["relations"] = [relation]
        entry = row["current"]["entries"][0]
        entry["compose_declared"] = True
        entry["compose_relation"] = (
            "declared_without_live_mapping" if row["port"] == 8080 else "declared_and_live"
        )
    before = capture(before_document, identifier="group-before", scope_requested=requested)
    resolved_document = batch_document(
        [8080, 8081], capture_id="obs-boundary-group-2", protocol="tcp"
    )
    for row in resolved_document["ports"]:
        row["relations"] = [relation]
        entry = row["current"]["entries"][0]
        entry["compose_declared"] = True
        entry["compose_relation"] = "declared_and_live"
    resolved = capture(resolved_document, identifier="group-resolved", scope_requested=requested)
    comparison = compare_captures(before, resolved, source_report_id="saved")
    group_item = comparison["not_observed"][0]
    assert group_item["after_evidence_ids"] == ["current:8080:tcp", "current:8081:tcp"]
    assert set(group_item["after_evidence_ids"]) <= set(resolved["facts"])

    scan_scope = scope("single_port", [80], protocol="tcp")
    degraded_document = batch_document([80], protocol="tcp")
    degraded_document["scan"].update(ready=False, complete=False)
    degraded = capture(degraded_document, identifier="scan-before", scope_requested=scan_scope)
    healthy = capture(
        batch_document([80], capture_id="obs-boundary-scan-2", protocol="tcp"),
        identifier="scan-healthy",
        scope_requested=scan_scope,
    )
    comparison = compare_captures(degraded, healthy, source_report_id="saved")
    assert comparison["not_observed"][0]["after_evidence_ids"] == ["scan"]
    assert comparison["not_observed"][0]["after_evidence_ids"][0] in healthy["facts"]


def test_comparison_evidence_ids_belong_to_their_frozen_capture():
    requested = {
        "kind": "all_known",
        "scope": {"kind": "all_known"},
        "protocol": "tcp",
        "history_hours": 6,
    }
    before = capture(
        batch_document([80, 8080], protocol="tcp", conflict_ports={80, 8080}),
        identifier="evidence-before",
        scope_requested=requested,
    )
    current = capture(
        batch_document(
            [80, 443, 8080],
            capture_id="obs-boundary-evidence-2",
            protocol="tcp",
            conflict_ports={80, 443},
        ),
        identifier="evidence-current",
        scope_requested=requested,
    )
    comparison = compare_captures(before, current, source_report_id="saved")
    for category in ("not_observed", "persisting", "added"):
        assert comparison[category]
        for item in comparison[category]:
            assert set(item["before_evidence_ids"]) <= set(before["facts"])
            assert set(item["after_evidence_ids"]) <= set(current["facts"])
    added = next(
        item
        for item in comparison["added"]
        if item["resources"] == [{"port": 443, "protocol": "tcp"}]
    )
    assert added["before_evidence_ids"] == []


def test_recovered_observation_is_context_not_a_new_degradation():
    document = batch_document([80])
    document["events"] = [
        {
            "schema_version": 1,
            "event_id": "obs-boundary-1:observation_recovered",
            "observation_id": "obs-boundary-1",
            "observed_at": 1_700_000_100,
            "port": None,
            "protocol": "all",
            "kind": "observation_recovered",
            "before": {"quality": "degraded"},
            "after": {"quality": "complete"},
            "evidence_refs": [],
            "source_quality": "complete",
        }
    ]
    work = capture(
        document,
        identifier="recovered",
        scope_requested=scope("single_port", [80]),
        kind="changes",
    )
    assert "observation_degraded" not in {item["kind"] for item in work["problems"]}
    assert work["conclusion"] != "limited_coverage"


def test_explicit_project_subset_is_not_complete_in_rule_or_model_data():
    document = batch_document([8080, 8081])
    relation_id = "plr1:compose_project:partial"
    for port, row in zip((8080, 8081), document["ports"], strict=True):
        row["relations"] = [
            {
                "id": relation_id,
                "kind": "compose_project",
                "source": "compose",
                "protocol": "tcp",
            }
        ]
        tcp = next(entry for entry in row["current"]["entries"] if entry["protocol"] == "tcp")
        tcp["compose_declared"] = True
        tcp["compose_relation"] = (
            "declared_without_live_mapping" if port == 8080 else "declared_and_live"
        )
    work = capture(
        document,
        identifier="partial",
        scope_requested=scope("selected_ports", [8080, 8081]),
    )
    problem = next(
        item
        for item in work["problems"]
        if item["kind"] == "project_declaration_without_live_mapping"
    )
    assert problem["relation"]["group"] == {
        "observed_members": 2,
        "affected_members": 1,
        "complete": False,
    }
    model_problem = next(
        item for item in work["ai_preview"]["payload"]["problems"] if item["id"] == problem["id"]
    )
    assert model_problem["relation"]["group"]["complete"] is False


def test_v1_report_database_migration_keeps_legacy_single_port_report(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    path = state / "analysis.sqlite3"
    legacy = {
        "id": "legacy-report",
        "schema_version": 1,
        "evidence": {"port": 443},
        "result": {"status": "completed"},
    }
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE reports (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL,
            source_analysis_id TEXT NOT NULL, port INTEGER NOT NULL,
            created_at INTEGER NOT NULL, requires_hidden_access INTEGER NOT NULL,
            body TEXT NOT NULL, bytes INTEGER NOT NULL,
            UNIQUE(owner_hash, source_analysis_id)
        );
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, port INTEGER NOT NULL,
            status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        """
    )
    encoded = json.dumps(legacy, sort_keys=True)
    connection.execute(
        "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy-report", owner_hash(OWNER), "legacy-analysis", 443, 100, 0, encoded, len(encoded)),
    )
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    store = ReportStore(tmp_path)
    store.open()
    try:
        assert store.connection is not None
        assert store.get("legacy-report", OWNER) == legacy
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {
            row[0]
            for row in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"reports", "jobs", "workbench_reports", "workbench_jobs"} <= tables
    finally:
        store.close()


def host(tmp_path, state):
    module = None

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
        selection = body["selection"]
        if selection["kind"] == "known":
            requested = sorted(
                set(state["known_ports"]) | set(selection.get("additional_ports", []))
            )
        elif selection["kind"] == "ports":
            requested = list(selection["ports"])
        else:
            requested = list(range(selection["start"], selection["end"] + 1))
        state["batch_requests"].append({"selection": selection, "ports": requested})
        if state["revocation_plan"]:
            state["hidden_ports"].update(state["revocation_plan"].pop(0))
        visible = [port for port in requested if port not in state["hidden_ports"]]
        document = batch_document(
            visible,
            capture_id=state["capture_id"],
            protocol=body["protocol"],
            conflict_ports=state["conflict_ports"],
        )
        document["selection"] = {"kind": selection["kind"], "protocol": body["protocol"]}
        document["coverage"].update(
            requested_count=len(requested),
            observed_count=len(visible),
            complete=len(requested) == len(visible),
            limitations=[] if len(requested) == len(visible) else ["hidden_withheld"],
        )
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
    app.state.analysis_module = module
    return app


@pytest.fixture
def state():
    return {
        "known_ports": [80, 443],
        "hidden_ports": set(),
        "conflict_ports": {80, 443},
        "capture_id": "obs-http-1",
        "revocation_plan": [],
        "batch_requests": [],
    }


def create_report(client, ports=None):
    scope_value = (
        {"kind": "all_known"} if ports is None else {"kind": "selected_ports", "ports": ports}
    )
    created = client.post(
        "/analysis/api/workbench/captures",
        json={"kind": "triage", "scope": scope_value, "protocol": "all", "history_hours": 6},
        headers=ACTION,
    )
    assert created.status_code == 201, created.text
    saved = client.post(
        "/analysis/api/workbench/reports",
        json={"capture_id": created.json()["id"]},
        headers=ACTION,
    )
    assert saved.status_code == 201, saved.text
    return saved.json()


def test_new_captures_and_rechecks_preserve_saved_reports(
    tmp_path, monkeypatch, state
):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    with TestClient(host(tmp_path, state)) as client:
        report = create_report(client)
        created = client.post(
            "/analysis/api/workbench/captures",
            json={"kind": "triage", "scope": {"kind": "all_known"}, "protocol": "all"},
            headers=ACTION,
        )
        assert created.status_code == 201
        recheck = client.post(
            "/analysis/api/workbench/rechecks",
            json={"report_id": report["id"]},
            headers=ACTION,
        )
        assert recheck.status_code == 201
        assert client.get("/analysis/api/workbench/reports/" + report["id"]).status_code == 200
        assert (
            client.get("/analysis/api/workbench/reports/" + report["id"] + "/export").status_code
            == 200
        )


def test_recheck_rejects_resource_withdrawn_after_source_report_access(
    tmp_path, monkeypatch, state
):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    state["known_ports"] = [80, 9000]
    state["conflict_ports"] = {80, 9000}
    with TestClient(host(tmp_path, state)) as client:
        report = create_report(client, ports=[80, 9000])
        state["capture_id"] = "obs-http-2"
        # The saved report access check sees both ports.  The recheck batch loses
        # 9000 and must not create a smaller report with old comparison evidence.
        state["revocation_plan"] = [set(), {9000}]
        recheck = client.post(
            "/analysis/api/workbench/rechecks",
            json={"report_id": report["id"]},
            headers=ACTION,
        )
        assert recheck.status_code == 404
        assert "9000" not in recheck.text


def test_invalid_cookie_is_replaced_when_a_capture_is_created(tmp_path, monkeypatch, state):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    app = host(tmp_path, state)
    with TestClient(app) as client:
        client.cookies.set(COOKIE, "broken", path="/analysis")
        created = client.post(
            "/analysis/api/workbench/captures",
            json={"kind": "triage", "scope": {"kind": "all_known"}, "protocol": "all"},
            headers=ACTION,
        )
        assert created.status_code == 201
        assert "port_light_analysis_session=" in created.headers.get("set-cookie", "")
        assert (
            client.get("/analysis/api/workbench/captures/" + created.json()["id"]).status_code
            == 200
        )


def test_report_list_skips_hidden_page_to_find_visible_owned_report(tmp_path, monkeypatch, state):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    app = host(tmp_path, state)
    with TestClient(app) as client:
        store = app.state.analysis_module.state.report_store
        tick = [1_700_000_000]
        store.clock = lambda: tick[0]
        report_ids = []
        for index, port in enumerate(range(20_000, 20_021)):
            tick[0] += 1
            report_ids.append(
                store.save_workbench(
                    capture(
                        batch_document(
                            [port], capture_id=f"obs-page-{index}", conflict_ports={port}
                        ),
                        identifier=f"capture-page-{index}",
                        scope_requested=scope("selected_ports", [port]),
                    ),
                    OWNER,
                )["id"]
            )
        state["hidden_ports"] = set(range(20_001, 20_021))
        client.cookies.set(COOKIE, OWNER, path="/analysis")
        listed = client.get("/analysis/api/workbench/reports")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["reports"]] == [report_ids[0]]


def test_dense_dual_protocol_capture_save_read_and_recheck_stay_bounded(
    tmp_path, monkeypatch, state
):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    ports = [*range(1, 1024), 65535]
    state["known_ports"] = ports
    state["conflict_ports"] = {65535}
    with TestClient(host(tmp_path, state)) as client:
        created = client.post(
            "/analysis/api/workbench/captures",
            json={
                "kind": "triage",
                "scope": {"kind": "selected_ports", "ports": ports},
                "protocol": "all",
                "history_hours": 24,
            },
            headers=ACTION,
        )
        assert created.status_code == 201, created.text
        workbench = created.json()
        assert len(workbench["resources"]) == 2048
        assert "current:65535:tcp" in workbench["facts"]
        assert any(
            resource["port"] == 65535
            for problem in workbench["ai_preview"]["payload"]["problems"]
            for resource in problem["resources"]
        )
        assert len(state["batch_requests"]) == 1

        saved = client.post(
            "/analysis/api/workbench/reports",
            json={"capture_id": workbench["id"]},
            headers=ACTION,
        )
        assert saved.status_code == 201, saved.text
        assert len(saved.content) < 2 * 1024 * 1024
        report = saved.json()
        assert len(state["batch_requests"]) == 3
        assert client.get("/analysis/api/workbench/reports/" + report["id"]).status_code == 200
        assert len(state["batch_requests"]) == 5

        state["capture_id"] = "obs-http-2"
        rechecked = client.post(
            "/analysis/api/workbench/rechecks",
            json={"report_id": report["id"]},
            headers=ACTION,
        )
        assert rechecked.status_code == 201, rechecked.text
        assert rechecked.json()["comparison"]["persisting"]
        assert len(state["batch_requests"]) == 8
