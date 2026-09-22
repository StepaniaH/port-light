"""HTTP boundary for the bounded troubleshooting workbench."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .evidence import AnalysisError
from .settings import AIConnectionInput
from .workbench import MAX_PORTS, build_capture, compare_captures, read_batch

Port = Annotated[int, Field(ge=1, le=65535)]


class InputDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AllKnownScope(InputDocument):
    kind: Literal["all_known"]


class SinglePortScope(InputDocument):
    kind: Literal["single_port"]
    port: Port


class SelectedPortsScope(InputDocument):
    kind: Literal["selected_ports"]
    ports: list[Port] = Field(min_length=1, max_length=MAX_PORTS * 2)

    @model_validator(mode="after")
    def canonical_ports(self):
        self.ports = sorted(set(self.ports))
        if len(self.ports) > MAX_PORTS:
            raise ValueError("selection exceeds port limit")
        return self


class PortRangeScope(InputDocument):
    kind: Literal["port_range"]
    start: Port
    end: Port

    @model_validator(mode="after")
    def bounded_range(self):
        if self.end < self.start or self.end - self.start + 1 > MAX_PORTS:
            raise ValueError("invalid bounded range")
        return self


Scope = Annotated[
    AllKnownScope | SinglePortScope | SelectedPortsScope | PortRangeScope,
    Field(discriminator="kind"),
]


class CaptureInput(InputDocument):
    kind: Literal["triage", "changes"]
    scope: Scope
    protocol: Literal["tcp", "udp", "all"] = "all"
    history_hours: Literal[1, 6, 24] = 6


class SaveInput(InputDocument):
    capture_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,64}$")


class RecheckInput(InputDocument):
    report_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,64}$")


def _failure(error: AnalysisError):
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}}, status_code=error.status
    )


async def _read_body(request: Request, schema, check_action):
    check_action(request)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise AnalysisError("invalid_input", "请求需要 JSON 数据。", 415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        # 1024 decimal ports plus the surrounding scope fits comfortably.  Do
        # not reuse the legacy 4 KiB single-port action limit here.
        if len(body) > 16 * 1024:
            raise AnalysisError("invalid_input", "请求数据过大。", 413)
    try:
        return schema.model_validate_json(body)
    except (ValidationError, ValueError):
        raise AnalysisError("invalid_input", "请检查范围、协议和请求字段。", 422) from None


def _batch_selection(scope) -> dict:
    if scope.kind == "all_known":
        return {"kind": "known"}
    if scope.kind == "single_port":
        return {"kind": "ports", "ports": [scope.port]}
    if scope.kind == "selected_ports":
        return {"kind": "ports", "ports": scope.ports}
    return {"kind": "range", "start": scope.start, "end": scope.end}


def _scope_requested(values: CaptureInput) -> dict:
    scope = values.scope.model_dump()
    return {
        "kind": scope["kind"],
        "scope": scope,
        "protocol": values.protocol,
        "history_hours": values.history_hours,
    }


def _requested_ports(scope) -> set[int] | None:
    if scope.kind == "all_known":
        return None
    if scope.kind == "single_port":
        return {scope.port}
    if scope.kind == "selected_ports":
        return set(scope.ports)
    return set(range(scope.start, scope.end + 1))


def _session_response(
    document: dict, request: Request, session: str, *, replace_session: bool
) -> JSONResponse:
    response = JSONResponse(document, status_code=201)
    if replace_session:
        response.set_cookie(
            "port_light_analysis_session",
            session,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/analysis",
        )
    return response


def workbench_router(
    app,
    core_version,
    *,
    workbench,
    reports,
    owner,
    check_action,
    resolve_connection,
):
    """Return routes mounted below the legacy module's `/api` prefix."""

    router = APIRouter(prefix="/workbench", tags=["analysis"])

    async def visible_ports(request: Request, resources: list[dict]) -> set[int]:
        ports = sorted({item["port"] for item in resources if type(item.get("port")) is int})
        if not ports:
            return set()
        visible = set()
        # Report lists can contain several 1024-port captures.  Authorize their
        # union in bounded chunks rather than probing each resource or report.
        for start in range(0, len(ports), MAX_PORTS // 2):
            batch = await read_batch(
                app,
                request,
                core_version=core_version,
                selection={"kind": "ports", "ports": ports[start : start + MAX_PORTS // 2]},
                protocol="all",
                history_hours=1,
                event_limit=1,
            )
            visible.update(item["port"] for item in batch["ports"])
        return visible

    async def require_resources(request: Request, resources: list[dict]):
        ports = {item["port"] for item in resources if type(item.get("port")) is int}
        visible = await visible_ports(request, resources)
        if not set(ports) <= visible:
            # Do not say which member was withheld.  The caller can only learn
            # that this multi-resource object is not currently readable.
            raise AnalysisError("access_restricted", "当前访问权限不能读取该排障对象。", 404)

    async def readable_capture(identifier: str, request: Request):
        document = workbench.get(identifier, owner(request))
        resources = document.get("resources", [])
        await require_resources(request, resources)
        return document

    async def readable_report(identifier: str, request: Request):
        document = reports.get_workbench(identifier, owner(request))
        await require_resources(request, document["resources"])
        return document

    @router.get("/options")
    async def options(request: Request):
        try:
            try:
                batch = await read_batch(
                    app,
                    request,
                    core_version=core_version,
                    selection={"kind": "known"},
                    protocol="all",
                    history_hours=1,
                    event_limit=1,
                )
                known_ports = [item["port"] for item in batch["ports"]]
                complete = len(known_ports) <= MAX_PORTS
                note = "" if complete else "scope_too_large"
            except AnalysisError as error:
                if error.code != "scope_too_large":
                    raise
                known_ports, complete, note = [], False, "scope_too_large"
            return {
                "schema_version": 1,
                "limits": {"max_ports": MAX_PORTS, "history_hours": [1, 6, 24]},
                "known_ports": known_ports,
                "known_ports_complete": complete,
                "known_ports_note": note,
                "range": {"min": 1, "max": 65535},
            }
        except AnalysisError as error:
            return _failure(error)

    @router.post("/captures")
    async def create_capture(request: Request):
        try:
            values = await _read_body(request, CaptureInput, check_action)
            selection = _batch_selection(values.scope)
            batch = await read_batch(
                app,
                request,
                core_version=core_version,
                selection=selection,
                protocol=values.protocol,
                history_hours=values.history_hours,
            )
            requested = _requested_ports(values.scope)
            visible = {item["port"] for item in batch["ports"]}
            if requested is not None and not requested <= visible:
                raise AnalysisError("access_restricted", "当前访问权限不能读取所选范围。", 404)
            existing_owner = owner(request)
            session = existing_owner or secrets.token_urlsafe(24)
            document = workbench.create(
                batch,
                owner=session,
                kind=values.kind,
                scope_requested=_scope_requested(values),
            )
            return _session_response(
                document, request, session, replace_session=not bool(existing_owner)
            )
        except AnalysisError as error:
            return _failure(error)

    @router.get("/captures/{identifier}")
    async def get_capture(identifier: str, request: Request):
        try:
            return await readable_capture(identifier, request)
        except AnalysisError as error:
            return _failure(error)

    @router.post("/captures/{identifier}/ai")
    async def start_ai(identifier: str, request: Request):
        try:
            values = await _read_body(request, AIConnectionInput, check_action)
            if values.confirmed is not True:
                raise AnalysisError(
                    "confirmation_required", "请先检查发送内容并确认本次调用。", 409
                )
            await readable_capture(identifier, request)
            provider, model, key = resolve_connection(values, request)
            return workbench.start_ai(
                identifier,
                owner(request),
                provider,
                model,
                key,
            )
        except AnalysisError as error:
            return _failure(error)

    @router.post("/captures/{identifier}/cancel")
    async def cancel_ai(identifier: str, request: Request):
        try:
            check_action(request)
            # Preserve the legacy behavior of stopping an owned provider attempt
            # even if disclosure access was revoked just before this request.
            document = await workbench.cancel(identifier, owner(request))
            await require_resources(request, document.get("resources", []))
            return document
        except AnalysisError as error:
            return _failure(error)

    @router.post("/reports")
    async def save_report(request: Request):
        try:
            values = await _read_body(request, SaveInput, check_action)
            capture = await readable_capture(values.capture_id, request)
            document = reports.save_workbench(capture, owner(request))
            return JSONResponse(document, status_code=201)
        except AnalysisError as error:
            return _failure(error)

    @router.get("/reports")
    async def list_reports(request: Request, limit: int = 20, cursor: str | None = None):
        try:
            if not 1 <= limit <= 50:
                raise AnalysisError("invalid_input", "报告列表分页参数无效。", 422)
            visible = []
            scan_cursor = cursor
            while len(visible) < limit:
                records, raw_next_cursor = reports.list_workbench(
                    owner(request), limit=50, cursor=scan_cursor
                )
                if not records:
                    break
                all_resources = [resource for record in records for resource in record["resources"]]
                current_visible = await visible_ports(request, all_resources)
                for record in records:
                    record_ports = {item["port"] for item in record["resources"]}
                    if not record_ports <= current_visible:
                        continue
                    visible.append(
                        {key: value for key, value in record.items() if key != "resources"}
                    )
                    if len(visible) == limit:
                        # Resume after the last item actually revealed.  Any
                        # intervening hidden reports are rechecked on the next
                        # page and never become a count or a cursor signal.
                        return {"reports": visible, "next_cursor": record["id"]}
                if raw_next_cursor is None:
                    break
                scan_cursor = raw_next_cursor
            return {"reports": visible, "next_cursor": None}
        except AnalysisError as error:
            return _failure(error)

    @router.get("/reports/{identifier}")
    async def get_report(identifier: str, request: Request):
        try:
            return await readable_report(identifier, request)
        except AnalysisError as error:
            return _failure(error)

    @router.get("/reports/{identifier}/export")
    async def export_report(identifier: str, request: Request):
        try:
            document = await readable_report(identifier, request)
            return JSONResponse(
                document,
                headers={
                    "Content-Disposition": f'attachment; filename="port-light-workbench-{document["id"]}.json"'
                },
            )
        except AnalysisError as error:
            return _failure(error)

    @router.post("/rechecks")
    async def recheck(request: Request):
        try:
            values = await _read_body(request, RecheckInput, check_action)
            report = await readable_report(values.report_id, request)
            previous = report["capture"]
            scope = previous["scope_requested"]
            ports = previous.get("scope_ports", [])
            if scope["kind"] == "all_known":
                selection = {"kind": "known", "additional_ports": ports}
            elif ports:
                selection = {"kind": "ports", "ports": ports}
            else:
                raise AnalysisError("not_ready", "原始范围没有可复查的资源。", 409)
            batch = await read_batch(
                app,
                request,
                core_version=core_version,
                selection=selection,
                protocol=previous["protocol"],
                history_hours=previous["history_hours"],
            )
            if not set(ports) <= {item["port"] for item in batch["ports"]}:
                # Rights can change between the saved-report authorization and
                # the new frozen batch.  Do not create a smaller report that
                # still carries comparison evidence for a now-hidden member.
                raise AnalysisError("access_restricted", "当前访问权限不能读取该排障对象。", 404)
            # This candidate uses a throwaway ID only to calculate facts and
            # stable problem IDs before the owned capture is materialized.
            candidate = build_capture(
                batch,
                identifier="recheck-candidate",
                kind="recheck",
                scope_requested=scope,
                problem_mode=previous["source_kind"],
            )
            comparison = compare_captures(previous, candidate, source_report_id=report["id"])
            session = owner(request)
            document = workbench.create(
                batch,
                owner=session,
                kind="recheck",
                scope_requested=scope,
                problem_mode=previous["source_kind"],
                comparison=comparison,
                source_report_id=report["id"],
            )
            return JSONResponse(document, status_code=201)
        except AnalysisError as error:
            return _failure(error)

    return router
