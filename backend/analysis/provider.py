"""Bounded Chat Completions transport and connectivity probes."""

from __future__ import annotations

import asyncio
import errno
import http.client
import json
import os
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

import httpx

from . import __version__
from .evidence import AnalysisError
from .settings import normalize_base_url, resolve_target_addresses, validate_target_url

MAX_RESPONSE_BYTES = 65536
CONNECT_TIMEOUT_S = 10.0

PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "token_parameter": "max_completion_tokens",
        "json_mode": True,
    },
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "token_parameter": "max_tokens",
        "json_mode": True,
    },
    "opencode-go": {
        "name": "OpenCode Go",
        "endpoint": "https://opencode.ai/zen/go/v1/chat/completions",
        "token_parameter": "max_tokens",
        "json_mode": False,
        "notice": (
            "OpenCode Go Chat Completions models target coding agents; "
            "review the service terms before use."
        ),
    },
}


def configured_providers():
    """Presets and defaults for the editable OpenAI-compatible connection."""
    providers = {key: dict(value) for key, value in PROVIDERS.items()}
    providers["custom"] = {
        "name": "Custom (OpenAI compatible)",
        "token_parameter": "max_tokens",
        "json_mode": False,
    }
    base_url = os.environ.get("PORT_LIGHT_BYOK_BASE_URL", "").strip().rstrip("/")
    if base_url:
        try:
            base_url = normalize_base_url(base_url)
        except AnalysisError:
            raise ValueError(
                "PORT_LIGHT_BYOK_BASE_URL must be an allowed HTTP(S) base URL "
                "without credentials, query, or fragment"
            ) from None
        token_parameter = os.environ.get("PORT_LIGHT_BYOK_TOKEN_PARAMETER", "max_tokens")
        if token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported PORT_LIGHT_BYOK_TOKEN_PARAMETER")
        providers["custom"] = {
            "name": os.environ.get("PORT_LIGHT_BYOK_NAME", "Custom (OpenAI compatible)")[:80],
            "base_url": base_url,
            "endpoint": base_url + "/chat/completions",
            "token_parameter": token_parameter,
            "json_mode": os.environ.get("PORT_LIGHT_BYOK_JSON_MODE", "0") == "1",
        }
    return providers


def configured_proxy():
    """Read only the explicit AI proxy; never expose its address in errors."""
    address = os.environ.get("PORT_LIGHT_AI_PROXY", "").strip()
    if not address:
        return None
    try:
        url = urlsplit(address)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.path not in {"", "/"} or url.query or url.fragment
                or any(character.isspace() for character in address)):
            raise ValueError("invalid proxy")
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("invalid proxy port")
    except ValueError:
        raise AnalysisError(
            "provider_connection", "Invalid AI proxy configuration.", 502
        ) from None
    return address


def decode_completion(body: bytes, *, probe=False) -> tuple[str, dict]:
    """Validate the shared Chat Completions envelope before using model content."""
    try:
        document = json.loads(body)
        if not isinstance(document, dict) or not isinstance(document.get("choices"), list):
            raise ValueError("invalid envelope")
        choice = document["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("invalid choice")
        message = choice["message"]
        finish = choice.get("finish_reason")
        if probe:
            if finish not in {"stop", "length"}:
                raise ValueError("invalid probe completion")
        elif finish != "stop":
            raise AnalysisError(
                "incomplete_output",
                "Model output was truncated and was not used or retried.",
                502,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # A small connectivity probe can spend its budget on reasoning.
            # The valid envelope still confirms the model accepted the request;
            # full analysis keeps requiring a complete, visible response.
            reasoning = message.get("reasoning_content")
            if (not probe or finish != "length" or not isinstance(reasoning, str)
                    or not reasoning.strip() or content not in (None, "")):
                raise ValueError("missing content")
            content = ""
        usage = document.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError("invalid usage")
        return content, {
            field: value
            for field in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(value := usage.get(field)) is int and 0 <= value <= 10**9
        }
    except (ValueError, KeyError, IndexError, TypeError):
        raise AnalysisError(
            "invalid_output", "Model returned an unreadable response.", 502
        ) from None


def _strip_zone(host: str) -> str:
    return host.split("%", 1)[0]


def _check_status(status_code: int) -> None:
    if status_code in {401, 403}:
        raise AnalysisError(
            "provider_auth",
            "Model provider rejected authentication or model access.",
            502,
        )
    if status_code == 429:
        raise AnalysisError(
            "provider_limit", "Model provider quota or rate limit exceeded.", 502
        )
    if status_code != 200:
        raise AnalysisError(
            "provider_error", "Model provider rejected the request.", 502
        )


class _ActiveSocket:
    """Track the active socket so cancellation or timeout aborts blocking I/O."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def set(self, sock: socket.socket | None) -> None:
        close_now = False
        with self._lock:
            self._sock = sock
            if self._cancelled and sock is not None:
                close_now = True
                self._sock = None
        if close_now:
            try:
                sock.close()
            except OSError:
                pass
            raise TimeoutError("Model request cancelled")

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            sock = self._sock
            self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def _connect_pinned(
    addresses: list[tuple], timeout: float, active: _ActiveSocket
) -> socket.socket:
    last_err: OSError | None = None
    for af, socktype, proto, _canonname, sockaddr in addresses:
        if active.cancelled:
            raise TimeoutError("Model request cancelled")
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            sock.settimeout(timeout)
            active.set(sock)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_err = exc
            active.set(None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            if active.cancelled:
                raise TimeoutError("Model request cancelled") from None
    if last_err is not None:
        raise last_err
    raise OSError("no resolved addresses available")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        timeout: float,
        addresses: list[tuple],
        active: _ActiveSocket,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._addresses = addresses
        self._active = active

    def connect(self) -> None:
        self.sock = _connect_pinned(self._addresses, self.timeout, self._active)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                raise


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        timeout: float,
        context: ssl.SSLContext,
        addresses: list[tuple],
        active: _ActiveSocket,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._addresses = addresses
        self._active = active

    def connect(self) -> None:
        raw_sock = _connect_pinned(self._addresses, self.timeout, self._active)
        try:
            raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                self._active.set(None)
                raw_sock.close()
                raise
        try:
            wrapped = self._context.wrap_socket(
                raw_sock, server_hostname=_strip_zone(self.host)
            )
        except BaseException:
            self._active.set(None)
            raw_sock.close()
            raise
        self._active.set(wrapped)
        self.sock = wrapped


def _post_pinned_sync(
    endpoint: str,
    addresses: list[tuple],
    payload: dict,
    headers: dict[str, str],
    *,
    timeout: float,
    active: _ActiveSocket,
) -> tuple[int, bytes]:
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    encoded_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        **headers,
    }
    deadline = time.monotonic() + timeout
    connect_timeout = min(CONNECT_TIMEOUT_S, max(timeout, 0.001))
    if parsed.scheme == "https":
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
            host,
            port=port,
            timeout=connect_timeout,
            context=ssl.create_default_context(),
            addresses=addresses,
            active=active,
        )
    else:
        conn = _PinnedHTTPConnection(
            host,
            port=port,
            timeout=connect_timeout,
            addresses=addresses,
            active=active,
        )
    try:
        conn.connect()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Model request timed out")
        conn.sock.settimeout(remaining)
        conn.request("POST", path, body=encoded_body, headers=request_headers)
        response = conn.getresponse()
        status = int(response.status)
        if status != 200:
            return status, b""
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Model request timed out")
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            chunk = response.read(8192)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AnalysisError(
                    "invalid_output", "Model response exceeded size limit.", 502
                )
        return status, bytes(body)
    finally:
        active.set(None)
        conn.close()


class ChatGateway:
    def __init__(self, transport=None, *, providers=None):
        self.transport = transport
        self.providers = providers if providers is not None else PROVIDERS

    def configuration(self, provider, connection=None):
        settings = self.providers.get(provider)
        if settings is None:
            raise AnalysisError(
                "unsupported_provider", "Provider is not supported in the current mode.", 422
            )
        if connection is not None:
            if provider != "custom":
                raise AnalysisError(
                    "invalid_input", "Preset providers cannot override base URL.", 422
                )
            settings = {**settings, **connection,
                        "endpoint": normalize_base_url(connection["base_url"]) + "/chat/completions"}
        if not settings.get("endpoint"):
            raise AnalysisError(
                "configuration_changed",
                "Save a custom base URL in AI settings before starting.",
                409,
            )
        return settings

    async def _post(self, provider, key, payload, *, session_id="", timeout=60, connection=None):
        settings = self.configuration(provider, connection)
        validate_target_url(settings["endpoint"])
        if provider == "opencode-go" and payload.get("model") == "deepseek-v4.1-flash":
            # Go counts reasoning against the output limit. This bounded
            # selection task needs the budget for the visible JSON response.
            payload = {**payload, "reasoning_effort": "none"}
        headers = {
            "Authorization": f"Bearer {key}",
            "User-Agent": f"Port-Light/{__version__}",
        }
        if provider == "opencode-go":
            headers["x-opencode-session"] = session_id
        proxy = None if self.transport is not None else configured_proxy()
        if self.transport is not None or proxy is not None:
            if proxy is not None:
                await asyncio.to_thread(
                    resolve_target_addresses, settings["endpoint"], allow_unresolved=True
                )
            try:
                async with (
                    asyncio.timeout(timeout),
                    httpx.AsyncClient(
                        transport=self.transport,
                        timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_S),
                        follow_redirects=False,
                        trust_env=False,
                        proxy=proxy,
                    ) as client,
                    client.stream(
                        "POST", settings["endpoint"], json=payload, headers=headers
                    ) as response,
                ):
                    _check_status(response.status_code)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise AnalysisError(
                                "invalid_output", "Model response exceeded size limit.", 502
                            )
                return bytes(body)
            except (TimeoutError, httpx.TimeoutException):
                raise AnalysisError(
                    "provider_timeout", "Model request timed out and was not retried.", 504
                ) from None
            except (httpx.HTTPError, httpx.InvalidURL):
                raise AnalysisError(
                    "provider_connection",
                    "Unable to connect to model provider; request was not retried.",
                    502,
                ) from None
        active = _ActiveSocket()
        try:
            async with asyncio.timeout(timeout):
                addresses = await asyncio.to_thread(
                    resolve_target_addresses, settings["endpoint"]
                )
                status_code, body = await asyncio.to_thread(
                    _post_pinned_sync,
                    settings["endpoint"],
                    addresses,
                    payload,
                    headers,
                    timeout=timeout,
                    active=active,
                )
            _check_status(status_code)
            return body
        except AnalysisError:
            raise
        except TimeoutError:
            active.cancel()
            raise AnalysisError(
                "provider_timeout", "Model request timed out and was not retried.", 504
            ) from None
        except asyncio.CancelledError:
            active.cancel()
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise AnalysisError(
                "provider_connection",
                "Unable to connect to model provider; request was not retried.",
                502,
            ) from None

    async def probe(self, provider, model, key, *, session_id="", connection=None):
        """Check the saved Chat Completions path with a fixed, small request."""
        settings = self.configuration(provider, connection)
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "Reply with a JSON object containing only ok: true."
                 if settings["json_mode"] else "Reply with OK."}
            ],
            "stream": False,
            settings["token_parameter"]: 64,
        }
        if settings["json_mode"]:
            payload["response_format"] = {"type": "json_object"}
        if provider == "openai":
            payload["store"] = False
        body = await self._post(provider, key, payload, session_id=session_id, timeout=30,
                                connection=connection)
        decode_completion(body, probe=True)
