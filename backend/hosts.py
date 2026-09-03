"""Other Port-Light instances: stored peers and a same-origin occupancy proxy.

Each peer still scans its own host. This process only pulls ``GET /api/ports``
(and health) so one UI can show several occupancy maps. It does not attach a
remote ``docker.sock``.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse, urlunparse

from . import port_store, settings as app_settings

LOCAL_ID = "local"
MAX_PEERS = 6
MAX_NAME = 40
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
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


class HostsError(ValueError):
    """Invalid peer list or URL."""


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PassThroughErrors(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


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
    return {"id": LOCAL_ID, "name": local_display_name(), "local": True}


def list_public_peers() -> list[dict]:
    return [_public_peer(p) for p in _resolved_peers()]


def hosts_readonly() -> bool:
    return app_settings.settings_readonly()


def catalog() -> dict:
    return {
        "local": public_local(),
        "peers": list_public_peers(),
        "readonly": hosts_readonly(),
    }


def replace_peers(raw_peers) -> list[dict]:
    if hosts_readonly():
        raise PermissionError("hosts are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    normalized = _normalize_peers(raw_peers, _file_peers())
    port_store.replace_peers(normalized)
    return [_public_peer(p) for p in normalized]


def get_peer(host_id: str) -> dict | None:
    if not host_id or host_id == LOCAL_ID:
        return None
    for peer in _resolved_peers():
        if peer["id"] == host_id:
            return peer
    return None


def origin_url(url: str) -> str:
    parsed = _parse_peer_url(url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def fetch_peer_json(
    peer: dict,
    path: str,
    query: dict[str, str],
    if_none_match: str | None = None,
) -> tuple[int, dict | None, str | None]:
    """GET ``path`` on the peer. Returns ``(status, json_or_none, etag)``.

    Redirects are not followed. 3xx and oversize bodies become 502.
    """
    if not path.startswith("/api/"):
        return 502, None, None
    try:
        origin = _parse_peer_url(peer["url"])
        _validate_addresses(origin)
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
        urllib.request.ProxyHandler({}), _RefuseRedirect, _PassThroughErrors)
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
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
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
    return port_store.get_peers()


def _env_peers() -> list[dict]:
    raw = os.environ.get("PORT_LIGHT_PEERS")
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HostsError("PORT_LIGHT_PEERS must be a JSON array") from exc
    return _normalize_peers(data, [])


def _public_peer(peer: dict) -> dict:
    return {
        "id": peer["id"],
        "name": peer["name"],
        "url": peer["url"],
        "username": str(peer.get("username") or ""),
        "has_auth": bool(peer.get("username") or peer.get("password")),
        "local": False,
    }


def _normalize_peers(raw_peers, existing: list[dict]) -> list[dict]:
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
            host_id = secrets.token_hex(4)
        while host_id in seen_ids:
            host_id = secrets.token_hex(4)
        seen_ids.add(host_id)
        prev = by_id.get(host_id, {})
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
            "url": url,
            "username": username,
            "password": password,
        })
    return out


def _ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _ip_allowed(ip.ipv4_mapped)
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


def _validate_addresses(parsed) -> None:
    """Validate every current DNS answer before passing the URL to urllib.

    urllib resolves again when connecting, so this does not pin the validated
    addresses. Use literal private IPs or trusted DNS to prevent rebinding.
    """
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(
        not _ip_allowed(ipaddress.ip_address(address[4][0]))
        for address in addresses
    ):
        raise HostsError("that host resolves to a disallowed address")


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
        ip = ipaddress.ip_address(host)
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
