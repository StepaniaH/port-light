"""Docker scanner: container info, published ports, host-network sockets, labels."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field, replace

from .models import PortMapping
from .netaddr import binding_ips, prefixless, safe_http_url, strip_brackets
from .port_scanner import descendant_pids, is_host_netns_mode, socket_inodes_for_tree
from .scan_status import ScanUnavailable
from . import degradations

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
_CLIENT = None
_AVAIL = False
_AVAIL_AT = 0.0
_AVAIL_TTL = 5.0
_NET_DRIVER: dict[str, str] = {}
_NET_DRIVER_MAX = 256
_LAST_PUBLISH: dict[str, list[dict]] = {}
_LAST_PUBLISH_MAX = 256
_TRAEFIK_BAD_HOST = re.compile(r"[\\^$+*?()\[\]{}|]")


def _docker_client():
    global _CLIENT
    if not HAS_DOCKER:
        return None
    with _LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            _CLIENT = docker.from_env(timeout=3)
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
            degradations.report("docker", "daemon", "unreachable")
            ok = False
    with _LOCK:
        _AVAIL, _AVAIL_AT = ok, time.monotonic()
    return ok


@dataclass
class ContainerInfo:
    name: str
    status: str
    image: str
    ports: list[PortMapping] = field(default_factory=list)
    compose_project: str | None = None
    compose_service: str | None = None
    network_mode: str | None = None
    pid: int | None = None
    urls: list[str] = field(default_factory=list)
    vhost_urls: list[str] = field(default_factory=list)
    vhost_port: int | None = None
    label_port: int | None = None
    port_labels: dict = field(default_factory=dict)
    socket_inodes: set[int] = field(default_factory=set)
    container_id: str | None = None
    pids: set[int] = field(default_factory=set)


def scan_containers() -> list[ContainerInfo]:
    client = _docker_client()
    if client is None:
        _mark_available(False)
        raise ScanUnavailable("Docker unavailable")

    try:
        containers = client.containers.list(all=True)
    except Exception:
        _drop_client()
        degradations.report("docker", "daemon", "list failed")
        raise ScanUnavailable("Docker list failed")
    _mark_available(True)

    result: list[ContainerInfo] = []
    exposed_by_id: dict[str, dict] = {}
    for c in containers:
        labels = c.labels or {}
        attrs = c.attrs or {}
        host_config = attrs.get("HostConfig") or {}
        network_mode = host_config.get("NetworkMode") or ""
        state = attrs.get("State") or {}
        pid = int(state.get("Pid") or 0) or None
        cid = getattr(c, "id", None) or (attrs.get("Id") or "") or None

        env = (attrs.get("Config") or {}).get("Env")
        vurls, vport = extract_nginx_vhosts(labels, env)
        ports = extract_ports(attrs)
        ports.extend(_macvlan_ports(client, attrs, ports))
        result.append(ContainerInfo(
            name=c.name,
            status=c.status,
            image=attrs.get("Config", {}).get("Image", "unknown"),
            ports=ports,
            compose_project=labels.get("com.docker.compose.project"),
            compose_service=labels.get("com.docker.compose.service"),
            network_mode=network_mode or None,
            pid=pid,
            urls=extract_label_urls(labels, env, include_nginx=False, ports=ports),
            vhost_urls=vurls,
            vhost_port=vport,
            label_port=_traefik_service_port(labels),
            port_labels=extract_port_labels(labels),
            container_id=cid,
        ))
        if cid:
            exposed_by_id[cid] = (attrs.get("Config") or {}).get("ExposedPorts") or {}
    _attach_host_netns_sockets(result, exposed_by_id)
    return result


def _network_is_host_netns(
    mode: str | None,
    by_name: dict[str, ContainerInfo],
    by_id: dict[str, ContainerInfo],
    seen: set[str] | None = None,
) -> bool:
    """True for ``host`` and for ``container:`` joiners of a host-netns target."""
    mode = mode or ""
    if mode == "host" or is_host_netns_mode(mode):
        return True
    if not mode.startswith("container:"):
        return False
    ref = mode.split(":", 1)[1].strip()
    if not ref:
        return False
    seen = seen if seen is not None else set()
    if ref in seen:
        return False
    seen.add(ref)
    target = _resolve_container_ref(ref, by_name, by_id)
    if target is None:
        return False
    return _network_is_host_netns(target.network_mode, by_name, by_id, seen)


def _resolve_container_ref(
    ref: str,
    by_name: dict[str, ContainerInfo],
    by_id: dict[str, ContainerInfo],
) -> ContainerInfo | None:
    if ref in by_name:
        return by_name[ref]
    if ref in by_id:
        return by_id[ref]
    if len(ref) < 12:
        return None
    matches: list[ContainerInfo] = []
    seen: set[str] = set()
    for key, info in by_id.items():
        if not key.startswith(ref):
            continue
        cid = info.container_id or key
        if cid in seen:
            continue
        seen.add(cid)
        matches.append(info)
    if len(matches) == 1:
        return matches[0]
    return None


def _attach_host_netns_sockets(
    containers: list[ContainerInfo],
    exposed_by_id: dict[str, dict] | None = None,
) -> None:
    by_name = {c.name: c for c in containers if c.name}
    by_id: dict[str, ContainerInfo] = {}
    for c in containers:
        if not c.container_id:
            continue
        by_id[c.container_id] = c
        by_id[c.container_id[:12]] = c
    exposed_by_id = exposed_by_id or {}
    for c in containers:
        if not _network_is_host_netns(c.network_mode, by_name, by_id):
            continue
        if (c.network_mode or "").startswith("container:"):
            _add_expose_ports(c.ports, exposed_by_id.get(c.container_id) or {})
        if not c.pid:
            continue
        c.pids = set(descendant_pids(c.pid))
        if c.pid:
            c.pids.add(c.pid)
        c.socket_inodes = socket_inodes_for_tree(c.pid)


def _add_expose_ports(ports: list[PortMapping], exposed: dict) -> None:
    seen = {
        (p.host_port, p.host_ip, p.container_port, p.protocol)
        for p in ports
    }
    for spec in exposed or {}:
        cp, protocol = _split_port_spec(spec)
        if cp is None:
            continue
        protocol = str(protocol or "tcp").lower()
        key = (cp, "0.0.0.0", cp, protocol)
        if key in seen:
            continue
        seen.add(key)
        ports.append(PortMapping(
            host_port=cp,
            host_ip="0.0.0.0",
            container_port=cp,
            protocol=protocol,
            source="expose",
        ))


def extract_ports(attrs: dict) -> list[PortMapping]:
    """Host→container mappings from PortBindings, NetworkSettings, and host-network ExposedPorts."""
    ports: list[PortMapping] = []
    seen: set[tuple] = set()

    def _add(host_port, host_ip, container_port, protocol, source="publish"):
        try:
            hp = int(host_port)
            cp = int(container_port) if container_port is not None else None
        except (TypeError, ValueError):
            return
        if hp < 1 or hp > 65535:
            return
        host_ip = host_ip or "0.0.0.0"
        if isinstance(host_ip, str):
            host_ip = strip_brackets(host_ip)
        protocol = str(protocol or "tcp").lower()
        key = (hp, host_ip, cp, protocol)
        if key in seen:
            if source != "expose":
                for i, row in enumerate(ports):
                    if (row.host_port, row.host_ip, row.container_port, row.protocol) == key:
                        ports[i] = replace(row, source=source)
            return
        seen.add(key)
        ports.append(PortMapping(
            host_port=hp,
            host_ip=host_ip,
            container_port=cp,
            protocol=protocol,
            source=source,
        ))

    def _add_bindings(spec_map):
        for spec, binding_list in (spec_map or {}).items():
            if isinstance(binding_list, dict):
                binding_list = [binding_list]
            if not isinstance(binding_list, list):
                continue
            cp, protocol = _split_port_spec(spec)
            assigned: list[tuple[int, str]] = []
            pending_ips: list[str] = []
            for b in binding_list:
                if not isinstance(b, dict):
                    continue
                hp = _usable_host_port(b.get("HostPort"))
                hips = binding_ips(b.get("HostIp"))
                if hp is None:
                    pending_ips.extend(hips)
                    continue
                for hip in hips:
                    assigned.append((hp, hip))
                    _add(hp, hip, cp, protocol)
            if assigned and pending_ips:
                hp = assigned[0][0]
                for hip in pending_ips:
                    _add(hp, hip, cp, protocol)

    _add_bindings((attrs.get("HostConfig") or {}).get("PortBindings") or {})
    _add_bindings((attrs.get("NetworkSettings") or {}).get("Ports") or {})

    network_mode = (attrs.get("HostConfig") or {}).get("NetworkMode") or ""
    if network_mode == "host" or is_host_netns_mode(network_mode):
        exposed = (attrs.get("Config") or {}).get("ExposedPorts") or {}
        for spec in exposed:
            cp, protocol = _split_port_spec(spec)
            if cp is not None:
                _add(cp, "0.0.0.0", cp, protocol, "expose")

    cid = str(attrs.get("Id") or "")
    status = str((attrs.get("State") or {}).get("Status") or "").lower()
    published = [p for p in ports if p.source != "expose"]
    expose = [p for p in ports if p.source == "expose"]
    stopped = status not in ("running", "paused", "restarting")
    if cid and stopped:
        recalled = _recall_publish(cid)
        if recalled:
            published = _merge_recalled(published, recalled)
    if published and cid:
        _remember_publish(cid, published)
        return published + expose
    return ports


def _merge_recalled(current: list[PortMapping], recalled: list[PortMapping]) -> list[PortMapping]:
    keys = {
        (p.host_port, p.host_ip, p.container_port, p.protocol)
        for p in current
    }
    out = list(current)
    for p in recalled:
        key = (p.host_port, p.host_ip, p.container_port, p.protocol)
        if key in keys:
            continue
        keys.add(key)
        out.append(p)
    return out


def _lru_put(store: dict, key: str, value, max_size: int) -> None:
    if key in store:
        del store[key]
    elif max_size > 0 and len(store) >= max_size:
        oldest = next(iter(store), None)
        if oldest is not None:
            del store[oldest]
    store[key] = value


def _remember_publish(cid: str, ports: list[PortMapping]) -> None:
    with _LOCK:
        _lru_put(_LAST_PUBLISH, cid, list(ports), _LAST_PUBLISH_MAX)


def _recall_publish(cid: str) -> list[PortMapping]:
    with _LOCK:
        rows = _LAST_PUBLISH.get(cid)
        if rows is not None:
            _LAST_PUBLISH[cid] = _LAST_PUBLISH.pop(cid)
    return list(rows) if rows else []


def _network_driver(client, net_id: str) -> str:
    if not net_id:
        return ""
    with _LOCK:
        cached = _NET_DRIVER.get(net_id)
    if cached is not None:
        return cached
    driver = ""
    try:
        net = client.networks.get(net_id)
        driver = str((net.attrs or {}).get("Driver") or "")
    except Exception:
        driver = ""
    with _LOCK:
        _lru_put(_NET_DRIVER, net_id, driver, _NET_DRIVER_MAX)
    return driver


def _macvlan_ports(client, attrs: dict, existing: list[PortMapping]) -> list[PortMapping]:
    """macvlan/ipvlan IPs are reachable on the LAN without Docker PortBindings."""
    extra: list[PortMapping] = []
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    exposed = (attrs.get("Config") or {}).get("ExposedPorts") or {}
    if not isinstance(networks, dict) or not exposed:
        return extra
    seen = {(p.host_port, p.host_ip, p.protocol) for p in existing}
    for name, net in networks.items():
        if not isinstance(net, dict):
            continue
        nid = net.get("NetworkID") or name
        if _network_driver(client, nid) not in ("macvlan", "ipvlan"):
            continue
        ips = _macvlan_ips(net)
        if not ips:
            continue
        for ip in ips:
            for spec in exposed:
                cp, protocol = _split_port_spec(spec)
                if cp is None:
                    continue
                protocol = str(protocol or "tcp").lower()
                row_key = (cp, ip, protocol)
                if row_key in seen:
                    continue
                seen.add(row_key)
                extra.append(PortMapping(
                    host_port=cp,
                    host_ip=ip,
                    container_port=cp,
                    protocol=protocol,
                    source="macvlan",
                ))
    return extra


def _usable_host_port(raw) -> int | None:
    if raw is None or raw is False:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        hp = int(text)
    except (TypeError, ValueError):
        return None
    if hp < 1 or hp > 65535:
        return None
    return hp


def _macvlan_ips(net: dict) -> list[str]:
    ips: list[str] = []
    for key in ("IPAddress", "GlobalIPv6Address", "IPv6Address"):
        ip = prefixless(net.get(key))
        if ip and ip not in ips:
            ips.append(ip)
    ipam = net.get("IPAMConfig")
    if isinstance(ipam, dict):
        for key in ("IPv4Address", "IPv6Address"):
            ip = prefixless(ipam.get(key))
            if ip and ip not in ips:
                ips.append(ip)
    for key in ("SecondaryIPAddresses", "SecondaryIPv6Addresses"):
        extra = net.get(key) or []
        if not isinstance(extra, list):
            continue
        for item in extra:
            if isinstance(item, str):
                ip = prefixless(item)
            elif isinstance(item, dict):
                ip = prefixless(
                    item.get("Addr") or item.get("IPAddress") or item.get("IPv6Address"),
                )
            else:
                continue
            if ip and ip not in ips:
                ips.append(ip)
    return ips


_PORT_LIGHT_LABEL = re.compile(r"^port-light\.port\.(\d+)\.(name|category)$")


def extract_port_labels(labels: dict | None) -> dict:
    """``port-light.port.<container-port>.name|category`` labels, keyed by port.

    Values are trimmed and capped at 80 chars; anything malformed is ignored.
    """
    out: dict = {}
    for key, val in (labels or {}).items():
        m = _PORT_LIGHT_LABEL.match(str(key or ""))
        if not m:
            continue
        try:
            cp = int(m.group(1))
        except ValueError:
            continue
        if not 1 <= cp <= 65535:
            continue
        text = str(val or "").strip()[:80]
        if not text:
            continue
        out.setdefault(cp, {})[m.group(2)] = text
    return out


def _traefik_service_port(labels: dict | None) -> int | None:
    """Container port for Traefik/homepage URLs: the Host() router's service, if unique."""
    service_ports: dict[str, int] = {}
    router_service: dict[str, str] = {}
    host_routers: set[str] = set()
    for key, val in (labels or {}).items():
        lk = str(key).lower()
        if lk.endswith(".loadbalancer.server.port"):
            parts = lk.split(".")
            try:
                name = parts[parts.index("services") + 1]
            except (ValueError, IndexError):
                continue
            try:
                port = int(str(val).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                service_ports[name] = port
            continue
        if "routers" not in lk:
            continue
        router = _traefik_router_name(lk)
        if not router:
            continue
        if lk.endswith(".service") and isinstance(val, str) and val.strip():
            router_service[router] = val.strip().lower()
        elif lk.endswith(".rule"):
            low = str(val).lower()
            if "host(" in low or "hostsni(" in low or "hostheader(" in low:
                host_routers.add(router)
    picked: list[int] = []
    for router in host_routers:
        svc = router_service.get(router) or router
        port = service_ports.get(svc)
        if port and port not in picked:
            picked.append(port)
    if len(picked) == 1:
        return picked[0]
    if len(picked) > 1:
        return None
    uniq = list(dict.fromkeys(service_ports.values()))
    if len(uniq) == 1:
        return uniq[0]
    return None


def _traefik_router_name(label_key: str) -> str:
    parts = str(label_key).lower().split(".")
    try:
        idx = parts.index("routers")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return ""


def _traefik_router_https(labels: dict, router: str) -> bool:
    if not router:
        return True
    prefix = f"traefik.http.routers.{router}.".lower()
    entrypoints = ""
    tls = False
    for key, val in labels.items():
        lk = str(key).lower()
        if not lk.startswith(prefix):
            continue
        field = lk[len(prefix):]
        if field == "tls" or field.startswith("tls."):
            if not _label_is_off(val):
                tls = True
        elif field == "entrypoints":
            entrypoints = str(val).lower()
    if tls:
        return True
    if "websecure" in entrypoints or "https" in entrypoints:
        return True
    if entrypoints and "web" in entrypoints.split(",") and "websecure" not in entrypoints:
        return False
    if entrypoints.strip() in ("web", "http"):
        return False
    return True


def _ok_traefik_host(host: str) -> bool:
    if not host or "*" in host:
        return False
    if _TRAEFIK_BAD_HOST.search(host):
        return False
    if "/" in host or " " in host:
        return False
    return True


def extract_label_urls(
    labels: dict,
    env: list | None = None,
    *,
    include_nginx: bool = True,
    ports: list[PortMapping] | None = None,
) -> list[str]:
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
            router = _traefik_router_name(lk)
            scheme = "https" if _traefik_router_https(labels, router) else "http"
            regexp = "hostregexp" in val.lower()
            for args in _TRAEFIK_HOST_FN.findall(val):
                for host in _TRAEFIK_HOST_ARG.findall(args):
                    if not _ok_traefik_host(host):
                        continue
                    if regexp and _TRAEFIK_BAD_HOST.search(host):
                        continue
                    _add(f"{scheme}://{host}")
        elif lk == "caddy" or (lk.startswith("caddy_") and lk[6:].isdigit()):
            raw = val.strip()
            if raw.lower().startswith(("http://", "https://")):
                first = raw.split()[0].rstrip("/")
                if "*" not in first:
                    _add(first)
            else:
                host = raw.split()[0].strip()
                if _looks_like_hostname(host):
                    _add(host)
        elif lk in ("homepage.href", "wud.href"):
            if "{" in val or "}" in val:
                continue
            _add(val)
        elif lk == "net.unraid.docker.webui":
            _add(expand_unraid_webui(val, ports))

    if include_nginx:
        for url in _nginx_vhost_urls(labels, env_map):
            _add(url)

    return urls


def extract_nginx_vhosts(
    labels: dict | None,
    env: list | None = None,
) -> tuple[list[str], int | None]:
    """nginx-proxy VIRTUAL_HOST / LETSENCRYPT_HOST plus optional VIRTUAL_PORT."""
    env_map = _env_map(env)
    labels = labels or {}
    urls: list[str] = []
    seen: set[str] = set()
    for raw in _nginx_vhost_urls(labels, env_map):
        cleaned = safe_http_url(raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls, _nginx_virtual_port(labels, env_map, bool(urls))


def _nginx_vhost_urls(labels: dict, env_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key, val in labels.items():
        if not val or not isinstance(val, str):
            continue
        if str(key).lower() not in ("virtual_host", "letsencrypt_host"):
            continue
        for host in val.split(","):
            host = host.strip()
            if host and "*" not in host:
                out.append(host)
    for key in ("VIRTUAL_HOST", "LETSENCRYPT_HOST"):
        val = env_map.get(key)
        if not val:
            continue
        for host in val.split(","):
            host = host.strip()
            if host and "*" not in host:
                out.append(host)
    return out


def _nginx_virtual_port(labels: dict, env_map: dict[str, str], has_vhost: bool) -> int | None:
    candidates = (
        env_map.get("VIRTUAL_PORT"),
        labels.get("VIRTUAL_PORT"),
        labels.get("virtual_port"),
    )
    for raw in candidates:
        if raw is None or raw is False:
            continue
        try:
            port = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535:
            return port
    return 80 if has_vhost else None


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
    if "*" in text:
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


def expand_unraid_webui(val: str, ports: list[PortMapping] | None = None) -> str:
    """Turn Unraid ``http://[IP]:[PORT:8096]/`` templates into a real URL."""
    text = (val or "").strip()
    missing = False

    def _host_port(match: re.Match) -> str:
        nonlocal missing
        n = int(match.group(1))
        for row in ports or []:
            cp = row.container_port
            hp = row.host_port
            if cp is None:
                continue
            try:
                if int(cp) == n and 1 <= int(hp) <= 65535:
                    return str(hp)
            except (TypeError, ValueError):
                continue
        if ports is None:
            return str(n)
        missing = True
        return ""

    text = _UNRAID_PORT.sub(_host_port, text)
    if missing:
        return ""
    text = text.replace("[IP]", "localhost").replace("[HOSTNAME]", "localhost")
    if re.search(r"\[PORT\]", text, re.IGNORECASE):
        return ""
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
