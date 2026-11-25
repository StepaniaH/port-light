"""Analysis settings, legacy report reads/deletes, and workbench router mount."""

import asyncio
import re
import secrets
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..auth import is_same_origin_request
from .evidence import AnalysisError, require_port_access
from .provider import ChatGateway
from .settings import (
    MAX_CONNECTIONS,
    AIConnectionInput,
    AIConnectionTestInput,
    AIProfileRevisionInput,
    AIProfileSelectionInput,
    AISettingsEditInput,
    AISettingsInput,
    BYOKStore,
    empty_profile,
)
from .workbench import AttemptGate, Workbench, WorkbenchDemoGateway, WorkbenchGateway
from .workbench_routes import workbench_router

COOKIE = "port_light_analysis_session"


def owner(request):
    value = request.cookies.get(COOKIE, "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{32,64}", value) else ""


def check_action(request):
    if request.headers.get("x-port-light-analysis") != "1":
        raise AnalysisError(
            "invalid_action",
            "Analysis requests must include the workspace action header.",
            403,
        )
    if not is_same_origin_request(request):
        raise AnalysisError(
            "invalid_origin", "Cross-origin analysis requests are not allowed.", 403
        )


async def read_body(request, schema):
    check_action(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise AnalysisError("invalid_input", "Request body must be JSON.", 415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise AnalysisError("invalid_input", "Request body exceeds size limit.", 413)
    try:
        return schema.model_validate_json(body)
    except (ValidationError, ValueError):
        # Do not echo request values: malformed requests may accidentally contain a key.
        raise AnalysisError("invalid_input", "Invalid request fields.", 422) from None


async def read_empty_body(request):
    check_action(request)
    async for chunk in request.stream():
        if chunk:
            raise AnalysisError(
                "invalid_input", "This action does not accept a request body.", 422
            )


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
    providers=None,
):
    providers = providers or {}
    attempt_gate = AttemptGate()
    connection_tester = ChatGateway(providers=providers)
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
        workbench_cleanup = asyncio.create_task(
            workbench.reap(), name="port-light-workbench-expiry"
        )
        try:
            yield
        finally:
            workbench_cleanup.cancel()
            await asyncio.gather(workbench_cleanup, return_exceptions=True)
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
            raise AnalysisError("settings_readonly", "Hub settings are read-only.", 403)

    def resolve_connection(values: AIConnectionInput, request: Request):
        if values.saved:
            profile = settings_store.resolve(
                values.config_revision,
                allowed_providers=allowed_providers,
                demo=demo,
            )
            return profile.provider, profile.model, profile.key, profile.connection()
        if values.provider == "custom" and not providers.get("custom", {}).get("endpoint"):
            raise AnalysisError(
                "configuration_changed",
                "Save a custom base URL in AI settings before starting.",
                409,
            )
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
            return JSONResponse(
                {**settings_document(), "saved_connection_id": profile.identifier},
                status_code=201,
            )
        except AnalysisError as error:
            return failure(error)

    @router.put("/settings/ai/connections/{identifier}")
    async def update_connection(identifier: str, request: Request):
        try:
            values = await read_body(request, AISettingsEditInput)
            require_settings_writable()
            profile = settings_store.save_connection(
                identifier=identifier,
                config_revision=values.config_revision,
                **profile_options(values, request),
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
            settings_store.activate(
                values.profile_id,
                values.config_revision,
                allowed_providers=allowed_providers,
                demo=demo,
            )
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
            require_settings_writable()
            if values.confirmed is not True:
                raise AnalysisError(
                    "confirmation_required",
                    "Confirm this billable connection test before continuing.",
                    409,
                )
            if demo:
                raise AnalysisError(
                    "unsupported_provider",
                    "Connection tests are unavailable in demo mode.",
                    422,
                )
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
                    profile.provider,
                    profile.model,
                    profile.key,
                    session_id=gate_id,
                    **({"connection": profile.connection()} if profile.connection() else {}),
                )
            finally:
                attempt_gate.release(gate_id)
            return {
                "status": "connected",
                "config_revision": None if values.draft else profile.revision,
            }
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

    @router.delete("/reports")
    async def clear_reports(request: Request):
        try:
            check_action(request)
            return {"deleted_count": reports.delete_all(owner(request))}
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

    @router.delete("/reports/{identifier}")
    async def delete_report(identifier: str, request: Request):
        try:
            check_action(request)
            reports.delete(identifier, owner(request))
            return {"deleted": identifier}
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
