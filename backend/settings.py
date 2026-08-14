"""Resolved app settings: defaults < environment < data-dir file.

Compose env is the deployment default. Saving in the Web UI writes
``settings`` into ``port_light.json`` and those keys win on the next request.

Set ``PORT_LIGHT_SETTINGS_SOURCE=env`` (or ``SETTINGS_READONLY=1``) to ignore
the file and disable PUT — GitOps / compose-only hosts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from . import port_store

Origin = Literal["default", "env", "file"]
SourceMode = Literal["auto", "env", "file"]

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class FieldSpec:
    key: str
    kind: Literal["bool", "int", "str", "choice"]
    env: str
    default: Any
    group: str
    label: str
    help: str
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()
    max_length: int = 253


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "locale", "choice", "LOCALE", "auto", "appearance", "Language",
        "Auto follows the browser.",
        choices=("auto", "en", "zh-CN", "zh-TW", "ja"),
    ),
    FieldSpec(
        "theme", "choice", "THEME", "system", "appearance", "Theme",
        "System follows the browser color scheme.",
        choices=("system", "dark", "light"),
    ),
    FieldSpec(
        "grid_density", "choice", "GRID_DENSITY", "comfortable", "appearance", "Grid density",
        "Compact packs more port cards on screen.",
        choices=("comfortable", "compact"),
    ),
    FieldSpec(
        "show_status_text", "bool", "SHOW_STATUS_TEXT", False, "appearance",
        "Status text on cards", "Short in-use / configured label on each card.",
    ),
    FieldSpec(
        "show_access_badge", "bool", "SHOW_ACCESS_BADGE", True, "appearance",
        "Access-port badge", "Mark ports people usually open in a browser.",
    ),
    FieldSpec(
        "show_protocol_badge", "bool", "SHOW_PROTOCOL_BADGE", True, "appearance",
        "Protocol badge", "Show udp / tcp,udp on cards that are not TCP-only.",
    ),
    FieldSpec(
        "copy_on_click", "bool", "COPY_ON_CLICK", True, "grid",
        "Copy port on click", "Copies the port number to the clipboard.",
    ),
    FieldSpec(
        "auto_refresh", "bool", "AUTO_REFRESH", True, "grid",
        "Auto-refresh", "Poll the occupancy map on an interval.",
    ),
    FieldSpec(
        "refresh_ms", "int", "REFRESH_MS", 5000, "grid",
        "Refresh interval (ms)", "Used when auto-refresh is on. 1000–300000.",
        min=1000, max=300000,
    ),
    FieldSpec(
        "port_range_start", "int", "PORT_RANGE_START", 1, "grid",
        "Range start", "Lower bound for the free-count and the default toolbar range.",
        min=1, max=65535,
    ),
    FieldSpec(
        "port_range_end", "int", "PORT_RANGE_END", 9999, "grid",
        "Range end", "Upper bound. Does not paint every unused port green.",
        min=1, max=65535,
    ),
    FieldSpec(
        "compose_scan_depth", "int", "COMPOSE_SCAN_DEPTH", 4, "scanning",
        "Compose scan depth", "How many directory levels under COMPOSE_SCAN_DIR to walk.",
        min=1, max=16,
    ),
    FieldSpec(
        "compose_scan_max_files", "int", "COMPOSE_SCAN_MAX_FILES", 400, "scanning",
        "Compose file cap", "Stop after this many compose files per refresh.",
        min=1, max=5000,
    ),
    FieldSpec(
        "guess_urls", "bool", "GUESS_URLS", True, "links",
        "Guess access URLs", "Add http(s)://host:port for known access ports.",
    ),
    FieldSpec(
        "url_host", "str", "URL_HOST", "", "links",
        "URL hostname", "Used in guessed links instead of localhost. Leave empty for localhost / 127.0.0.1.",
        max_length=253,
    ),
    FieldSpec(
        "url_scheme", "choice", "URL_SCHEME", "auto", "links",
        "URL scheme", "auto uses https for 443/8443/9443 and names containing HTTPS.",
        choices=("auto", "http", "https"),
    ),
)

_FIELD_BY_KEY = {f.key: f for f in FIELDS}


def settings_source() -> SourceMode:
    raw = (os.environ.get("PORT_LIGHT_SETTINGS_SOURCE") or "auto").strip().lower()
    if raw in ("env", "file", "auto"):
        return raw  # type: ignore[return-value]
    return "auto"


def settings_readonly() -> bool:
    if settings_source() == "env":
        return True
    flag = (os.environ.get("SETTINGS_READONLY") or "").strip().lower()
    return flag in _TRUE


def _env_raw(spec: FieldSpec) -> str | None:
    if spec.env not in os.environ:
        return None
    return os.environ.get(spec.env)


def _coerce(spec: FieldSpec, raw: Any) -> Any:
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"{spec.key} must be a boolean")
    if spec.kind == "int":
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{spec.key} must be an integer") from exc
        if spec.min is not None and value < spec.min:
            raise ValueError(f"{spec.key} must be >= {spec.min}")
        if spec.max is not None and value > spec.max:
            raise ValueError(f"{spec.key} must be <= {spec.max}")
        return value
    text = "" if raw is None else str(raw).strip()
    if spec.kind == "choice":
        if text not in spec.choices:
            raise ValueError(f"{spec.key} must be one of {', '.join(spec.choices)}")
        return text
    if len(text) > spec.max_length:
        raise ValueError(f"{spec.key} is too long")
    if spec.key == "url_host" and text:
        if any(ch.isspace() for ch in text) or "/" in text or ":" in text:
            raise ValueError("url_host must be a hostname without scheme, port, or path")
    return text


def _parse_env(spec: FieldSpec) -> Any | None:
    raw = _env_raw(spec)
    if raw is None or raw == "":
        return None
    try:
        return _coerce(spec, raw)
    except ValueError:
        return None


def resolve() -> tuple[dict[str, Any], dict[str, Origin]]:
    stored = port_store.get_stored_settings()
    mode = settings_source()
    values: dict[str, Any] = {}
    origins: dict[str, Origin] = {}
    for spec in FIELDS:
        env_val = _parse_env(spec)
        file_val = stored[spec.key] if spec.key in stored else None
        if file_val is not None:
            try:
                file_val = _coerce(spec, file_val)
            except ValueError:
                file_val = None

        if mode == "env":
            if env_val is not None:
                values[spec.key], origins[spec.key] = env_val, "env"
            else:
                values[spec.key], origins[spec.key] = spec.default, "default"
        elif mode == "file":
            if file_val is not None:
                values[spec.key], origins[spec.key] = file_val, "file"
            elif env_val is not None:
                values[spec.key], origins[spec.key] = env_val, "env"
            else:
                values[spec.key], origins[spec.key] = spec.default, "default"
        else:
            if file_val is not None:
                values[spec.key], origins[spec.key] = file_val, "file"
            elif env_val is not None:
                values[spec.key], origins[spec.key] = env_val, "env"
            else:
                values[spec.key], origins[spec.key] = spec.default, "default"
    start, end = values["port_range_start"], values["port_range_end"]
    if end < start:
        values["port_range_end"] = start
    return values, origins


def snapshot() -> dict[str, Any]:
    values, origins = resolve()
    readonly = settings_readonly()
    fields = []
    for spec in FIELDS:
        fields.append({
            "key": spec.key,
            "type": spec.kind,
            "env": spec.env,
            "group": spec.group,
            "label": spec.label,
            "help": spec.help,
            "choices": list(spec.choices),
            "min": spec.min,
            "max": spec.max,
            "origin": origins[spec.key],
            "locked": readonly,
        })
    return {
        "source": settings_source(),
        "readonly": readonly,
        "values": values,
        "origins": origins,
        "fields": fields,
        "env_only": {
            "compose_scan_dir": os.environ.get("COMPOSE_SCAN_DIR", "/compose"),
            "custom_ports_file": os.environ.get("CUSTOM_PORTS_FILE", "/data/custom_ports.json"),
            "data_dir": os.environ.get("PORT_LIGHT_DATA_DIR", "/data"),
            "auth_required": bool(os.environ.get("AUTH_USER") and os.environ.get("AUTH_PASSWORD")),
            "hidden_unlock_required": bool(os.environ.get("HIDDEN_UNLOCK_PASSWORD")),
            "settings_source_env": "PORT_LIGHT_SETTINGS_SOURCE",
        },
    }


def apply_patch(patch: dict[str, Any]) -> dict[str, Any]:
    if settings_readonly():
        raise PermissionError("settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    if not isinstance(patch, dict):
        raise ValueError("body must be an object")
    unknown = [k for k in patch if k not in _FIELD_BY_KEY]
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(unknown)}")
    coerced: dict[str, Any] = {}
    for key, raw in patch.items():
        spec = _FIELD_BY_KEY[key]
        if raw is None:
            coerced[key] = None
            continue
        coerced[key] = _coerce(spec, raw)
    if "port_range_start" in coerced or "port_range_end" in coerced:
        current, _ = resolve()
        start = coerced.get("port_range_start", current["port_range_start"])
        end = coerced.get("port_range_end", current["port_range_end"])
        if start is not None and end is not None and end < start:
            raise ValueError("port_range_end must be >= port_range_start")
    port_store.update_stored_settings(coerced)
    return snapshot()
