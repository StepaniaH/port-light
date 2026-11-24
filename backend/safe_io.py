"""Bounded, non-blocking regular-file reads outside sensitive host trees."""

from __future__ import annotations

import errno
import os
import stat

MAX_COMPOSE_BYTES = 2 * 1024 * 1024
MAX_ENV_BYTES = 512 * 1024
MAX_CUSTOM_PORTS_BYTES = 512 * 1024

_SENSITIVE_TREES = ("/proc", "/sys", "/dev")
_DATA_PRIVATE_DIRS = ("analysis", "cli-state", "themes")
_DATA_PRIVATE_PREFIXES = ("port_light.json", "history.db", "custom_ports.json")
_READ_CHUNK = 65536


def _within_tree(real_path: str, tree_root: str) -> bool:
    norm = os.path.normpath(tree_root)
    if norm == os.sep:
        return False
    return real_path == norm or real_path.startswith(norm + os.sep)


def _is_private_data_entry(real_path: str, data_real: str) -> bool:
    for subdir in _DATA_PRIVATE_DIRS:
        if _within_tree(real_path, os.path.join(data_real, subdir)):
            return True
    if os.path.dirname(real_path) == data_real:
        name = os.path.basename(real_path)
        if name.startswith(_DATA_PRIVATE_PREFIXES) or name.endswith(".tmp"):
            return True
    return False


def is_blocked_path(
    path: str | os.PathLike[str],
    *,
    allow_data_dir: bool = False,
) -> bool:
    real = os.path.realpath(path)
    for tree in _SENSITIVE_TREES:
        if _within_tree(real, tree) or _within_tree(real, os.path.realpath(tree)):
            return True
    if not allow_data_dir:
        raw_data = os.environ.get("PORT_LIGHT_DATA_DIR", "/data").strip() or "/data"
        data_real = os.path.realpath(raw_data)
        if _within_tree(real, data_real):
            if _is_private_data_entry(real, data_real):
                return True
            raw_compose = os.environ.get("COMPOSE_SCAN_DIR", "").strip()
            if raw_compose:
                compose_real = os.path.realpath(raw_compose)
                if (
                    compose_real != data_real
                    and _within_tree(compose_real, data_real)
                    and _within_tree(real, compose_real)
                ):
                    return False
            return True
    return False


def read_regular_bytes(
    path: str | os.PathLike[str],
    *,
    max_bytes: int,
    allow_data_dir: bool = False,
) -> bytes:
    target = os.fspath(path)
    if is_blocked_path(target, allow_data_dir=allow_data_dir):
        raise PermissionError(errno.EACCES, "blocked path", target)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(target, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", target)
        if st.st_size > max_bytes:
            raise ValueError(f"file exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        limit = max_bytes + 1
        while total < limit:
            chunk = os.read(fd, min(_READ_CHUNK, limit - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"file exceeds {max_bytes} bytes")
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_regular_text(
    path: str | os.PathLike[str],
    *,
    max_bytes: int,
    allow_data_dir: bool = False,
    errors: str = "replace",
) -> str:
    return read_regular_bytes(
        path, max_bytes=max_bytes, allow_data_dir=allow_data_dir,
    ).decode("utf-8", errors=errors)
