"""Analysis analysis routes; public core authentication remains in control."""

import asyncio
import re
import secrets
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .analysis import Analysis, report_document
from .evidence import AnalysisError, read_evidence, require_port_access
from .provider import ChatGateway, DemoGateway
from .settings import (
    MAX_CONNECTIONS, AIConnectionInput, AIConnectionTestInput, AIProfileRevisionInput,
    AIProfileSelectionInput, AISettingsEditInput, AISettingsInput, BYOKStore, empty_profile,
)
from .workbench import AttemptGate, Workbench, WorkbenchDemoGateway, WorkbenchGateway
from .workbench_routes import workbench_router

COOKIE = "port_light_analysis_session"


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    port: int = Field(ge=1, le=65535)
    include_history: bool = True
    protocol: Literal["tcp", "udp", "all"] = "all"
    question_type: Literal["current_occupancy", "recorded_changes"] = "current_occupancy"
    max_records: int = Field(default=20, ge=1, le=20)


class SaveReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completed_analysis_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,64}$")


def owner(request):
    value = request.cookies.get(COOKIE, "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{32,64}", value) else ""


def check_action(request):
    if request.headers.get("x-port-light-analysis") != "1":
        raise AnalysisError("invalid_action", "请从分析页面发起操作。", 403)
    origin = request.headers.get("origin")
    if (
        origin and origin != str(request.base_url.replace(path="/")).rstrip("/")
    ) or request.headers.get("sec-fetch-site") == "cross-site":
        raise AnalysisError("invalid_origin", "该操作必须从当前 Port-Light 页面发起。", 403)


async def read_body(request, schema):
    check_action(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise AnalysisError("invalid_input", "请求需要 JSON 数据。", 415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise AnalysisError("invalid_input", "请求数据过大。", 413)
    try:
        return schema.model_validate_json(body)
    except (ValidationError, ValueError):
        # Do not echo request values: malformed requests may accidentally contain a key.
        raise AnalysisError("invalid_input", "请检查端口、模型和请求字段。", 422) from None


async def read_empty_body(request):
    check_action(request)
    async for chunk in request.stream():
        if chunk:
            raise AnalysisError("invalid_input", "该操作不接受请求数据。", 422)


def failure(error):
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}}, status_code=error.status
    )


def analysis_router(
    app,
    core_version,
    *,
    reports,
    settings_store: BYOKStore,
    settings_readonly,
    demo=False,
    gateway=None,
    providers=None,
):
    providers = providers or {}
    attempt_gate = AttemptGate()
    connection_tester = ChatGateway(providers=providers)
    analysis = Analysis(
        gateway or (DemoGateway() if demo else ChatGateway(providers=providers)),
        demo=demo,
        providers=providers,
        reports=reports,
        attempt_gate=attempt_gate,
    )
    workbench = Workbench(
        WorkbenchDemoGateway() if demo else WorkbenchGateway(providers=providers),
        demo=demo,
        providers=providers,
        reports=reports,
        attempt_gate=attempt_gate,
    )

    @asynccontextmanager
    async def lifespan(_app):
        reports.open()
        cleanup = asyncio.create_task(analysis.reap(), name="port-light-analysis-expiry")
        workbench_cleanup = asyncio.create_task(
            workbench.reap(), name="port-light-workbench-expiry"
        )
        try:
            yield
        finally:
            cleanup.cancel()
            workbench_cleanup.cancel()
            await asyncio.gather(cleanup, workbench_cleanup, return_exceptions=True)
            await analysis.close()
            await workbench.close()
            reports.close()

    router = APIRouter(prefix="/api", tags=["analysis"], lifespan=lifespan)

    allowed_providers = {"demo"} if demo else set(providers)

    def readonly():
        try:
            # Only an exact boolean False from the current host unlocks writes.
            # Missing callbacks, exceptions, and non-booleans are all read-only.
            return settings_readonly() is not False
        except Exception:  # noqa: BLE001 - An unavailable host setting locks writes.
            return True

    def provider_rows():
        return [
            {
                "id": identifier,
                "name": config["name"],
                **({"base_url": config["endpoint"].removesuffix("/chat/completions")}
                   if config.get("endpoint") else {}),
                **({"notice": config["notice"]} if config.get("notice") else {}),
            }
            for identifier, config in providers.items()
        ]

    def settings_document():
        connections = settings_store.load_connections()
        profile = connections.active
        return {
            "schema_version": 1,
            "analysis_mode": "demo" if demo else "byok",
            "readonly": readonly(),
            "enabled": True,
            "capabilities": {
                "byok_port_analysis": True,
            },
            "providers": provider_rows(),
            "ai": connections.public_profile(profile) if profile is not None else empty_profile(),
            "connections": connections.public_rows(),
            "active_connection_id": connections.active_id,
            "connections_limit": MAX_CONNECTIONS,
        }

    def profile_options(values, request):
        return {
            "provider": values.provider,
            "model": values.model,
            "supplied_key": request.headers.get("x-port-light-model-key"),
            "allowed_providers": allowed_providers,
            "demo": demo,
            "base_url": values.base_url,
            "token_parameter": values.token_parameter,
            "json_mode": values.json_mode,
        }

    def require_settings_writable():
        if readonly():
            raise AnalysisError("settings_readonly", "当前 Hub 设置为只读。", 403)

    def resolve_connection(values: AIConnectionInput, request: Request):
        if values.saved:
            profile = settings_store.resolve(
                values.config_revision,
                allowed_providers=allowed_providers,
                demo=demo,
            )
            return profile.provider, profile.model, profile.key, profile.connection()
        if values.provider == "custom" and not providers.get("custom", {}).get("endpoint"):
            raise AnalysisError("configuration_changed", "请先在 AI 设置中保存 API 地址。", 409)
        key = request.headers.get("x-port-light-model-key", "")
        settings_store.require_safe_model(values.model, key, check_saved_keys=not demo)
        return values.provider, values.model, key, None

    @router.get("/settings")
    async def settings():
        try:
            return settings_document()
        except AnalysisError as error:
            return failure(error)

    @router.put("/settings/ai")
    async def save_settings(request: Request):
        try:
            values = await read_body(request, AISettingsInput)
            require_settings_writable()
            settings_store.save(**profile_options(values, request))
            return settings_document()
        except AnalysisError as error:
            return failure(error)

    @router.post("/settings/ai/connections")
    async def create_connection(request: Request):
        try:
            values = await read_body(request, AISettingsInput)
            require_settings_writable()
            profile = settings_store.save_connection(**profile_options(values, request))
            return JSONResponse({**settings_document(), "saved_connection_id": profile.identifier}, status_code=201)
        except AnalysisError as error:
            return failure(error)

    @router.put("/settings/ai/connections/{identifier}")
    async def update_connection(identifier: str, request: Request):
        try:
            values = await read_body(request, AISettingsEditInput)
            require_settings_writable()
            profile = settings_store.save_connection(
                identifier=identifier, config_revision=values.config_revision, **profile_options(values, request),
            )
            return {**settings_document(), "saved_connection_id": profile.identifier}
        except AnalysisError as error:
            return failure(error)

    @router.delete("/settings/ai/connections/{identifier}")
    async def delete_connection(identifier: str, request: Request):
        try:
            values = await read_body(request, AIProfileRevisionInput)
            require_settings_writable()
            settings_store.delete_connection(identifier, values.config_revision)
            return settings_document()
        except AnalysisError as error:
            return failure(error)

    @router.post("/settings/ai/active")
    async def activate_connection(request: Request):
        try:
            values = await read_body(request, AIProfileSelectionInput)
            require_settings_writable()
            settings_store.activate(values.profile_id, values.config_revision,
                                    allowed_providers=allowed_providers, demo=demo)
            return settings_document()
        except AnalysisError as error:
            return failure(error)

    @router.delete("/settings/ai")
    async def clear_settings(request: Request):
        try:
            await read_empty_body(request)
            require_settings_writable()
            settings_store.clear()
            return settings_document()
        except AnalysisError as error:
            return failure(error)

    @router.post("/settings/ai/test")
    async def test_settings_connection(request: Request):
        try:
            values = await read_body(request, AIConnectionTestInput)
            if values.confirmed is not True:
                raise AnalysisError(
                    "confirmation_required", "请确认本次可能计费的连接测试。", 409
                )
            if demo:
                raise AnalysisError("unsupported_provider", "演示模式不连接模型服务。", 422)
            profile = settings_store.resolve_test(
                values,
                request.headers.get("x-port-light-model-key"),
                allowed_providers=allowed_providers,
            )
            gate_id = "settings:" + secrets.token_urlsafe(16)
            session = owner(request) or "settings:anonymous"
            attempt_gate.acquire(gate_id, session)
            try:
                await connection_tester.probe(
                    profile.provider, profile.model, profile.key, session_id=gate_id,
                    **({"connection": profile.connection()} if profile.connection() else {}),
                )
            finally:
                attempt_gate.release(gate_id)
            return {"status": "connected", "config_revision": None if values.draft else profile.revision}
        except AnalysisError as error:
            return failure(error)

    async def readable_snapshot(identifier, request):
        snapshot = analysis.get(identifier, owner(request))
        port = snapshot["evidence"]["port"] if "evidence" in snapshot else snapshot["port"]
        await require_port_access(app, request, port)
        return snapshot

    @router.post("/analysis/previews")
    async def preview(request: Request):
        try:
            values = await read_body(request, PreviewInput)
            if values.question_type == "recorded_changes" and not values.include_history:
                raise AnalysisError("invalid_input", "解释变化需要选择发送历史与事件。", 422)
            session = owner(request) or secrets.token_urlsafe(24)
            evidence, requires_hidden_access = await read_evidence(
                app,
                request,
                values.port,
                values.include_history,
                core_version,
                values.protocol,
                values.question_type,
                values.max_records,
            )
            response = JSONResponse(
                analysis.preview(evidence, session, requires_hidden_access=requires_hidden_access),
                status_code=201,
            )
            response.set_cookie(
                COOKIE,
                session,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path="/analysis",
            )
            return response
        except AnalysisError as error:
            return failure(error)

    @router.post("/analysis/{identifier}/start")
    async def start(identifier: str, request: Request):
        try:
            values = await read_body(request, AIConnectionInput)
            if values.confirmed is not True:
                raise AnalysisError("confirmation_required", "请先检查预览并确认本次发送。", 409)
            await readable_snapshot(identifier, request)
            provider, model, key, connection = resolve_connection(values, request)
            return JSONResponse(
                analysis.start(
                    identifier,
                    owner(request),
                    provider,
                    model,
                    key,
                    connection=connection,
                ),
                status_code=202,
            )
        except AnalysisError as error:
            return failure(error)

    @router.get("/analysis/{identifier}")
    async def get(identifier: str, request: Request):
        try:
            return await readable_snapshot(identifier, request)
        except AnalysisError as error:
            return failure(error)

    @router.post("/analysis/{identifier}/cancel")
    async def cancel(identifier: str, request: Request):
        try:
            check_action(request)
            snapshot = await analysis.cancel(identifier, owner(request))
            # Stop an owned task even when disclosure rights have been revoked.
            await require_port_access(app, request, snapshot["evidence"]["port"])
            return snapshot
        except AnalysisError as error:
            return failure(error)

    @router.get("/analysis/{identifier}/report")
    async def report(identifier: str, request: Request):
        try:
            document = report_document(await readable_snapshot(identifier, request))
            port = document["evidence"]["port"]
            return JSONResponse(
                document,
                headers={
                    "Content-Disposition": f'attachment; filename="port-light-analysis-{port}.json"'
                },
            )
        except AnalysisError as error:
            return failure(error)

    @router.post("/reports")
    async def save_report(request: Request):
        try:
            values = await read_body(request, SaveReportInput)
            snapshot = await readable_snapshot(values.completed_analysis_id, request)
            document = reports.save(snapshot, owner(request))
            analysis.record_saved_report(
                values.completed_analysis_id, owner(request), document["id"]
            )
            return JSONResponse(document, status_code=201)
        except AnalysisError as error:
            return failure(error)

    @router.get("/reports")
    async def list_reports(request: Request):
        try:
            visible = []
            for record in reports.list(owner(request)):
                try:
                    await require_port_access(app, request, record["port"])
                except AnalysisError as error:
                    if error.status in (403, 404):
                        continue
                    raise
                visible.append(record)
            return {"reports": visible}
        except AnalysisError as error:
            return failure(error)

    async def saved_report(identifier, request):
        document = reports.get(identifier, owner(request))
        await require_port_access(app, request, document["evidence"]["port"])
        return document

    @router.get("/reports/{identifier}")
    async def get_report(identifier: str, request: Request):
        try:
            return await saved_report(identifier, request)
        except AnalysisError as error:
            return failure(error)

    @router.get("/reports/{identifier}/export")
    async def export_report(identifier: str, request: Request):
        try:
            document = await saved_report(identifier, request)
            return JSONResponse(
                document,
                headers={
                    "Content-Disposition": f'attachment; filename="port-light-report-{document["id"]}.json"',
                },
            )
        except AnalysisError as error:
            return failure(error)

    router.include_router(
        workbench_router(
            app,
            core_version,
            workbench=workbench,
            reports=reports,
            owner=owner,
            check_action=check_action,
            resolve_connection=resolve_connection,
        )
    )

    return router
