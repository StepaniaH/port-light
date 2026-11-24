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
from .safe_io import MAX_COMPOSE_BYTES, MAX_ENV_BYTES, is_blocked_path, is_within_root, read_regular_text
from . import degradations

_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache",
})
_MAX_RANGE = 4096
_MAX_SCAN_PORTS = 65536
_MAX_EXPANDED_CHARS = MAX_COMPOSE_BYTES * 2
_MAX_INTERP_DEPTH = 32
_VAR_NAME_RE = re.compile(r"\w+")

_COMPOSE_PREFIXES = ("compose.", "docker-compose.")
_COMPOSE_SUFFIXES = (".yml", ".yaml")


_AUTO_OVERRIDE_NAMES = frozenset({
    "compose.override.yml", "compose.override.yaml",
    "docker-compose.override.yml", "docker-compose.override.yaml",
})


class _ComposeFileLimit(ComposeWouldFail):
    """Raised when Compose include expansion exceeds the scan file budget."""


@dataclass
class _FileBudget:
    limit: int
    seen: set[str] = field(default_factory=set)
    truncated: bool = False

    def admit(self, path: str | os.PathLike[str]) -> bool:
        real = os.path.realpath(path)
        if real in self.seen:
            return True
        if len(self.seen) >= self.limit:
            self.truncated = True
            return False
        self.seen.add(real)
        return True


def _confined_within(path: str | os.PathLike[str], scan_root: str | None) -> bool:
    if scan_root is None:
        return True
    return is_within_root(path, scan_root)


def _glob_prefix(pattern: str) -> str:
    parts = Path(pattern).parts
    prefix_parts: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        prefix_parts.append(part)
    if not prefix_parts:
        return ""
    return str(Path(*prefix_parts))


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
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
) -> set[str]:
    real = os.path.realpath(filepath)
    if real in chain:
        return set()
    data, _env_vars, parent = _compose_doc(
        filepath, extra_env, env_base, apply_override=False, cache=cache,
        scan_root=scan_root,
    )
    names = _macvlan_network_names(data)
    nested = chain | {real}
    spec_root = env_base if env_base is not None else parent
    for group, inc_env, inc_base in _include_specs(
        data.get("include"), spec_root, owner_file=filepath,
        scan_root=scan_root, file_budget=file_budget,
    ):
        nested_env = {**(extra_env or {}), **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            names |= _macvlan_names_tree(
                str(inc_path), nested_env, nested, nested_base, cache=cache,
                scan_root=scan_root, file_budget=file_budget,
            )
    return names


def _extends_macvlan_names(
    svc_cfg: dict,
    filepath: str,
    env_vars: dict[str, str],
    local_services: dict,
    chain: frozenset[tuple[str, str]],
    work_dir: Path | None = None,
    cache: dict | None = None,
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
) -> set[str]:
    names: set[str] = set()
    ref = _extends_ref(svc_cfg.get("extends"), filepath, work_dir, scan_root=scan_root)
    if ref is None:
        return names
    ref_file, ref_svc = ref
    cycle_key = (os.path.realpath(ref_file), ref_svc)
    if cycle_key in chain:
        return names
    if os.path.abspath(ref_file) == os.path.abspath(filepath):
        other_path = filepath
        other_local = local_services
        other = other_local.get(ref_svc) if isinstance(other_local, dict) else None
        nested_dir = work_dir
        next_env = env_vars
    else:
        other_path = ref_file
        other_local, other_env = _services_from_file(
            ref_file, env_vars, cache=cache, scan_root=scan_root,
        )
        next_env = {**other_env, **env_vars}
        names |= _macvlan_names_tree(
            ref_file, env_vars, frozenset(), cache=cache,
            scan_root=scan_root, file_budget=file_budget,
        )
        other = other_local.get(ref_svc)
        nested_dir = Path(ref_file).parent
    if isinstance(other, dict):
        names |= _extends_macvlan_names(
            other, other_path, next_env, other_local, chain | {cycle_key},
            nested_dir, cache=cache, scan_root=scan_root, file_budget=file_budget,
        )
    return names


def _read_text(
    path: Path,
    max_bytes: int = MAX_COMPOSE_BYTES,
    *,
    allowed_root: str | None = None,
) -> str | None:
    try:
        text = read_regular_text(path, max_bytes=max_bytes, allowed_root=allowed_root)
    except (OSError, ValueError):
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
        cfg = _unwrap_compose(cfg)
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
    publish_mode: str | None = None

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
    scan_root = os.path.realpath(scan_dir)
    file_budget = _FileBudget(limit=files_cap, seen={os.path.realpath(p) for p in files})
    included_abs: set[str] = set()
    included_real: set[str] = set()
    limit_failed_abs: set[str] = set()
    incomplete = False
    cache: dict = {}
    for filepath in files:
        try:
            inc_abs, inc_real = _included_paths(
                filepath, cache=cache, scan_root=scan_root, file_budget=file_budget,
            )
            included_abs |= inc_abs
            included_real |= inc_real
        except _ComposeFileLimit as exc:
            truncated = True
            incomplete = True
            limit_failed_abs.add(os.path.abspath(filepath))
            degradations.report("compose", _degradation_scope(scan_dir, exc, filepath), "compose file limit reached")
        except ComposeWouldFail as exc:
            incomplete = True
            degradations.report("compose", _degradation_scope(scan_dir, exc, filepath), "invalid compose file")
    ports: list[ComposePort] = []
    diagnostics: list[dict] = []
    seen_walk: set[str] = set()
    for filepath in files:
        abs_path = os.path.abspath(filepath)
        real = os.path.realpath(filepath)
        if abs_path in seen_walk or abs_path in included_abs or abs_path in limit_failed_abs:
            continue
        if not os.path.islink(filepath) and real in included_real:
            continue
        seen_walk.add(abs_path)
        try:
            ports.extend(_parse_compose_file(
                filepath, scan_dir, frozenset(), cache=cache,
                scan_root=scan_root, file_budget=file_budget,
            ))
        except _ComposeFileLimit as exc:
            truncated = True
            incomplete = True
            degradations.report("compose", _degradation_scope(scan_dir, exc, filepath), "compose file limit reached")
        except ComposeWouldFail as exc:
            incomplete = True
            relative = _degradation_scope(scan_dir, exc, filepath)
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
        truncated=truncated or file_budget.truncated,
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


def _degradation_scope(scan_dir: str, exc: ComposeWouldFail, filepath: str = "") -> str:
    """Scan-root-relative Compose file path without interpolated content."""
    path = exc.filepath if isinstance(exc, ComposePortLimit) and exc.filepath else filepath
    if not path:
        return "compose"
    if os.path.isabs(path):
        try:
            return os.path.relpath(path, scan_dir)
        except ValueError:
            return os.path.basename(path) or "compose"
    return path


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
        if os.path.lexists(path) and not os.path.isdir(path):
            return path
    return None


def _compose_doc(
    filepath: str,
    extra_env: dict[str, str] | None = None,
    env_base: Path | None = None,
    apply_override: bool = True,
    cache: dict | None = None,
    *,
    scan_root: str | None = None,
) -> tuple[dict, dict[str, str], Path]:
    """Load one Compose file: project env, top-level env_file, optional sibling override.

    Raises ``ComposeWouldFail`` when the file is unreadable or invalid —
    Compose itself would refuse it. Results are memoized per scan when a
    ``cache`` dict is passed (the same file is otherwise loaded several
    times per scan: include discovery, macvlan names, service parsing).
    """
    if cache is not None:
        eff_root = (
            os.path.abspath(str(env_base))
            if env_base is not None
            else os.path.abspath(os.path.dirname(filepath))
        )
        key = (
            os.path.realpath(filepath),
            eff_root,
            os.path.abspath(os.path.dirname(filepath)) if apply_override else None,
            bool(apply_override),
            None if extra_env is None else tuple(sorted(extra_env.items())),
            scan_root,
        )
        hit = cache.get(key)
        if hit is not None:
            return hit
        result = _compose_doc_uncached(
            filepath, extra_env, env_base, apply_override, scan_root=scan_root,
        )
        cache[key] = result
        return result
    return _compose_doc_uncached(
        filepath, extra_env, env_base, apply_override, scan_root=scan_root,
    )


def _compose_doc_uncached(
    filepath: str,
    extra_env: dict[str, str] | None,
    env_base: Path | None,
    apply_override: bool,
    *,
    scan_root: str | None = None,
) -> tuple[dict, dict[str, str], Path]:
    raw = _read_text(Path(filepath), allowed_root=None if apply_override else scan_root)
    if raw is None:
        raise ComposeWouldFail(str(filepath))
    parent = Path(filepath).parent
    env_root = env_base if env_base is not None else parent
    env_scan_root = scan_root if (env_base is not None or not apply_override) else None
    env_vars = {
        **_load_env_file(env_root, owner_file=str(filepath), scan_root=env_scan_root),
        **(extra_env or {}),
    }
    interpolated = substitute_vars(raw, env_vars, required=False)
    try:
        data = _load_yaml(interpolated)
    except yaml.YAMLError as exc:
        raise ComposeWouldFail(str(filepath)) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ComposeWouldFail(str(filepath))
    extra_file_env = _require_env_files(
        env_root, data.get("env_file"), owner_file=str(filepath), scan_root=scan_root,
    )
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
            if ov_raw is None:
                raise ComposeWouldFail(str(filepath))
            ov_interpolated = substitute_vars(ov_raw, env_vars)
            try:
                ov_data = _load_yaml(ov_interpolated)
            except yaml.YAMLError as exc:
                raise ComposeWouldFail(str(filepath)) from exc
            if isinstance(ov_data, dict):
                ov_env = _require_env_files(
                    env_root, ov_data.get("env_file"), owner_file=str(filepath),
                    scan_root=scan_root,
                )
                if ov_env:
                    env_vars = {**env_vars, **ov_env}
                data = _overlay_compose_docs(data, ov_data)
            elif ov_data is not None:
                raise ComposeWouldFail(str(filepath))
    return data, env_vars, parent


def _included_paths(
    filepath: str,
    extra_env: dict[str, str] | None = None,
    chain: frozenset[str] = frozenset(),
    env_base: Path | None = None,
    apply_override: bool = True,
    cache: dict | None = None,
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
) -> tuple[set[str], set[str]]:
    """Paths and realpaths reached via ``include:`` (not scanned as their own stack)."""
    real = os.path.realpath(filepath)
    if real in chain:
        return set(), set()
    data, env_vars, parent = _compose_doc(
        filepath, extra_env, env_base, apply_override=apply_override, cache=cache,
        scan_root=scan_root,
    )
    out_abs: set[str] = set()
    out_real: set[str] = set()
    nested_chain = chain | {real}
    spec_root = env_base if env_base is not None else parent
    for group, inc_env, inc_base in _include_specs(
        data.get("include"), spec_root, owner_file=filepath,
        scan_root=scan_root, file_budget=file_budget,
    ):
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            out_abs.add(os.path.abspath(str(inc_path)))
            out_real.add(os.path.realpath(str(inc_path)))
            sub_abs, sub_real = _included_paths(
                str(inc_path), nested_env, nested_chain, nested_base,
                apply_override=False, cache=cache,
                scan_root=scan_root, file_budget=file_budget,
            )
            out_abs |= sub_abs
            out_real |= sub_real
    return out_abs, out_real


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
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
) -> list[ComposePort]:
    real = os.path.realpath(filepath)
    if real in chain:
        return []
    data, env_vars, parent = _compose_doc(
        filepath, extra_env, env_base, apply_override=apply_override, cache=cache,
        scan_root=scan_root,
    )
    return _parse_compose_data(
        data, filepath, parent, env_vars, scan_dir, chain | {real},
        extra_env, project_dir, project_name, env_base, extra_macvlan, cache,
        scan_root=scan_root, file_budget=file_budget,
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
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
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
            cache=cache, scan_root=scan_root, file_budget=file_budget,
        )
    merged = None
    env_vars = extra_env or {}
    parent = files[0].parent
    first = str(files[0])
    for path in files:
        data, env_vars, parent = _compose_doc(
            str(path), extra_env, env_base, apply_override=False, cache=cache,
            scan_root=scan_root,
        )
        merged = data if merged is None else _overlay_compose_docs(merged, data)
    if merged is None:
        return []
    return _parse_compose_data(
        merged, first, parent, env_vars, scan_dir, chain | extra_chain,
        extra_env, project_dir, project_name, env_base, extra_macvlan, cache,
        scan_root=scan_root, file_budget=file_budget,
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
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
) -> list[ComposePort]:
    ports: list[ComposePort] = []
    this_dir = project_dir if project_dir is not None else _project_dir_key(parent, scan_dir)
    fallback_name = Path(this_dir).name
    if not fallback_name or fallback_name in (".", ".."):
        fallback_name = parent.name
    this_name = _project_display_name(data.get("name"), project_name or fallback_name)

    spec_root = env_base if env_base is not None else parent
    specs = _include_specs(
        data.get("include"), spec_root, owner_file=filepath,
        scan_root=scan_root, file_budget=file_budget,
    )
    macvlan_names = set(extra_macvlan or ()) | _macvlan_network_names(data)
    for group, inc_env, inc_base in specs:
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        for inc_path in group:
            macvlan_names |= _macvlan_names_tree(
                str(inc_path), nested_env, chain, nested_base, cache=cache,
                scan_root=scan_root, file_budget=file_budget,
            )
    for group, inc_env, inc_base in specs:
        nested_env = {**env_vars, **inc_env}
        nested_base = inc_base if inc_base is not None else env_base
        ports.extend(_parse_include_group(
            group, scan_dir, chain, nested_env,
            this_dir, this_name, nested_base, macvlan_names, cache,
            scan_root=scan_root, file_budget=file_budget,
        ))

    local_services = _service_map(data.get("services"))
    rel_path = os.path.relpath(filepath, scan_dir)
    work_dir = spec_root

    for svc_name, svc_cfg in local_services.items():
        _require_env_files(work_dir, svc_cfg.get("env_file"), owner_file=filepath, scan_root=scan_root)
        lan_names = macvlan_names | _extends_macvlan_names(
            svc_cfg, filepath, env_vars, local_services, frozenset(), work_dir,
            cache=cache, scan_root=scan_root, file_budget=file_budget,
        )
        svc_cfg = _resolve_extends(
            svc_cfg, filepath, env_vars, frozenset(), local_services, work_dir,
            cache=cache, scan_root=scan_root,
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
                    publish_mode=p.get("mode"),
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
    include,
    parent: Path,
    owner_file: str = "",
    *,
    scan_root: str | None = None,
    file_budget: _FileBudget | None = None,
) -> list[tuple[list[Path], dict[str, str], Path | None]]:
    if not include:
        return []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        return []
    scope = owner_file or "include"
    out: list[tuple[list[Path], dict[str, str], Path | None]] = []
    for item in include:
        paths: list[str] = []
        extra: dict[str, str] = {}
        env_base: Path | None = None
        if isinstance(item, str):
            paths = [item]
        elif isinstance(item, dict):
            extra = _require_env_files(
                parent, item.get("env_file"), owner_file=scope, scan_root=scan_root,
            )
            proj = item.get("project_directory")
            if isinstance(proj, str) and proj.strip():
                try:
                    proj_dir = Path(os.path.normpath(os.path.join(str(parent), proj)))
                except (TypeError, ValueError, OSError):
                    raise ComposeWouldFail(scope) from None
                if not _confined_within(proj_dir, scan_root) or is_blocked_path(proj_dir):
                    raise ComposeWouldFail(scope)
                if proj_dir.is_dir():
                    extra = {
                        **_load_env_file(proj_dir, owner_file=scope, scan_root=scan_root),
                        **extra,
                    }
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
                    prefix = _glob_prefix(pattern)
                    if scan_root is not None and (not prefix or not _confined_within(prefix, scan_root)):
                        raise ComposeWouldFail(scope)
                    candidates: list[Path] = []
                    new_reals: set[str] = set()
                    for match in _glob.iglob(pattern):
                        cand = Path(os.path.normpath(match))
                        if not _confined_within(cand, scan_root) or is_blocked_path(cand):
                            raise ComposeWouldFail(scope)
                        if not cand.is_file():
                            continue
                        if file_budget is not None:
                            real = os.path.realpath(cand)
                            if real not in file_budget.seen:
                                new_reals.add(real)
                                if len(file_budget.seen) + len(new_reals) > file_budget.limit:
                                    file_budget.truncated = True
                                    raise _ComposeFileLimit(scope)
                        candidates.append(cand)
                    candidates.sort()
                else:
                    candidates = [Path(os.path.normpath(os.path.join(str(parent), path)))]
            except ComposeWouldFail:
                raise
            except (TypeError, ValueError, OSError):
                raise ComposeWouldFail(scope) from None
            for cand in candidates:
                if not _confined_within(cand, scan_root) or is_blocked_path(cand):
                    raise ComposeWouldFail(scope)
                if cand.is_file():
                    if file_budget is not None and not file_budget.admit(cand):
                        raise _ComposeFileLimit(scope)
                    group.append(cand)
        if paths and not group:
            raise ComposeWouldFail(scope)
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


def _require_env_files(
    parent: Path,
    env_file,
    owner_file: str = "",
    *,
    scan_root: str | None = None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    scope = owner_file or "env_file"
    for name, required in _env_file_specs(env_file):
        try:
            candidate = Path(os.path.normpath(os.path.join(str(parent), name)))
        except (TypeError, ValueError, OSError) as exc:
            raise ComposeWouldFail(scope) from exc
        if not _confined_within(candidate, scan_root) or is_blocked_path(candidate):
            raise ComposeWouldFail(scope)
        if not os.path.lexists(candidate):
            if required:
                raise ComposeWouldFail(scope)
            continue
        try:
            text = read_regular_text(
                candidate, max_bytes=MAX_ENV_BYTES, allowed_root=scan_root,
            )
        except FileNotFoundError as exc:
            if required:
                raise ComposeWouldFail(scope) from exc
            continue
        except (OSError, ValueError) as exc:
            raise ComposeWouldFail(scope) from exc
        merged.update(_parse_env_text(text))
    return merged


def _load_env_file(
    directory: Path,
    owner_file: str = "",
    *,
    scan_root: str | None = None,
) -> dict[str, str]:
    return _read_env_file(directory / ".env", owner_file=owner_file, scan_root=scan_root)


def _read_env_file(
    path: Path,
    owner_file: str = "",
    *,
    scan_root: str | None = None,
) -> dict[str, str]:
    if scan_root is not None and os.path.lexists(path) and not _confined_within(path, scan_root):
        raise ComposeWouldFail(owner_file or "env_file")
    try:
        text = read_regular_text(path, max_bytes=MAX_ENV_BYTES, allowed_root=scan_root)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ComposeWouldFail(owner_file or "env_file") from exc
    return _parse_env_text(text)


def _parse_env_text(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
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


def _extends_ref(
    ext,
    filepath: str,
    work_dir: Path | None = None,
    *,
    scan_root: str | None = None,
) -> tuple[str, str] | None:
    ext = _unwrap_compose(ext)
    if isinstance(ext, str) and ext.strip():
        return os.path.abspath(filepath), ext.strip()
    if not isinstance(ext, dict):
        return None
    svc = ext.get("service")
    if not isinstance(svc, str) or not svc.strip():
        return None
    svc = svc.strip()
    f = ext.get("file")
    if not f:
        return os.path.abspath(filepath), svc
    if not isinstance(f, str):
        return None
    base = work_dir if work_dir is not None else Path(filepath).parent
    try:
        cand = Path(os.path.normpath(os.path.join(str(base), f)))
    except (TypeError, ValueError, OSError):
        raise ComposeWouldFail(str(filepath)) from None
    if not _confined_within(cand, scan_root) or is_blocked_path(cand) or not cand.is_file():
        raise ComposeWouldFail(str(filepath))
    return str(cand), svc


def _services_from_file(
    filepath: str,
    env_vars: dict[str, str],
    cache: dict | None = None,
    *,
    scan_root: str | None = None,
) -> tuple[dict, dict[str, str]]:
    loaded = _compose_doc(
        filepath,
        extra_env=env_vars,
        env_base=None,
        apply_override=False,
        cache=cache,
        scan_root=scan_root,
    )
    if loaded is None:
        raise ComposeWouldFail(str(filepath))
    data, merged_env, _parent = loaded
    return _service_map(data.get("services")), merged_env


def _resolve_extends(
    svc_cfg: dict,
    filepath: str,
    env_vars: dict[str, str],
    chain: frozenset[tuple[str, str]],
    local_services: dict,
    work_dir: Path | None = None,
    cache: dict | None = None,
    *,
    scan_root: str | None = None,
) -> dict:
    ref = _extends_ref(svc_cfg.get("extends"), filepath, work_dir, scan_root=scan_root)
    if ref is None:
        return svc_cfg
    ref_file, ref_svc = ref
    cycle_key = (os.path.realpath(ref_file), ref_svc)
    if cycle_key in chain:
        return svc_cfg
    if os.path.abspath(ref_file) == os.path.abspath(filepath):
        other_local = local_services
        other_path = filepath
        other = other_local.get(ref_svc) if isinstance(other_local, dict) else None
        nested_dir = work_dir
        next_env = env_vars
    else:
        other_path = ref_file
        other_local, other_env = _services_from_file(
            ref_file, env_vars, cache=cache, scan_root=scan_root,
        )
        next_env = {**other_env, **env_vars}
        other = other_local.get(ref_svc)
        nested_dir = Path(ref_file).parent
    if not isinstance(other, dict):
        raise ComposeWouldFail(str(filepath))
    _require_env_files(
        nested_dir if nested_dir is not None else Path(other_path).parent,
        other.get("env_file"),
        owner_file=filepath,
        scan_root=scan_root,
    )
    base = _resolve_extends(
        other, other_path, next_env, chain | {cycle_key}, other_local, nested_dir,
        cache=cache, scan_root=scan_root,
    )
    return _overlay_port_fields(base, svc_cfg)


def _brace_pairs(text: str) -> dict[int, int]:
    """Map each opening '{' index to its matching '}' index in O(len(text))."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for idx, ch in enumerate(text):
        if ch == "{":
            stack.append(idx)
        elif ch == "}" and stack:
            pairs[stack.pop()] = idx
    return pairs


def _substitute_vars_inner(
    text: str,
    env_vars: dict[str, str],
    *,
    required: bool,
    budget: list[int],
    depth: int,
) -> str:
    if depth > _MAX_INTERP_DEPTH:
        raise ComposeWouldFail("variable expansion limit")

    def _charge(size: int) -> None:
        budget[0] -= size
        if budget[0] < 0:
            raise ComposeWouldFail("variable expansion limit")

    def _expand_name(name: str) -> str:
        m = _VAR_NAME_RE.match(name)
        if m:
            var = m.group(0)
            rest = name[m.end():]
            if not rest:
                if var in env_vars:
                    val = env_vars[var]
                    _charge(len(val))
                    return val
                literal = "${" + name + "}"
                _charge(len(literal))
                return literal
            if rest.startswith(":?"):
                val = env_vars.get(var)
                if val is None or val == "":
                    if required:
                        raise ComposeWouldFail("${" + var + ":?}")
                    return ""
                _charge(len(val))
                return val
            if rest.startswith(":+"):
                val = env_vars.get(var)
                if val is None or val == "":
                    return ""
                return _substitute_vars_inner(
                    rest[2:], env_vars, required=required, budget=budget, depth=depth + 1,
                )
            if rest.startswith(":-"):
                val = env_vars.get(var)
                if val is None or val == "":
                    return _substitute_vars_inner(
                        rest[2:], env_vars, required=required, budget=budget, depth=depth + 1,
                    )
                _charge(len(val))
                return val
            if rest.startswith("?"):
                if var not in env_vars:
                    if required:
                        raise ComposeWouldFail("${" + var + "?}")
                    return ""
                val = env_vars[var]
                _charge(len(val))
                return val
            if rest.startswith("+"):
                if var not in env_vars:
                    return ""
                return _substitute_vars_inner(
                    rest[1:], env_vars, required=required, budget=budget, depth=depth + 1,
                )
            if rest.startswith("-"):
                if var in env_vars:
                    val = env_vars[var]
                    _charge(len(val))
                    return val
                return _substitute_vars_inner(
                    rest[1:], env_vars, required=required, budget=budget, depth=depth + 1,
                )
        literal = "${" + name + "}"
        _charge(len(literal))
        return literal

    out: list[str] = []
    pairs: dict[int, int] | None = None
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "$":
            nxt_dollar = text.find("$", i)
            if nxt_dollar == -1:
                chunk = text[i:]
                _charge(len(chunk))
                out.append(chunk)
                break
            chunk = text[i:nxt_dollar]
            _charge(len(chunk))
            out.append(chunk)
            i = nxt_dollar
            continue
        if i + 1 >= n:
            _charge(1)
            out.append("$")
            break
        nxt = text[i + 1]
        if nxt == "$":
            _charge(1)
            out.append("$")
            i += 2
            continue
        if nxt == "{":
            if pairs is None:
                pairs = _brace_pairs(text)
            close = pairs.get(i + 1)
            if close is None:
                _charge(1)
                out.append("$")
                i += 1
                continue
            inner = text[i + 2:close]
            out.append(_expand_name(inner))
            i = close + 1
            continue
        m = _VAR_NAME_RE.match(text, i + 1)
        if m:
            name = m.group(0)
            piece = env_vars[name] if name in env_vars else "$" + name
            _charge(len(piece))
            out.append(piece)
            i = m.end()
            continue
        _charge(1)
        out.append("$")
        i += 1
    return "".join(out)


def substitute_vars(
    text: str,
    env_vars: dict[str, str],
    *,
    required: bool = True,
    max_output: int = _MAX_EXPANDED_CHARS,
) -> str:
    """Interpolate Compose ``$VAR`` / ``${VAR}`` from the *project* env only.

    Port-Light's process environment is not the user's compose shell; mixing it
    in lets ``HOSTNAME`` / ``PORT_RANGE_*`` rewrite other stacks' port lines.
    ``required=False`` is the first YAML pass so ``env_file`` can still load
    ``${VAR:?}`` values; the second pass is strict (Compose would abort).
    """
    return _substitute_vars_inner(
        text, env_vars, required=required, budget=[max_output], depth=0,
    )
