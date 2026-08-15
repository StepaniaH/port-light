"""Compose scanner: declared host ports, includes, ranges, nested trees."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", "data",
})
_COMPOSE_NAMES = frozenset({
    "compose.yml", "compose.yaml",
    "docker-compose.yml", "docker-compose.yaml",
    "compose.override.yml", "compose.override.yaml",
    "docker-compose.override.yml", "docker-compose.override.yaml",
})
_MAX_RANGE = 128

_VAR_RE = re.compile(r"\$\{([^}]+)\}|\$(\w+)")


@dataclass
class ComposePort:
    port: int
    compose_file: str
    project_dir: str
    service_name: str
    container_port: int | None = None
    protocol: str = "tcp"
    host_ip: str | None = None
    network_mode: str | None = None


def scan_compose_files(
    scan_dir: str,
    max_depth: int | None = None,
    max_files: int | None = None,
) -> list[ComposePort]:
    ports: list[ComposePort] = []
    if not os.path.isdir(scan_dir):
        return ports

    depth = max_depth if max_depth is not None else _env_int("COMPOSE_SCAN_DEPTH", 4)
    files_cap = max_files if max_files is not None else _env_int("COMPOSE_SCAN_MAX_FILES", 400)

    files = _find_compose_files(scan_dir, depth, files_cap)
    seen_real: set[str] = set()
    for filepath in files:
        real = os.path.realpath(filepath)
        if real in seen_real:
            continue
        ports.extend(_parse_compose_file(filepath, scan_dir, seen_real))
    return ports


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _find_compose_files(scan_dir: str, max_depth: int, max_files: int) -> list[str]:
    found: list[str] = []
    scan_dir = os.path.abspath(scan_dir)
    for root, dirs, files in os.walk(scan_dir):
        rel = os.path.relpath(root, scan_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name in _COMPOSE_NAMES:
                found.append(os.path.join(root, name))
                if len(found) >= max_files:
                    return found
    return found


def _parse_compose_file(
    filepath: str,
    scan_dir: str,
    seen_real: set[str],
    extra_env: dict[str, str] | None = None,
) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in seen_real:
        return []
    seen_real.add(real)

    ports: list[ComposePort] = []
    try:
        raw = Path(filepath).read_text()
    except OSError:
        return ports

    env_vars = {**_load_env_file(Path(filepath).parent), **(extra_env or {})}
    raw = substitute_vars(raw, env_vars)

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return ports
    if not isinstance(data, dict):
        return ports

    parent = Path(filepath).parent
    for inc_path, inc_env in _include_specs(data.get("include"), parent):
        ports.extend(_parse_compose_file(str(inc_path), scan_dir, seen_real, inc_env))

    if "services" not in data:
        return ports

    rel_path = os.path.relpath(filepath, scan_dir)
    project_dir = parent.name

    for svc_name, svc_cfg in data.get("services", {}).items():
        if not isinstance(svc_cfg, dict):
            continue
        net = str(svc_cfg.get("network_mode") or "").strip().lower() or None
        for entry in svc_cfg.get("ports") or []:
            for p in parse_port_entry(entry):
                ports.append(ComposePort(
                    port=p["host_port"],
                    compose_file=rel_path,
                    project_dir=project_dir,
                    service_name=svc_name,
                    container_port=p.get("container_port"),
                    protocol=p.get("protocol", "tcp"),
                    host_ip=p.get("host_ip"),
                    network_mode=net,
                ))
        if net == "host":
            for entry in svc_cfg.get("expose") or []:
                for p in parse_expose_entry(entry):
                    ports.append(ComposePort(
                        port=p["host_port"],
                        compose_file=rel_path,
                        project_dir=project_dir,
                        service_name=svc_name,
                        container_port=p.get("container_port"),
                        protocol=p.get("protocol", "tcp"),
                        host_ip=p.get("host_ip"),
                        network_mode=net,
                    ))
    return ports


def _include_specs(include, parent: Path) -> list[tuple[Path, dict[str, str]]]:
    if not include:
        return []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        return []
    out: list[tuple[Path, dict[str, str]]] = []
    for item in include:
        path = None
        extra: dict[str, str] = {}
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = item.get("path")
            extra = _env_files_from_include(parent, item.get("env_file"))
        if not path:
            continue
        resolved = (parent / path).resolve()
        if resolved.is_file():
            out.append((resolved, extra))
    return out


def _env_files_from_include(parent: Path, env_file) -> dict[str, str]:
    names: list[str] = []
    if isinstance(env_file, str):
        names = [env_file]
    elif isinstance(env_file, list):
        names = [n for n in env_file if isinstance(n, str)]
    merged: dict[str, str] = {}
    for name in names:
        merged.update(_read_env_file((parent / name).resolve()))
    return merged


def _load_env_file(directory: Path) -> dict[str, str]:
    return _read_env_file(directory / ".env")


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key:
                continue
            env[key] = val.strip().strip("\"'")
    except OSError:
        pass
    return env


def substitute_vars(text: str, env_vars: dict[str, str]) -> str:
    merged = {**os.environ, **env_vars}

    def _replacer(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if ":-" in name:
            var, _, default = name.partition(":-")
            val = merged.get(var)
            if val is None or val == "":
                return default
            return val
        if "-" in name:
            var, _, default = name.partition("-")
            return merged[var] if var in merged else default
        return merged.get(name, m.group(0))

    return _VAR_RE.sub(_replacer, text)


def parse_port_entry(entry) -> list[dict]:
    if isinstance(entry, str):
        return parse_short_port(entry)
    if isinstance(entry, dict):
        host = entry.get("published")
        target = entry.get("target")
        proto = entry.get("protocol", "tcp")
        host_ip = entry.get("host_ip") or None
        if host is None:
            return []
        try:
            host_ports = expand_port_range(str(host))
            container_port = int(str(target).split("-")[0]) if target is not None else None
        except (ValueError, TypeError):
            return []
        return [
            {
                "host_port": hp,
                "container_port": container_port,
                "protocol": proto,
                "host_ip": host_ip,
            }
            for hp in host_ports
        ]
    return []


def parse_expose_entry(entry) -> list[dict]:
    """Host-network ``expose``: the container port is the host port."""
    if isinstance(entry, bool) or entry is None:
        return []
    if isinstance(entry, int):
        if 1 <= entry <= 65535:
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
        protocol = (protocol or "tcp").lower()
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
    if "/" in entry:
        entry, protocol = entry.rsplit("/", 1)
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
            host_ip = ":".join(parts[:-2]) or None
        host_spec, container_spec = parts[-2], parts[-1]
    try:
        host_ports = expand_port_range(host_spec)
        container_port = int(container_spec.split("-")[0])
    except ValueError:
        return []
    return [
        {
            "host_port": hp,
            "container_port": container_port,
            "protocol": protocol,
            "host_ip": host_ip,
        }
        for hp in host_ports
    ]


def expand_port_range(spec: str, cap: int = _MAX_RANGE) -> list[int]:
    spec = spec.strip()
    if "-" not in spec:
        return [int(spec)]
    left, right = spec.split("-", 1)
    start, end = int(left), int(right)
    if end < start:
        start, end = end, start
    if end - start + 1 > cap:
        end = start + cap - 1
    return list(range(start, end + 1))
