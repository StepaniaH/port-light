"""Compose scanner: declared host ports, includes, ranges, nested trees."""

from __future__ import annotations

import glob as _glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .compose_limits import ComposeWouldFail, ComposePortLimit, _PortBudget, _port_budget, _consume_ports
from .compose_ports import (_norm_proto as _norm_proto, _normalize_host_ip as _normalize_host_ip, parse_port_entry as parse_port_entry, parse_expose_entry as parse_expose_entry, parse_short_port as parse_short_port, _port_interval as _port_interval, expand_port_range as expand_port_range, _target_ports as _target_ports, _published_unset as _published_unset)
from .compose_documents import (_unwrap_compose as _unwrap_compose, _service_map as _service_map, _port_entries as _port_entries, _overlay_port_fields as _overlay_port_fields, _overlay_compose_docs as _overlay_compose_docs, _load_yaml as _load_yaml, _ComposeTag as _ComposeTag)

from .port_scanner import is_host_netns_mode
from . import degradations

_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache",
})
_MAX_RANGE = 4096
_MAX_SCAN_PORTS = 65536

_COMPOSE_PREFIXES = ("compose.", "docker-compose.")
_COMPOSE_SUFFIXES = (".yml", ".yaml")


_AUTO_OVERRIDE_NAMES = frozenset({
    "compose.override.yml", "compose.override.yaml",
    "docker-compose.override.yml", "docker-compose.override.yaml",
})


def _parse_entry(entry, parser, filepath: str):
    try:
        return parser(entry)
    except ComposePortLimit as exc:
        exc.filepath = filepath
        raise


def _macvlan_names_tree(
    filepath: str,
    extra_env: dict[str, str] | None,
    chain: frozenset[str],
    env_base: Path | None = None,
    cache: dict | None = None,
) -> set[str]:
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    data, _env_vars, parent = _compose_doc(
        filepath, extra_env, env_base, apply_override=False, cache=cache,
    )
    names = _macvlan_network_names(data)
    nested = chain | {real}
    spec_root = env_base if env_base is not None else parent
    for group, inc_env, inc_base in _include_specs(data.get("include"), spec_root):
        nested_env = {**(extra_env or {}), **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            names |= _macvlan_names_tree(
                str(inc_path), nested_env, nested, nested_base, cache=cache,
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

    def __post_init__(self):
        _consume_ports(1, emitted=True, filepath=self.compose_file)
    mapping_source: str = "ports"


@dataclass
class ComposeScan:
    ports: list[ComposePort] = field(default_factory=list)
    truncated: bool = False
    incomplete: bool = False
    files_scanned: int = 0
    diagnostics: list[dict] = field(default_factory=list)


def scan_compose_files(
    scan_dir: str,
    max_depth: int | None = None,
    max_files: int | None = None,
    exclude_dirs: tuple[str, ...] | list[str] | None = None,
) -> list[ComposePort]:
    return scan_compose_tree(
        scan_dir, max_depth=max_depth, max_files=max_files, exclude_dirs=exclude_dirs,
    ).ports


def scan_compose_tree(scan_dir: str, max_depth: int | None = None,
                      max_files: int | None = None,
                      exclude_dirs: tuple[str, ...] | list[str] | None = None) -> ComposeScan:
    token = _port_budget.set(_PortBudget(limit=_MAX_SCAN_PORTS))
    try:
        return _scan_compose_tree(scan_dir, max_depth, max_files, exclude_dirs)
    finally:
        _port_budget.reset(token)


def _scan_compose_tree(
    scan_dir: str,
    max_depth: int | None = None,
    max_files: int | None = None,
    exclude_dirs: tuple[str, ...] | list[str] | None = None,
) -> ComposeScan:
    if not os.path.isdir(scan_dir):
        degradations.report("compose", "scan root", "directory unavailable")
        return ComposeScan(incomplete=True)

    depth = max_depth if max_depth is not None else _env_int("COMPOSE_SCAN_DEPTH", 4)
    files_cap = max_files if max_files is not None else _env_int("COMPOSE_SCAN_MAX_FILES", 400)

    try:
        files, truncated = _find_compose_files(
            scan_dir, depth, files_cap, exclude_dirs=exclude_dirs or (),
        )
    except OSError:
        degradations.report("compose", "scan root", "directory unreadable")
        return ComposeScan(incomplete=True)
    included: set[str] = set()
    incomplete = False
    cache: dict = {}
    for filepath in files:
        try:
            included |= _included_reals(filepath, cache=cache)
        except ComposeWouldFail as exc:
            incomplete = True
            degradations.report("compose", _degradation_scope(scan_dir, exc), "invalid compose file")
    ports: list[ComposePort] = []
    diagnostics: list[dict] = []
    seen_walk: set[str] = set()
    for filepath in files:
        real = os.path.realpath(filepath)
        if real in seen_walk or real in included:
            continue
        seen_walk.add(real)
        try:
            ports.extend(_parse_compose_file(filepath, scan_dir, frozenset(), cache=cache))
        except ComposeWouldFail as exc:
            incomplete = True
            path = exc.filepath if isinstance(exc, ComposePortLimit) and exc.filepath else filepath
            relative = os.path.relpath(path, scan_dir) if os.path.isabs(path) else path
            if len(diagnostics) < 8:
                diagnostics.append({"code": exc.code if isinstance(exc, ComposePortLimit) else "invalid_file",
                                    "file": relative,
                                    "range": exc.spec if isinstance(exc, ComposePortLimit) else "",
                                    "limit": exc.limit if isinstance(exc, ComposePortLimit) else None})
            degradations.report("compose", relative, "port expansion limit" if isinstance(exc, ComposePortLimit) else "invalid compose file")
            if isinstance(exc, ComposePortLimit) and exc.code == "port_budget":
                break
    return ComposeScan(
        ports=ports,
        truncated=truncated,
        incomplete=incomplete,
        files_scanned=len(seen_walk),
        diagnostics=diagnostics,
    )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _degradation_scope(scan_dir: str, exc: ComposeWouldFail) -> str:
    """Scan-root-relative path when the exception carries one, else its text."""
    text = str(exc.args[0]) if exc.args else ""
    if text and os.path.isabs(text):
        try:
            return os.path.relpath(text, scan_dir)
        except ValueError:
            return text
    return text or "unknown"


def _find_compose_files(
    scan_dir: str,
    max_depth: int,
    max_files: int,
    exclude_dirs: tuple[str, ...] | list[str] = (),
) -> tuple[list[str], bool]:
    found: list[str] = []
    scan_dir = os.path.abspath(scan_dir)
    skip_dirs = _SKIP_DIRS | frozenset(exclude_dirs)
    def on_error(exc):
        raise exc

    for root, dirs, files in os.walk(scan_dir, onerror=on_error):
        rel = os.path.relpath(root, scan_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs.clear()
            continue
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith("."))
        if depth == max_depth:
            dirs.clear()
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
    cache: dict | None = None,
) -> tuple[dict, dict[str, str], Path]:
    """Load one Compose file: project env, top-level env_file, optional sibling override.

    Raises ``ComposeWouldFail`` when the file is unreadable or invalid —
    Compose itself would refuse it. Results are memoized per scan when a
    ``cache`` dict is passed (the same file is otherwise loaded several
    times per scan: include discovery, macvlan names, service parsing).
    """
    if cache is not None:
        key = (
            os.path.realpath(filepath),
            bool(apply_override),
            None if extra_env is None else tuple(sorted(extra_env.items())),
            str(env_base) if env_base is not None else None,
        )
        hit = cache.get(key)
        if hit is not None:
            return hit
        result = _compose_doc_uncached(filepath, extra_env, env_base, apply_override)
        cache[key] = result
        return result
    return _compose_doc_uncached(filepath, extra_env, env_base, apply_override)


def _compose_doc_uncached(
    filepath: str,
    extra_env: dict[str, str] | None,
    env_base: Path | None,
    apply_override: bool,
) -> tuple[dict, dict[str, str], Path]:
    raw = _read_text(Path(filepath))
    if raw is None:
        raise ComposeWouldFail(str(filepath))
    parent = Path(filepath).parent
    env_root = env_base if env_base is not None else parent
    env_vars = {**_load_env_file(env_root), **(extra_env or {})}
    interpolated = substitute_vars(raw, env_vars, required=False)
    try:
        data = _load_yaml(interpolated)
    except yaml.YAMLError as exc:
        raise ComposeWouldFail(str(filepath)) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ComposeWouldFail(str(filepath))
    extra_file_env = _require_env_files(env_root, data.get("env_file"))
    if extra_file_env:
        env_vars = {**env_vars, **extra_file_env}
    interpolated = substitute_vars(raw, env_vars, required=True)
    try:
        data = _load_yaml(interpolated)
    except yaml.YAMLError as exc:
        raise ComposeWouldFail(str(filepath)) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ComposeWouldFail(str(filepath))
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
    cache: dict | None = None,
) -> set[str]:
    """Realpaths reached via ``include:`` (not scanned as their own stack)."""
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    data, env_vars, parent = _compose_doc(
        filepath, extra_env, env_base, apply_override=apply_override, cache=cache,
    )
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
                apply_override=False, cache=cache,
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
    cache: dict | None = None,
) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in chain:
        return []
    data, env_vars, parent = _compose_doc(
        filepath, extra_env, env_base, apply_override=apply_override, cache=cache,
    )
    return _parse_compose_data(
        data, filepath, parent, env_vars, scan_dir, chain | {real},
        extra_env, project_dir, project_name, env_base, extra_macvlan, cache,
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
    cache: dict | None = None,
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
            cache=cache,
        )
    merged = None
    env_vars = extra_env or {}
    parent = files[0].parent
    first = str(files[0])
    for path in files:
        data, env_vars, parent = _compose_doc(
            str(path), extra_env, env_base, apply_override=False, cache=cache,
        )
        merged = data if merged is None else _overlay_compose_docs(merged, data)
    if merged is None:
        return []
    return _parse_compose_data(
        merged, first, parent, env_vars, scan_dir, chain | extra_chain,
        extra_env, project_dir, project_name, env_base, extra_macvlan, cache,
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
    cache: dict | None = None,
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
                str(inc_path), nested_env, chain, nested_base, cache=cache,
            )
    for group, inc_env, inc_base in specs:
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        ports.extend(_parse_include_group(
            group, scan_dir, chain, nested_env,
            this_dir, this_name, nested_base, macvlan_names, cache,
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
            entries = [("ports", entry) for entry in _port_entries(svc_cfg.get("ports"))]
            deploy = _unwrap_compose(svc_cfg.get("deploy"))
            if isinstance(deploy, dict):
                entries.extend(("deploy.ports", entry) for entry in _port_entries(deploy.get("ports")))
        for source, entry in entries:
            for p in _parse_entry(entry, parse_port_entry, filepath):
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
                    mapping_source=source,
                ))
        if _is_host_network(net):
            for entry in _port_entries(svc_cfg.get("expose")):
                for p in _parse_entry(entry, parse_expose_entry, filepath):
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
                        mapping_source="expose",
                    ))
            continue
        lan_ips = _service_macvlan_ips(svc_cfg, lan_names)
        if not lan_ips:
            continue
        lan_rows: list[dict] = []
        for entry in _port_entries(svc_cfg.get("expose")):
            lan_rows.extend(_parse_entry(entry, parse_expose_entry, filepath))
        for source, entry in entries:
            parsed = _parse_entry(entry, parse_port_entry, filepath)
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
                extra = _parse_entry(entry.get("target"), parse_expose_entry, filepath)
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
                    mapping_source="lan",
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
