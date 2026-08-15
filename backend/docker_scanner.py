"""Docker scanner: container info, published ports, host-network sockets, labels."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .port_scanner import socket_inodes_for_pid

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False


_TRAEFIK_HOST_FN = re.compile(r"Host(?:SNI|Regexp|Header)?\(\s*([^)]*)\)", re.IGNORECASE)
_TRAEFIK_HOST_ARG = re.compile(r"""[`'"]([^`'"]+)[`'"]""")
_UNRAID_PORT = re.compile(r"\[PORT:(\d+)\]", re.IGNORECASE)
_CADDY_DIRECTIVES = frozenset({
    "reverse_proxy", "file_server", "redir", "handle", "handle_path",
    "route", "respond", "log", "encode", "root", "php_fastcgi",
    "basicauth", "basic_auth", "header", "tls", "import", "bind",
    "rewrite", "uri", "try_files", "templates", "metrics",
})
_LOCK = threading.Lock()
_BAD_URL_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "blob", "about"})
_CLIENT = None
_AVAIL = False
_AVAIL_AT = 0.0
_AVAIL_TTL = 5.0


def _docker_client():
    global _CLIENT
    if not HAS_DOCKER:
        return None
    with _LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            _CLIENT = docker.from_env()
            return _CLIENT
        except Exception:
            _CLIENT = None
            return None


def _mark_available(ok: bool) -> None:
    global _AVAIL, _AVAIL_AT
    with _LOCK:
        _AVAIL = ok
        _AVAIL_AT = time.monotonic()


def _drop_client() -> None:
    global _CLIENT, _AVAIL, _AVAIL_AT
    with _LOCK:
        _CLIENT = None
        _AVAIL = False
        _AVAIL_AT = time.monotonic()


def docker_available() -> bool:
    global _AVAIL, _AVAIL_AT
    if not HAS_DOCKER:
        return False
    now = time.monotonic()
    with _LOCK:
        if now - _AVAIL_AT < _AVAIL_TTL:
            return _AVAIL
    client = _docker_client()
    ok = False
    if client is not None:
        try:
            ok = bool(client.ping())
        except Exception:
            _drop_client()
            ok = False
    with _LOCK:
        _AVAIL, _AVAIL_AT = ok, time.monotonic()
    return ok


@dataclass
class ContainerInfo:
    name: str
    status: str
    image: str
    ports: list[dict] = field(default_factory=list)
    compose_project: str | None = None
    compose_service: str | None = None
    network_mode: str | None = None
    pid: int | None = None
    urls: list[str] = field(default_factory=list)
    socket_inodes: set[int] = field(default_factory=set)


def scan_containers() -> list[ContainerInfo]:
    client = _docker_client()
    if client is None:
        return []

    try:
        containers = client.containers.list(all=True)
    except Exception:
        _drop_client()
        return []
    _mark_available(True)

    result: list[ContainerInfo] = []
    for c in containers:
        labels = c.labels or {}
        attrs = c.attrs or {}
        host_config = attrs.get("HostConfig") or {}
        network_mode = host_config.get("NetworkMode") or ""
        state = attrs.get("State") or {}
        pid = int(state.get("Pid") or 0) or None
        inodes: set[int] = set()
        if network_mode == "host" and pid:
            inodes = socket_inodes_for_pid(pid)

        result.append(ContainerInfo(
            name=c.name,
            status=c.status,
            image=attrs.get("Config", {}).get("Image", "unknown"),
            ports=extract_ports(attrs),
            compose_project=labels.get("com.docker.compose.project"),
            compose_service=labels.get("com.docker.compose.service"),
            network_mode=network_mode or None,
            pid=pid,
            urls=extract_label_urls(labels, (attrs.get("Config") or {}).get("Env")),
            socket_inodes=inodes,
        ))
    return result


def extract_ports(attrs: dict) -> list[dict]:
    """Host→container mappings from PortBindings, NetworkSettings, and host-network ExposedPorts."""
    ports: list[dict] = []
    seen: set[tuple] = set()

    def _add(host_port, host_ip, container_port, protocol):
        try:
            hp = int(host_port)
            cp = int(container_port) if container_port is not None else None
        except (TypeError, ValueError):
            return
        if hp < 1 or hp > 65535:
            return
        host_ip = host_ip or "0.0.0.0"
        key = (hp, host_ip, cp, protocol)
        if key in seen:
            return
        seen.add(key)
        ports.append({
            "host_port": hp,
            "host_ip": host_ip,
            "container_port": cp,
            "protocol": protocol,
        })

    bindings = (attrs.get("HostConfig") or {}).get("PortBindings") or {}
    for spec, binding_list in bindings.items():
        cp, protocol = _split_port_spec(spec)
        if not binding_list:
            continue
        for b in binding_list:
            host_port = (b or {}).get("HostPort")
            if host_port:
                _add(host_port, (b or {}).get("HostIp") or "0.0.0.0", cp, protocol)

    ns_ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
    for spec, binding_list in ns_ports.items():
        cp, protocol = _split_port_spec(spec)
        if not binding_list:
            continue
        for b in binding_list:
            host_port = (b or {}).get("HostPort")
            if host_port:
                _add(host_port, (b or {}).get("HostIp") or "0.0.0.0", cp, protocol)

    network_mode = (attrs.get("HostConfig") or {}).get("NetworkMode") or ""
    if network_mode == "host":
        exposed = (attrs.get("Config") or {}).get("ExposedPorts") or {}
        for spec in exposed:
            cp, protocol = _split_port_spec(spec)
            if cp is not None:
                _add(cp, "0.0.0.0", cp, protocol)

    return ports


def extract_label_urls(labels: dict, env: list | None = None) -> list[str]:
    """Traefik Host() / HostSNI() / HostHeader(), Caddy sites, nginx-proxy VIRTUAL_HOST."""
    urls: list[str] = []
    seen: set[str] = set()
    labels = labels or {}
    env_map = _env_map(env)
    traefik_off = any(
        str(key).lower() == "traefik.enable" and _label_is_off(val)
        for key, val in labels.items()
    )

    def _add(url: str):
        cleaned = safe_http_url(url)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)

    for key, val in labels.items():
        if not val or not isinstance(val, str):
            continue
        lk = key.lower()
        if "traefik" in lk and lk.endswith(".rule"):
            if traefik_off:
                continue
            for args in _TRAEFIK_HOST_FN.findall(val):
                for host in _TRAEFIK_HOST_ARG.findall(args):
                    if "*" not in host:
                        _add(host)
        elif lk == "caddy" or (lk.startswith("caddy_") and lk[6:].isdigit()):
            host = val.split()[0].strip()
            if _looks_like_hostname(host):
                _add(host)
        elif lk in ("homepage.href", "wud.href"):
            _add(val)
        elif lk == "net.unraid.docker.webui":
            _add(expand_unraid_webui(val))
        elif lk in ("virtual_host", "letsencrypt_host"):
            for host in val.split(","):
                host = host.strip()
                if host and "*" not in host:
                    _add(host)

    for key in ("VIRTUAL_HOST", "LETSENCRYPT_HOST"):
        val = env_map.get(key)
        if not val:
            continue
        for host in val.split(","):
            host = host.strip()
            if host and "*" not in host:
                _add(host)

    return urls


def _env_map(env: list | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in env or []:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, _, val = item.partition("=")
        if key:
            out[key] = val
    return out


def _looks_like_hostname(host: str) -> bool:
    text = (host or "").strip()
    if not text or text.startswith("{") or "/" in text:
        return False
    if text.startswith("["):
        return True
    head = text.split(":", 1)[0].lower()
    if not head or head in _CADDY_DIRECTIVES:
        return False
    if head in ("localhost", "127.0.0.1", "::1"):
        return True
    return "." in head


def _label_is_off(val) -> bool:
    if val is False:
        return True
    return str(val).strip().lower() in ("false", "0", "no", "off")


def expand_unraid_webui(val: str) -> str:
    """Turn Unraid ``http://[IP]:[PORT:8096]/`` templates into a real URL."""
    text = (val or "").strip()
    text = _UNRAID_PORT.sub(r"\1", text)
    text = text.replace("[IP]", "localhost").replace("[HOSTNAME]", "localhost")
    if re.search(r"\[PORT\]", text, re.IGNORECASE):
        return ""
    return text


def safe_http_url(url: str | None) -> str | None:
    """Keep http(s) links only. Traefik/Caddy hosts get https:// prepended."""
    if not url or not isinstance(url, str):
        return None
    text = url.strip().rstrip("/")
    if not text or any(ch.isspace() for ch in text):
        return None
    _bad = _BAD_URL_SCHEMES
    if "://" in text:
        scheme, _, rest = text.partition("://")
        if scheme.lower() not in ("http", "https") or not rest:
            return None
    else:
        head = text.split(":", 1)[0].lower()
        if head in _bad:
            return None
        if text.startswith("//"):
            text = "https:" + text
        elif text.startswith("/"):
            return None
        else:
            text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname or parsed.username is not None:
        return None
    if parsed.hostname.lower() in _bad:
        return None
    return text


def _split_port_spec(spec: str) -> tuple[int | None, str]:
    spec = str(spec)
    if "/" in spec:
        cp, protocol = spec.split("/", 1)
    else:
        cp, protocol = spec, "tcp"
    try:
        return int(cp), protocol
    except ValueError:
        return None, protocol
