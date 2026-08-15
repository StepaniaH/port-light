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

_VAR_RE = re.compile(r"\$\$|\$\{([^}]+)\}|\$(\w+)")


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
    project_name: str | None = None


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
    seen_walk: set[str] = set()
    for filepath in files:
        real = os.path.realpath(filepath)
        if real in seen_walk:
            continue
        seen_walk.add(real)
        ports.extend(_parse_compose_file(filepath, scan_dir, frozenset()))
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
    chain: frozenset[str],
    extra_env: dict[str, str] | None = None,
    project_dir: str | None = None,
    project_name: str | None = None,
) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in chain:
        return []
    chain = chain | {real}

    ports: list[ComposePort] = []
    try:
        raw = Path(filepath).read_text()
    except OSError:
        return ports
    if raw.startswith("\ufeff"):
        raw = raw[1:]

    env_vars = {**_load_env_file(Path(filepath).parent), **(extra_env or {})}
    raw = substitute_vars(raw, env_vars)

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return ports
    if not isinstance(data, dict):
        return ports

    parent = Path(filepath).parent
    this_dir = project_dir if project_dir is not None else parent.name
    raw_name = data.get("name")
    this_name = (
        raw_name.strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else (project_name or this_dir)
    )

    for inc_path, inc_env in _include_specs(data.get("include"), parent):
        ports.extend(_parse_compose_file(
            str(inc_path), scan_dir, chain, inc_env,
            project_dir=this_dir,
            project_name=this_name,
        ))

    local_services = data.get("services")
    if not isinstance(local_services, dict):
        return ports

    rel_path = os.path.relpath(filepath, scan_dir)

    for svc_name, svc_cfg in local_services.items():
        if not isinstance(svc_cfg, dict):
            continue
        svc_cfg = _resolve_extends(
            svc_cfg, filepath, env_vars, frozenset(), local_services,
        )
        net = str(svc_cfg.get("network_mode") or "").strip().lower() or None
        for entry in svc_cfg.get("ports") or []:
            for p in parse_port_entry(entry):
                ports.append(ComposePort(
                    port=p["host_port"],
                    compose_file=rel_path,
                    project_dir=this_dir,
                    project_name=this_name,
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
                        project_dir=this_dir,
                        project_name=this_name,
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
        text = path.read_text()
        if text.startswith("\ufeff"):
            text = text[1:]
        for line in text.splitlines():
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


def _extends_ref(ext, filepath: str) -> tuple[str, str] | None:
    if isinstance(ext, str) and ext.strip():
        return os.path.realpath(filepath), ext.strip()
    if not isinstance(ext, dict):
        return None
    svc = ext.get("service")
    if not isinstance(svc, str) or not svc.strip():
        return None
    svc = svc.strip()
    f = ext.get("file")
    if not f:
        return os.path.realpath(filepath), svc
    if not isinstance(f, str):
        return None
    resolved = (Path(filepath).parent / f).resolve()
    if not resolved.is_file():
        return None
    return str(resolved), svc


def _services_from_file(filepath: str, env_vars: dict[str, str]) -> dict:
    try:
        raw = Path(filepath).read_text()
    except OSError:
        return {}
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    raw = substitute_vars(raw, env_vars)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    svcs = data.get("services")
    return svcs if isinstance(svcs, dict) else {}


def _overlay_port_fields(base: dict, child: dict) -> dict:
    out = dict(base)
    if child.get("network_mode"):
        out["network_mode"] = child["network_mode"]
    for key in ("ports", "expose"):
        merged: list = []
        for src in (base, child):
            val = src.get(key)
            if isinstance(val, list):
                merged.extend(val)
            elif val is not None and val is not False:
                merged.append(val)
        if merged:
            out[key] = merged
    return out


def _resolve_extends(
    svc_cfg: dict,
    filepath: str,
    env_vars: dict[str, str],
    chain: frozenset[tuple[str, str]],
    local_services: dict,
) -> dict:
    ref = _extends_ref(svc_cfg.get("extends"), filepath)
    if ref is None:
        return svc_cfg
    if ref in chain:
        return svc_cfg
    ref_file, ref_svc = ref
    if os.path.realpath(ref_file) == os.path.realpath(filepath):
        other_local = local_services
        other_path = filepath
        other = other_local.get(ref_svc) if isinstance(other_local, dict) else None
    else:
        other_path = ref_file
        other_local = _services_from_file(ref_file, env_vars)
        other = other_local.get(ref_svc)
    if not isinstance(other, dict):
        return svc_cfg
    base = _resolve_extends(
        other, other_path, env_vars, chain | {ref}, other_local,
    )
    return _overlay_port_fields(base, svc_cfg)


def substitute_vars(text: str, env_vars: dict[str, str]) -> str:
    merged = {**os.environ, **env_vars}

    def _replacer(m: re.Match) -> str:
        if m.group(0) == "$$":
            return "$"
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
        proto = entry.get("protocol") or "tcp"
        host_ip = entry.get("host_ip") or None
        if host is None:
            return []
        host_s = str(host)
        if isinstance(host, str) and "/" in host_s:
            host_s, slash_proto = host_s.rsplit("/", 1)
            if "protocol" not in entry and slash_proto:
                proto = slash_proto
        try:
            host_ports = expand_port_range(host_s)
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
            if 1 <= hp <= 65535
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
        if 1 <= hp <= 65535
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
