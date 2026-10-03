import asyncio
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


def test_analysis_validation_session_isolation_and_lifespan(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    lifecycle = []
    requests = []

    @asynccontextmanager
    async def lifespan(app):
        lifecycle.append("started")
        async with module.router.lifespan_context(module):
            yield
        lifecycle.append("stopped")

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/observations/ports/{port}")
    def port(port: int, request: Request):
        requests.append(request.headers.get("authorization"))
        if port == 9000:
            return JSONResponse({}, status_code=404)
        source = observation(port)
        source["history"]["state"] = "unavailable"
        return source

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"port_observation": 1}}

    @app.get("/api/ports/{port}/history")
    def history(port: int):
        return JSONResponse({}, status_code=503)

    module = create_module(
        {
            "core_app": app,
            "core_version": "0.8.4",
            "data_dir": tmp_path,
        }
    )
    app.mount("/analysis", module)
    headers = {
        "X-Port-Light-Analysis": "1",
        "Authorization": "Basic fixture-auth",
        "Origin": "http://testserver",
    }
    with TestClient(app) as client:
        assert lifecycle == ["started"]
        assert (
            client.post("/analysis/api/analysis/previews", json={"port": 8080}).status_code == 403
        )
        denied = client.post(
            "/analysis/api/analysis/previews",
            json={"port": 8080},
            headers={**headers, "Origin": "https://example.invalid"},
        )
        assert denied.status_code == 403
        for body in ({"port": True}, {"port": 70000}, {"port": 8080, "api_key": "fixture-secret"}):
            invalid = client.post("/analysis/api/analysis/previews", json=body, headers=headers)
            assert invalid.status_code == 422 and "fixture-secret" not in invalid.text
        assert (
            client.post(
                "/analysis/api/analysis/previews",
                content=b"x" * 4097,
                headers={**headers, "Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/analysis/api/analysis/previews", json={"port": 9000}, headers=headers
            ).status_code
            == 404
        )
        preview = client.post(
            "/analysis/api/analysis/previews", json={"port": 8080}, headers=headers
        )
        assert preview.status_code == 201
        assert (
            "HttpOnly" in preview.headers["set-cookie"]
            and "SameSite=strict" in preview.headers["set-cookie"]
        )
        assert requests[-1] == "Basic fixture-auth"
        identifier = preview.json()["id"]
        assert any("历史未启用" in value for value in preview.json()["evidence"]["limitations"])
        path = f"/analysis/api/analysis/{identifier}"
        assert client.get(path + "/report").status_code == 409
        session = client.cookies.get(COOKIE)
        client.cookies.clear()
        assert client.get(path).status_code == 404
        assert client.get(path + "/report").status_code == 404
        client.cookies.set(COOKIE, session)
        for _ in range(2):
            started = client.post(
                path + "/start",
                json={"provider": "demo", "model": "synthetic-demo", "confirmed": True},
                headers=headers,
            )
            assert started.status_code == 202
        for _ in range(50):
            status = client.get(path).json()
            if status["status"] != "running":
                break
            time.sleep(0.01)
        assert status["status"] == "completed"
        report = client.get(path + "/report")
        assert report.status_code == 200
        assert report.json()["mode"] == "demo"
        assert "attachment" in report.headers["content-disposition"]
        assert "fixture-auth" not in report.text and session not in report.text
    assert lifecycle == ["started", "stopped"]


@pytest.mark.parametrize("operation", ["read", "start", "report", "cancel"])
def test_frozen_evidence_requires_current_port_access(operation, tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    restricted = {"enabled": False}
    unlock = secrets.token_urlsafe(24)
    calls = []

    async def gateway(*args, **kwargs):
        calls.append(1)
        if operation == "cancel":
            await asyncio.Event().wait()
        return await DemoGateway()(*args, **kwargs)

    monkeypatch.setattr("backend.analysis.routes.DemoGateway", lambda: gateway)

    @asynccontextmanager
    async def lifespan(_app):
        async with module.router.lifespan_context(module):
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/observations/ports/{port}")
    def detail(port: int, request: Request, include_hidden: bool = False):
        if restricted["enabled"] and (
            request.headers.get("x-hidden-unlock") != unlock or not include_hidden
        ):
            return JSONResponse({}, status_code=404)
        return observation(port, hidden=restricted["enabled"])

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"port_observation": 1}}

    module = create_module(
        {
            "core_app": app,
            "core_version": "0.8.4",
            "data_dir": tmp_path,
        }
    )
    app.mount("/analysis", module)
    headers = {"X-Port-Light-Analysis": "1"}
    start_body = {"provider": "demo", "model": "synthetic-demo", "confirmed": True}
    with TestClient(app) as client:
        preview = client.post(
            "/analysis/api/analysis/previews",
            json={"port": 8080, "include_history": False},
            headers=headers,
        )
        assert preview.status_code == 201
        path = "/analysis/api/analysis/" + preview.json()["id"]
        if operation in {"report", "cancel"}:
            assert client.post(path + "/start", json=start_body, headers=headers).status_code == 202
            if operation == "report":
                deadline = time.monotonic() + 2
                while (
                    client.get(path).json()["status"] == "running" and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                assert client.get(path).json()["status"] == "completed"
        restricted["enabled"] = True
        if operation in {"read", "report"}:
            denied = client.get(path + ("/report" if operation == "report" else ""))
        else:
            denied = client.post(path + "/" + operation, json=start_body, headers=headers)
        assert denied.status_code == 404
        assert "evidence" not in denied.json() and "interpretation" not in denied.json()
        allowed = client.get(path, headers={"X-Hidden-Unlock": unlock})
        assert allowed.status_code == 200
        if operation == "start":
            assert allowed.json()["status"] == "preview" and not calls
        if operation == "cancel":
            assert allowed.json()["status"] == "cancelled"
        assert unlock not in allowed.text
