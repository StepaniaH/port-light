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

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


HEALTH_PATHS = frozenset({"/api/health"})


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
