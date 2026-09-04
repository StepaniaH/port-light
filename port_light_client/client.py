"""Dependency-free client interface for a running Port-Light instance."""

from __future__ import annotations

import base64
import json
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol


class PortLightError(Exception):
    """A normalized configuration, transport, or Port-Light response error."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class Transport(Protocol):
    """Internal seam used by the client and its in-memory test adapter."""

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def normalize_base_url(value: str) -> str:
    text = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PortLightError(
            "invalid_url",
            "Port-Light URL must be an http:// or https:// address with a host",
        )
    if parsed.username is not None or parsed.password is not None:
        raise PortLightError(
            "invalid_url",
            "Port-Light URL must not contain credentials; use PORT_LIGHT_AUTH",
        )
    if parsed.query or parsed.fragment:
        raise PortLightError(
            "invalid_url",
            "Port-Light URL must not contain a query string or fragment",
        )
    return text


def _http_error(code: int, detail: str) -> PortLightError:
    if code in (401, 403):
        kind = "authentication_failed"
    elif code == 404:
        kind = "not_found"
    elif code == 409:
        kind = "conflict"
    elif code in (400, 422):
        kind = "invalid_request"
    elif code == 503:
        kind = "occupancy_unavailable"
    elif 300 <= code < 400:
        kind = "redirect_refused"
    elif code >= 500:
        kind = "server_error"
    else:
        kind = "http_error"
    return PortLightError(kind, detail or f"Port-Light returned HTTP {code}", status=code)


class HttpTransport:
    """Production transport adapter using only the Python standard library."""

    def __init__(
        self,
        base_url: str,
        *,
        basic_auth: str = "",
        timeout: float = 5.0,
        ca_file: str | None = None,
        opener: Any | None = None,
    ):
        self.base_url = normalize_base_url(base_url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise PortLightError("invalid_timeout", "timeout must be a finite number greater than zero")
        self.timeout = timeout
        self._basic_auth = basic_auth
        if opener is None:
            try:
                context = (
                    ssl.create_default_context(cafile=ca_file)
                    if ca_file else ssl.create_default_context()
                )
            except (OSError, ssl.SSLError) as exc:
                raise PortLightError(
                    "tls_error",
                    "could not load the HTTPS CA bundle",
                ) from exc
            opener = urllib.request.build_opener(
                _RejectRedirects(), urllib.request.HTTPSHandler(context=context),
            )
        self._opener = opener

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise PortLightError("invalid_request", "request path must start with /")
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "application/json")
        if self._basic_auth:
            token = base64.b64encode(self._basic_auth.encode()).decode()
            request_headers["Authorization"] = "Basic " + token
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers=request_headers,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(256 * 1024)
            detail = ""
            try:
                payload = json.loads(raw) if raw else {}
                if isinstance(payload, dict):
                    detail = str(payload.get("detail") or "")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise _http_error(exc.code, detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLError):
                raise PortLightError(
                    "tls_error",
                    "could not verify the Port-Light HTTPS certificate",
                ) from exc
            raise PortLightError(
                "unreachable",
                f"cannot reach Port-Light at {self.base_url}",
            ) from exc

        if len(body) > 4 * 1024 * 1024:
            raise PortLightError(
                "invalid_response",
                "Port-Light returned a response larger than 4 MiB",
            )
        if not body:
            return {}
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PortLightError(
                "invalid_response",
                "Port-Light returned a response that was not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an unexpected JSON value",
            )
        return payload


def compact_port(row: Mapping[str, Any]) -> dict[str, Any]:
    containers = row.get("containers") or []
    names = [
        item.get("name") for item in containers
        if isinstance(item, Mapping) and item.get("name")
    ]
    known = row.get("known_service") or {}
    if not names and isinstance(known, Mapping) and known.get("name"):
        names = [known["name"]]
    return {
        "port": row.get("port"),
        "status": row.get("status"),
        "protocol": row.get("protocol"),
        "bind_scope": row.get("bind_scope"),
        "names": names,
    }


class PortLightClient:
    """Small domain interface shared by command-line and MCP adapters."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:2100",
        *,
        basic_auth: str = "",
        agent_token: str = "",
        timeout: float = 5.0,
        ca_file: str | None = None,
        transport: Transport | None = None,
    ):
        self.base_url = normalize_base_url(base_url)
        self._agent_token = agent_token
        self._meta_cache: dict[str, Any] | None = None
        self._transport = transport or HttpTransport(
            self.base_url,
            basic_auth=basic_auth,
            timeout=timeout,
            ca_file=ca_file,
        )

    def meta(self) -> dict[str, Any]:
        if self._meta_cache is None:
            self._meta_cache = self._transport.request("GET", "/api/meta")
        return dict(self._meta_cache)

    def require_capability(self, name: str, version: int = 1) -> None:
        capabilities = self.meta().get("capabilities")
        if capabilities is None:
            # Legacy servers are probed through the requested operation. The
            # response is still validated before the caller trusts it.
            return
        if not isinstance(capabilities, dict) or capabilities.get(name, 0) < version:
            raise PortLightError(
                "unsupported_server",
                f"Port-Light does not advertise the required {name} capability; upgrade the server",
            )

    def doctor(self) -> dict[str, Any]:
        document = self._transport.request("GET", "/api/doctor")
        document.pop("report", None)
        return document

    def check_port(self, port: int) -> dict[str, Any]:
        _validate_port(port)
        row = self._transport.request(
            "GET", f"/api/ports/{port}?include_hidden=false",
        )
        return compact_port(row)

    def suggest_ports(
        self,
        *,
        count: int = 1,
        start: int | None = None,
        end: int | None = None,
        reserve: bool = False,
        label: str = "",
        ttl: int | None = None,
        scope: str = "self",
        require_count: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= count <= 64:
            raise PortLightError("invalid_request", "count must be between 1 and 64")
        if start is not None:
            _validate_port(start)
        if end is not None:
            _validate_port(end)
        if ttl is not None and not 60 <= ttl <= 604800:
            raise PortLightError("invalid_request", "ttl must be between 60 and 604800 seconds")
        if scope not in ("self", "all"):
            raise PortLightError("invalid_request", "scope must be self or all")
        params: list[tuple[str, str]] = [("count", str(count)), ("scope", scope)]
        if start is not None:
            params.append(("start", str(start)))
        if end is not None:
            params.append(("end", str(end)))
        if reserve:
            params.append(("reserve", "true"))
        if label:
            params.append(("label", label))
        if ttl is not None:
            params.append(("ttl", str(ttl)))
        if require_count:
            params.append(("require_count", "true"))
        headers = {"X-Agent-Token": self._agent_token} if self._agent_token else None
        return self._transport.request(
            "GET",
            "/api/ports/suggest?" + urllib.parse.urlencode(params),
            headers=headers,
        )

    def reserve_ports(
        self,
        *,
        count: int = 1,
        start: int | None = None,
        end: int | None = None,
        label: str = "",
        ttl: int | None = 3600,
        scope: str = "self",
    ) -> dict[str, Any]:
        result = self.suggest_ports(
            count=count,
            start=start,
            end=end,
            reserve=True,
            label=label,
            ttl=ttl,
            scope=scope,
            require_count=True,
        )
        ports = result.get("ports")
        reservations = result.get("reservations")
        if not isinstance(ports, list) or not isinstance(reservations, list):
            raise PortLightError(
                "unsupported_server",
                "Port-Light returned an unsupported reservation response; upgrade the server",
            )
        if len(reservations) != len(ports):
            raise PortLightError(
                "unsupported_server",
                "Port-Light did not return one reservation token per selected port; upgrade the server",
            )
        if (
            any(type(port) is not int or not 1 <= port <= 65535 for port in ports)
            or len(set(ports)) != len(ports)
        ):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned invalid reserved port numbers",
            )
        for reservation in reservations:
            if not isinstance(reservation, dict) or not reservation.get("token"):
                raise PortLightError(
                    "unsupported_server",
                    "Port-Light did not return secure reservation tokens; upgrade the server",
                )
        if [reservation.get("port") for reservation in reservations] != ports:
            raise PortLightError(
                "invalid_response",
                "Port-Light returned reservation tokens for different ports",
            )
        if not reservations:
            raise PortLightError("no_capacity", "no free ports were available in the requested range")
        return result

    def release_port(self, port: int, token: str) -> dict[str, Any]:
        _validate_port(port)
        if not token:
            raise PortLightError("reservation_token_missing", "reservation token is required")
        self._transport.request(
            "DELETE",
            f"/api/reservations/{port}",
            headers={"X-Reservation-Token": token},
        )
        return {"released": port}

    def list_occupancy(
        self,
        *,
        start: int = 1,
        end: int = 9999,
        limit: int = 200,
    ) -> dict[str, Any]:
        _validate_port(start)
        _validate_port(end)
        if not 1 <= limit <= 500:
            raise PortLightError("invalid_request", "limit must be between 1 and 500")
        query = urllib.parse.urlencode({
            "range_start": start,
            "range_end": end,
            "include_hidden": "false",
        })
        data = self._transport.request("GET", "/api/ports?" + query)
        rows = data.get("ports") or []
        return {
            "summary": data.get("summary"),
            "ports": [compact_port(row) for row in rows[:limit] if isinstance(row, Mapping)],
        }

    def port_history(self, port: int, *, hours: int = 24) -> dict[str, Any]:
        _validate_port(port)
        if not 1 <= hours <= 720:
            raise PortLightError("invalid_request", "hours must be between 1 and 720")
        return self._transport.request(
            "GET", f"/api/ports/{port}/history?hours={hours}",
        )

    def health(self) -> dict[str, Any]:
        return self._transport.request("GET", "/api/health")


def _validate_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise PortLightError("invalid_request", "port must be between 1 and 65535")
