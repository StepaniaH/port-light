"""Port scanner: listening TCP and UDP sockets on the host.

When running in a Docker container with ``/host/proc`` mounted (read-only),
reads the host's ``/host/proc/1/net/{tcp,tcp6,udp,udp6}``. PID 1 is the host
init network namespace; ``/host/proc/net`` is the *container* namespace.

When running on bare metal or with ``network_mode: host``, uses ``ss -tulnphH``
which can fill process names.

Falls back to local ``/proc/net/*`` if neither host proc nor ss work.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .scan_status import ScanUnavailable
from . import degradations
from .netaddr import normalize_ip


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
    """True when we can read the *host* listen table, not a container netns."""
    if os.path.exists("/host/proc/1/net/tcp"):
        return True
    if os.path.exists("/host/proc"):
        return False
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return False
    return os.path.exists("/proc/net/tcp")


def listen_scan_source() -> str:
    """Which listen table ``scan_listening_ports`` will try first."""
    if os.path.exists("/host/proc/1/net/tcp"):
        return "host_proc"
    if os.path.exists("/host/proc"):
        return "none"
    if sys.platform == "darwin" and shutil.which("lsof"):
        return "lsof"
    if shutil.which("ss"):
        return "ss"
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return "none"
    if os.path.exists("/proc/net/tcp"):
        return "proc"
    return "none"


def _looks_like_host_netns() -> bool:
    """Host network namespace usually has docker0/br0 or several non-lo NICs."""
    try:
        names = os.listdir("/sys/class/net")
    except OSError:
        return False
    if any(n in names for n in ("docker0", "br0", "cni0", "flannel.1", "virbr0")):
        return True
    real = [n for n in names if n != "lo"]
    veth = sum(1 for n in names if n.startswith("veth"))
    return veth >= 1 and len(real) >= 3


def host_listen_trusted() -> bool:
    """Green Host pill: a listen table we believe is the host, not a bridge netns."""
    if host_proc_available():
        return True
    if os.path.exists("/host/proc"):
        return False
    source = listen_scan_source()
    if source == "ss" and (
        os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
    ):
        return _looks_like_host_netns()
    return source != "none"


def scan_listening_ports(
    prefer_pids: Iterable[int] | None = None,
) -> list[ListeningPort]:
    """Return listening/bound TCP and UDP ports on the host."""
    try:
        result = _scan_with_host_proc(prefer_pids=prefer_pids)
        if result is not None:
            return result
    except OSError as exc:
        raise ScanUnavailable("host listen table unreadable") from exc

    if not host_listen_trusted():
        degradations.report("listen", "host", "untrusted listen table")
        raise ScanUnavailable("host listen table unavailable")

    try:
        return _scan_with_ss()
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        degradations.report("listen", "ss", "scan failed")

    if sys.platform == "darwin" and shutil.which("lsof"):
        try:
            return _scan_with_lsof()
        except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            degradations.report("listen", "lsof", "scan failed")

    try:
        return _scan_with_proc(prefer_pids=prefer_pids)
    except (FileNotFoundError, OSError):
        pass

    raise ScanUnavailable("all listen scanners failed")


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


def descendant_pids(
    pid: int,
    proc_root: str = "/host/proc",
    max_pids: int = 256,
) -> list[int]:
    """Breadth-first child PIDs via ``/proc/<pid>/task/*/children``."""
    if pid <= 0:
        return []
    out: list[int] = []
    seen: set[int] = set()
    queue = [pid]
    while queue and len(out) < max_pids:
        cur = queue.pop(0)
        if cur in seen or cur <= 0:
            continue
        seen.add(cur)
        out.append(cur)
        for child in _read_children(cur, proc_root):
            if child not in seen:
                queue.append(child)
    return out


def socket_inodes_for_tree(
    pid: int,
    proc_root: str = "/host/proc",
    max_pids: int = 256,
) -> set[int]:
    """Socket inodes for a process and its descendants (host-network workers)."""
    inodes: set[int] = set()
    for child in descendant_pids(pid, proc_root, max_pids):
        inodes |= socket_inodes_for_pid(child, proc_root)
    return inodes


def _read_children(pid: int, proc_root: str) -> list[int]:
    children: list[int] = []
    roots = [proc_root]
    if proc_root != "/proc":
        roots.append("/proc")
    for root in roots:
        task = os.path.join(root, str(pid), "task")
        try:
            tids = os.listdir(task)
        except OSError:
            continue
        for tid in tids:
            path = os.path.join(task, tid, "children")
            try:
                with open(path) as fh:
                    text = fh.read()
            except OSError:
                continue
            for part in text.split():
                try:
                    children.append(int(part))
                except ValueError:
                    continue
        break
    return children


_LSOF_RE = re.compile(
    r"^(?P<command>\S+)\s+(?P<pid>\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+"
    r"(?P<node>\S+)\s+(?P<name>\S+)(?:\s+\([^)]*\))?\s*$"
)


def parse_lsof_line(line: str) -> ListeningPort | None:
    """Parse one ``lsof -nP -i`` row (header and blanks skipped). Exported for tests."""
    stripped = line.strip()
    if not stripped or stripped.startswith("COMMAND"):
        return None
    m = _LSOF_RE.match(stripped)
    if not m:
        return None
    node = m.group("node").lower()
    if node not in ("tcp", "udp"):
        return None
    protocol = node
    try:
        pid = int(m.group("pid"))
    except ValueError:
        return None
    name = m.group("name")
    host, sep, port_s = name.rpartition(":")
    if not sep:
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not 0 < port <= 65535:
        return None
    host = m.group("name")[: m.group("name").rfind(":")]
    if host.startswith("[") and host.endswith("]"):
        protocol += "6"
        host = host[1:-1]
    elif host != "*" and ":" in host:
        protocol += "6"
    if host in ("*", ""):
        ip = "0.0.0.0"
    else:
        ip = host
    return ListeningPort(port=port, protocol=protocol, ip=ip,
                         process_name=m.group("command").rsplit("/", 1)[-1],
                         pid=pid)


def _scan_with_lsof() -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    seen: set[tuple[int, str, str]] = set()
    for args in (["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], ["lsof", "-nP", "-iUDP"]):
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        # lsof exits 1 on "nothing matched" — an empty table, not a failure.
        if result.returncode != 0 and not (
            result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip()
        ):
            raise subprocess.CalledProcessError(result.returncode, args)
        for line in result.stdout.splitlines():
            parsed = parse_lsof_line(line)
            if not parsed:
                continue
            key = (parsed.port, parsed.protocol, parsed.ip)
            if key not in seen:
                seen.add(key)
                ports.append(parsed)
    return ports


def _scan_with_ss() -> list[ListeningPort]:
    last_error: Exception | None = None
    # No "-h" anywhere: in iproute2 that means help, which exits 0 with usage
    # text and used to be mistaken for a legitimately empty listen table.
    for args in (
        ["ss", "-tulnpH"],
        ["ss", "-tulnp"],
    ):
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise exc
        if result.returncode != 0:
            last_error = subprocess.CalledProcessError(result.returncode, args)
            continue
        ports: list[ListeningPort] = []
        for line in result.stdout.strip().splitlines():
            parsed = parse_ss_line(line)
            if parsed:
                ports.append(parsed)
        if not ports and result.stdout.strip():
            # Output we could not parse a single listener from is a format
            # surprise, not an idle machine — let the next variant try.
            last_error = ValueError(f"unparseable ss output from {args}")
            continue
        return ports
    if last_error:
        raise last_error
    return []


def parse_ss_line(line: str) -> ListeningPort | None:
    """Parse one ``ss -tulnphH`` line. Exported for tests."""
    stripped = line.strip()
    protocol = "tcp"
    for prefix in ("tcp6 ", "udp6 ", "tcp4 ", "udp4 ", "tcp ", "udp "):
        if stripped.startswith(prefix):
            token = prefix.strip()
            protocol = "udp" if token.startswith("udp") else "tcp"
            if token.endswith("6"):
                protocol = protocol + "6"
            stripped = stripped[len(prefix):]
            break

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
    ip, port, family = _split_local_spec(local_spec, proto_base)
    if port is None or port < 1 or port > 65535:
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

    if protocol.endswith("6") and ip in ("*", "0.0.0.0"):
        ip = "::"

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


def _split_local_spec(local_spec: str, proto: str = "tcp") -> tuple[str, int | None, str]:
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
        try:
            port = socket.getservbyname(port_str, proto if proto in ("tcp", "udp") else "tcp")
        except (OSError, TypeError):
            return ip, None, family
    if ip in ("*", "0.0.0.0"):
        ip = "0.0.0.0"
        family = "tcp"
    elif ip == "::":
        family = "tcp6"
    return ip, port, family


def _scan_with_host_proc(
    prefer_pids: Iterable[int] | None = None,
) -> list[ListeningPort] | None:
    if not os.path.exists("/host/proc/1/net/tcp"):
        if os.path.exists("/host/proc"):
            raise ScanUnavailable("mounted host listen table unavailable")
        return None
    ports: list[ListeningPort] = []
    for proto, path in [
        ("tcp", "/host/proc/1/net/tcp"),
        ("tcp6", "/host/proc/1/net/tcp6"),
        ("udp", "/host/proc/1/net/udp"),
        ("udp6", "/host/proc/1/net/udp6"),
    ]:
        ports.extend(_read_proc_net_file(path, proto))
    _fill_process_names(ports, "/host/proc", prefer_pids=prefer_pids)
    return ports


def _scan_with_proc(prefer_pids: Iterable[int] | None = None) -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    for proto, path in [
        ("tcp", "/proc/net/tcp"),
        ("tcp6", "/proc/net/tcp6"),
        ("udp", "/proc/net/udp"),
        ("udp6", "/proc/net/udp6"),
    ]:
        ports.extend(_read_proc_net_file(path, proto))
    _fill_process_names(ports, "/proc", prefer_pids=prefer_pids)
    return ports


def _read_proc_net_file(path: str, protocol: str) -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    try:
        with open(path) as f:
            header = next(f, "")
            if "local_address" not in header:
                raise ScanUnavailable("invalid listen table")
            for line in f:
                parsed = parse_proc_net_line(line, protocol)
                if parsed:
                    ports.append(parsed)
    except FileNotFoundError:
        if not protocol.endswith("6"):
            raise
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
    if port < 1 or port > 65535:
        return None
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


def _fill_process_names(
    ports: list[ListeningPort],
    proc_root: str,
    budget_s: float = 1.5,
    prefer_pids: Iterable[int] | None = None,
) -> None:
    """Fill process names from ``/proc/<pid>/fd`` → socket inodes."""
    needed = {p.inode for p in ports if p.inode and not p.process_name}
    if not needed:
        return
    owners = _inode_to_comm(needed, proc_root, budget_s, prefer_pids=prefer_pids)
    for p in ports:
        if p.process_name or not p.inode:
            continue
        info = owners.get(p.inode)
        if info:
            p.process_name, p.pid = info


def _inode_to_comm(
    needed: set[int],
    proc_root: str,
    budget_s: float,
    prefer_pids: Iterable[int] | None = None,
) -> dict[int, tuple[str, int]]:
    found: dict[int, tuple[str, int]] = {}
    start = time.monotonic()
    try:
        names = os.listdir(proc_root)
    except OSError:
        names = []
    ordered: list[int] = []
    seen: set[int] = set()
    for raw in prefer_pids or []:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in seen:
            ordered.append(pid)
            seen.add(pid)
    prefer_n = len(ordered)
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid not in seen:
            ordered.append(pid)
            seen.add(pid)
    for i, pid in enumerate(ordered):
        if i >= prefer_n and time.monotonic() - start > budget_s:
            break
        fd_dir = os.path.join(proc_root, str(pid), "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if not (target.startswith("socket:[") and target.endswith("]")):
                continue
            try:
                ino = int(target[8:-1])
            except ValueError:
                continue
            if ino in needed and ino not in found:
                comm = _read_comm(proc_root, str(pid))
                if comm:
                    found[ino] = (comm, pid)
                    if len(found) >= len(needed):
                        return found
        if len(found) >= len(needed):
            return found
    return found


def _read_comm(proc_root: str, pid: str) -> str:
    path = os.path.join(proc_root, pid, "comm")
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return ""
    return text.splitlines()[0].strip()[:32] if text else ""


def is_host_netns_mode(mode: str | None) -> bool:
    """True for ``host`` and ``ns:`` paths that are the host network namespace."""
    text = (mode or "").strip()
    if text.lower() == "host":
        return True
    if not text.lower().startswith("ns:"):
        return False
    path = text.split(":", 1)[1].strip()
    if not path:
        return False
    if path in ("/proc/1/ns/net", "/host/proc/1/ns/net"):
        return True
    for host in ("/host/proc/1/ns/net", "/proc/1/ns/net"):
        try:
            if os.path.exists(path) and os.path.exists(host) and os.path.samestat(os.stat(path), os.stat(host)):
                return True
        except OSError:
            continue
    return False


def _parse_ipv6_hex(hex_str: str) -> str:
    raw = bytes.fromhex(hex_str)
    if len(raw) != 16:
        return "::"
    addr_bytes = b""
    for i in range(0, 16, 4):
        addr_bytes += raw[i:i + 4][::-1]
    return socket.inet_ntop(socket.AF_INET6, addr_bytes)
