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


def _parse_compose_file(filepath: str, scan_dir: str, seen_real: set[str]) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in seen_real:
        return []
    seen_real.add(real)

    ports: list[ComposePort] = []
    try:
        raw = Path(filepath).read_text()
    except OSError:
        return ports

    env_vars = _load_env_file(Path(filepath).parent)
    raw = substitute_vars(raw, env_vars)

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return ports
    if not isinstance(data, dict):
        return ports

    parent = Path(filepath).parent
    for inc in _include_paths(data.get("include"), parent):
        ports.extend(_parse_compose_file(str(inc), scan_dir, seen_real))

    if "services" not in data:
        return ports

    rel_path = os.path.relpath(filepath, scan_dir)
    project_dir = parent.name

    for svc_name, svc_cfg in data.get("services", {}).items():
        if not isinstance(svc_cfg, dict):
            continue
        for entry in svc_cfg.get("ports") or []:
            for p in parse_port_entry(entry):
                ports.append(ComposePort(
                    port=p["host_port"],
                    compose_file=rel_path,
                    project_dir=project_dir,
                    service_name=svc_name,
                    container_port=p.get("container_port"),
                    protocol=p.get("protocol", "tcp"),
                ))
    return ports


def _include_paths(include, parent: Path) -> list[Path]:
    if not include:
        return []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        return []
    out: list[Path] = []
    for item in include:
        path = None
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = item.get("path")
        if not path:
            continue
        resolved = (parent / path).resolve()
        if resolved.is_file():
            out.append(resolved)
    return out


def _load_env_file(directory: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = directory / ".env"
    if not env_path.exists():
        return env
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip("\"'")
    except OSError:
        pass
    return env


def substitute_vars(text: str, env_vars: dict[str, str]) -> str:
    merged = {**os.environ, **env_vars}

    def _replacer(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if ":-" in name:
            var, _, default = name.partition(":-")
            return merged.get(var, default)
        return merged.get(name, m.group(0))

    return _VAR_RE.sub(_replacer, text)


def parse_port_entry(entry) -> list[dict]:
    if isinstance(entry, str):
        return parse_short_port(entry)
    if isinstance(entry, dict):
        host = entry.get("published")
        target = entry.get("target")
        proto = entry.get("protocol", "tcp")
        if host is None:
            return []
        try:
            host_ports = expand_port_range(str(host))
            container_port = int(str(target).split("-")[0]) if target is not None else None
        except (ValueError, TypeError):
            return []
        return [
            {"host_port": hp, "container_port": container_port, "protocol": proto}
            for hp in host_ports
        ]
    return []


def parse_short_port(entry: str) -> list[dict]:
    protocol = "tcp"
    if "/" in entry:
        entry, protocol = entry.rsplit("/", 1)
    entry = entry.strip()
    parts = entry.split(":")
    if len(parts) == 1:
        return []
    if len(parts) == 2:
        host_spec, container_spec = parts
    else:
        host_spec, container_spec = parts[-2], parts[-1]
    try:
        host_ports = expand_port_range(host_spec)
        container_port = int(container_spec.split("-")[0])
    except ValueError:
        return []
    return [
        {"host_port": hp, "container_port": container_port, "protocol": protocol}
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
