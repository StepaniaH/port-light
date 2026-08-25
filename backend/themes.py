"""Named custom palettes stored in <data-dir>/themes.json.

Same ownership pattern as custom_ports.json: one JSON file in the data dir,
atomic writes, corrupt files quarantined with a degradation line. Colors are
validated against a strict hex grammar so nothing unvetted can reach a CSS
custom property.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

from . import degradations

MAX_THEMES = 24

COLOR_KEYS: tuple[str, ...] = (
    "bg", "elevated", "card", "cardHover", "border", "text", "textDim",
    "used", "configured", "free", "accent", "conflict", "access", "hidden",
    "danger",
)

_ID_RE = re.compile(r"[0-9a-f]{8}\Z")
_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")

_LOCK = threading.Lock()


class ThemeError(ValueError):
    """Raised for invalid payloads or capacity violations."""


def _file() -> Path:
    return Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data")) / "themes.json"


def _families() -> tuple[str, ...]:
    from .settings import _FIELD_BY_KEY

    return tuple(c for c in _FIELD_BY_KEY["theme_palette"].choices if c)


def validate(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ThemeError("body must be an object")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 40:
        raise ThemeError("name must be 1-40 characters")
    based_on = str(payload.get("basedOn") or "").strip()
    if based_on and based_on not in _families():
        raise ThemeError("unknown basedOn family: " + based_on)
    mode = payload.get("mode")
    if mode not in ("dark", "light"):
        raise ThemeError("mode must be dark or light")
    colors = payload.get("colors")
    if not isinstance(colors, dict):
        raise ThemeError("colors must be an object")
    clean: dict[str, str] = {}
    for key in COLOR_KEYS:
        value = str(colors.get(key) or "").strip()
        if not _COLOR_RE.fullmatch(value):
            raise ThemeError("color '" + key + "' must be a #hex value")
        clean[key] = value.lower()
    return {"name": name, "basedOn": based_on, "mode": mode, "colors": clean}


def _load() -> list[dict]:
    f = _file()
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            os.replace(f, f.parent / (f.name + ".bad"))
        except OSError:
            pass
        degradations.report("themes", f.name, "corrupt file quarantined")
        return []
    except OSError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        theme_id = str(item.get("id") or "")
        if not _ID_RE.fullmatch(theme_id):
            continue
        try:
            clean = validate({
                "name": item.get("name"),
                "basedOn": item.get("basedOn"),
                "mode": item.get("mode"),
                "colors": item.get("colors"),
            })
        except ThemeError:
            continue
        clean["id"] = theme_id
        out.append(clean)
    return out


def _save(items: list[dict]) -> None:
    d = _file().parent
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".themes.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, _file())
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise ThemeError("cannot write themes.json (permission denied)") from exc
        raise
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def list_themes() -> list[dict]:
    with _LOCK:
        return _load()


def theme_exists(theme_id: str) -> bool:
    with _LOCK:
        return any(t["id"] == theme_id for t in _load())


def add_theme(payload: object) -> dict:
    clean = validate(payload)
    with _LOCK:
        items = _load()
        if len(items) >= MAX_THEMES:
            raise ThemeError("custom theme limit reached (" + str(MAX_THEMES) + ")")
        clean["id"] = secrets.token_hex(4)
        items.append(clean)
        _save(items)
        return dict(clean)


def update_theme(theme_id: str, payload: object) -> dict:
    clean = validate(payload)
    with _LOCK:
        items = _load()
        for index, item in enumerate(items):
            if item["id"] == theme_id:
                clean["id"] = theme_id
                items[index] = clean
                _save(items)
                return dict(clean)
    raise ThemeError("no such theme")


def delete_theme(theme_id: str) -> bool:
    with _LOCK:
        items = _load()
        kept = [item for item in items if item["id"] != theme_id]
        if len(kept) == len(items):
            return False
        _save(kept)
        return True
