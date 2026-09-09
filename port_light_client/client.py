"""Dependency-free client interface for a running Port-Light instance."""

from __future__ import annotations

import base64
import json as jsonlib
import math
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol


DEFAULT_BASE_URL = "http://127.0.0.1:2100"


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
        json: Mapping[str, Any] | None = None,
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


def _http_error(code: int, detail: str, server_code: str = "") -> PortLightError:
    if code == 503 and server_code == "authentication_misconfigured":
        kind = "authentication_misconfigured"
    elif code == 405:
        kind = "upgrade_required"
    elif code in (401, 403):
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
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
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
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise PortLightError("invalid_request", "request path must start with /")
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "application/json")
        data = None
        if json is not None:
            data = jsonlib.dumps(json).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if self._basic_auth:
            token = base64.b64encode(self._basic_auth.encode()).decode()
            request_headers["Authorization"] = "Basic " + token
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers=request_headers,
            data=data,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(256 * 1024)
            detail = ""
            server_code = ""
            try:
                payload = jsonlib.loads(raw) if raw else {}
                if isinstance(payload, dict):
                    detail = str(payload.get("detail") or "")
                    server_code = str(payload.get("code") or "")
            except (UnicodeDecodeError, jsonlib.JSONDecodeError):
                pass
            raise _http_error(exc.code, detail, server_code) from exc
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
            payload = jsonlib.loads(body)
        except (UnicodeDecodeError, jsonlib.JSONDecodeError) as exc:
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


def validate_request_key(key: str) -> str:
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):
        raise PortLightError("invalid_request", "retain a random URL-safe request key of 43–128 characters before reserving")
    return key


def compact_port(row: Mapping[str, Any]) -> dict[str, Any]:
    containers = row.get("containers") or []
    names = [
        item["name"] for item in containers
        if isinstance(item, Mapping)
        and isinstance(item.get("name"), str)
        and item["name"]
    ]
    known = row.get("known_service") or {}
    if (
        not names
        and isinstance(known, Mapping)
        and isinstance(known.get("name"), str)
        and known["name"]
    ):
        names = [known["name"]]
    return {
        "port": row.get("port"),
        "status": row.get("status"),
        "protocol": row.get("protocol"),
        "bind_scope": row.get("bind_scope"),
        "names": names,
    }


class PortLightClient:
    """Validated HTTP client shared by command-line and MCP adapters."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
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

    def _require_capability(
        self,
        name: str,
        version: int = 1,
        *,
        allow_legacy: bool = True,
    ) -> None:
        capabilities = self.meta().get("capabilities")
        if capabilities is None:
            if allow_legacy:
                return
            raise PortLightError(
                "unsupported_server",
                f"Port-Light does not advertise the required {name} capability; upgrade the server",
            )
        advertised = capabilities.get(name) if isinstance(capabilities, Mapping) else None
        if type(advertised) is not int or advertised < version:
            raise PortLightError(
                "unsupported_server",
                f"Port-Light does not advertise the required {name} capability; upgrade the server",
            )

    def doctor(self) -> dict[str, Any]:
        self._require_capability("doctor")
        document = self._transport.request("GET", "/api/doctor")
        document.pop("report", None)
        counts = document.get("counts")
        context = document.get("context")
        checks = document.get("checks")
        count_keys = ("pass", "warning", "fail", "info")
        if (
            document.get("schema_version") != 1
            or document.get("overall") not in ("healthy", "attention")
            or not isinstance(counts, Mapping)
            or any(type(counts.get(key)) is not int or counts[key] < 0 for key in count_keys)
            or not isinstance(context, Mapping)
            or not isinstance(context.get("version"), str)
            or not context["version"]
            or not isinstance(checks, list)
            or any(
                not isinstance(check, Mapping)
                or not isinstance(check.get("id"), str)
                or check.get("status") not in ("pass", "warning", "fail", "info")
                or not isinstance(check.get("detail"), str)
                for check in checks
            )
            or any(
                counts[key] != sum(check["status"] == key for check in checks)
                for key in count_keys
            )
            or document["overall"]
            != ("attention" if counts["warning"] or counts["fail"] else "healthy")
        ):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid Doctor response",
            )
        return document

    def check_port(self, port: int) -> dict[str, Any]:
        _validate_port(port)
        self._require_capability("port_check")
        row = self._transport.request(
            "GET", f"/api/ports/{port}?include_hidden=false",
        )
        _validate_port_row(row, expected_port=port)
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
        request_key: str | None = None,
        rule: str | None = None,
    ) -> dict[str, Any]:
        if type(count) is not int or not 1 <= count <= 64:
            raise PortLightError("invalid_request", "count must be between 1 and 64")
        if start is not None:
            _validate_port(start)
        if end is not None:
            _validate_port(end)
        if ttl is not None and (
            type(ttl) is not int or not 60 <= ttl <= 604800
        ):
            raise PortLightError("invalid_request", "ttl must be between 60 and 604800 seconds")
        if start is not None and end is not None and end < start:
            raise PortLightError("invalid_request", "end must be greater than or equal to start")
        if scope not in ("self", "all"):
            raise PortLightError("invalid_request", "scope must be self or all")
        if rule is not None:
            self._require_capability("port_rules", allow_legacy=False)
        reservation_expected = reserve or ttl is not None
        if reservation_expected:
            self._require_capability("reservations")
        if require_count:
            # An older server can ignore require_count after creating a partial
            # reservation, so exact mutation semantics must be known in advance.
            self._require_capability("exact_reservations", allow_legacy=False)
        if scope == "all":
            self._require_capability("scope_all")
        params: list[tuple[str, str]] = [("count", str(count)), ("scope", scope)]
        if rule is not None:
            params.append(("rule", rule))
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
        headers = {"X-Agent-Token": self._agent_token} if self._agent_token else {}
        if reservation_expected:
            self._require_capability("idempotent_reservations", allow_legacy=False)
            headers["Idempotency-Key"] = validate_request_key(request_key)
            result = self._transport.request("POST", "/api/reservations", headers=headers, json={
                "count": count, "start": start, "end": end, "label": label,
                "ttl": ttl, "scope": scope, "require_count": require_count,
                **({"rule": rule} if rule is not None else {}),
            })
        else:
            result = self._transport.request(
                "GET", "/api/ports/suggest?" + urllib.parse.urlencode(params), headers=headers or None)
        _validate_suggestion(
            result,
            count=count,
            start=start,
            end=end,
            scope=scope,
            reservation_expected=reservation_expected,
            ttl=ttl,
        )
        return result

    def reserve_ports(
        self,
        *,
        count: int = 1,
        start: int | None = None,
        end: int | None = None,
        label: str = "",
        ttl: int | None = 3600,
        scope: str = "self",
        request_key: str | None = None,
        rule: str | None = None,
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
            request_key=request_key,
            **({"rule": rule} if rule is not None else {}),
        )
        reservations = result["reservations"]
        if not reservations:
            raise PortLightError("no_capacity", "no free ports were available in the requested range")
        return result

    def recover_reservation(self, request_key: str, parameters: dict) -> dict[str, Any]:
        self._require_capability("idempotent_reservations", allow_legacy=False)
        headers = {"Idempotency-Key": validate_request_key(request_key)}
        if self._agent_token:
            headers["X-Agent-Token"] = self._agent_token
        result = self._transport.request("GET", "/api/reservations/request", headers=headers)
        _validate_suggestion(result, count=parameters.get("count", 1),
                             start=parameters.get("start"), end=parameters.get("end"),
                             scope=parameters.get("scope", "self"), reservation_expected=True,
                             ttl=parameters.get("ttl"))
        return result

    def release_port(self, port: int, token: str) -> dict[str, Any]:
        _validate_port(port)
        if not token:
            raise PortLightError("reservation_token_missing", "reservation token is required")
        self._require_capability("reservation_release")
        result = self._transport.request(
            "DELETE",
            f"/api/reservations/{port}",
            headers={"X-Reservation-Token": token},
        )
        if result.get("status") != "ok":
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid reservation release response",
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
        if type(limit) is not int or not 1 <= limit <= 500:
            raise PortLightError("invalid_request", "limit must be between 1 and 500")
        query = urllib.parse.urlencode({
            "range_start": start,
            "range_end": end,
            "include_hidden": "false",
        })
        data = self._transport.request("GET", "/api/ports?" + query)
        rows = data.get("ports")
        summary = data.get("summary")
        if (
            not isinstance(rows, list)
            or not isinstance(summary, Mapping)
            or any(not isinstance(row, Mapping) for row in rows)
        ):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid occupancy response",
            )
        for row in rows:
            _validate_port_row(row)
            if not start <= row["port"] <= end:
                raise PortLightError(
                    "invalid_response",
                    "Port-Light returned occupancy outside the requested range",
                )
        if len({row["port"] for row in rows}) != len(rows):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned duplicate occupancy rows",
            )
        return {
            "summary": dict(summary),
            "ports": [compact_port(row) for row in rows[:limit]],
        }

    def port_history(self, port: int, *, hours: int = 24) -> dict[str, Any]:
        _validate_port(port)
        if type(hours) is not int or not 1 <= hours <= 720:
            raise PortLightError("invalid_request", "hours must be between 1 and 720")
        result = self._transport.request(
            "GET", f"/api/ports/{port}/history?hours={hours}",
        )
        if result.get("port") != port or not isinstance(result.get("events"), list):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid port history response",
            )
        return result

    def health(self) -> dict[str, Any]:
        result = self._transport.request("GET", "/api/health")
        if (
            result.get("status") not in ("ok", "degraded")
            or not isinstance(result.get("degradations"), list)
        ):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid health response",
            )
        return result


def create_client(
    environ: Mapping[str, str],
    *,
    base_url: str | None = None,
    timeout: float | str | None = None,
    ca_file: str | None = None,
) -> PortLightClient:
    """Build a client from shared CLI/MCP environment configuration."""
    timeout_value = timeout if timeout is not None else environ.get("PORT_LIGHT_TIMEOUT", "5")
    try:
        parsed_timeout = float(timeout_value)
    except (TypeError, ValueError) as exc:
        raise PortLightError(
            "invalid_timeout",
            "PORT_LIGHT_TIMEOUT must be a number greater than zero",
        ) from exc
    return PortLightClient(
        base_url if base_url is not None else environ.get("PORT_LIGHT_URL", DEFAULT_BASE_URL),
        basic_auth=environ.get("PORT_LIGHT_AUTH", ""),
        agent_token=environ.get("PORT_LIGHT_AGENT_TOKEN", "") or environ.get("AGENT_TOKEN", ""),
        timeout=parsed_timeout,
        ca_file=ca_file if ca_file is not None else environ.get("PORT_LIGHT_CA_FILE") or None,
    )


def _validate_port_row(row: Mapping[str, Any], *, expected_port: int | None = None) -> None:
    port = row.get("port")
    if (
        type(port) is not int
        or not 1 <= port <= 65535
        or (expected_port is not None and port != expected_port)
        or row.get("status") not in ("free", "configured", "used")
        or not isinstance(row.get("protocol"), str)
        or not row["protocol"]
        or not (row.get("bind_scope") is None or isinstance(row["bind_scope"], str))
    ):
        raise PortLightError(
            "invalid_response",
            "Port-Light returned an invalid port response",
        )


def _validate_scope(actual: Any, requested: str) -> bool:
    if requested == "self":
        return actual == "self"
    if not isinstance(actual, str) or not actual.startswith("all:"):
        return False
    reached, separator, total = actual.removeprefix("all:").partition("/")
    if not separator:
        return False
    try:
        reached_count = int(reached)
        total_count = int(total)
    except ValueError:
        return False
    return reached_count == total_count and reached_count >= 0


def _validate_suggestion(
    result: Mapping[str, Any],
    *,
    count: int,
    start: int | None,
    end: int | None,
    scope: str,
    reservation_expected: bool,
    ttl: int | None,
) -> None:
    ports = result.get("ports")
    result_range = result.get("range")
    if (
        not isinstance(ports, list)
        or any(type(port) is not int or not 1 <= port <= 65535 for port in ports)
        or len(set(ports)) != len(ports)
        or len(ports) > count
        or not isinstance(result_range, Mapping)
        or type(result_range.get("start")) is not int
        or type(result_range.get("end")) is not int
        or not 1 <= result_range["start"] <= result_range["end"] <= 65535
        or (start is not None and result_range["start"] != start)
        or (end is not None and result_range["end"] != end)
        or any(not result_range["start"] <= port <= result_range["end"] for port in ports)
        or not _validate_scope(result.get("scope"), scope)
    ):
        raise PortLightError(
            "invalid_response",
            "Port-Light returned an invalid port suggestion response",
        )
    reservations = result.get("reservations")
    if not reservation_expected:
        if reservations not in (None, []):
            raise PortLightError(
                "invalid_response",
                "Port-Light unexpectedly created reservations",
            )
        return
    if not isinstance(reservations, list) or len(reservations) != len(ports):
        raise PortLightError(
            "unsupported_server",
            "Port-Light did not return one reservation token per selected port; upgrade the server",
        )
    for reservation in reservations:
        if (
            not isinstance(reservation, Mapping)
            or not isinstance(reservation.get("token"), str)
            or not reservation["token"]
        ):
            raise PortLightError(
                "unsupported_server",
                "Port-Light did not return secure reservation tokens; upgrade the server",
            )
        expires_at = reservation.get("expires_at")
        if expires_at is not None and (
            type(expires_at) not in (int, float)
            or not math.isfinite(expires_at)
            or expires_at <= 0
        ):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid reservation expiry",
            )
        if (ttl is None) != (expires_at is None):
            raise PortLightError(
                "invalid_response",
                "Port-Light returned a reservation with an unexpected expiry policy",
            )
    if (
        any(type(reservation.get("port")) is not int for reservation in reservations)
        or [reservation["port"] for reservation in reservations] != ports
    ):
        raise PortLightError(
            "invalid_response",
            "Port-Light returned reservation tokens for different ports",
        )


def _validate_port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise PortLightError("invalid_request", "port must be between 1 and 65535")
