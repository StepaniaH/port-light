import secrets
import time
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from tests.analysis.observation_fixture import observation
from backend.analysis.module import create_module
from backend.analysis.provider import DemoGateway
from backend.analysis.routes import COOKIE


def host(data_dir, state):
    @asynccontextmanager
    async def lifespan(_app):
        async with module.router.lifespan_context(module):
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"port_observation": 1}}

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

    module = create_module(
        {
            "core_app": app,
            "core_version": "0.8.4",
            "data_dir": data_dir,
        }
    )
    app.mount("/analysis", module)
    return app


def complete(client, headers):
    response = client.post(
        "/analysis/api/analysis/previews", json={"port": 8080}, headers=headers
    )
    assert response.status_code == 201
    identifier = response.json()["id"]
    path = "/analysis/api/analysis/" + identifier
    assert (
        client.post(
            path + "/start",
            json={"provider": "demo", "model": "synthetic-demo", "confirmed": True},
            headers=headers,
        ).status_code
        == 202
    )
    deadline = time.monotonic() + 2
    while (
        client.get(path, headers=headers).json()["status"] == "running"
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert client.get(path, headers=headers).json()["status"] == "completed"
    return identifier


@pytest.mark.parametrize("initially_hidden", [False, True])
def test_saved_reports_survive_restart_with_same_session_and_current_port_access(
    tmp_path, monkeypatch, initially_hidden
):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    state = {"hidden": initially_hidden, "unlock": secrets.token_urlsafe(24)}
    calls = []

    async def gateway(*args, **kwargs):
        calls.append(1)
        return await DemoGateway()(*args, **kwargs)

    monkeypatch.setattr("backend.analysis.routes.DemoGateway", lambda: gateway)
    headers = {"X-Port-Light-Analysis": "1", "X-Hidden-Unlock": state["unlock"]}
    with TestClient(host(tmp_path, state)) as client:
        identifier = complete(client, headers)
        assert client.get("/analysis/api/reports", headers=headers).json() == {"reports": []}
        invalid = client.post(
            "/analysis/api/reports",
            json={"completed_analysis_id": identifier, "interpretation": {}},
            headers=headers,
        )
        assert invalid.status_code == 422
        response = client.post(
            "/analysis/api/reports", json={"completed_analysis_id": identifier}, headers=headers
        )
        assert response.status_code == 201
        saved = response.json()
        restored_analysis = client.get(
            "/analysis/api/analysis/" + identifier, headers=headers
        ).json()
        assert restored_analysis["report_id"] == saved["id"]
        assert saved["requires_hidden_access"] is initially_hidden
        assert "requires_hidden_access" not in str(saved["evidence"])
        duplicate = client.post(
            "/analysis/api/reports", json={"completed_analysis_id": identifier}, headers=headers
        )
        assert duplicate.json()["id"] == saved["id"]
        path = "/analysis/api/reports/" + saved["id"]
        assert client.get(path + "/export", headers=headers).json() == saved
        cookie = client.cookies.get(COOKIE)
        assert state["unlock"] not in response.text and cookie not in response.text
        client.cookies.clear()
        assert client.get("/analysis/api/reports", headers=headers).json() == {"reports": []}
        assert client.get(path, headers=headers).status_code == 404
        client.cookies.set(COOKIE, cookie, path="/analysis")
        state["hidden"] = True
        assert client.get(path).status_code == 404
        assert client.get(path + "/export").status_code == 404
        assert client.get("/analysis/api/reports").json() == {"reports": []}
        assert client.get(path, headers=headers).json() == saved
        state["hidden"] = False
        assert client.get(path).json() == saved
        assert client.get(path + "/export").json() == saved
        state["hidden"] = True

    with TestClient(host(tmp_path, state)) as client:
        client.cookies.set(COOKIE, cookie, path="/analysis")
        state["degraded"] = True
        listing = client.get("/analysis/api/reports", headers=headers).json()["reports"]
        assert [row["id"] for row in listing] == [saved["id"]]
        assert client.get(path, headers=headers).json() == saved
        export = client.get(path + "/export", headers=headers)
        assert export.json() == saved and "attachment" in export.headers["content-disposition"]
        receipt = client.get("/analysis/api/analysis/" + identifier, headers=headers).json()
        assert receipt["result_available"] is False
        assert len(calls) == 1
    raw = (tmp_path / "state/analysis.sqlite3").read_bytes()
    assert cookie.encode() not in raw and state["unlock"].encode() not in raw


def test_bad_database_keeps_facts_available_without_starting_an_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    calls = []

    async def unexpected_request(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Unavailable storage must prevent provider requests")

    monkeypatch.setattr("backend.analysis.routes.DemoGateway", lambda: unexpected_request)
    state = {"hidden": False, "unlock": secrets.token_urlsafe(24)}
    path = tmp_path / "state/analysis.sqlite3"
    path.parent.mkdir()
    original = b"Synthetic corrupt database; preserve this content"
    path.write_bytes(original)
    with TestClient(host(tmp_path, state)) as client:
        assert client.get("/analysis/api/meta").json()["report_storage"]["available"] is False
        headers = {"X-Port-Light-Analysis": "1"}
        preview = client.post(
            "/analysis/api/analysis/previews", json={"port": 8080}, headers=headers
        )
        assert preview.status_code == 201
        started = client.post(
            "/analysis/api/analysis/" + preview.json()["id"] + "/start",
            json={"provider": "demo", "model": "synthetic-demo", "confirmed": True},
            headers=headers,
        )
        assert started.status_code == 503
    assert not calls
    assert path.read_bytes() == original
