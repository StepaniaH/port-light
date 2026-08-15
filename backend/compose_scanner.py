"""Compose scanner: declared host ports, includes, ranges, nested trees."""

from __future__ import annotations

import glob as _glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .port_scanner import is_host_netns_mode

_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", "data",
})
_MAX_RANGE = 128

_VAR_RE = re.compile(r"\$\$|\$\{([^}]+)\}|\$(\w+)")
_COMPOSE_PREFIXES = ("compose.", "docker-compose.")
_COMPOSE_SUFFIXES = (".yml", ".yaml")


class _ComposeLoader(yaml.SafeLoader):
    """Keep Compose ``!reset`` / ``!override`` so extends can replace, not merge."""


class _ComposeTag:
    __slots__ = ("name", "value")

    def __init__(self, name: str, value):
        self.name = name
        self.value = value


def _unknown_compose_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = None
    suffix = str(tag_suffix or "").lstrip("!").lower()
    if suffix in ("reset", "override"):
        return _ComposeTag(suffix, value)
    return value


_ComposeLoader.add_multi_constructor("!", _unknown_compose_tag)

# Compose uses YAML 1.2. PyYAML's 1.1 sexagesimal ints turn unquoted
# ``22:22`` / ``8080:22`` into numbers and we drop them as unpublished.
_ComposeLoader.yaml_implicit_resolvers = {
    key: list(resolvers)
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_INT_NO_SEXAGESIMAL = re.compile(
    r"""^(?:[-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+)$""",
    re.X,
)
for _ch, _resolvers in list(_ComposeLoader.yaml_implicit_resolvers.items()):
    _ComposeLoader.yaml_implicit_resolvers[_ch] = [
        (tag, regexp) for tag, regexp in _resolvers
        if tag != "tag:yaml.org,2002:int"
    ]
    if not _ComposeLoader.yaml_implicit_resolvers[_ch]:
        del _ComposeLoader.yaml_implicit_resolvers[_ch]
_ComposeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    _INT_NO_SEXAGESIMAL,
    list("-+0123456789"),
)

_AUTO_OVERRIDE_NAMES = frozenset({
    "compose.override.yml", "compose.override.yaml",
    "docker-compose.override.yml", "docker-compose.override.yaml",
})


def _load_yaml(text: str):
    docs = [doc for doc in yaml.load_all(text, Loader=_ComposeLoader) if doc is not None]
    if not docs:
        return None
    merged = None
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        merged = doc if merged is None else _overlay_compose_docs(merged, doc)
    return merged


def _compose_dict(
    filepath: str,
    extra_env: dict[str, str] | None = None,
    env_base: Path | None = None,
) -> dict | None:
    raw = _read_text(Path(filepath))
    if raw is None:
        return None
    env_root = env_base if env_base is not None else Path(filepath).parent
    env_vars = {**_load_env_file(env_root), **(extra_env or {})}
    try:
        data = _load_yaml(substitute_vars(raw, env_vars))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _macvlan_names_tree(
    filepath: str,
    extra_env: dict[str, str] | None,
    chain: frozenset[str],
    env_base: Path | None = None,
) -> set[str]:
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    data = _compose_dict(filepath, extra_env, env_base)
    if not data:
        return set()
    names = _macvlan_network_names(data)
    parent = Path(filepath).parent
    nested = chain | {real}
    for inc_path, inc_env, inc_base in _include_specs(data.get("include"), parent):
        names |= _macvlan_names_tree(
            str(inc_path),
            {**(extra_env or {}), **inc_env},
            nested,
            inc_base or env_base,
        )
    return names


def _extends_macvlan_names(
    svc_cfg: dict,
    filepath: str,
    env_vars: dict[str, str],
    local_services: dict,
    chain: frozenset[tuple[str, str]],
) -> set[str]:
    names: set[str] = set()
    ref = _extends_ref(svc_cfg.get("extends"), filepath)
    if ref is None or ref in chain:
        return names
    ref_file, ref_svc = ref
    if os.path.realpath(ref_file) == os.path.realpath(filepath):
        other_path = filepath
        other_local = local_services
        other = other_local.get(ref_svc) if isinstance(other_local, dict) else None
    else:
        other_path = ref_file
        merged_env = {**_load_env_file(Path(ref_file).parent), **env_vars}
        names |= _macvlan_names_tree(ref_file, merged_env, frozenset())
        other_local = _services_from_file(ref_file, merged_env)
        other = other_local.get(ref_svc)
    if isinstance(other, dict):
        names |= _extends_macvlan_names(
            other, other_path, env_vars, other_local, chain | {ref},
        )
    return names


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


def _is_compose_filename(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(_COMPOSE_PREFIXES) and lower.endswith(_COMPOSE_SUFFIXES)


def _project_dir_key(parent: Path, scan_dir: str) -> str:
    try:
        rel = os.path.relpath(str(parent), os.path.abspath(scan_dir))
    except ValueError:
        return parent.name
    if rel == ".":
        return parent.name or "."
    return rel.replace("\\", "/")


def _project_display_name(raw_name, fallback: str) -> str:
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()
    if isinstance(raw_name, (int, float)) and not isinstance(raw_name, bool):
        return str(raw_name)
    return fallback


def _norm_proto(proto) -> str:
    text = str(proto or "tcp").strip().lower() or "tcp"
    if "/" in text:
        text = text.split("/", 1)[0] or "tcp"
    return text


def _is_host_network(net: str | None) -> bool:
    return is_host_netns_mode(net)


def _is_shared_netns(net: str | None) -> bool:
    if not net:
        return False
    return net.startswith("service:") or net.startswith("container:")


def _macvlan_network_names(data: dict) -> set[str]:
    nets = _unwrap_compose(data.get("networks"))
    if not isinstance(nets, dict):
        return set()
    names: set[str] = set()
    for name, cfg in nets.items():
        cfg = _unwrap_compose(cfg)
        if not isinstance(cfg, dict):
            continue
        driver = str(_unwrap_compose(cfg.get("driver")) or "").lower()
        if driver in ("macvlan", "ipvlan") or _network_is_external(cfg):
            names.add(str(name))
    return names


def _network_is_external(cfg: dict) -> bool:
    ext = _unwrap_compose(cfg.get("external"))
    if ext is True:
        return True
    if isinstance(ext, str) and ext.strip().lower() in ("true", "yes"):
        return True
    return isinstance(ext, dict)


def _service_macvlan_ips(svc_cfg: dict, macvlan_names: set[str]) -> list[str]:
    nets = _unwrap_compose(svc_cfg.get("networks"))
    ips: list[str] = []
    if isinstance(nets, dict):
        items = nets.items()
    elif isinstance(nets, list):
        items = ((item, {}) for item in nets if isinstance(item, str))
    else:
        return ips
    for name, cfg in items:
        if str(name) not in macvlan_names:
            continue
        if not isinstance(cfg, dict):
            continue
        for key in ("ipv4_address", "ipv6_address"):
            raw = str(cfg.get(key) or "").strip()
            if not raw:
                continue
            ip = raw.split("/", 1)[0].strip()
            if ip and ip not in ips:
                ips.append(ip)
    return ips


def _unwrap_compose(val):
    return val.value if isinstance(val, _ComposeTag) else val


def _port_entries(ports_cfg) -> list:
    ports_cfg = _unwrap_compose(ports_cfg)
    if ports_cfg is None or ports_cfg is False:
        return []
    if isinstance(ports_cfg, dict):
        if any(k in ports_cfg for k in ("published", "target", "host_ip", "protocol", "mode")):
            return [ports_cfg]
        return [{k: v} for k, v in ports_cfg.items()]
    if isinstance(ports_cfg, list):
        return [_unwrap_compose(item) for item in ports_cfg]
    return [ports_cfg]


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


@dataclass
class ComposeScan:
    ports: list[ComposePort] = field(default_factory=list)
    truncated: bool = False
    files_scanned: int = 0


def scan_compose_files(
    scan_dir: str,
    max_depth: int | None = None,
    max_files: int | None = None,
) -> list[ComposePort]:
    return scan_compose_tree(scan_dir, max_depth=max_depth, max_files=max_files).ports


def scan_compose_tree(
    scan_dir: str,
    max_depth: int | None = None,
    max_files: int | None = None,
) -> ComposeScan:
    if not os.path.isdir(scan_dir):
        return ComposeScan()

    depth = max_depth if max_depth is not None else _env_int("COMPOSE_SCAN_DEPTH", 4)
    files_cap = max_files if max_files is not None else _env_int("COMPOSE_SCAN_MAX_FILES", 400)

    files, truncated = _find_compose_files(scan_dir, depth, files_cap)
    included: set[str] = set()
    for filepath in files:
        included |= _included_reals(filepath)
    ports: list[ComposePort] = []
    seen_walk: set[str] = set()
    for filepath in files:
        real = os.path.realpath(filepath)
        if real in seen_walk or real in included:
            continue
        seen_walk.add(real)
        ports.extend(_parse_compose_file(filepath, scan_dir, frozenset()))
    return ComposeScan(ports=ports, truncated=truncated, files_scanned=len(seen_walk))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _find_compose_files(scan_dir: str, max_depth: int, max_files: int) -> tuple[list[str], bool]:
    found: list[str] = []
    scan_dir = os.path.abspath(scan_dir)
    for root, dirs, files in os.walk(scan_dir):
        rel = os.path.relpath(root, scan_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs.clear()
            continue
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not _is_compose_filename(name):
                continue
            if _is_auto_override_name(name) and _auto_override_has_base(root, name):
                continue
            if len(found) >= max_files:
                return found, True
            found.append(os.path.join(root, name))
    return found, False


def _is_auto_override_name(name: str) -> bool:
    return name.lower() in _AUTO_OVERRIDE_NAMES


def _auto_override_has_base(root: str, name: str) -> bool:
    lower = name.lower()
    if lower.startswith("docker-compose.override."):
        stems = ("docker-compose.yml", "docker-compose.yaml")
    else:
        stems = ("compose.yml", "compose.yaml")
    return any(os.path.isfile(os.path.join(root, s)) for s in stems)


def _sibling_override_path(filepath: str) -> str | None:
    parent = os.path.dirname(filepath)
    name = os.path.basename(filepath).lower()
    if name in ("compose.yml", "compose.yaml"):
        candidates = ("compose.override.yml", "compose.override.yaml")
    elif name in ("docker-compose.yml", "docker-compose.yaml"):
        candidates = ("docker-compose.override.yml", "docker-compose.override.yaml")
    else:
        return None
    for cand in candidates:
        path = os.path.join(parent, cand)
        if os.path.isfile(path):
            return path
    return None


def _compose_doc(
    filepath: str,
    extra_env: dict[str, str] | None = None,
    env_base: Path | None = None,
) -> tuple[dict, dict[str, str], Path] | None:
    """Load one Compose file: project env, top-level env_file, sibling override."""
    raw = _read_text(Path(filepath))
    if raw is None:
        return None
    parent = Path(filepath).parent
    env_root = env_base if env_base is not None else parent
    env_vars = {**_load_env_file(env_root), **(extra_env or {})}
    try:
        data = _load_yaml(substitute_vars(raw, env_vars))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    extra_file_env = _env_files_from_include(env_root, data.get("env_file"))
    if extra_file_env:
        env_vars = {**env_vars, **extra_file_env}
        try:
            data = _load_yaml(substitute_vars(raw, env_vars))
        except yaml.YAMLError:
            return None
        if not isinstance(data, dict):
            return None
    ov_path = _sibling_override_path(filepath)
    if ov_path:
        ov_raw = _read_text(Path(ov_path))
        if ov_raw is not None:
            try:
                ov_data = _load_yaml(substitute_vars(ov_raw, env_vars))
            except yaml.YAMLError:
                ov_data = None
            if isinstance(ov_data, dict):
                data = _overlay_compose_docs(data, ov_data)
    return data, env_vars, parent


def _included_reals(
    filepath: str,
    extra_env: dict[str, str] | None = None,
    chain: frozenset[str] = frozenset(),
    env_base: Path | None = None,
) -> set[str]:
    """Realpaths reached via ``include:`` (not scanned as their own stack)."""
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    loaded = _compose_doc(filepath, extra_env, env_base)
    if loaded is None:
        return set()
    data, env_vars, parent = loaded
    out: set[str] = set()
    for inc_path, inc_env, inc_base in _include_specs(data.get("include"), parent):
        nested = os.path.realpath(inc_path)
        out.add(nested)
        out |= _included_reals(
            str(inc_path),
            {**env_vars, **inc_env},
            chain | {real},
            inc_base if inc_base is not None else env_base,
        )
    return out


def _parse_compose_file(
    filepath: str,
    scan_dir: str,
    chain: frozenset[str],
    extra_env: dict[str, str] | None = None,
    project_dir: str | None = None,
    project_name: str | None = None,
    env_base: Path | None = None,
) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in chain:
        return []
    chain = chain | {real}

    ports: list[ComposePort] = []
    loaded = _compose_doc(filepath, extra_env, env_base)
    if loaded is None:
        return ports
    data, env_vars, parent = loaded

    this_dir = project_dir if project_dir is not None else _project_dir_key(parent, scan_dir)
    fallback_name = Path(this_dir).name
    if not fallback_name or fallback_name in (".", ".."):
        fallback_name = parent.name
    this_name = _project_display_name(data.get("name"), project_name or fallback_name)

    macvlan_names = _macvlan_network_names(data)
    for inc_path, inc_env, inc_base in _include_specs(data.get("include"), parent):
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        ports.extend(_parse_compose_file(
            str(inc_path), scan_dir, chain, nested_env,
            project_dir=this_dir,
            project_name=this_name,
            env_base=nested_base,
        ))
        macvlan_names |= _macvlan_names_tree(str(inc_path), nested_env, chain, nested_base)

    local_services = data.get("services")
    if not isinstance(local_services, dict):
        return ports

    rel_path = os.path.relpath(filepath, scan_dir)

    for svc_name, svc_cfg in local_services.items():
        if not isinstance(svc_cfg, dict):
            continue
        lan_names = macvlan_names | _extends_macvlan_names(
            svc_cfg, filepath, env_vars, local_services, frozenset(),
        )
        svc_cfg = _resolve_extends(
            svc_cfg, filepath, env_vars, frozenset(), local_services,
        )
        net = str(_unwrap_compose(svc_cfg.get("network_mode")) or "").strip().lower() or None
        if _is_shared_netns(net):
            continue
        entries: list = []
        if not _is_host_network(net):
            entries = _port_entries(svc_cfg.get("ports"))
            deploy = _unwrap_compose(svc_cfg.get("deploy"))
            if isinstance(deploy, dict):
                entries.extend(_port_entries(deploy.get("ports")))
        for entry in entries:
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
        if _is_host_network(net):
            for entry in _port_entries(svc_cfg.get("expose")):
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
            continue
        lan_ips = _service_macvlan_ips(svc_cfg, lan_names)
        if not lan_ips:
            continue
        lan_rows: list[dict] = []
        for entry in _port_entries(svc_cfg.get("expose")):
            lan_rows.extend(parse_expose_entry(entry))
        for entry in entries:
            parsed = parse_port_entry(entry)
            if parsed:
                for p in parsed:
                    cp = p.get("container_port")
                    if cp:
                        lan_rows.append({
                            "host_port": cp,
                            "container_port": cp,
                            "protocol": p.get("protocol", "tcp"),
                            "host_ip": None,
                        })
                continue
            if isinstance(entry, dict) and entry.get("target") is not None:
                extra = parse_expose_entry(entry.get("target"))
                proto = entry.get("protocol")
                if proto:
                    for row in extra:
                        row["protocol"] = _norm_proto(proto)
                lan_rows.extend(extra)
        for ip in lan_ips:
            for p in lan_rows:
                ports.append(ComposePort(
                    port=p["host_port"],
                    compose_file=rel_path,
                    project_dir=this_dir,
                    project_name=this_name,
                    service_name=svc_name,
                    container_port=p.get("container_port"),
                    protocol=p.get("protocol", "tcp"),
                    host_ip=ip,
                    network_mode=net,
                ))
    return ports


def _include_specs(include, parent: Path) -> list[tuple[Path, dict[str, str], Path | None]]:
    if not include:
        return []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        return []
    out: list[tuple[Path, dict[str, str], Path | None]] = []
    for item in include:
        paths: list[str] = []
        extra: dict[str, str] = {}
        env_base: Path | None = None
        if isinstance(item, str):
            paths = [item]
        elif isinstance(item, dict):
            extra = _env_files_from_include(parent, item.get("env_file"))
            proj = item.get("project_directory")
            if isinstance(proj, str) and proj.strip():
                try:
                    proj_dir = (parent / proj).resolve()
                except (TypeError, ValueError, OSError):
                    proj_dir = None
                if proj_dir is not None and proj_dir.is_dir():
                    extra = {**_load_env_file(proj_dir), **extra}
                    env_base = proj_dir
            raw_path = item.get("path")
            if isinstance(raw_path, str):
                paths = [raw_path]
            elif isinstance(raw_path, list):
                paths = [p for p in raw_path if isinstance(p, str)]
        for path in paths:
            if not path:
                continue
            try:
                if any(ch in path for ch in "*?["):
                    pattern = os.path.normpath(os.path.join(str(parent), path))
                    candidates = [Path(p).resolve() for p in _glob.glob(pattern)]
                else:
                    candidates = [(parent / path).resolve()]
            except (TypeError, ValueError, OSError):
                continue
            for resolved in candidates:
                if resolved.is_file():
                    out.append((resolved, extra, env_base))
    return out


def _env_file_paths(env_file) -> list[str]:
    names: list[str] = []
    if isinstance(env_file, str):
        return [env_file]
    if isinstance(env_file, dict):
        path = env_file.get("path")
        return [path] if isinstance(path, str) else []
    if not isinstance(env_file, list):
        return []
    for item in env_file:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            path = item.get("path")
            if isinstance(path, str):
                names.append(path)
    return names


def _env_files_from_include(parent: Path, env_file) -> dict[str, str]:
    merged: dict[str, str] = {}
    for name in _env_file_paths(env_file):
        merged.update(_read_env_file((parent / name).resolve()))
    return merged


def _load_env_file(directory: Path) -> dict[str, str]:
    return _read_env_file(directory / ".env")


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    text = _read_text(path)
    if text is None:
        return env
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
    raw = _read_text(Path(filepath))
    if raw is None:
        return {}
    raw = substitute_vars(raw, env_vars)
    try:
        data = _load_yaml(raw)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    svcs = data.get("services")
    return svcs if isinstance(svcs, dict) else {}


def _overlay_port_fields(base: dict, child: dict) -> dict:
    out = dict(base)
    child_net = _unwrap_compose(child.get("network_mode"))
    if child_net:
        out["network_mode"] = child_net
    for key in ("ports", "expose"):
        child_val = child.get(key)
        if isinstance(child_val, _ComposeTag) and child_val.name in ("reset", "override"):
            out[key] = _port_entries(child_val.value)
            continue
        merged: list = []
        for src in (base, child):
            val = _unwrap_compose(src.get(key))
            if isinstance(val, list):
                merged.extend(_unwrap_compose(item) for item in val)
            elif isinstance(val, dict):
                merged.extend(_port_entries(val))
            elif val is not None and val is not False:
                merged.append(val)
        if merged:
            out[key] = merged
    child_nets_raw = child.get("networks")
    if isinstance(child_nets_raw, _ComposeTag) and child_nets_raw.name in ("reset", "override"):
        out["networks"] = _unwrap_compose(child_nets_raw.value)
    elif child_nets_raw is not None:
        child_nets = _unwrap_compose(child_nets_raw)
        base_nets = _unwrap_compose(out.get("networks"))
        if isinstance(base_nets, dict) and isinstance(child_nets, dict):
            merged_nets = dict(base_nets)
            merged_nets.update(child_nets)
            out["networks"] = merged_nets
        else:
            out["networks"] = child_nets
    return out


def _overlay_compose_docs(base: dict, child: dict) -> dict:
    """Merge a Compose override (or a later YAML document) onto *base*."""
    out = dict(base)
    child_name = child.get("name")
    if isinstance(child_name, str) and child_name.strip():
        out["name"] = child_name
    for key in ("include", "env_file"):
        extra = child.get(key)
        if extra is None:
            continue
        existing = out.get(key)
        if existing is None:
            out[key] = extra
        elif isinstance(existing, list) and isinstance(extra, list):
            out[key] = [*existing, *extra]
        elif isinstance(existing, list):
            out[key] = [*existing, extra]
        elif isinstance(extra, list):
            out[key] = [existing, *extra]
        else:
            out[key] = [existing, extra]
    child_nets = child.get("networks")
    if child_nets is not None:
        out["networks"] = _overlay_port_fields(
            {"networks": out.get("networks")}, {"networks": child_nets},
        ).get("networks")
    base_svcs = _unwrap_compose(out.get("services"))
    child_svcs = _unwrap_compose(child.get("services"))
    if isinstance(child_svcs, dict):
        merged = dict(base_svcs) if isinstance(base_svcs, dict) else {}
        for name, cfg in child_svcs.items():
            prev = merged.get(name)
            if isinstance(prev, dict) and isinstance(cfg, dict):
                merged[name] = _overlay_port_fields(prev, cfg)
            else:
                merged[name] = cfg
        out["services"] = merged
    return out


def _normalize_host_ip(host_ip) -> str | None:
    if host_ip is None:
        return None
    text = str(host_ip).strip()
    if not text:
        return None
    if text.startswith("[") and text.endswith("]") and ":" in text:
        text = text[1:-1]
    if "%" in text:
        text = text.split("%", 1)[0]
    return text or None


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
        merged_env = {**_load_env_file(Path(ref_file).parent), **env_vars}
        other_local = _services_from_file(ref_file, merged_env)
        other = other_local.get(ref_svc)
    if not isinstance(other, dict):
        return svc_cfg
    base = _resolve_extends(
        other, other_path, env_vars, chain | {ref}, other_local,
    )
    return _overlay_port_fields(base, svc_cfg)


def substitute_vars(text: str, env_vars: dict[str, str]) -> str:
    """Interpolate Compose ``$VAR`` / ``${VAR}`` from the *project* env only.

    Port-Light's process environment is not the user's compose shell; mixing it
    in lets ``HOSTNAME`` / ``PORT_RANGE_*`` rewrite other stacks' port lines.
    """

    def _replacer(m: re.Match) -> str:
        if m.group(0) == "$$":
            return "$"
        name = m.group(1) or m.group(2)
        if ":?" in name:
            var, _, _err = name.partition(":?")
            val = env_vars.get(var)
            if val is None or val == "":
                return ""
            return val
        if "?" in name:
            var, _, _err = name.partition("?")
            if var not in env_vars:
                return ""
            return env_vars[var]
        if ":-" in name:
            var, _, default = name.partition(":-")
            val = env_vars.get(var)
            if val is None or val == "":
                return default
            return val
        if "-" in name:
            var, _, default = name.partition("-")
            return env_vars[var] if var in env_vars else default
        return env_vars.get(name, m.group(0))

    return _VAR_RE.sub(_replacer, text)


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
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        if isinstance(entry, float) and not entry.is_integer():
            return []
        entry = int(entry)
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
