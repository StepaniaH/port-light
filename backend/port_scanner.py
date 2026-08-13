"""Port scanner: listening TCP and UDP sockets on the host.

When running in a Docker container with ``/host/proc`` mounted (read-only),
reads the host's ``/host/proc/1/net/{tcp,tcp6,udp,udp6}``. PID 1 is the host
init network namespace; ``/host/proc/net`` is the *container* namespace.

When running on bare metal or with ``network_mode: host``, uses ``ss -tulnpH``
which can fill process names.

Falls back to local ``/proc/net/*`` if neither host proc nor ss work.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
from dataclasses import asdict, dataclass


@dataclass
class ListeningPort:
    port: int
    protocol: str  # tcp, tcp6, udp, udp6
    ip: str
    process_name: str | None = None
    pid: int | None = None
    inode: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_SS_PROC_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def host_proc_available() -> bool:
    return os.path.exists("/host/proc/1/net/tcp") or os.path.exists("/proc/net/tcp")


def scan_listening_ports() -> list[ListeningPort]:
    """Return listening/bound TCP and UDP ports on the host."""
    try:
        result = _scan_with_host_proc()
        if result:
            return result
    except (FileNotFoundError, OSError):
        pass

    try:
        result = _scan_with_ss()
        if result:
            return result
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    try:
        result = _scan_with_proc()
        if result:
            return result
    except (FileNotFoundError, OSError):
        pass

    return []


def socket_inodes_for_pid(pid: int, proc_root: str = "/host/proc") -> set[int]:
    """Return socket inodes from ``/proc/<pid>/fd`` (host-network attribution)."""
    inodes: set[int] = set()
    if pid <= 0:
        return inodes
    fd_dir = os.path.join(proc_root, str(pid), "fd")
    try:
        names = os.listdir(fd_dir)
    except OSError:
        fd_dir = os.path.join("/proc", str(pid), "fd")
        try:
            names = os.listdir(fd_dir)
        except OSError:
            return inodes
    for name in names:
        try:
            target = os.readlink(os.path.join(fd_dir, name))
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            try:
                inodes.add(int(target[8:-1]))
            except ValueError:
                continue
    return inodes


def _scan_with_ss() -> list[ListeningPort]:
    result = subprocess.run(
        ["ss", "-tulnpH"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, "ss")
    ports: list[ListeningPort] = []
    for line in result.stdout.strip().splitlines():
        parsed = parse_ss_line(line)
        if parsed:
            ports.append(parsed)
    return ports


def parse_ss_line(line: str) -> ListeningPort | None:
    """Parse one ``ss -tulnpH`` line. Exported for tests."""
    stripped = line.strip()
    protocol = "tcp"
    if stripped.startswith(("tcp ", "tcp6 ", "udp ", "udp6 ")):
        protocol, _, rest = stripped.partition(" ")
        stripped = rest
    else:
        rest = stripped

    parts = stripped.split()
    if len(parts) < 4:
        return None

    state = parts[0]
    proto_base = protocol.replace("6", "")
    if proto_base == "tcp" and state != "LISTEN":
        return None
    if proto_base == "udp" and state not in ("UNCONN", "LISTEN"):
        return None

    local_spec = parts[3]
    ip, port, family = _split_local_spec(local_spec)
    if port is None:
        return None
    if family == "tcp6" or (protocol.endswith("6") and family == "tcp"):
        if proto_base == "udp":
            protocol = "udp6"
        else:
            protocol = "tcp6"
    elif proto_base == "udp":
        protocol = "udp"
    else:
        protocol = "tcp"

    ip = normalize_ip(ip)

    process_name = pid = None
    proc_part = " ".join(parts[5:]) if len(parts) > 5 else ""
    pm = _SS_PROC_RE.search(proc_part)
    if pm:
        process_name = pm.group(1)
        pid = int(pm.group(2))

    return ListeningPort(
        port=port, protocol=protocol, ip=ip,
        process_name=process_name, pid=pid,
    )


def _split_local_spec(local_spec: str) -> tuple[str, int | None, str]:
    if "]" in local_spec:
        addr_part, _, port_part = local_spec.rpartition("]")
        ip = addr_part.strip("[]")
        port_str = port_part.lstrip(":")
        family = "tcp6"
    elif local_spec.count(":") > 1 and not local_spec.replace(".", "").replace(":", "").isdigit():
        # bare IPv6 without brackets is unusual in ss; fall through
        ip, _, port_str = local_spec.rpartition(":")
        family = "tcp6"
    elif ":" in local_spec:
        ip, port_str = local_spec.rsplit(":", 1)
        family = "tcp"
    else:
        return "", None, "tcp"
    try:
        port = int(port_str)
    except ValueError:
        return ip, None, family
    if ip in ("*", "0.0.0.0"):
        ip = "0.0.0.0"
        family = "tcp"
    elif ip == "::":
        family = "tcp6"
    return ip, port, family


def _scan_with_host_proc() -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    for proto, path in [
        ("tcp", "/host/proc/1/net/tcp"),
        ("tcp6", "/host/proc/1/net/tcp6"),
        ("udp", "/host/proc/1/net/udp"),
        ("udp6", "/host/proc/1/net/udp6"),
    ]:
        ports.extend(_read_proc_net_file(path, proto))
    return ports


def _scan_with_proc() -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    for proto, path in [
        ("tcp", "/proc/net/tcp"),
        ("tcp6", "/proc/net/tcp6"),
        ("udp", "/proc/net/udp"),
        ("udp6", "/proc/net/udp6"),
    ]:
        ports.extend(_read_proc_net_file(path, proto))
    return ports


def _read_proc_net_file(path: str, protocol: str) -> list[ListeningPort]:
    if not os.path.exists(path):
        return []
    ports: list[ListeningPort] = []
    try:
        with open(path) as f:
            next(f, None)
            for line in f:
                parsed = parse_proc_net_line(line, protocol)
                if parsed:
                    ports.append(parsed)
    except OSError:
        return []
    return ports


def parse_proc_net_line(line: str, protocol: str) -> ListeningPort | None:
    """Parse one line from /proc/net/{tcp,tcp6,udp,udp6}. Exported for tests."""
    parts = line.split()
    if len(parts) < 10:
        return None
    st = parts[3]
    base = protocol.replace("6", "")
    if base == "tcp" and st != "0A":
        return None
    if base == "udp" and st != "07":
        return None

    ip_hex, port_hex = parts[1].split(":")
    port = int(port_hex, 16)
    if protocol.endswith("6"):
        ip = _parse_ipv6_hex(ip_hex)
    else:
        ip = socket.inet_ntoa(struct.pack("<I", int(ip_hex, 16)))
    ip = normalize_ip(ip)

    try:
        inode = int(parts[9])
    except ValueError:
        inode = None

    return ListeningPort(
        port=port, protocol=protocol, ip=ip,
        process_name=None, pid=None, inode=inode,
    )


def normalize_ip(ip: str) -> str:
    """Collapse IPv4-mapped IPv6 and wildcard forms."""
    if not ip:
        return "0.0.0.0"
    lowered = ip.lower()
    if lowered in ("*",):
        return "0.0.0.0"
    if lowered.startswith("::ffff:"):
        return ip.split(":")[-1]
    if lowered in ("0.0.0.0", "::", "::0"):
        return "0.0.0.0" if lowered == "0.0.0.0" else "::"
    return ip


def _parse_ipv6_hex(hex_str: str) -> str:
    raw = bytes.fromhex(hex_str)
    if len(raw) != 16:
        return "::"
    addr_bytes = b""
    for i in range(0, 16, 4):
        addr_bytes += raw[i:i + 4][::-1]
    return socket.inet_ntop(socket.AF_INET6, addr_bytes)
