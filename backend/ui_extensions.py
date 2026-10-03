"""Small, local navigation hooks for applications composed with Port-Light."""
from __future__ import annotations

import re
from fastapi import FastAPI


def register_ui_link(
    app: FastAPI, *, key: str, path: str, label: str, labels: dict[str, str] | None = None,
    workspace: dict | None = None, settings: dict | None = None,
) -> None:
    """Register an authenticated local page before application startup.

    Optional workspaces mount in the shared shell; callers own their assets and lifecycle.
    """
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", key):
        raise ValueError("Invalid navigation key")
    if (not isinstance(path, str) or len(path) > 160
            or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]*", path)):
        raise ValueError("Navigation paths must be local absolute paths")
    translations = dict(labels or {})
    if any(not isinstance(locale, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Za-z]{2,4})?", locale)
           for locale in translations):
        raise ValueError("Invalid navigation locale")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 40
           or any(ord(char) < 32 for char in value)
           for value in [label, *translations.values()]):
        raise ValueError("Navigation labels must contain 1 to 40 characters")
    item = {"key": key, "path": path, "label": label, "labels": translations}
    def validate_assets(interface: dict, fields: set[str], name: str) -> None:
        if (not isinstance(interface, dict) or set(interface) != fields
                or type(interface["api"]) is not int or interface["api"] != 1 or path == "/"):
            raise ValueError(f"Invalid {name} interface")
        for field, suffix in (("entry", "js"), ("stylesheet", "css")):
            asset = interface[field]
            if (not isinstance(asset, str) or len(asset) > 240
                    or not asset.startswith(path.rstrip("/") + "/")
                    or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\." + suffix, asset)):
                raise ValueError(f"{name.capitalize()} assets must be local files under the navigation path")

    if workspace is not None:
        validate_assets(workspace, {"api", "entry", "stylesheet", "port_action"}, "workspace")
        if type(workspace["port_action"]) is not bool:
            raise ValueError("Invalid workspace interface")
        item["workspace"] = dict(workspace)
    if settings is not None:
        validate_assets(settings, {"api", "entry", "stylesheet"}, "settings")
        item["settings"] = dict(settings)
    links = tuple(getattr(app.state, "ui_links", ()))
    if len(links) >= 4 or any(item["key"] == key for item in links):
        raise ValueError("Navigation registration is full or the key is already registered")
    app.state.ui_links = (*links, item)
