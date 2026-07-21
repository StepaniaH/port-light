"""Port scanner: detects listening TCP ports on the host.

When running in a Docker container with ``/host/proc`` mounted (read-only),
reads the host's ``/host/proc/net/tcp`` and ``/host/proc/net/tcp6`` directly.
This works without root or nsenter — just needs the /proc mount.

When running on bare metal or with ``network_mode: host``, uses ``ss -tlnpH``
which provides process names in addition to port numbers.

Falls back to local ``/proc/net/tcp`` if neither host proc nor ss work.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
from dataclasses import dataclass, asdict


@dataclass
class ListeningPort:
    port: int
    protocol: str  # "tcp" | "tcp6"
    ip: str  # "0.0.0.0", "::", or specific address
    process_name: str | None = None
    pid: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── ss parser ──────────────────────────────────────────────────────────

_SS_PROC_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def scan_listening_ports() -> list[ListeningPort]:
    """Return all listening TCP ports on the host.

    Strategy:
    1. /host/proc/1/net/tcp + tcp6  (Docker container with /host/proc mount)
       — this is checked first because it sees the host's real ports
    2. ss -tlnpH  (host network or bare metal — gives process names)
    3. /proc/net/tcp  (last resort, local only)
    """
    # Try host proc first — in a container this sees the host's ports
    try:
        result = _scan_with_host_proc()
        if result and len(result) > 1:
            return result
    except (FileNotFoundError, OSError):
        pass

    # Fall back to ss (bare metal or host network mode)
    try:
        result = _scan_with_ss()
        if result:
            return result
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Last resort: local /proc
    try:
        result = _scan_with_proc()
        if result:
            return result
    except (FileNotFoundError, OSError):
        pass

    return []


def _scan_with_ss() -> list[ListeningPort]:
    """Run ss directly (host-network mode or bare metal)."""
    result = subprocess.run(
        ['ss', '-tlnpH'],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, 'ss')
    ports: list[ListeningPort] = []
    for line in result.stdout.strip().splitlines():
        parsed = _parse_ss_line(line)
        if parsed:
            ports.append(parsed)
    return ports


def _parse_ss_line(line: str) -> ListeningPort | None:
    stripped = line.strip()
    if stripped.startswith('tcp6 '):
        protocol = 'tcp6'
        stripped = stripped[5:]
    elif stripped.startswith('tcp '):
        protocol = 'tcp'
        stripped = stripped[4:]
    else:
        protocol = 'tcp'

    parts = stripped.split()
    if len(parts) < 4 or parts[0] != 'LISTEN':
        return None

    local_spec = parts[3]
    if ']' in local_spec:
        addr_part, _, port_part = local_spec.rpartition(']')
        ip = addr_part.strip('[]')
        port_str = port_part.lstrip(':')
        protocol = 'tcp6'
    elif ':' in local_spec:
        ip, port_str = local_spec.rsplit(':', 1)
    else:
        return None

    try:
        port = int(port_str)
    except ValueError:
        return None

    if ip in ('*', '0.0.0.0'):
        ip = '0.0.0.0'
        protocol = 'tcp'
    elif ip == '::':
        ip = '::'
        protocol = 'tcp6'

    process_name = pid = None
    proc_part = ' '.join(parts[5:]) if len(parts) > 5 else ''
    pm = _SS_PROC_RE.search(proc_part)
    if pm:
        process_name = pm.group(1)
        pid = int(pm.group(2))

    return ListeningPort(
        port=port, protocol=protocol, ip=ip,
        process_name=process_name, pid=pid,
    )


# ── /host/proc scanner (Docker container, no root needed) ───────────────

def _scan_with_host_proc() -> list[ListeningPort]:
    """Parse host's /proc/1/net/tcp and /proc/1/net/tcp6 from /host/proc mount.

    This works in a bridge container without root or nsenter — just needs
    /proc mounted at /host/proc (read-only).

    Key detail: /host/proc/net/tcp is the *container's* network namespace
    (it's a symlink to /proc/self/net). The host's network namespace is at
    /host/proc/1/net/tcp (PID 1 = host's init process).
    """
    ports: list[ListeningPort] = []
    for proto, path in [
        ('tcp', '/host/proc/1/net/tcp'),
        ('tcp6', '/host/proc/1/net/tcp6'),
    ]:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                for line in f.readlines()[1:]:  # skip header
                    parsed = _parse_proc_net_line(line, proto)
                    if parsed:
                        ports.append(parsed)
        except OSError:
            continue
    return ports


def _parse_proc_net_line(line: str, protocol: str) -> ListeningPort | None:
    """Parse one line from /proc/net/tcp or /proc/net/tcp6."""
    parts = line.split()
    if len(parts) < 4 or parts[3] != '0A':  # 0A = LISTEN
        return None

    ip_hex, port_hex = parts[1].split(':')
    port = int(port_hex, 16)

    if protocol == 'tcp6':
        # IPv6: 32 hex chars in little-endian groups
        ip = _parse_ipv6_hex(ip_hex)
    else:
        # IPv4: 8 hex chars, little-endian uint32
        ip = socket.inet_ntoa(struct.pack('<I', int(ip_hex, 16)))

    # Normalize wildcard
    if ip == '0.0.0.0' or ip == '::':
        ip = '0.0.0.0' if protocol == 'tcp' else '::'

    return ListeningPort(
        port=port, protocol=protocol, ip=ip,
        process_name=None, pid=None,
    )


def _parse_ipv6_hex(hex_str: str) -> str:
    """Convert 32-char hex from /proc/net/tcp6 to IPv6 address string."""
    # /proc/net/tcp6 stores addresses as 4 little-endian 32-bit words
    raw = bytes.fromhex(hex_str)
    if len(raw) != 16:
        return '::'
    # Each 4-byte group is in little-endian order
    addr_bytes = b''
    for i in range(0, 16, 4):
        addr_bytes += raw[i:i+4][::-1]
    return socket.inet_ntop(socket.AF_INET6, addr_bytes)


# ── local /proc fallback ────────────────────────────────────────────────

def _scan_with_proc() -> list[ListeningPort]:
    """Fallback: parse local /proc/net/tcp (IPv4 only, no process names)."""
    ports: list[ListeningPort] = []
    for proto, path in [('tcp', '/proc/net/tcp'), ('tcp6', '/proc/net/tcp6')]:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                for line in f.readlines()[1:]:
                    parsed = _parse_proc_net_line(line, proto)
                    if parsed:
                        ports.append(parsed)
        except OSError:
            continue
    return ports
