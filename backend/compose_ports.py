"""Compose published/exposed port syntax."""
from .netaddr import clean_bind_ip, proto_base
from .compose_limits import ComposeWouldFail, ComposePortLimit, _consume_ports

_MAX_RANGE = 4096

def _norm_proto(proto) -> str:
    return proto_base(proto)


def _normalize_host_ip(host_ip) -> str | None:
    if host_ip is None:
        return None
    text = clean_bind_ip(str(host_ip))
    return text or None


def parse_port_entry(entry) -> list[dict]:
    if isinstance(entry, bool) or entry is None:
        return []
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return []
    if isinstance(entry, str):
        return parse_short_port(entry)
    if isinstance(entry, dict):
        if (
            "published" not in entry
            and "target" not in entry
            and "host_ip" not in entry
            and len(entry) == 1
        ):
            key, val = next(iter(entry.items()))
            if isinstance(val, dict):
                return parse_port_entry(val)
            key_s = str(key)
            if "/" in key_s:
                port_part, proto = key_s.rsplit("/", 1)
                rows = parse_short_port(f"{port_part}:{val}")
                for row in rows:
                    row["protocol"] = _norm_proto(proto)
                return rows
            return parse_short_port(f"{key}:{val}")
        host = entry.get("published")
        target = entry.get("target")
        proto = _norm_proto(entry.get("protocol") or "tcp")
        host_ip = _normalize_host_ip(entry.get("host_ip"))
        mode = str(entry.get("mode") or "").strip().lower()
        if isinstance(target, str) and "/" in target:
            t_s, t_proto = target.rsplit("/", 1)
            if t_s.strip() and t_proto.strip():
                if "protocol" not in entry:
                    proto = _norm_proto(t_proto)
                target = t_s.strip()
        if _published_unset(host):
            if mode == "host" and target is not None:
                host = target
            else:
                return []
        if target is None and mode != "host":
            return []
        host_s = str(host)
        if isinstance(host, str) and "/" in host_s:
            host_s, slash_proto = host_s.rsplit("/", 1)
            if "protocol" not in entry and slash_proto:
                proto = _norm_proto(slash_proto)
        try:
            host_ports = expand_port_range(host_s)
            container_ports = _target_ports(target, len(host_ports))
        except (ValueError, TypeError):
            return []
        return [
            {
                "host_port": hp,
                "container_port": cp,
                "protocol": proto,
                "host_ip": host_ip,
            }
            for hp, cp in zip(host_ports, container_ports, strict=True)
            if 1 <= hp <= 65535
        ]
    return []


def parse_expose_entry(entry) -> list[dict]:
    """Host-network ``expose``: the container port is the host port."""
    if isinstance(entry, bool) or entry is None:
        return []
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        if isinstance(entry, float) and not entry.is_integer():
            return []
        entry = int(entry)
        if 1 <= entry <= 65535:
            _consume_ports(1)
            return [{
                "host_port": entry,
                "container_port": entry,
                "protocol": "tcp",
                "host_ip": None,
            }]
        return []
    if not isinstance(entry, str):
        return []
    protocol = "tcp"
    text = entry.strip()
    if "/" in text:
        text, protocol = text.rsplit("/", 1)
        protocol = _norm_proto(protocol or "tcp")
    if ":" in text:
        return []
    try:
        host_ports = expand_port_range(text)
    except ValueError:
        return []
    return [
        {
            "host_port": hp,
            "container_port": hp,
            "protocol": protocol,
            "host_ip": None,
        }
        for hp in host_ports
        if 1 <= hp <= 65535
    ]


def parse_short_port(entry: str) -> list[dict]:
    protocol = "tcp"
    entry = entry.strip()
    if "/" in entry:
        left, right = entry.rsplit("/", 1)
        if ":" in right:
            proto_tok, _, tail = right.partition(":")
            protocol = _norm_proto(proto_tok)
            entry = f"{left}:{tail}" if tail else left
        else:
            entry, protocol = left, _norm_proto(right)
    protocol = _norm_proto(protocol)
    entry = entry.strip()
    host_ip = None
    if entry.startswith("["):
        end = entry.find("]")
        if end == -1:
            return []
        host_ip = entry[1:end] or None
        entry = entry[end + 1:].lstrip(":")
    parts = entry.split(":")
    if len(parts) == 1:
        return []
    if len(parts) == 2:
        host_spec, container_spec = parts
    else:
        if host_ip is None:
            head = parts[0]
            joined = ":".join(parts[:-2])
            if head.isdigit() and "." not in joined:
                return []
            host_ip = joined or None
        host_spec, container_spec = parts[-2], parts[-1]
    try:
        host_ports = expand_port_range(host_spec)
        container_ports = _target_ports(container_spec, len(host_ports))
    except ValueError:
        return []
    return [
        {
            "host_port": hp,
            "container_port": cp,
            "protocol": protocol,
            "host_ip": host_ip,
        }
        for hp, cp in zip(host_ports, container_ports, strict=True)
        if 1 <= hp <= 65535
    ]


def _port_interval(spec: str, cap: int = _MAX_RANGE) -> range:
    spec = spec.strip()
    if "-" not in spec:
        start = end = int(spec)
    else:
        left, right = spec.split("-", 1)
        start, end = sorted((int(left), int(right)))
    if end - start + 1 > cap:
        raise ComposePortLimit("port_range", cap, f"{start}-{end}")
    return range(start, end + 1)


def expand_port_range(spec: str, cap: int = _MAX_RANGE) -> list[int]:
    ports = _port_interval(spec, cap)
    _consume_ports(len(ports))
    return list(ports)


def _target_ports(spec, count: int) -> range | list:
    if spec is None:
        return [None] * count
    ports = _port_interval(str(spec))
    if len(ports) == 1:
        return [ports.start] * count
    if len(ports) != count:
        raise ComposeWouldFail("published and target ranges must have equal lengths")
    return ports


def _published_unset(host) -> bool:
    if host is None or host is False:
        return True
    text = str(host).strip()
    if not text:
        return True
    if isinstance(host, str) and "/" in text:
        text = text.split("/", 1)[0].strip()
    try:
        return int(str(text).split("-", 1)[0]) == 0
    except (TypeError, ValueError):
        return False
