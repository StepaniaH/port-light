"""Compose scanner: declared host ports, includes, ranges, nested trees."""

from __future__ import annotations

import glob as _glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .netaddr import clean_bind_ip, proto_base
from .port_scanner import is_host_netns_mode

_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache",
})
_MAX_RANGE = 128

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


class ComposeWouldFail(Exception):
    """Compose would refuse this project (required interp / env_file / include)."""


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


def _macvlan_names_tree(
    filepath: str,
    extra_env: dict[str, str] | None,
    chain: frozenset[str],
    env_base: Path | None = None,
) -> set[str]:
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    loaded = _compose_doc(filepath, extra_env, env_base, apply_override=False)
    if loaded is None:
        return set()
    data, _env_vars, parent = loaded
    names = _macvlan_network_names(data)
    nested = chain | {real}
    spec_root = env_base if env_base is not None else parent
    for group, inc_env, inc_base in _include_specs(data.get("include"), spec_root):
        nested_env = {**(extra_env or {}), **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            names |= _macvlan_names_tree(
                str(inc_path), nested_env, nested, nested_base,
            )
    return names


def _extends_macvlan_names(
    svc_cfg: dict,
    filepath: str,
    env_vars: dict[str, str],
    local_services: dict,
    chain: frozenset[tuple[str, str]],
    work_dir: Path | None = None,
) -> set[str]:
    names: set[str] = set()
    ref = _extends_ref(svc_cfg.get("extends"), filepath, work_dir)
    if ref is None or ref in chain:
        return names
    ref_file, ref_svc = ref
    if os.path.realpath(ref_file) == os.path.realpath(filepath):
        other_path = filepath
        other_local = local_services
        other = other_local.get(ref_svc) if isinstance(other_local, dict) else None
        nested_dir = work_dir
    else:
        other_path = ref_file
        merged_env = {**_load_env_file(Path(ref_file).parent), **env_vars}
        names |= _macvlan_names_tree(ref_file, merged_env, frozenset())
        other_local = _services_from_file(ref_file, env_vars)
        other = other_local.get(ref_svc)
        nested_dir = Path(ref_file).parent
    if isinstance(other, dict):
        names |= _extends_macvlan_names(
            other, other_path, env_vars, other_local, chain | {ref}, nested_dir,
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
    return proto_base(proto)


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


def _service_map(raw) -> dict:
    data = _unwrap_compose(raw)
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for name, cfg in data.items():
        body = _unwrap_compose(cfg)
        if isinstance(body, dict):
            out[name] = body
    return out


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
    incomplete: bool = False
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
    incomplete = False
    for filepath in files:
        try:
            included |= _included_reals(filepath)
        except ComposeWouldFail:
            incomplete = True
    ports: list[ComposePort] = []
    seen_walk: set[str] = set()
    for filepath in files:
        real = os.path.realpath(filepath)
        if real in seen_walk or real in included:
            continue
        seen_walk.add(real)
        try:
            ports.extend(_parse_compose_file(filepath, scan_dir, frozenset()))
        except ComposeWouldFail:
            incomplete = True
    return ComposeScan(
        ports=ports,
        truncated=truncated,
        incomplete=incomplete,
        files_scanned=len(seen_walk),
    )


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
    apply_override: bool = True,
) -> tuple[dict, dict[str, str], Path] | None:
    """Load one Compose file: project env, top-level env_file, optional sibling override."""
    raw = _read_text(Path(filepath))
    if raw is None:
        return None
    parent = Path(filepath).parent
    env_root = env_base if env_base is not None else parent
    env_vars = {**_load_env_file(env_root), **(extra_env or {})}
    interpolated = substitute_vars(raw, env_vars, required=False)
    try:
        data = _load_yaml(interpolated)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    extra_file_env = _require_env_files(env_root, data.get("env_file"))
    if extra_file_env:
        env_vars = {**env_vars, **extra_file_env}
    interpolated = substitute_vars(raw, env_vars, required=True)
    try:
        data = _load_yaml(interpolated)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    if apply_override:
        ov_path = _sibling_override_path(filepath)
        if ov_path:
            ov_raw = _read_text(Path(ov_path))
            if ov_raw is not None:
                ov_interpolated = substitute_vars(ov_raw, env_vars)
                try:
                    ov_data = _load_yaml(ov_interpolated)
                except yaml.YAMLError as exc:
                    raise ComposeWouldFail(str(ov_path)) from exc
                if isinstance(ov_data, dict):
                    data = _overlay_compose_docs(data, ov_data)
                elif ov_data is not None:
                    raise ComposeWouldFail(str(ov_path))
    return data, env_vars, parent


def _included_reals(
    filepath: str,
    extra_env: dict[str, str] | None = None,
    chain: frozenset[str] = frozenset(),
    env_base: Path | None = None,
    apply_override: bool = True,
) -> set[str]:
    """Realpaths reached via ``include:`` (not scanned as their own stack)."""
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    loaded = _compose_doc(filepath, extra_env, env_base, apply_override=apply_override)
    if loaded is None:
        return set()
    data, env_vars, parent = loaded
    out: set[str] = set()
    nested_chain = chain | {real}
    spec_root = env_base if env_base is not None else parent
    for group, inc_env, inc_base in _include_specs(data.get("include"), spec_root):
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            nested = os.path.realpath(inc_path)
            out.add(nested)
            out |= _included_reals(
                str(inc_path), nested_env, nested_chain, nested_base,
                apply_override=False,
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
    apply_override: bool = True,
    extra_macvlan: set[str] | None = None,
) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in chain:
        return []
    loaded = _compose_doc(filepath, extra_env, env_base, apply_override=apply_override)
    if loaded is None:
        return []
    data, env_vars, parent = loaded
    return _parse_compose_data(
        data, filepath, parent, env_vars, scan_dir, chain | {real},
        extra_env, project_dir, project_name, env_base, extra_macvlan,
    )


def _parse_include_group(
    paths: list[Path],
    scan_dir: str,
    chain: frozenset[str],
    extra_env: dict[str, str] | None,
    project_dir: str | None,
    project_name: str | None,
    env_base: Path | None,
    extra_macvlan: set[str] | None,
) -> list[ComposePort]:
    files = [path for path in paths if path.is_file()]
    if len(files) != len(paths):
        raise ComposeWouldFail("include path is not a file")
    if not files:
        return []
    extra_chain = frozenset(os.path.realpath(str(path)) for path in files)
    if extra_chain <= chain:
        return []
    if len(files) == 1:
        return _parse_compose_file(
            str(files[0]), scan_dir, chain, extra_env,
            project_dir=project_dir, project_name=project_name,
            env_base=env_base, apply_override=False, extra_macvlan=extra_macvlan,
        )
    merged = None
    env_vars = extra_env or {}
    parent = files[0].parent
    first = str(files[0])
    for path in files:
        loaded = _compose_doc(str(path), extra_env, env_base, apply_override=False)
        if loaded is None:
            raise ComposeWouldFail(str(path))
        data, env_vars, parent = loaded
        merged = data if merged is None else _overlay_compose_docs(merged, data)
    if merged is None:
        return []
    return _parse_compose_data(
        merged, first, parent, env_vars, scan_dir, chain | extra_chain,
        extra_env, project_dir, project_name, env_base, extra_macvlan,
    )


def _parse_compose_data(
    data: dict,
    filepath: str,
    parent: Path,
    env_vars: dict[str, str],
    scan_dir: str,
    chain: frozenset[str],
    extra_env: dict[str, str] | None,
    project_dir: str | None,
    project_name: str | None,
    env_base: Path | None,
    extra_macvlan: set[str] | None,
) -> list[ComposePort]:
    ports: list[ComposePort] = []
    this_dir = project_dir if project_dir is not None else _project_dir_key(parent, scan_dir)
    fallback_name = Path(this_dir).name
    if not fallback_name or fallback_name in (".", ".."):
        fallback_name = parent.name
    this_name = _project_display_name(data.get("name"), project_name or fallback_name)

    spec_root = env_base if env_base is not None else parent
    specs = _include_specs(data.get("include"), spec_root)
    macvlan_names = set(extra_macvlan or ()) | _macvlan_network_names(data)
    for group, inc_env, inc_base in specs:
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            macvlan_names |= _macvlan_names_tree(
                str(inc_path), nested_env, chain, nested_base,
            )
    for group, inc_env, inc_base in specs:
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        ports.extend(_parse_include_group(
            group, scan_dir, chain, nested_env,
            this_dir, this_name, nested_base, macvlan_names,
        ))

    local_services = _service_map(data.get("services"))
    rel_path = os.path.relpath(filepath, scan_dir)
    work_dir = spec_root

    for svc_name, svc_cfg in local_services.items():
        _require_env_files(work_dir, svc_cfg.get("env_file"))
        lan_names = macvlan_names | _extends_macvlan_names(
            svc_cfg, filepath, env_vars, local_services, frozenset(), work_dir,
        )
        svc_cfg = _resolve_extends(
            svc_cfg, filepath, env_vars, frozenset(), local_services, work_dir,
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


def _include_specs(
    include, parent: Path,
) -> list[tuple[list[Path], dict[str, str], Path | None]]:
    if not include:
        return []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        return []
    out: list[tuple[list[Path], dict[str, str], Path | None]] = []
    for item in include:
        paths: list[str] = []
        extra: dict[str, str] = {}
        env_base: Path | None = None
        if isinstance(item, str):
            paths = [item]
        elif isinstance(item, dict):
            extra = _require_env_files(parent, item.get("env_file"))
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
        group: list[Path] = []
        for path in paths:
            if not path:
                continue
            try:
                if any(ch in path for ch in "*?["):
                    pattern = os.path.normpath(os.path.join(str(parent), path))
                    candidates = sorted(Path(p).resolve() for p in _glob.glob(pattern))
                else:
                    candidates = [(parent / path).resolve()]
            except (TypeError, ValueError, OSError):
                raise ComposeWouldFail(path) from None
            for resolved in candidates:
                if resolved.is_file():
                    group.append(resolved)
        if paths and not group:
            raise ComposeWouldFail("include matched no files")
        if group:
            out.append((group, extra, env_base))
    return out


def _flag_required(raw) -> bool:
    if raw is False:
        return False
    if isinstance(raw, str) and raw.strip().lower() in ("false", "no", "0"):
        return False
    return True


def _env_file_specs(env_file) -> list[tuple[str, bool]]:
    """``(path, required)``. Compose defaults ``required`` to true."""
    env_file = _unwrap_compose(env_file)
    if not env_file:
        return []
    if isinstance(env_file, str):
        return [(env_file, True)]
    if isinstance(env_file, dict):
        path = env_file.get("path")
        if not isinstance(path, str) or not path.strip():
            return []
        return [(path, _flag_required(env_file.get("required", True)))]
    if not isinstance(env_file, list):
        return []
    names: list[tuple[str, bool]] = []
    for item in env_file:
        item = _unwrap_compose(item)
        if isinstance(item, str) and item.strip():
            names.append((item, True))
        elif isinstance(item, dict):
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                names.append((path, _flag_required(item.get("required", True))))
    return names


def _require_env_files(parent: Path, env_file) -> dict[str, str]:
    merged: dict[str, str] = {}
    for name, required in _env_file_specs(env_file):
        path = (parent / name).resolve()
        if not path.is_file():
            if required:
                raise ComposeWouldFail(str(path))
            continue
        merged.update(_read_env_file(path))
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
        val = val.strip()
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        else:
            for i, ch in enumerate(val):
                if ch == "#" and (i == 0 or val[i - 1].isspace()):
                    val = val[:i].rstrip()
                    break
        env[key] = val
    return env


def _extends_ref(ext, filepath: str, work_dir: Path | None = None) -> tuple[str, str] | None:
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
    base = work_dir if work_dir is not None else Path(filepath).parent
    resolved = (base / f).resolve()
    if not resolved.is_file():
        raise ComposeWouldFail(str(resolved))
    return str(resolved), svc


def _services_from_file(filepath: str, env_vars: dict[str, str]) -> dict:
    loaded = _compose_doc(
        filepath,
        extra_env=env_vars,
        env_base=Path(filepath).parent,
        apply_override=False,
    )
    if loaded is None:
        raise ComposeWouldFail(filepath)
    data, _merged, _parent = loaded
    return _service_map(data.get("services"))


def _overlay_port_fields(base: dict, child: dict) -> dict:
    out = dict(base)
    child_net_raw = child.get("network_mode")
    if isinstance(child_net_raw, _ComposeTag):
        if child_net_raw.name == "reset":
            out.pop("network_mode", None)
        else:
            net = _unwrap_compose(child_net_raw.value)
            if net:
                out["network_mode"] = net
            else:
                out.pop("network_mode", None)
    else:
        child_net = _unwrap_compose(child_net_raw)
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
    child_deploy_raw = child.get("deploy")
    if child_deploy_raw is not None:
        base_deploy = _unwrap_compose(out.get("deploy"))
        if not isinstance(base_deploy, dict):
            base_deploy = {}
        if isinstance(child_deploy_raw, _ComposeTag) and child_deploy_raw.name in ("reset", "override"):
            body = _unwrap_compose(child_deploy_raw.value)
            out["deploy"] = body if isinstance(body, dict) else {}
        else:
            child_deploy = _unwrap_compose(child_deploy_raw)
            if not isinstance(child_deploy, dict):
                out["deploy"] = child_deploy
            else:
                merged_deploy = dict(base_deploy)
                merged_deploy.update({k: v for k, v in child_deploy.items() if k != "ports"})
                if "ports" in child_deploy:
                    merged_deploy["ports"] = _overlay_port_fields(
                        {"ports": base_deploy.get("ports")},
                        {"ports": child_deploy.get("ports")},
                    ).get("ports")
                out["deploy"] = merged_deploy
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
    base_svcs = _service_map(out.get("services"))
    child_svcs_raw = child.get("services")
    if isinstance(child_svcs_raw, _ComposeTag) and child_svcs_raw.name in ("reset", "override"):
        out["services"] = _service_map(child_svcs_raw.value)
    elif isinstance(child_svcs_raw, dict) or isinstance(_unwrap_compose(child_svcs_raw), dict):
        child_svcs = child_svcs_raw if isinstance(child_svcs_raw, dict) else _unwrap_compose(child_svcs_raw)
        merged = dict(base_svcs)
        for name, cfg in child_svcs.items():
            tagged = isinstance(cfg, _ComposeTag) and cfg.name in ("reset", "override")
            body = _unwrap_compose(cfg)
            if tagged:
                merged[name] = body if isinstance(body, dict) else {}
                continue
            prev = merged.get(name)
            if isinstance(prev, dict) and isinstance(body, dict):
                merged[name] = _overlay_port_fields(prev, body)
            elif isinstance(body, dict):
                merged[name] = body
        out["services"] = merged
    return out


def _normalize_host_ip(host_ip) -> str | None:
    if host_ip is None:
        return None
    text = clean_bind_ip(str(host_ip))
    return text or None


def _resolve_extends(
    svc_cfg: dict,
    filepath: str,
    env_vars: dict[str, str],
    chain: frozenset[tuple[str, str]],
    local_services: dict,
    work_dir: Path | None = None,
) -> dict:
    ref = _extends_ref(svc_cfg.get("extends"), filepath, work_dir)
    if ref is None:
        return svc_cfg
    if ref in chain:
        return svc_cfg
    ref_file, ref_svc = ref
    if os.path.realpath(ref_file) == os.path.realpath(filepath):
        other_local = local_services
        other_path = filepath
        other = other_local.get(ref_svc) if isinstance(other_local, dict) else None
        nested_dir = work_dir
    else:
        other_path = ref_file
        other_local = _services_from_file(ref_file, env_vars)
        other = other_local.get(ref_svc)
        nested_dir = Path(ref_file).parent
    if not isinstance(other, dict):
        raise ComposeWouldFail(ref_svc)
    _require_env_files(
        nested_dir if nested_dir is not None else Path(other_path).parent,
        other.get("env_file"),
    )
    base = _resolve_extends(
        other, other_path, env_vars, chain | {ref}, other_local, nested_dir,
    )
    return _overlay_port_fields(base, svc_cfg)


def substitute_vars(
    text: str,
    env_vars: dict[str, str],
    *,
    required: bool = True,
) -> str:
    """Interpolate Compose ``$VAR`` / ``${VAR}`` from the *project* env only.

    Port-Light's process environment is not the user's compose shell; mixing it
    in lets ``HOSTNAME`` / ``PORT_RANGE_*`` rewrite other stacks' port lines.
    ``required=False`` is the first YAML pass so ``env_file`` can still load
    ``${VAR:?}`` values; the second pass is strict (Compose would abort).
    """

    def _braced(s: str, start: int) -> tuple[str, int] | None:
        depth = 1
        j = start
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[start:j], j + 1
            j += 1
        return None

    def _expand_name(name: str) -> str:
        if ":?" in name:
            var, _, _err = name.partition(":?")
            val = env_vars.get(var)
            if val is None or val == "":
                if required:
                    raise ComposeWouldFail("${" + var + ":?}")
                return ""
            return val
        if ":+" in name:
            var, _, alt = name.partition(":+")
            val = env_vars.get(var)
            if val is None or val == "":
                return ""
            return substitute_vars(alt, env_vars, required=required)
        if ":-" in name:
            var, _, default = name.partition(":-")
            val = env_vars.get(var)
            if val is None or val == "":
                return substitute_vars(default, env_vars, required=required)
            return val
        if "?" in name:
            var, _, _err = name.partition("?")
            if var not in env_vars:
                if required:
                    raise ComposeWouldFail("${" + var + "?}")
                return ""
            return env_vars[var]
        if "+" in name:
            var, _, alt = name.partition("+")
            if var not in env_vars:
                return ""
            return substitute_vars(alt, env_vars, required=required)
        if "-" in name:
            var, _, default = name.partition("-")
            if var in env_vars:
                return env_vars[var]
            return substitute_vars(default, env_vars, required=required)
        if name in env_vars:
            return env_vars[name]
        return "${" + name + "}"

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "$" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt == "$":
            out.append("$")
            i += 2
            continue
        if nxt == "{":
            parsed = _braced(text, i + 2)
            if parsed is None:
                out.append(ch)
                i += 1
                continue
            inner, end = parsed
            out.append(_expand_name(inner))
            i = end
            continue
        m = re.match(r"\w+", text[i + 1:])
        if m:
            name = m.group(0)
            out.append(env_vars[name] if name in env_vars else "$" + name)
            i += 1 + len(name)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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
