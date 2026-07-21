"""Port scanner: detects listening TCP ports on the host.

Primary method: ``ss -tlnp`` (requires iproute2).
Fallback: parse ``/proc/net/tcp`` (IPv4 only, no process names).
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

# `ss -tlnpH` output (no header) looks like:
#   LISTEN 0 4096  0.0.0.0:8443       0.0.0.0:*
#   LISTEN 0 4096  [::]:8443          [::]:*
#   LISTEN 0 5      0.0.0.0:8080       0.0.0.0:*  users:(("python3",pid=42,fd=4))
#
# Some iproute2 versions prepend "tcp "/"tcp6 " — handle both.
# We use -H to suppress the header line.

_SS_PROC_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def scan_listening_ports() -> list[ListeningPort]:
    """Return all listening TCP ports on the host.

    Tries nsenter first (peeks into host network namespace from a bridge
    container), falls back to plain ss (for host-network mode or bare metal),
    then falls back to /proc/net/tcp.
    """
    for scanner in (_scan_with_nsenter, _scan_with_ss, _scan_with_proc):
        try:
            result = scanner()
            if result:
                return result
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return []


def _scan_with_nsenter() -> list[ListeningPort]:
    """Use nsenter to run ss inside PID 1's network namespace (the host)."""
    result = subprocess.run(
        ['nsenter', '-t', '1', '-n', 'ss', '-tlnpH'],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, 'nsenter')
    ports: list[ListeningPort] = []
    for line in result.stdout.strip().splitlines():
        parsed = _parse_ss_line(line)
        if parsed:
            ports.append(parsed)
    return ports


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
    # Strip optional protocol prefix ("tcp " / "tcp6 ")
    stripped = line.strip()
    if stripped.startswith('tcp6 '):
        protocol = 'tcp6'
        stripped = stripped[5:]
    elif stripped.startswith('tcp '):
        protocol = 'tcp'
        stripped = stripped[4:]
    else:
        protocol = 'tcp'  # determined by address format below

    # Tokenise: LISTEN <recv-q> <send-q> <local> <peer> [process...]
    parts = stripped.split()
    if len(parts) < 4 or parts[0] != 'LISTEN':
        return None

    local_spec = parts[3]
    # local_spec: "0.0.0.0:443", "[::]:443", "127.0.0.1:443", "[::1]:443"
    if ']' in local_spec:
        # IPv6: [::]:443 or [fe80::1]:443
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

    # Normalise wildcard addresses
    if ip in ('*', '0.0.0.0'):
        ip = '0.0.0.0'
        protocol = 'tcp'
    elif ip == '::':
        ip = '::'
        protocol = 'tcp6'

    # Process info (optional, last field(s))
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


# ── /proc fallback ──────────────────────────────────────────────────────

def _scan_with_proc() -> list[ListeningPort]:
    """Fallback: parse /proc/net/tcp (IPv4 only, no process names)."""
    ports: list[ListeningPort] = []
    path = '/proc/net/tcp'
    if not os.path.exists(path):
        return ports

    with open(path) as f:
        for line in f.readlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) < 8 or parts[3] != '0A':  # 0A = LISTEN
                continue
            ip_hex, port_hex = parts[1].split(':')
            port = int(port_hex, 16)
            ip = socket.inet_ntoa(struct.pack('<I', int(ip_hex, 16)))
            ports.append(ListeningPort(
                port=port, protocol='tcp', ip=ip,
                process_name=None, pid=None,
            ))
    return ports
