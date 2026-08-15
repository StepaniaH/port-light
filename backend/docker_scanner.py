"""Docker scanner: container info, published ports, host-network sockets, labels."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from .port_scanner import socket_inodes_for_pid

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False


_TRAEFIK_HOST = re.compile(r"Host(?:Regexp)?\(\s*`([^`]+)`\s*\)", re.IGNORECASE)
_LOCK = threading.Lock()
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
            urls=extract_label_urls(labels),
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


def extract_label_urls(labels: dict) -> list[str]:
    """Traefik Host() rules and Caddy site addresses."""
    urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str):
        url = url.strip().rstrip("/")
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for key, val in (labels or {}).items():
        if not val or not isinstance(val, str):
            continue
        lk = key.lower()
        if "traefik" in lk and lk.endswith(".rule"):
            for host in _TRAEFIK_HOST.findall(val):
                if "*" not in host:
                    _add(host)
        elif lk == "caddy":
            host = val.split()[0].strip()
            if host and not host.startswith("{") and "/" not in host:
                _add(host)
        elif lk in ("homepage.href", "wud.href"):
            _add(val)

    return urls


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
