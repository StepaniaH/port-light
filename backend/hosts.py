"""Other Port-Light instances: stored peers and a same-origin occupancy proxy.

Each peer still scans its own host. This process only pulls ``GET /api/ports``
(and health) so one UI can show several occupancy maps. It does not attach a
remote ``docker.sock``.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse, urlunparse

from . import port_store, settings as app_settings
from .netaddr import strip_zone

LOCAL_ID = "local"
MAX_PEERS = 32
PEER_FETCH_CONCURRENCY = 6
MAX_NAME = 40
MAX_DESCRIPTION = port_store.MAX_PEER_DESCRIPTION
MAX_USER = 128
MAX_PASSWORD = 256
FETCH_TIMEOUT_S = 4.0
MAX_BODY = 2_000_000
_ID_RE = re.compile(r"^[a-z0-9]{8,16}$")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
})
_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_PEER_STATUSES = frozenset({"used", "configured", "free", "unknown"})
_PEER_SOURCE_TYPES = frozenset({"docker", "system", "host", "manual", "unknown"})
_PEER_BIND_SCOPES = frozenset({"public", "lan", "link", "localhost"})
_PEER_SCAN_STATES = frozenset({"ok", "failed", "disabled", "unknown"})
_PEER_HEALTH_STATUSES = frozenset({"ok", "degraded"})
_PEER_LISTEN_SOURCES = frozenset({"host_proc", "ss", "lsof", "proc", "none"})
_PEER_DIAG_CODES = frozenset({"port_range", "port_budget", "invalid_file"})
_PEER_CONTAINER_STATUSES = frozenset({
    "created", "running", "paused", "restarting", "removing", "exited", "dead",
})
_PEER_PUBLISH_MODES = frozenset({"host", "ingress"})
_PEER_HISTORY_STATES = frozenset({"used", "configured", "free"})
_MAX_PEER_PORTS = 65535
_MAX_PEER_CONTAINERS = 256
_MAX_PEER_COMPOSE = 256
_MAX_PEER_DEGRADATIONS = 20
_MAX_PEER_DIAGNOSTICS = 16
_MAX_PEER_VIOLATIONS = 64
_MAX_PEER_URLS = 128
_MAX_PEER_IPS = 128
_MAX_PEER_HISTORY_EVENTS = 5000


class HostsError(ValueError):
    """Invalid peer list or URL."""


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PassThroughErrors(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def _connect_pinned(
    addresses: list[tuple],
    timeout: float | None,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    last_err: OSError | None = None
    for af, socktype, proto, _canonname, sockaddr in addresses:
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_err = exc
            if sock is not None:
                sock.close()
    if last_err is not None:
        raise last_err
    raise OSError("no resolved addresses available")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, addresses: list[tuple], **kwargs) -> None:
        super().__init__(host, **kwargs)
        self._addresses = addresses

    def connect(self) -> None:
        self.sock = _connect_pinned(self._addresses, self.timeout, self.source_address)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                raise


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, addresses: list[tuple], **kwargs) -> None:
        super().__init__(host, **kwargs)
        self._addresses = addresses

    def connect(self) -> None:
        self.sock = _connect_pinned(self._addresses, self.timeout, self.source_address)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                raise
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=strip_zone(self.host),
        )


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, addresses: list[tuple]) -> None:
        super().__init__()
        self._addresses = addresses

    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req, addresses=self._addresses)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, addresses: list[tuple]) -> None:
        super().__init__()
        self._addresses = addresses

    def https_open(self, req):
        return self.do_open(
            _PinnedHTTPSConnection, req,
            context=self._context, addresses=self._addresses,
        )


def local_display_name() -> str:
    values, _ = app_settings.resolve()
    raw = str(values.get("host_name") or "").strip()
    if raw:
        return raw[:MAX_NAME]
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return (name or "This machine")[:MAX_NAME]


def public_local() -> dict:
    values, _ = app_settings.resolve()
    return {
        "id": LOCAL_ID,
        "name": local_display_name(),
        "description": str(values.get("host_description") or "").strip()[:MAX_DESCRIPTION],
        "local": True,
    }


def list_public_peers() -> list[dict]:
    return [_public_peer(p) for p in _resolved_peers()]


def hosts_readonly() -> bool:
    return app_settings.settings_readonly()


def catalog() -> dict:
    return {
        "local": public_local(),
        "peers": list_public_peers(),
        "readonly": hosts_readonly(),
        "max_peers": MAX_PEERS,
    }


def replace_peers(raw_peers) -> list[dict]:
    if hosts_readonly():
        raise PermissionError("hosts are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    normalized = _normalize_peers(raw_peers, _resolved_peers())
    port_store.replace_peers(normalized)
    return [_public_peer(p) for p in normalized]


def get_peer(host_id: str) -> dict | None:
    if not host_id or host_id == LOCAL_ID:
        return None
    for peer in _resolved_peers():
        if peer["id"] == host_id:
            return peer
    return None


def connection_test_peer(values: dict) -> dict:
    """Resolve the current form without saving it or exposing saved passwords."""
    previous = get_peer(values.get("id", "")) or {}
    url = origin_url(values["url"])
    if (previous.get("password") and previous.get("url") != url
            and not values.get("password") and not values.get("clear_auth")):
        raise HostsError("credentials_required")
    return {
        "url": url,
        "username": "" if values.get("clear_auth") else values.get("username", previous.get("username", "")),
        "password": "" if values.get("clear_auth") else values.get("password", previous.get("password", "")),
    }


def origin_url(url: str) -> str:
    parsed = _parse_peer_url(url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _fetch_sync(
    peer: dict,
    path: str,
    query: dict[str, str],
    if_none_match: str | None = None,
) -> tuple[int, dict | None, str | None]:
    if not path.startswith("/api/"):
        return 502, None, None
    try:
        origin = _parse_peer_url(peer["url"])
        addresses = _validate_addresses(origin)
    except (HostsError, OSError, ValueError):
        return 502, None, None
    url = urlunparse(origin) + path
    if query:
        url = url + "?" + urlencode(query)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Port-Light-hub",
    }
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    user = peer.get("username") or ""
    password = peer.get("password") or ""
    req = urllib.request.Request(url, headers=headers, method="GET")
    if user or password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        req.add_header("Authorization", "Basic " + token)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PinnedHTTPHandler(addresses),
        _PinnedHTTPSHandler(addresses),
        _RefuseRedirect,
        _PassThroughErrors,
    )
    try:
        with opener.open(req, timeout=FETCH_TIMEOUT_S) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            etag = resp.headers.get("ETag")
            raw = resp.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        etag = exc.headers.get("ETag") if exc.headers else None
        if status in _REDIRECTS:
            return 502, None, None
        if status == 304:
            return 304, None, etag
        try:
            raw = exc.read(MAX_BODY + 1)
        except Exception:
            return status, None, etag
    except TimeoutError:
        return 504, None, None
    except urllib.error.URLError as exc:
        return (504 if isinstance(exc.reason, TimeoutError) else 502), None, None
    except (OSError, ValueError):
        return 502, None, None
    if status in _REDIRECTS:
        return 502, None, None
    if status == 304:
        return 304, None, etag
    if len(raw) > MAX_BODY:
        return 502, None, None
    if status != 200:
        return status, None, etag
    if not raw:
        return 502, None, etag
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 502, None, etag
    if not isinstance(data, dict):
        return 502, None, etag
    return status, data, etag


def fetch_peer_json(
    peer: dict,
    path: str,
    query: dict[str, str],
    if_none_match: str | None = None,
) -> tuple[int, dict | None, str | None]:
    """GET ``path`` on the peer. Returns ``(status, json_or_none, etag)``.

    Redirects are not followed. 3xx and oversize bodies become 502.
    """
    return _fetch_sync(peer, path, query, if_none_match)


async def fetch_peers_json(
    peers: list[dict],
    path: str,
    query: dict[str, str],
) -> list[tuple[int, dict | None, str | None]]:
    """Fetch one resource from many peers without unbounded fan-out."""
    gate = asyncio.Semaphore(PEER_FETCH_CONCURRENCY)

    async def fetch_one(peer: dict) -> tuple[int, dict | None, str | None]:
        async with gate:
            return await asyncio.to_thread(fetch_peer_json, peer, path, query)

    return list(await asyncio.gather(*(fetch_one(peer) for peer in peers)))


def _resolved_peers() -> list[dict]:
    source = app_settings.settings_source()
    if source == "env":
        return _env_peers()
    if source == "file":
        return _file_peers()
    if port_store.peers_stored():
        return _file_peers()
    return _env_peers()


def _file_peers() -> list[dict]:
    peers = port_store.get_peers()
    if len(peers) > MAX_PEERS:
        raise HostsError(f"at most {MAX_PEERS} other machines")
    return peers


def _env_peers() -> list[dict]:
    raw = os.environ.get("PORT_LIGHT_PEERS")
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HostsError("PORT_LIGHT_PEERS must be a JSON array") from exc
    return _normalize_peers(data, [], deterministic_ids=True)


def _public_peer(peer: dict) -> dict:
    return {
        "id": peer["id"],
        "name": peer["name"],
        "description": str(peer.get("description") or ""),
        "url": peer["url"],
        "username": str(peer.get("username") or ""),
        "has_auth": bool(peer.get("username") or peer.get("password")),
        "local": False,
    }


def _normalize_peers(
    raw_peers,
    existing: list[dict],
    *,
    deterministic_ids: bool = False,
) -> list[dict]:
    if raw_peers is None:
        return []
    if not isinstance(raw_peers, list):
        raise HostsError("peers must be a list")
    if len(raw_peers) > MAX_PEERS:
        raise HostsError(f"at most {MAX_PEERS} other machines")
    by_id = {p["id"]: p for p in existing if isinstance(p, dict) and p.get("id")}
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    out: list[dict] = []
    for item in raw_peers:
        if not isinstance(item, dict):
            raise HostsError("each peer must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise HostsError("name is required")
        if len(name) > MAX_NAME:
            raise HostsError("name is too long")
        url = origin_url(str(item.get("url") or ""))
        url_key = url.rstrip("/").lower()
        if url_key in seen_urls:
            raise HostsError("duplicate URL")
        seen_urls.add(url_key)
        host_id = str(item.get("id") or "").strip().lower()
        if host_id == LOCAL_ID:
            raise HostsError("id 'local' is reserved")
        if host_id and not _ID_RE.match(host_id):
            raise HostsError("invalid id")
        if not host_id:
            if deterministic_ids:
                host_id = hashlib.sha256(url_key.encode("utf-8")).hexdigest()[:12]
                suffix = 1
                while host_id in seen_ids:
                    host_id = hashlib.sha256(f"{url_key}#{suffix}".encode("utf-8")).hexdigest()[:12]
                    suffix += 1
            else:
                host_id = secrets.token_hex(4)
                while host_id in seen_ids:
                    host_id = secrets.token_hex(4)
        else:
            while host_id in seen_ids:
                host_id = secrets.token_hex(4)
        seen_ids.add(host_id)
        prev = by_id.get(host_id, {})
        if (prev.get("password") and prev.get("url") != url
                and not item.get("password")
                and not (item.get("username") == "" and item.get("password") == "")):
            raise HostsError("credentials_required")
        if "description" not in item:
            description = str(prev.get("description") or "").strip()
        else:
            description = str(item.get("description") or "").strip()
        if len(description) > MAX_DESCRIPTION:
            raise HostsError("description is too long")
        if "username" not in item:
            username = str(prev.get("username") or "")
        else:
            username = str(item.get("username") or "").strip()
        if len(username) > MAX_USER:
            raise HostsError("username is too long")
        if "password" not in item:
            password = str(prev.get("password") or "")
        else:
            password = str(item.get("password") or "")
        if len(password) > MAX_PASSWORD:
            raise HostsError("password is too long")
        out.append({
            "id": host_id,
            "name": name,
            "description": description,
            "url": url,
            "username": username,
            "password": password,
        })
    return out


def _ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _ip_allowed(ip.ipv4_mapped)
    if ip in _BLOCKED_IPS:
        return False
    if ip.is_unspecified or ip.is_multicast:
        return False
    if ip.is_loopback:
        return True
    if ip.version == 4:
        if ip.is_link_local:
            return False
        if ip.is_private:
            return True
        return ip in _CGNAT
    return bool(ip.is_private or ip.is_link_local)


def _validate_addresses(parsed) -> list[tuple]:
    """Resolve and validate every current DNS answer for connection-level pinning."""
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(
        not _ip_allowed(ipaddress.ip_address(strip_zone(address[4][0])))
        for address in addresses
    ):
        raise HostsError("that host resolves to a disallowed address")
    return list(addresses)


def _parse_peer_url(url: str):
    text = (url or "").strip()
    if not text:
        raise HostsError("url is required")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise HostsError("url must be http or https")
    if parsed.username or parsed.password:
        raise HostsError("put credentials in the username/password fields, not the URL")
    if not parsed.hostname:
        raise HostsError("url host is required")
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTS:
        raise HostsError("that host is not allowed")
    try:
        ip = ipaddress.ip_address(strip_zone(host))
    except ValueError:
        ip = None
    if ip is not None and not _ip_allowed(ip):
        raise HostsError("that host is not allowed")
    port = parsed.port
    if port is not None and not (1 <= port <= 65535):
        raise HostsError("url port is invalid")
    host_part = parsed.hostname
    if ":" in (host_part or ""):
        host_part = f"[{host_part}]"
    if port and not (parsed.scheme == "http" and port == 80) and not (parsed.scheme == "https" and port == 443):
        netloc = f"{host_part}:{port}"
    else:
        netloc = host_part
    return parsed._replace(scheme=parsed.scheme, netloc=netloc, path="", params="", query="", fragment="")


def _peer_int(value, *, field: str, lo: int = 0, hi: int | None = None) -> int:
    if type(value) is not int or value < lo or (hi is not None and value > hi):
        raise HostsError(f"invalid peer {field}")
    return value


def _peer_opt_int(value, *, field: str, lo: int = 0, hi: int | None = None) -> int | None:
    if value is None:
        return None
    return _peer_int(value, field=field, lo=lo, hi=hi)


def _peer_bool(value, *, field: str) -> bool:
    if type(value) is not bool:
        raise HostsError(f"invalid peer {field}")
    return value


def _peer_str(value, *, field: str, max_len: int = 512) -> str:
    if not isinstance(value, str):
        raise HostsError(f"invalid peer {field}")
    return value[:max_len]


def _peer_opt_str(value, *, field: str, max_len: int = 512) -> str | None:
    if value is None:
        return None
    return _peer_str(value, field=field, max_len=max_len)


def _peer_str_list(value, *, field: str, max_items: int, max_len: int) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise HostsError(f"invalid peer {field}")
    return [_peer_str(item, field=field, max_len=max_len) for item in value]


def _normalize_peer_container(raw) -> dict:
    if not isinstance(raw, dict):
        raise HostsError("invalid peer container")
    out: dict = {}
    for key, max_len in (
        ("name", 256),
        ("image", 512),
        ("compose_project", 256),
        ("compose_service", 256),
        ("network_mode", 256),
        ("protocol", 32),
    ):
        if key in raw:
            out[key] = _peer_opt_str(raw[key], field=f"container.{key}", max_len=max_len)
    if "status" in raw:
        status = _peer_opt_str(raw["status"], field="container.status", max_len=32)
        if status is not None and status not in _PEER_CONTAINER_STATUSES:
            raise HostsError("invalid peer container.status")
        out["status"] = status
    for key, max_items, max_len in (
        ("urls", _MAX_PEER_URLS, 2048),
        ("vhost_urls", _MAX_PEER_URLS, 2048),
        ("bind_ips", _MAX_PEER_IPS, 128),
        ("protocols", 16, 32),
    ):
        if key in raw:
            out[key] = _peer_str_list(
                raw[key], field=f"container.{key}", max_items=max_items, max_len=max_len,
            )
    for key in ("container_port", "vhost_port", "label_port"):
        if key in raw:
            out[key] = _peer_opt_int(raw[key], field=f"container.{key}", lo=1, hi=65535)
    return out


def _normalize_peer_compose(raw) -> dict:
    if not isinstance(raw, dict):
        raise HostsError("invalid peer compose config")
    out: dict = {}
    for key, max_len in (
        ("project_dir", 512),
        ("project_name", 256),
        ("service_name", 256),
        ("compose_file", 512),
        ("protocol", 32),
        ("host_ip", 128),
        ("network_mode", 256),
        ("mapping_source", 64),
    ):
        if key in raw:
            out[key] = _peer_opt_str(raw[key], field=f"compose.{key}", max_len=max_len)
    if "container_port" in raw:
        out["container_port"] = _peer_opt_int(
            raw["container_port"], field="compose.container_port", lo=1, hi=65535,
        )
    if "publish_mode" in raw:
        mode = _peer_opt_str(raw["publish_mode"], field="compose.publish_mode", max_len=32)
        if mode is not None and mode not in _PEER_PUBLISH_MODES:
            raise HostsError("invalid peer compose.publish_mode")
        out["publish_mode"] = mode
    return out


def _normalize_peer_known_service(raw) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HostsError("invalid peer known_service")
    out: dict = {}
    for key, max_len in (("name", 256), ("description", 512), ("category", 64)):
        if key in raw:
            out[key] = _peer_opt_str(raw[key], field=f"known_service.{key}", max_len=max_len)
    for key in ("is_access_port", "from_label"):
        if key in raw:
            out[key] = _peer_bool(raw[key], field=f"known_service.{key}")
    return out


def _normalize_peer_rule_violation(raw) -> dict:
    if not isinstance(raw, dict):
        raise HostsError("invalid peer rule_violation")
    out: dict = {}
    for key, max_len in (("rule", 64), ("project", 256), ("service", 256)):
        if key in raw:
            out[key] = _peer_str(raw[key], field=f"rule_violation.{key}", max_len=max_len)
    for key in ("start", "end"):
        if key in raw:
            out[key] = _peer_int(raw[key], field=f"rule_violation.{key}", lo=1, hi=65535)
    return out


def normalize_peer_port_row(raw, *, expected_port: int | None = None) -> dict:
    """Validate and normalize one proxied peer port row before returning to browsers."""
    if not isinstance(raw, dict):
        raise HostsError("invalid peer port row")
    port = _peer_int(raw.get("port"), field="port", lo=1, hi=65535)
    if expected_port is not None and port != expected_port:
        raise HostsError("peer port mismatch")
    status = raw.get("status")
    if not isinstance(status, str) or status not in _PEER_STATUSES:
        raise HostsError("invalid peer status")
    out: dict = {"port": port, "status": status}
    if "source_type" in raw:
        st = _peer_opt_str(raw["source_type"], field="source_type", max_len=32)
        out["source_type"] = st if st in _PEER_SOURCE_TYPES else "unknown"
    if "bind_scope" in raw:
        scope = _peer_opt_str(raw["bind_scope"], field="bind_scope", max_len=32)
        if scope is not None and scope not in _PEER_BIND_SCOPES:
            raise HostsError("invalid peer bind_scope")
        out["bind_scope"] = scope
    for key, max_len in (
        ("protocol", 32),
        ("ip", 128),
        ("process", 256),
        ("manual_label", 512),
        ("machine", 128),
    ):
        if key in raw:
            out[key] = _peer_opt_str(raw[key], field=key, max_len=max_len)
    if "pid" in raw:
        out["pid"] = _peer_opt_int(raw["pid"], field="pid", lo=0)
    if "expires_at" in raw:
        out["expires_at"] = _peer_opt_int(raw["expires_at"], field="expires_at", lo=0)
    for key in ("is_reservation", "is_hidden", "conflict"):
        if key in raw:
            out[key] = _peer_bool(raw[key], field=key)
    if "ips" in raw:
        out["ips"] = _peer_str_list(raw["ips"], field="ips", max_items=_MAX_PEER_IPS, max_len=128)
    if "urls" in raw:
        out["urls"] = _peer_str_list(raw["urls"], field="urls", max_items=_MAX_PEER_URLS, max_len=2048)
    if "known_service" in raw:
        out["known_service"] = _normalize_peer_known_service(raw["known_service"])
    if "containers" in raw:
        items = raw["containers"]
        if not isinstance(items, list) or len(items) > _MAX_PEER_CONTAINERS:
            raise HostsError("invalid peer containers")
        out["containers"] = [_normalize_peer_container(item) for item in items]
    if "compose_configs" in raw:
        items = raw["compose_configs"]
        if not isinstance(items, list) or len(items) > _MAX_PEER_COMPOSE:
            raise HostsError("invalid peer compose_configs")
        out["compose_configs"] = [_normalize_peer_compose(item) for item in items]
    if "rule_violations" in raw:
        items = raw["rule_violations"]
        if not isinstance(items, list) or len(items) > _MAX_PEER_VIOLATIONS:
            raise HostsError("invalid peer rule_violations")
        out["rule_violations"] = [_normalize_peer_rule_violation(item) for item in items]
    return out


def _normalize_peer_sources(raw) -> dict[str, str]:
    if not isinstance(raw, dict) or len(raw) > 16:
        raise HostsError("invalid peer sources")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or len(key) > 32:
            raise HostsError("invalid peer source key")
        if not isinstance(value, str) or value not in _PEER_SCAN_STATES:
            raise HostsError("invalid peer source state")
        out[key] = value
    return out


def _normalize_peer_summary(raw) -> dict:
    if not isinstance(raw, dict):
        raise HostsError("invalid peer summary")
    out: dict = {}
    for key in ("used", "configured", "free", "hidden"):
        if key in raw:
            out[key] = _peer_opt_int(raw[key], field=f"summary.{key}", lo=0, hi=65535)
    for key in ("range_start", "range_end"):
        if key in raw:
            out[key] = _peer_opt_int(raw[key], field=f"summary.{key}", lo=1, hi=65535)
    if "compose_files" in raw:
        out["compose_files"] = _peer_opt_int(raw["compose_files"], field="summary.compose_files", lo=0)
    for key in ("hidden_locked", "scan_complete", "stale", "compose_truncated", "compose_incomplete"):
        if key in raw:
            out[key] = _peer_bool(raw[key], field=f"summary.{key}")
    if "hidden_ports" in raw:
        raw_hidden = raw["hidden_ports"]
        if not isinstance(raw_hidden, list) or len(raw_hidden) > _MAX_PEER_PORTS:
            raise HostsError("invalid peer summary.hidden_ports")
        out["hidden_ports"] = [
            _peer_int(p, field="summary.hidden_ports", lo=1, hi=65535) for p in raw_hidden
        ]
    if "hidden_occupancy" in raw:
        raw_occ = raw["hidden_occupancy"]
        if not isinstance(raw_occ, list) or len(raw_occ) > _MAX_PEER_PORTS:
            raise HostsError("invalid peer summary.hidden_occupancy")
        occ_out: list[dict] = []
        for item in raw_occ:
            if not isinstance(item, dict):
                raise HostsError("invalid peer summary.hidden_occupancy")
            occ_port = _peer_int(item.get("port"), field="summary.hidden_occupancy.port", lo=1, hi=65535)
            occ_status = item.get("status")
            if not isinstance(occ_status, str) or occ_status not in _PEER_STATUSES:
                raise HostsError("invalid peer summary.hidden_occupancy.status")
            occ_out.append({"port": occ_port, "status": occ_status})
        out["hidden_occupancy"] = occ_out
    if "sources" in raw:
        out["sources"] = _normalize_peer_sources(raw["sources"])
    if "compose_diagnostics" in raw:
        raw_diag = raw["compose_diagnostics"]
        if not isinstance(raw_diag, list) or len(raw_diag) > _MAX_PEER_DIAGNOSTICS:
            raise HostsError("invalid peer summary.compose_diagnostics")
        diag_out: list[dict] = []
        for item in raw_diag:
            if not isinstance(item, dict):
                raise HostsError("invalid peer compose_diagnostic")
            code = item.get("code")
            if not isinstance(code, str) or code not in _PEER_DIAG_CODES:
                raise HostsError("invalid peer compose_diagnostic.code")
            entry: dict = {"code": code}
            if "file" in item:
                entry["file"] = _peer_opt_str(item["file"], field="compose_diagnostic.file", max_len=512)
            if "range" in item:
                entry["range"] = _peer_opt_str(item["range"], field="compose_diagnostic.range", max_len=128)
            if "limit" in item:
                entry["limit"] = _peer_opt_int(item["limit"], field="compose_diagnostic.limit", lo=0)
            diag_out.append(entry)
        out["compose_diagnostics"] = diag_out
    return out


def normalize_peer_ports_response(raw) -> dict:
    """Validate and normalize a proxied peer ``GET /api/ports`` payload."""
    if not isinstance(raw, dict):
        raise HostsError("invalid peer ports payload")
    raw_ports = raw.get("ports")
    if not isinstance(raw_ports, list) or len(raw_ports) > _MAX_PEER_PORTS:
        raise HostsError("invalid peer ports list")
    raw_summary = raw.get("summary")
    if not isinstance(raw_summary, dict):
        raise HostsError("invalid peer summary")
    return {
        "ports": [normalize_peer_port_row(row) for row in raw_ports],
        "summary": _normalize_peer_summary(raw_summary),
    }


def normalize_peer_port_response(raw, *, expected_port: int) -> dict:
    """Validate and normalize a proxied peer ``GET /api/ports/{port}`` payload."""
    return normalize_peer_port_row(raw, expected_port=expected_port)


def normalize_peer_health_response(raw) -> dict:
    """Validate and normalize a proxied peer ``GET /api/health`` payload."""
    if not isinstance(raw, dict):
        raise HostsError("invalid peer health payload")
    status = raw.get("status")
    if not isinstance(status, str) or status not in _PEER_HEALTH_STATUSES:
        raise HostsError("invalid peer health status")
    out: dict = {"status": status}
    if "version" in raw:
        out["version"] = _peer_str(raw["version"], field="health.version", max_len=64)
    if "auth_required" in raw:
        out["auth_required"] = _peer_bool(raw["auth_required"], field="health.auth_required")
    if "scanners" in raw:
        scanners = raw["scanners"]
        if not isinstance(scanners, dict):
            raise HostsError("invalid peer health.scanners")
        sc_out: dict = {}
        for key in ("proc", "docker", "compose"):
            if key in scanners:
                sc_out[key] = _peer_bool(scanners[key], field=f"health.scanners.{key}")
        if "listen_source" in scanners:
            src = scanners["listen_source"]
            if not isinstance(src, str) or src not in _PEER_LISTEN_SOURCES:
                raise HostsError("invalid peer health.scanners.listen_source")
            sc_out["listen_source"] = src
        out["scanners"] = sc_out
    if "occupancy" in raw:
        occ = raw["occupancy"]
        if not isinstance(occ, dict):
            raise HostsError("invalid peer health.occupancy")
        occ_out: dict = {}
        for key in ("ready", "initialized", "stale", "compose_incomplete", "compose_truncated"):
            if key in occ:
                occ_out[key] = _peer_bool(occ[key], field=f"health.occupancy.{key}")
        if "scan_age_seconds" in occ:
            age = occ["scan_age_seconds"]
            if age is not None:
                if (
                    isinstance(age, bool)
                    or not isinstance(age, (int, float))
                    or not math.isfinite(age)
                    or age < 0
                ):
                    raise HostsError("invalid peer health.occupancy.scan_age_seconds")
            occ_out["scan_age_seconds"] = age
        if "compose_files_scanned" in occ:
            occ_out["compose_files_scanned"] = _peer_int(
                occ["compose_files_scanned"], field="health.occupancy.compose_files_scanned", lo=0,
            )
        if "sources" in occ:
            occ_out["sources"] = _normalize_peer_sources(occ["sources"])
        out["occupancy"] = occ_out
    if "degradations" in raw:
        raw_degs = raw["degradations"]
        if not isinstance(raw_degs, list) or len(raw_degs) > 100:
            raise HostsError("invalid peer health.degradations")
        degs_out: list[dict] = []
        for event in raw_degs[-_MAX_PEER_DEGRADATIONS:]:
            if not isinstance(event, dict):
                raise HostsError("invalid peer health degradation entry")
            entry: dict = {
                "source": _peer_str(event.get("source"), field="health.degradation.source", max_len=64),
                "reason": _peer_str(event.get("reason"), field="health.degradation.reason", max_len=256),
            }
            if "scope" in event:
                entry["scope"] = _peer_str(event["scope"], field="health.degradation.scope", max_len=256)
            if "ts" in event:
                entry["ts"] = _peer_int(event["ts"], field="health.degradation.ts", lo=0)
            degs_out.append(entry)
        out["degradations"] = degs_out
    return out


def normalize_peer_history_response(raw, *, expected_port: int) -> dict:
    """Validate and normalize a proxied peer ``GET /api/ports/{port}/history`` payload."""
    if not isinstance(raw, dict):
        raise HostsError("invalid peer history payload")
    port = _peer_int(raw.get("port"), field="history.port", lo=1, hi=65535)
    if port != expected_port:
        raise HostsError("peer history port mismatch")
    raw_events = raw.get("events")
    if not isinstance(raw_events, list):
        raise HostsError("invalid peer history events")
    retained = raw_events[-_MAX_PEER_HISTORY_EVENTS:] if len(raw_events) > _MAX_PEER_HISTORY_EVENTS else raw_events
    events_out: list[dict] = []
    for event in retained:
        if not isinstance(event, dict):
            raise HostsError("invalid peer history event")
        state = event.get("state")
        if not isinstance(state, str) or state not in _PEER_HISTORY_STATES:
            raise HostsError("invalid peer history event.state")
        entry: dict = {"state": state}
        if "ts" in event:
            entry["ts"] = _peer_int(event["ts"], field="history.event.ts", lo=0)
        if "holders" in event:
            entry["holders"] = _peer_str_list(
                event["holders"], field="history.event.holders", max_items=64, max_len=256,
            )
        events_out.append(entry)
    return {"port": port, "events": events_out}
