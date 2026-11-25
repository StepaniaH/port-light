"""Optional HTTP Basic Auth and hidden-port disclosure gate.

LAN default (no AUTH_* / HIDDEN_UNLOCK_PASSWORD): the app is open. Hide-from-grid
is a display filter; the API may still return hidden ports tagged ``is_hidden``.

When AUTH_USER+AUTH_PASSWORD and/or HIDDEN_UNLOCK_PASSWORD are set, hidden port
details are withheld unless the request is authorized.
"""

from __future__ import annotations

import os
import secrets
from base64 import b64decode
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


HEALTH_PATHS = frozenset({"/api/health"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _header_values(request: Request, name: str) -> list[str]:
    headers = request.headers
    if hasattr(headers, "getlist"):
        return list(headers.getlist(name))
    value = headers.get(name)
    return [] if value is None else [value]


def _first_header_token(value: str | None) -> str:
    if not value:
        return ""
    return value.split(",", 1)[0].strip()


def _canonical_origin(scheme: str, host: str, port: int | None) -> str | None:
    scheme = scheme.strip().lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    host = host.strip().lower()
    if not host:
        return None
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"
    if port is not None:
        if not (1 <= port <= 65535):
            return None
        if port == _DEFAULT_PORTS[scheme]:
            port = None
    return f"{scheme}://{host}:{port}" if port is not None else f"{scheme}://{host}"


def _parse_origin_header(raw: str) -> str | None:
    value = raw.strip()
    if not value or value.lower() == "null":
        return None
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    if any(ch in value for ch in ("?", "#", "@")):
        return None
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    if parsed.path or parsed.query or parsed.fragment:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if not parsed.netloc or parsed.netloc.endswith(":"):
        return None
    if "/" in value[len(parsed.scheme) + 3:]:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    return _canonical_origin(scheme, parsed.hostname, port)


def _parse_host_header(raw: str) -> tuple[str, int | None] | None:
    value = raw.strip()
    if not value:
        return None
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    if any(ch in value for ch in ("/", "?", "#", "@", "\\")):
        return None
    if value.endswith(":"):
        return None
    parsed = urlsplit("http://" + value)
    if (
        not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    return parsed.hostname.lower(), port


def _split_unquoted(value: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    escaped = False
    for ch in value:
        if escaped:
            buf.append(ch)
            escaped = False
        elif in_quotes and ch == "\\":
            buf.append(ch)
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == sep and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _unquote_forwarded_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        out: list[str] = []
        escaped = False
        for ch in inner:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            else:
                out.append(ch)
        return "".join(out)
    return value


def _parse_forwarded_header(header: str) -> tuple[str | None, str | None]:
    if not header or not header.strip():
        return None, None
    first = _split_unquoted(header, ",")[0]
    proto: str | None = None
    host: str | None = None
    for param in _split_unquoted(first, ";"):
        if "=" not in param:
            continue
        key, raw_val = param.split("=", 1)
        key = key.strip().lower()
        val = _unquote_forwarded_value(raw_val).strip()
        if key == "proto" and val.lower() in _DEFAULT_PORTS:
            proto = val.lower()
        elif key == "host" and val:
            host = val
    return proto, host


def _expected_request_origin(request: Request) -> str | None:
    fwd_proto, fwd_host = _parse_forwarded_header(request.headers.get("forwarded") or "")
    scheme = fwd_proto
    if not scheme:
        xfp = _first_header_token(request.headers.get("x-forwarded-proto")).lower()
        if xfp in _DEFAULT_PORTS:
            scheme = xfp
    if not scheme:
        scheme = (request.url.scheme or "").lower()

    raw_host = fwd_host or _first_header_token(request.headers.get("x-forwarded-host"))
    if not raw_host:
        raw_host = (request.headers.get("host") or request.base_url.netloc or "").strip()
    parsed_host = _parse_host_header(raw_host)
    if parsed_host is None:
        return None
    hostname, port = parsed_host
    if port is None:
        raw_port = _first_header_token(request.headers.get("x-forwarded-port"))
        if raw_port.isdigit():
            candidate = int(raw_port)
            if 1 <= candidate <= 65535:
                port = candidate
    return _canonical_origin(scheme, hostname, port)


def is_same_origin_request(request: Request) -> bool:
    """Validate browser same-origin semantics across direct and reverse-proxied requests."""
    origin_values = _header_values(request, "origin")
    if len(origin_values) > 1:
        return False
    origin: str | None = None
    if origin_values:
        origin = _parse_origin_header(origin_values[0])
        if origin is None:
            return False

    sec_fetch_values = [
        value.strip().lower()
        for value in _header_values(request, "sec-fetch-site")
        if value.strip()
    ]
    if len(sec_fetch_values) > 1:
        return False
    sec_fetch_site = sec_fetch_values[0] if sec_fetch_values else ""
    if sec_fetch_site:
        if sec_fetch_site == "same-origin":
            return True
        if sec_fetch_site == "none":
            if origin is None:
                return True
            expected = _expected_request_origin(request)
            return expected is not None and origin == expected
        return False

    if origin is None:
        return True
    expected = _expected_request_origin(request)
    return expected is not None and origin == expected


def auth_configured() -> bool:
    """An explicit but incomplete configuration must never disable the gate."""
    return "AUTH_USER" in os.environ or "AUTH_PASSWORD" in os.environ


def auth_configuration_valid() -> bool:
    return not auth_configured() or all(
        os.environ.get(key, "").strip() for key in ("AUTH_USER", "AUTH_PASSWORD"))


def hidden_unlock_configured() -> bool:
    return bool(os.environ.get("HIDDEN_UNLOCK_PASSWORD"))


def hidden_ports_withheld() -> bool:
    """True when hidden port details must not leak to anonymous callers."""
    return auth_configured() or hidden_unlock_configured()


def _equal(left: str, right: str) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except UnicodeError:
        return False


def valid_basic_header(authorization: str) -> bool:
    user = os.environ.get("AUTH_USER") or ""
    password = os.environ.get("AUTH_PASSWORD") or ""
    if not auth_configuration_valid() or not user or not password:
        return False
    if not authorization.lower().startswith("basic "):
        return False
    try:
        raw = b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    if ":" not in raw:
        return False
    given_user, given_password = raw.split(":", 1)
    return _equal(given_user, user) and _equal(given_password, password)


def valid_hidden_unlock(header_value: str) -> bool:
    expected = os.environ.get("HIDDEN_UNLOCK_PASSWORD") or ""
    if not expected:
        return False
    return _equal(header_value, expected)


def request_may_see_hidden(request: Request) -> bool:
    if not hidden_ports_withheld():
        return True
    auth_header = request.headers.get("authorization") or ""
    if auth_configured() and valid_basic_header(auth_header):
        return True
    unlock = request.headers.get("x-hidden-unlock") or ""
    if hidden_unlock_configured() and valid_hidden_unlock(unlock):
        return True
    return False


async def basic_auth_middleware(request: Request, call_next):
    if not auth_configured() or request.url.path in HEALTH_PATHS:
        return await call_next(request)
    if not auth_configuration_valid():
        return JSONResponse(status_code=503, content={
            "code": "authentication_misconfigured",
            "detail": "AUTH_USER and AUTH_PASSWORD must both be non-empty; "
                      "unset both variables to disable authentication",
        })
    if valid_basic_header(request.headers.get("authorization") or ""):
        return await call_next(request)
    return Response(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Port-Light", charset="UTF-8"'},
    )
