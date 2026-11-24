import secrets
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.analysis.module import create_module
from backend.analysis.routes import COOKIE
from tests.analysis.observation_fixture import observation, seed_legacy_report
from tests.analysis.test_workbench import batch_document


def host(data_dir, state):
    module = None

    @asynccontextmanager
    async def lifespan(_app):
        async with module.router.lifespan_context(module):
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"batch_port_observation": 1, "port_observation": 1}}

    @app.get("/api/observations/ports/{port}")
    def port(port: int, request: Request, include_hidden: bool = False, protocol: str = "all"):
        if state["hidden"] and (
            not include_hidden or request.headers.get("x-hidden-unlock") != state["unlock"]
        ):
            return JSONResponse({}, status_code=404)
        result = observation(port, hidden=state["hidden"], protocol=protocol)
        if state.get("degraded"):
            result["scan"].update(ready=False, complete=False, stale=True)
        return result

    @app.post("/api/observations/batch")
    async def batch(request: Request):
        body = await request.json()
        ports = body["selection"].get("ports", [8080])
        if state["hidden"] and request.headers.get("x-hidden-unlock") != state["unlock"]:
            ports = []
        document = batch_document(ports=ports, protocol=body["protocol"])
        document["selection"]["kind"] = body["selection"]["kind"]
        return document

    module = create_module(
        {
            "core_app": app,
            "core_version": "0.8.4",
            "data_dir": data_dir,
        }
    )
    app.mount("/analysis", module)
    app.state.analysis_module = module
    return app


@pytest.mark.parametrize("initially_hidden", [False, True])
def test_saved_legacy_reports_read_export_delete_and_access_controls(
    tmp_path, monkeypatch, initially_hidden
):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    state = {"hidden": initially_hidden, "unlock": secrets.token_urlsafe(24)}
    cookie = secrets.token_urlsafe(24)
    other_cookie = secrets.token_urlsafe(24)
    headers = {"X-Port-Light-Analysis": "1", "X-Hidden-Unlock": state["unlock"]}
    app = host(tmp_path, state)
    with TestClient(app) as client:
        assert client.post("/analysis/api/analysis/previews", json={"port": 8080}, headers=headers).status_code == 404
        assert client.post("/analysis/api/reports", json={}, headers=headers).status_code == 405

        store = app.state.analysis_module.state.report_store
        saved = seed_legacy_report(
            store, cookie, report_id="legacy-r1", source_analysis_id="legacy-a1", hidden=initially_hidden
        )
        second = seed_legacy_report(
            store, cookie, report_id="legacy-r2", source_analysis_id="legacy-a2", port=8081, created_at=1700000300
        )
        other = seed_legacy_report(
            store, other_cookie, report_id="legacy-other", source_analysis_id="legacy-a3"
        )

        assert client.get("/analysis/api/reports", headers=headers).json() == {"reports": []}
        path = "/analysis/api/reports/" + saved["id"]
        assert client.get(path, headers=headers).status_code == 404

        client.cookies.set(COOKIE, cookie, path="/analysis")
        assert [row["id"] for row in client.get("/analysis/api/reports", headers=headers).json()["reports"]] == [
            second["id"],
            saved["id"],
        ]
        assert client.get(path + "/export", headers=headers).json() == saved

        state["hidden"] = True
        assert client.get(path).status_code == 404
        assert client.get(path + "/export").status_code == 404
        assert client.get("/analysis/api/reports").json() == {"reports": []}
        assert client.get(path, headers=headers).json() == saved

        state["hidden"] = False
        assert client.get(path).json() == saved
        assert client.get(path + "/export").json() == saved
        state["hidden"] = True

    restarted_app = host(tmp_path, state)
    with TestClient(restarted_app) as client:
        client.cookies.set(COOKIE, cookie, path="/analysis")
        state["degraded"] = True
        listing = client.get("/analysis/api/reports", headers=headers).json()["reports"]
        assert [row["id"] for row in listing] == [second["id"], saved["id"]]
        assert client.get(path, headers=headers).json() == saved
        export = client.get(path + "/export", headers=headers)
        assert export.json() == saved and "attachment" in export.headers["content-disposition"]

        # Deletion requires CSRF action + same-origin and enforces owner isolation.
        assert client.delete(path).status_code == 403
        assert (
            client.delete(path, headers={**headers, "Origin": "https://example.invalid"}).status_code
            == 403
        )
        assert client.delete("/analysis/api/reports/" + other["id"], headers=headers).status_code == 404
        deleted = client.delete(path, headers=headers)
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": saved["id"]}
        assert client.get(path, headers=headers).status_code == 404
        assert client.delete(path, headers=headers).status_code == 404

        cleared = client.delete("/analysis/api/reports", headers=headers)
        assert cleared.status_code == 200
        assert cleared.json() == {"deleted_count": 1}
        assert client.get("/analysis/api/reports", headers=headers).json() == {"reports": []}

        # Another session's legacy report remains untouched.
        client.cookies.set(COOKIE, other_cookie, path="/analysis")
        assert [row["id"] for row in client.get("/analysis/api/reports", headers=headers).json()["reports"]] == [
            other["id"]
        ]

    raw = (tmp_path / "state/analysis.sqlite3").read_bytes()
    assert cookie.encode() not in raw and state["unlock"].encode() not in raw


def test_bad_database_keeps_captures_available_without_starting_an_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    calls = []

    async def unexpected_request(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Unavailable storage must prevent provider requests")

    monkeypatch.setattr("backend.analysis.routes.WorkbenchDemoGateway", lambda: unexpected_request)
    state = {"hidden": False, "unlock": secrets.token_urlsafe(24)}
    path = tmp_path / "state/analysis.sqlite3"
    path.parent.mkdir()
    original = b"Synthetic corrupt database; preserve this content"
    path.write_bytes(original)
    with TestClient(host(tmp_path, state)) as client:
        storage = client.get("/analysis/api/meta").json()["report_storage"]
        assert storage["available"] is False
        assert storage["used_workbench_reports"] == 0
        assert storage["used_workbench_bytes"] == 0
        headers = {"X-Port-Light-Analysis": "1"}
        capture = client.post(
            "/analysis/api/workbench/captures",
            json={"kind": "triage", "scope": {"kind": "single_port", "port": 8080}},
            headers=headers,
        )
        assert capture.status_code == 201
        started = client.post(
            "/analysis/api/workbench/captures/" + capture.json()["id"] + "/ai",
            json={"provider": "demo", "model": "synthetic-demo", "confirmed": True},
            headers=headers,
        )
        assert started.status_code == 503
        assert started.json()["error"]["code"] == "report_storage_unavailable"
    assert not calls
    assert path.read_bytes() == original
