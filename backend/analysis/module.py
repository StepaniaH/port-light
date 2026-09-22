"""Local troubleshooting and optional user-funded model requests."""

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from .baseline import CHECKS, MAX_INPUT_BYTES
from .provider import configured_providers
from .reports import ReportStore
from .routes import analysis_router
from .settings import BYOKStore
from .workbench import MAX_PORTS


def create_module(context: Mapping) -> FastAPI:
    """Build the bundled analysis application; the parent owns authentication."""
    core_version = context["core_version"]
    configured_readonly = context.get("settings_readonly")

    def settings_readonly():
        # A module built against an older host must never turn missing or broken
        # host configuration into permission to write a local credential.
        try:
            return configured_readonly() is not False
        except Exception:  # noqa: BLE001 - The host owns this reader.
            return True

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    demo = os.environ.get("PORT_LIGHT_ANALYSIS_DEMO") == "1"
    providers = (
        {"demo": {"name": "演示（不调用模型）"}} if demo else configured_providers()
    )
    reports = ReportStore(context["data_dir"])
    settings_store = BYOKStore(context["data_dir"])
    app.state.report_store = reports
    app.state.byok_store = settings_store

    def provider_rows():
        return [
            {
                "id": identifier,
                "name": config["name"],
                **({"notice": config["notice"]} if config.get("notice") else {}),
            }
            for identifier, config in providers.items()
        ]

    @app.middleware("http")
    async def cache_policy(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/api/meta")
    def metadata() -> dict:
        return {
            "schema_version": 1,
            "core_version": core_version,
            "capabilities": {
                "byok_port_analysis": 1,
                "troubleshooting_workbench": 1,
            },
            "enabled": {
                "byok_port_analysis": True,
            },
            "analysis_mode": "demo" if demo else "byok",
            "report_storage": reports.metadata(),
            "check_catalog": CHECKS,
            "analysis_limits": {"max_input_bytes": MAX_INPUT_BYTES, "max_output_tokens": 1800},
            "workbench_limits": {
                "max_ports": MAX_PORTS,
                "max_report_bytes": 2 * 1024 * 1024,
                "max_total_report_bytes": 32 * 1024 * 1024,
                "max_model_input_bytes": 32 * 1024,
            },
            "providers": provider_rows(),
        }

    @app.get("/", include_in_schema=False)
    def workspace() -> RedirectResponse:
        return RedirectResponse("/#/workspace/port-analysis", status_code=307)

    @app.get("/assets/{asset}", include_in_schema=False)
    def static_asset(asset: str) -> FileResponse:
        if asset not in {
            "analysis.js",
            "analysis.css",
            "settings.js",
            "settings.css",
            "index.html",
            "display.js",
            "messages.json",
        }:
            raise HTTPException(status_code=404)
        return FileResponse(
            Path(__file__).parent / "static" / asset, headers={"Cache-Control": "no-cache"}
        )

    app.include_router(
        analysis_router(
            context["core_app"],
            core_version,
            demo=demo,
            providers=providers,
            reports=reports,
            settings_store=settings_store,
            settings_readonly=settings_readonly,
        )
    )
    app.state.ui_link = {
        "key": "port-analysis",
        "path": "/analysis/",
        "label": "Troubleshooting",
        "labels": {
            "zh-CN": "排障工作台",
            "zh-TW": "排障工作台",
            "de": "Fehlersuche",
            "es": "Diagnóstico",
            "fr": "Diagnostic",
            "ja": "トラブルシューティング",
        },
        "workspace": {
            "api": 1,
            "entry": "/analysis/assets/analysis.js",
            "stylesheet": "/analysis/assets/analysis.css",
            "port_action": True,
        },
        "settings": {
            "api": 1,
            "entry": "/analysis/assets/settings.js",
            "stylesheet": "/analysis/assets/settings.css",
        },
    }
    return app
