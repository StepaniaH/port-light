"""Small, local navigation hooks for applications composed with Port-Light."""
from __future__ import annotations

import re
from fastapi import FastAPI


def register_ui_link(
    app: FastAPI, *, key: str, path: str, label: str, labels: dict[str, str] | None = None,
) -> None:
    """Register an authenticated local page before application startup.

    Links add navigation only; callers register their own routes and lifecycle.
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
    links = tuple(getattr(app.state, "ui_links", ()))
    if len(links) >= 4 or any(item["key"] == key for item in links):
        raise ValueError("Navigation registration is full or the key is already registered")
    app.state.ui_links = (*links, {"key": key, "path": path, "label": label, "labels": translations})
