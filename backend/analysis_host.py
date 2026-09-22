"""Own the bundled workbench without making scanner startup depend on its store."""

import hashlib
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from starlette.responses import JSONResponse

from . import settings
from .ui_extensions import register_ui_link

logger = logging.getLogger(__name__)


@asynccontextmanager
async def analysis_lifespan(app, version):
    previous_links = tuple(getattr(app.state, "ui_links", ()))
    app.state.analysis = None
    stack = AsyncExitStack()
    try:
        from .analysis.module import create_module

        child = create_module(
            {
                "core_app": app,
                "core_version": version,
                "data_dir": Path(
                    os.environ.get("PORT_LIGHT_DATA_DIR", "/data")
                ).resolve()
                / "analysis",
                "settings_readonly": settings.settings_readonly,
            }
        )
        # The workbench owns a separate store; scanner and user configuration stay intact.
        Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data"), "analysis").mkdir(
            mode=0o700, parents=True, exist_ok=True
        )
        await stack.enter_async_context(child.router.lifespan_context(child))
        register_ui_link(app, **child.state.ui_link)
        assets = Path(__file__).parent / "analysis" / "static"
        digest = hashlib.sha256()
        for asset in sorted(assets.iterdir()):
            if asset.is_file():
                digest.update(asset.name.encode())
                digest.update(asset.read_bytes())
        link = app.state.ui_links[-1]
        for interface in ("workspace", "settings"):
            link[interface]["revision"] = digest.hexdigest()
        app.state.analysis = child
    except Exception:
        # Do not log exception values: a malformed provider URL may contain secrets.
        logger.warning(
            "Local analysis is unavailable; the port dashboard remains active"
        )
        app.state.ui_links = previous_links
        await stack.aclose()
    try:
        yield
    finally:
        app.state.analysis = None
        app.state.ui_links = previous_links
        await stack.aclose()


class AnalysisDispatcher:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        child = getattr(self.app.state, "analysis", None)
        if child is None:
            response = JSONResponse(
                {
                    "error": {
                        "code": "analysis_unavailable",
                        "message": "Local analysis is unavailable.",
                    }
                },
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await child(scope, receive, send)
