"""Occupancy classification: merge listen / Docker / Compose / manual rows.

Pure functions over passed-in data. No HTTP, no env access.
"""

from __future__ import annotations

import ipaddress

from .known_ports import get_known_port
from .models import OccupancyRow
from .netaddr import clean_bind_ip, proto_family_of, safe_http_url
from .port_scanner import is_host_netns_mode


def free_port_payload(port: int, *, hidden: bool) -> OccupancyRow:
    return {
        "port": port,
        "status": "free",
        "source_type": "unknown",
        "protocol": "tcp",
        "known_service": get_known_port(port),
        "is_hidden": hidden,
        "conflict": False,
        "urls": [],
        "containers": [],
        "compose_configs": [],
    }


def classify(
    listening: list,
    containers: list,
    compose_ports: list,
    manual_ports: list[dict],
    hidden_ports: list[int],
    range_start: int,
    range_end: int,
    include_hidden: bool,
    hidden_locked: bool,
    options: dict | None = None,
) -> dict:
    hidden: set[int] = set()
    for raw in hidden_ports or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 65535:
            hidden.add(n)
    hidden_ports = list(hidden)

    listening_map: dict[int, dict] = {}
    inode_to_port: dict[int, int] = {}
    for lp in listening:
        if lp.port < 1 or lp.port > 65535:
            continue
        if lp.inode:
            inode_to_port[lp.inode] = lp.port
        rec = listening_map.get(lp.port)
        if rec is None:
            listening_map[lp.port] = {
                "protocol": proto_label([lp.protocol]),
                "protocols": [lp.protocol],
                "ip": lp.ip,
                "ips": [lp.ip] if lp.ip else [],
                "process": lp.process_name,
                "pid": lp.pid,
            }
            continue
        if lp.protocol and lp.protocol not in rec["protocols"]:
            rec["protocols"].append(lp.protocol)
            rec["protocol"] = proto_label(rec["protocols"])
        if lp.ip and lp.ip not in rec["ips"]:
            rec["ips"].append(lp.ip)
        if lp.process_name and not rec["process"]:
            rec["process"] = lp.process_name
            rec["pid"] = lp.pid

    container_map: dict[int, list[dict]] = {}

    def _add_container(port: int, c, extra: dict | None = None) -> None:
        if port < 1 or port > 65535:
            return
        extra = extra or {}
        hip = (extra.get("host_ip") or "").strip() or None
        proto = extra.get("protocol") or None
        cport = extra.get("container_port")
        lst = container_map.setdefault(port, [])
        existing = next((x for x in lst if x["name"] == c.name), None)
        if existing:
            if hip and hip not in existing["bind_ips"]:
                existing["bind_ips"].append(hip)
            if proto and proto not in existing.get("protocols", []):
                existing.setdefault("protocols", []).append(proto)
                existing["protocol"] = proto_label(existing["protocols"])
            if cport and not existing.get("container_port"):
                existing["container_port"] = cport
            if extra.get("source") != "expose":
                existing["expose_only"] = False
            return
        lst.append({
            "name": c.name,
            "status": c.status,
            "image": c.image,
            "compose_project": c.compose_project,
            "compose_service": c.compose_service,
            "network_mode": c.network_mode,
            "urls": list(c.urls or []),
            "vhost_urls": list(c.vhost_urls or []),
            "vhost_port": c.vhost_port,
            "vhost_host_ports": vhost_url_host_ports(c),
            "label_port": c.label_port,
            "label_host_ports": label_url_host_ports(c),
            "expose_only": extra.get("source") == "expose",
            "bind_ips": [hip] if hip else [],
            "protocols": [proto] if proto else [],
            "protocol": proto,
            "container_port": cport,
            "port_labels": dict(getattr(c, "port_labels", None) or {}),
        })

    for c in containers:
        for p in c.ports:
            _add_container(p.host_port, c, {
                "host_ip": p.host_ip,
                "protocol": p.protocol,
                "container_port": p.container_port,
                "source": p.source or "publish",
            })
        if c.socket_inodes:
            for inode in c.socket_inodes:
                port = inode_to_port.get(inode)
                if port:
                    _add_container(port, c)
        pids = set(c.pids or [])
        mode = c.network_mode or ""
        hostish = bool(pids) or bool(c.socket_inodes) or mode == "host" or is_host_netns_mode(mode)
        if hostish and c.pid:
            pids.add(c.pid)
        if pids:
            for port, rec in listening_map.items():
                if rec.get("pid") in pids:
                    _add_container(port, c)

    finalize_inode_url_hosts(container_map)

    compose_map: dict[int, list[dict]] = {}
    compose_seen: set[tuple] = set()
    for cp in compose_ports:
        if cp.port < 1 or cp.port > 65535:
            continue
        key = (cp.project_dir, cp.port, cp.service_name, cp.host_ip or "", proto_family(cp.protocol), cp.compose_file)
        if key in compose_seen:
            continue
        compose_seen.add(key)
        compose_map.setdefault(cp.port, []).append({
            "project_dir": cp.project_dir,
            "project_name": cp.project_name or cp.project_dir,
            "service_name": cp.service_name,
            "compose_file": cp.compose_file,
            "container_port": cp.container_port,
            "protocol": cp.protocol,
            "host_ip": cp.host_ip,
            "network_mode": cp.network_mode,
        })

    manual_map: dict[int, dict] = {}
    for mp in manual_ports:
        if not isinstance(mp, dict):
            continue
        try:
            port = int(mp["port"])
        except (KeyError, TypeError, ValueError):
            continue
        if port < 1 or port > 65535:
            continue
        manual_map[port] = {
            "label": mp.get("label", "") or "",
            "machine": mp.get("machine", "localhost") or "localhost",
            "is_reservation": bool(mp.get("is_reservation")),
            "expires_at": int(mp["expires_at"])
            if isinstance(mp.get("expires_at"), (int, float)) else None,
        }

    all_ports = set(listening_map) | set(container_map) | set(compose_map) | set(manual_map)
    hidden_status: dict[int, str] = {}

    port_list: list[OccupancyRow] = []
    for port in sorted(all_ports | hidden):
        lp_info = listening_map.get(port)
        ctors = container_map.get(port, [])
        composes = compose_map.get(port, [])
        manual = manual_map.get(port)

        is_listening = port in listening_map
        has_live = any(
            c["status"] in ("running", "paused", "restarting") and not c.get("expose_only")
            for c in ctors
        )
        is_manual = manual is not None
        has_occ = bool(lp_info or ctors or composes or is_manual)

        if is_listening or has_live:
            status = "used"
        elif composes or is_manual or ctors:
            status = "configured"
        else:
            status = "free"

        if port in hidden:
            hidden_status[port] = status
            if not include_hidden:
                continue

        if ctors:
            source_type = "docker"
        elif is_listening and not ctors:
            known = get_known_port(port)
            if known and known.get("category") == "system":
                source_type = "system"
            else:
                source_type = "host"
        elif composes:
            source_type = "docker"
        elif is_manual:
            source_type = "manual"
        else:
            source_type = "unknown"

        known = get_known_port(port)
        pl_hit = None
        for c in ctors:
            labs = c.get("port_labels") or {}
            hit = labs.get(port)
            if hit is None and c.get("container_port") is not None:
                hit = labs.get(c["container_port"])
            if hit and (hit.get("name") or hit.get("category")):
                pl_hit = hit
                break
        if pl_hit:
            known = dict(known or {})
            if pl_hit.get("name"):
                known["name"] = pl_hit["name"]
            if pl_hit.get("category"):
                known["category"] = pl_hit["category"]
            known["from_label"] = True
        ips = list((lp_info.get("ips") if lp_info else None) or [])
        for c in ctors:
            for hip in c.get("bind_ips") or []:
                hip = (hip or "").strip()
                if hip and hip not in ips:
                    ips.append(hip)
        for c in composes:
            hip = (c.get("host_ip") or "").strip()
            if hip and hip not in ips:
                ips.append(hip)
        if not ips:
            ips = [lp_info["ip"] if lp_info else "0.0.0.0"] if has_occ else []
        ip = (lp_info["ip"] if lp_info else None) or (ips[0] if ips else None)
        urls = collect_urls(port, ips, ctors, known, options) if has_occ else []
        for c in ctors:
            c.pop("vhost_host_ports", None)
            c.pop("port_labels", None)
            c.pop("label_host_ports", None)
            c.pop("expose_only", None)
        proto_bits = []
        if lp_info:
            proto_bits.extend(lp_info.get("protocols") or [lp_info["protocol"]])
        for c in ctors:
            proto_bits.extend(c.get("protocols") or ([c.get("protocol")] if c.get("protocol") else []))
        proto_bits.extend(c.get("protocol") or "tcp" for c in composes)

        ctors.sort(key=lambda c: (c.get("name") or "", c.get("network_mode") or ""))
        composes.sort(key=lambda c: (
            c.get("project_dir") or "",
            c.get("service_name") or "",
            c.get("compose_file") or "",
            c.get("host_ip") or "",
            c.get("protocol") or "",
        ))

        port_list.append({
            "port": port,
            "status": status,
            "source_type": source_type,
            "protocol": proto_label(proto_bits) if proto_bits else "tcp",
            "ip": ip,
            "ips": ips,
            "bind_scope": bind_scope_many(ips) if ips else None,
            "process": lp_info["process"] if lp_info else None,
            "pid": lp_info["pid"] if lp_info else None,
            "containers": ctors,
            "compose_configs": composes,
            "manual_label": manual["label"] if manual else None,
            "machine": manual["machine"] if manual else "localhost",
            "expires_at": manual["expires_at"] if manual else None,
            "is_reservation": manual["is_reservation"] if manual else False,
            "known_service": known,
            "is_hidden": port in hidden_ports,
            "conflict": compose_conflict(composes),
            "urls": urls,
        })

    used = sum(
        1 for p in port_list
        if p["status"] == "used" and range_start <= p["port"] <= range_end
    )
    configured = sum(
        1 for p in port_list
        if p["status"] == "configured" and range_start <= p["port"] <= range_end
    )
    hidden_in_range = sum(1 for p in hidden_ports if range_start <= p <= range_end)
    occupied = {p["port"] for p in port_list}
    occupied.update(hidden_ports)
    span = range_end - range_start + 1
    in_range = sum(1 for n in occupied if range_start <= n <= range_end)
    free = max(0, span - in_range)

    return {
        "ports": port_list,
        "summary": {
            "used": used,
            "configured": configured,
            "free": free,
            "hidden": hidden_in_range,
            "hidden_locked": hidden_locked,
            "hidden_ports": sorted(hidden_ports) if not hidden_locked else [],
            "hidden_occupancy": (
                [{"port": n, "status": hidden_status[n]} for n in sorted(hidden_status)]
                if not hidden_locked else []
            ),
            "range_start": range_start,
            "range_end": range_end,
            "compose_truncated": False,
            "compose_incomplete": False,
            "compose_files": 0,
        },
    }


def proto_label(protocols: list[str]) -> str:
    bases: list[str] = []
    for proto in protocols:
        base = proto.replace("6", "")
        if base not in bases:
            bases.append(base)
    return ",".join(bases) if bases else "tcp"


def strip_bind_ip(ip: str | None) -> str:
    return clean_bind_ip(ip)


def bind_key(ip: str | None) -> str:
    """Wildcard (`*`) or a canonical address. Empty Compose host_ip is 0.0.0.0."""
    text = strip_bind_ip(ip)
    if not text or text in ("*", "0.0.0.0", "::", "::0"):
        return "*"
    if text == "localhost":
        return "127.0.0.1"
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_unspecified:
        return "*"
    return str(addr)


def binds_overlap(a: str | None, b: str | None) -> bool:
    ka, kb = bind_key(a), bind_key(b)
    return ka == "*" or kb == "*" or ka == kb


def proto_family(proto: str | None) -> str:
    return proto_family_of(proto)


def compose_conflict(composes: list[dict]) -> bool:
    """True when two Compose projects publish the same port+protocol on overlapping binds."""
    by_dir: dict[str, list[tuple[str | None, str]]] = {}
    for row in composes:
        by_dir.setdefault(row.get("project_dir") or "", []).append(
            (row.get("host_ip"), proto_family(row.get("protocol"))),
        )
    dirs = list(by_dir.values())
    if len(dirs) < 2:
        return False
    for i, binds_a in enumerate(dirs):
        for binds_b in dirs[i + 1 :]:
            if any(
                pa == pb and binds_overlap(a, b)
                for a, pa in binds_a
                for b, pb in binds_b
            ):
                return True
    return False


def bind_scope(ip: str) -> str:
    text = strip_bind_ip(ip)
    if not text or text in ("*",):
        return "public"
    if text == "localhost":
        return "localhost"
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return "lan"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_unspecified:
        return "public"
    if addr.is_loopback:
        return "localhost"
    if addr.is_link_local:
        return "link"
    try:
        if addr in ipaddress.ip_network("172.17.0.0/16"):
            return "link"
    except TypeError:
        pass
    if addr.is_global:
        return "public"
    return "lan"


def bind_scope_many(ips: list[str]) -> str:
    scopes = {bind_scope(ip) for ip in ips or ["0.0.0.0"]}
    if "public" in scopes:
        return "public"
    if "lan" in scopes:
        return "lan"
    if "link" in scopes:
        return "link"
    return "localhost"


NO_HTTP_PORTS = frozenset({
    21, 22, 23, 25, 53, 110, 143, 445, 548, 554, 1194, 1935, 2456, 3260, 3389,
    5060, 5061, 5222, 5357, 5900, 7777, 8211, 8554, 9418, 9987, 10050, 10051,
    4317, 19132, 25565, 27015, 41641, 51820,
})


def host_for_url(host: str) -> str:
    """Bracket IPv6 literals so guessed links parse as URLs."""
    text = (host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return host.strip() or "localhost"
    if addr.version == 6:
        return f"[{addr}]"
    return str(addr)


def guess_url_host(ips: list[str], configured: str) -> str:
    """Localhost binds stay loopback; LAN-only binds use that address unless URL_HOST is set."""
    scope = bind_scope_many(ips)
    if scope == "localhost":
        return "127.0.0.1"
    if configured:
        return configured
    if scope == "lan":
        for ip in ips or []:
            if bind_scope(ip) == "lan":
                return ip
    return "localhost"


WEB_URL_PORTS = (80, 443, 8080, 8443)


def fallback_url_hosts(
    hosts: list[int],
    cport_to_hosts: dict[int, list[int]],
) -> list[int] | None:
    web_hosts: list[int] = []
    for web in WEB_URL_PORTS:
        web_hosts.extend(cport_to_hosts.get(web) or [])
    if web_hosts:
        return list(dict.fromkeys(web_hosts))
    uniq = list(dict.fromkeys(hosts))
    if len(uniq) == 1:
        return uniq
    if uniq:
        return [min(uniq)]
    return None


def label_url_host_ports(c) -> list[int] | None:
    """Host ports that should receive Traefik/homepage URLs. None = inode-only."""
    mappings = [p for p in (c.ports or []) if p.host_port]
    hosts: list[int] = []
    cport_to_hosts: dict[int, list[int]] = {}
    for p in mappings:
        try:
            hp = int(p.host_port)
        except (TypeError, ValueError):
            continue
        hosts.append(hp)
        cp = p.container_port
        if cp is None:
            continue
        try:
            cport_to_hosts.setdefault(int(cp), []).append(hp)
        except (TypeError, ValueError):
            continue
    lport = getattr(c, "label_port", None)
    if lport:
        try:
            matched = cport_to_hosts.get(int(lport))
        except (TypeError, ValueError):
            matched = None
        if matched:
            return list(dict.fromkeys(matched))
    return fallback_url_hosts(hosts, cport_to_hosts)


def vhost_url_host_ports(c) -> list[int] | None:
    """Host ports that should receive nginx-proxy VIRTUAL_HOST URLs."""
    if not getattr(c, "vhost_urls", None):
        return []
    mappings = [p for p in (c.ports or []) if p.host_port]
    hosts: list[int] = []
    cport_to_hosts: dict[int, list[int]] = {}
    for p in mappings:
        try:
            hp = int(p.host_port)
        except (TypeError, ValueError):
            continue
        hosts.append(hp)
        cp = p.container_port
        if cp is None:
            continue
        try:
            cport_to_hosts.setdefault(int(cp), []).append(hp)
        except (TypeError, ValueError):
            continue
    vport = getattr(c, "vhost_port", None)
    if vport:
        try:
            matched = cport_to_hosts.get(int(vport))
        except (TypeError, ValueError):
            matched = None
        if matched:
            return list(dict.fromkeys(matched))
    return fallback_url_hosts(hosts, cport_to_hosts)


def finalize_inode_url_hosts(container_map: dict[int, list[dict]]) -> None:
    """Host-net inode rows have empty ``c.ports``; pick web / lowest attributed port."""
    by_name: dict[str, list[int]] = {}
    for port, lst in container_map.items():
        for c in lst:
            by_name.setdefault(c["name"], []).append(port)
    for _port, lst in container_map.items():
        for c in lst:
            hosts = list(dict.fromkeys(by_name.get(c["name"]) or []))
            web = [h for h in hosts if h in WEB_URL_PORTS]
            picked = web or (hosts if len(hosts) <= 1 else [min(hosts)])
            if c.get("label_host_ports") is None:
                c["label_host_ports"] = picked
            if c.get("vhost_host_ports") is None:
                c["vhost_host_ports"] = picked


def collect_urls(
    port: int,
    ips: list[str],
    containers: list[dict],
    known: dict | None,
    options: dict | None = None,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for c in containers:
        label_urls = c.get("urls") or []
        if label_urls:
            wanted = c.get("label_host_ports")
            attach = wanted is None or port in wanted
            if attach:
                for raw in label_urls:
                    u = safe_http_url(raw)
                    if u and u not in seen:
                        seen.add(u)
                        urls.append(u)
        vurls = c.get("vhost_urls") or []
        if not vurls:
            continue
        wanted_vhost = c.get("vhost_host_ports")
        if wanted_vhost is None:
            vport = c.get("vhost_port")
            cport = c.get("container_port")
            attach_vhost = vport is None or cport == vport
        else:
            attach_vhost = port in wanted_vhost
        if attach_vhost:
            for raw in vurls:
                u = safe_http_url(raw)
                if u and u not in seen:
                    seen.add(u)
                    urls.append(u)
    opts = options or {}
    if opts.get("guess_urls") is False:
        return urls
    if known and known.get("is_access_port"):
        configured = (opts.get("url_host") or "").strip()
        host = guess_url_host(ips, configured)
        name = (known.get("name") or "").upper()
        scheme_pref = opts.get("url_scheme") or "auto"
        if scheme_pref in ("http", "https"):
            scheme = scheme_pref
        else:
            scheme = "https" if port in (443, 8443, 9443) or "HTTPS" in name else "http"
        if port in NO_HTTP_PORTS:
            return urls
        host = host_for_url(host)
        guess = f"{scheme}://{host}:{port}"
        if guess not in seen:
            urls.append(guess)
    return urls
