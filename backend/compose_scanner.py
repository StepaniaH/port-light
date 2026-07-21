"""Compose scanner: scans docker-compose files for expected port mappings.

Handles:
- Short format:  "8080:80", "8080:80/tcp", "0.0.0.0:8080:80"
- Long format:   {target: 80, published: 8080, protocol: tcp}
- ${VAR} substitution from .env files and os.environ
- Port ranges (takes first port): "3000-3002:80"
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ComposePort:
    port: int
    compose_file: str  # path relative to scan dir
    project_dir: str   # parent directory name
    service_name: str
    container_port: int | None = None
    protocol: str = 'tcp'


def scan_compose_files(scan_dir: str) -> list[ComposePort]:
    """Scan *scan_dir* for docker-compose files and extract port mappings."""
    ports: list[ComposePort] = []
    if not os.path.isdir(scan_dir):
        return ports

    patterns = [
        '*/docker-compose.y*ml',
        '*/compose.y*ml',
        'docker-compose.y*ml',
        'compose.y*ml',
    ]

    seen: set[str] = set()
    for pattern in patterns:
        for filepath in glob.glob(os.path.join(scan_dir, pattern)):
            fp = os.path.realpath(filepath)
            if fp in seen:
                continue
            seen.add(fp)
            ports.extend(_parse_compose_file(filepath, scan_dir))

    return ports


def _parse_compose_file(filepath: str, scan_dir: str) -> list[ComposePort]:
    ports: list[ComposePort] = []
    project_dir = Path(filepath).parent.name

    try:
        with open(filepath) as f:
            raw = f.read()
    except OSError:
        return ports

    env_vars = _load_env_file(Path(filepath).parent)
    raw = _substitute_vars(raw, env_vars)

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return ports

    if not isinstance(data, dict) or 'services' not in data:
        return ports

    rel_path = os.path.relpath(filepath, scan_dir)

    for svc_name, svc_cfg in data.get('services', {}).items():
        if not isinstance(svc_cfg, dict):
            continue
        for entry in svc_cfg.get('ports', []) or []:
            for p in _parse_port_entry(entry):
                ports.append(ComposePort(
                    port=p['host_port'],
                    compose_file=rel_path,
                    project_dir=project_dir,
                    service_name=svc_name,
                    container_port=p.get('container_port'),
                    protocol=p.get('protocol', 'tcp'),
                ))

    return ports


# ── helpers ──────────────────────────────────────────────────────────────

def _load_env_file(directory: Path) -> dict[str, str]:
    """Load .env from *directory* (simple KEY=VALUE parser)."""
    env: dict[str, str] = {}
    env_path = directory / '.env'
    if not env_path.exists():
        return env
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                env[key.strip()] = val.strip().strip('"\'')
    except OSError:
        pass
    return env


_VAR_RE = re.compile(r'\$\{([^}]+)\}|\$(\w+)')


def _substitute_vars(text: str, env_vars: dict[str, str]) -> str:
    """Replace ${VAR} / $VAR using env_vars + os.environ."""
    merged = {**os.environ, **env_vars}

    def _replacer(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return merged.get(name, m.group(0))

    return _VAR_RE.sub(_replacer, text)


def _parse_port_entry(entry) -> list[dict]:
    """Parse one entry from a compose ``ports:`` list."""
    if isinstance(entry, str):
        return _parse_short_port(entry)
    if isinstance(entry, dict):
        host = entry.get('published')
        target = entry.get('target')
        proto = entry.get('protocol', 'tcp')
        if host is not None:
            try:
                return [{
                    'host_port': int(str(host).split('-')[0]),
                    'container_port': int(target) if target else None,
                    'protocol': proto,
                }]
            except (ValueError, TypeError):
                pass
    # int or unparseable — container-only port, no host mapping
    return []


def _parse_short_port(entry: str) -> list[dict]:
    """Parse short-format port string: '8080:80', '0.0.0.0:8080:80', etc."""
    protocol = 'tcp'
    if '/' in entry:
        entry, protocol = entry.rsplit('/', 1)
    entry = entry.strip()

    parts = entry.split(':')
    if len(parts) == 1:
        # "8080" — container-only, no host port
        return []
    if len(parts) == 2:
        host_spec, container_spec = parts
    else:
        # "0.0.0.0:8080:80"
        host_spec, container_spec = parts[-2], parts[-1]

    try:
        host_port = int(host_spec.split('-')[0])
        container_port = int(container_spec.split('-')[0])
    except ValueError:
        return []

    return [{
        'host_port': host_port,
        'container_port': container_port,
        'protocol': protocol,
    }]
