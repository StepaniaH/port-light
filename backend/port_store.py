"""Port store — persistent user data: manual ports, hidden ports, peers.

All data is stored in a single JSON file under the data directory
(typically a Docker volume). No personal info is baked into the code;
everything is user-created at runtime.

File format::

    {
      "manual_ports": [
        {"port": 1234, "label": "My Service", "machine": "localhost"}
      ],
      "hidden_ports": [1234, 5678],
      "peers": [
        {"id": "a1b2c3d4", "name": "NAS", "url": "http://10.0.0.2:2100",
         "username": "", "password": ""}
      ]
    }

The ``machines`` array may still exist in older files. It is not scanned.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from . import degradations


class StoreWriteError(Exception):
    """Raised when the data file cannot be written."""


_LOCK = threading.Lock()
_generation = 0


def store_generation() -> int:
    """Bumps on every successful write. Occupancy uses this to drop a stale scan snapshot."""
    return _generation


def _data_dir() -> Path:
    return Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data"))


def _data_file() -> Path:
    return _data_dir() / "port_light.json"


def _load() -> dict:
    """Load the full data structure from disk."""
    f = _data_file()
    if not f.exists():
        return {"manual_ports": [], "hidden_ports": [], "machines": [], "peers": []}
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError:
        corrupt = f.parent / (f.name + ".corrupt")
        try:
            os.replace(f, corrupt)
        except OSError:
            pass
        degradations.report("store", f.name, "corrupt file quarantined")
        return {"manual_ports": [], "hidden_ports": [], "machines": [], "peers": []}
    except OSError:
        return {"manual_ports": [], "hidden_ports": [], "machines": [], "peers": []}


def _save(data: dict) -> None:
    d = _data_dir()
    target = _data_file()
    tmp_name = None
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".port_light.", suffix=".tmp", dir=str(d))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
        tmp_name = None
        global _generation
        _generation += 1
    except OSError as exc:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise StoreWriteError(
                f"cannot write {target} (permission denied). "
                "The data directory must be writable by this process."
            ) from exc
        raise
    except Exception:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


# ── Manual ports ──────────────────────────────────────────────

def _entry_port(entry) -> int | None:
    if not isinstance(entry, dict):
        return None
    try:
        port = int(entry.get("port"))
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port


def _entry_machine(entry) -> str:
    if not isinstance(entry, dict):
        return "localhost"
    return str(entry.get("machine") or "localhost")


def _hidden_port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port


def _manuals_from(data: dict) -> list[dict]:
    out: list[dict] = []
    for entry in data.get("manual_ports") or []:
        port = _entry_port(entry)
        if port is None:
            continue
        item = {
            "port": port,
            "label": str(entry.get("label") or ""),
            "machine": _entry_machine(entry),
        }
        exp = entry.get("expires_at")
        if isinstance(exp, (int, float)):
            item["expires_at"] = int(exp)
        out.append(item)
    return out


def _hidden_from(data: dict) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for raw in data.get("hidden_ports") or []:
        port = _hidden_port(raw)
        if port is None or port in seen:
            continue
        seen.add(port)
        out.append(port)
    return out


def _now() -> int:
    return int(time.time())


def _drop_expired_locked(data: dict) -> bool:
    """Remove expired leases from data.manual_ports. Returns True if changed."""
    now = _now()
    mp = data.get("manual_ports", [])
    kept = []
    changed = False
    for entry in mp:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        exp = entry.get("expires_at")
        if isinstance(exp, (int, float)) and exp <= now:
            changed = True
            continue
        kept.append(entry)
    if changed:
        data["manual_ports"] = kept
        _save(data)
    return changed


def get_manual_ports() -> list[dict]:
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
    return _manuals_from(data)


def occupancy_user_state() -> tuple[list[dict], list[int]]:
    """One locked snapshot so occupancy does not tear across two reads."""
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
    return _manuals_from(data), _hidden_from(data)


def add_manual_port(
    port: int,
    label: str = "",
    machine: str = "localhost",
    ttl: int | None = None,
) -> dict:
    with _LOCK:
        data = _load()
        mp = data.setdefault("manual_ports", [])
        kept: list[dict] = []
        for entry in mp:
            if _entry_port(entry) is None:
                continue
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                continue
            kept.append(entry)
        entry = {"port": port, "label": label, "machine": machine}
        if ttl is not None:
            entry["expires_at"] = _now() + max(60, min(int(ttl), 604800))
        kept.append(entry)
        data["manual_ports"] = kept
        _save(data)
        return entry


def remove_manual_port(port: int, machine: str = "localhost") -> bool:
    with _LOCK:
        data = _load()
        mp = data.get("manual_ports", [])
        kept: list[dict] = []
        removed = False
        for entry in mp:
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                removed = True
                continue
            if _entry_port(entry) is not None:
                kept.append(entry)
        if removed:
            data["manual_ports"] = kept
            _save(data)
            return True
        return False


# ── Hidden ports ──────────────────────────────────────────────

def get_hidden_ports() -> list[int]:
    return _hidden_from(_load())


def add_hidden_port(port: int) -> bool:
    n = _hidden_port(port)
    if n is None:
        return False
    with _LOCK:
        data = _load()
        hp = [_hidden_port(p) for p in data.get("hidden_ports") or []]
        hp = [p for p in hp if p is not None]
        if n in hp:
            if data.get("hidden_ports") != hp:
                data["hidden_ports"] = hp
                _save(data)
            return False
        hp.append(n)
        data["hidden_ports"] = hp
        _save(data)
        return True


def remove_hidden_port(port: int) -> bool:
    with _LOCK:
        data = _load()
        hp = [_hidden_port(p) for p in data.get("hidden_ports") or []]
        hp = [p for p in hp if p is not None]
        if port not in hp:
            return False
        hp.remove(port)
        data["hidden_ports"] = hp
        _save(data)
        return True


def update_manual_port(port: int, label: str, machine: str = "localhost") -> dict | None:
    with _LOCK:
        data = _load()
        mp = data.get("manual_ports", [])
        kept: list = []
        found = None
        for entry in mp:
            if _entry_port(entry) is None:
                continue
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                entry = dict(entry) if isinstance(entry, dict) else {"port": port, "machine": machine}
                entry["label"] = label
                found = entry
            kept.append(entry)
        if found:
            data["manual_ports"] = kept
            _save(data)
            return found
        return None


def get_stored_settings() -> dict:
    data = _load()
    raw = data.get("settings")
    return dict(raw) if isinstance(raw, dict) else {}


def update_stored_settings(patch: dict) -> dict:
    """Merge *patch* into stored settings. ``None`` values delete a key."""
    with _LOCK:
        data = _load()
        current = dict(data.get("settings") or {})
        for key, value in patch.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        data["settings"] = current
        _save(data)
        return current


# ── Machines ──────────────────────────────────────────────────

def get_machines() -> list[dict]:
    data = _load()
    machines = [m for m in data.get("machines", []) if isinstance(m, dict) and m.get("name")]
    if not any(m.get("name") == "localhost" for m in machines):
        machines.insert(0, {"name": "localhost", "host": "127.0.0.1", "note": "This machine"})
    return machines


def add_machine(name: str, host: str, note: str = "") -> dict:
    with _LOCK:
        data = _load()
        machines = data.setdefault("machines", [])
        machines[:] = [m for m in machines if isinstance(m, dict) and m.get("name") != name]
        entry = {"name": name, "host": host, "note": note}
        machines.append(entry)
        _save(data)
        return entry


def remove_machine(name: str) -> bool:
    with _LOCK:
        data = _load()
        machines = data.get("machines", [])
        before = len(machines)
        machines[:] = [m for m in machines if isinstance(m, dict) and m.get("name") != name]
        if len(machines) < before:
            _save(data)
            return True
        return False


def peers_stored() -> bool:
    """True when the file has a ``peers`` key, including an empty list."""
    return "peers" in _load()


def get_peers() -> list[dict]:
    data = _load()
    raw = data.get("peers")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        host_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not host_id or not name or not url:
            continue
        out.append({
            "id": host_id,
            "name": name,
            "url": url,
            "username": str(item.get("username") or ""),
            "password": str(item.get("password") or ""),
        })
    return out


def replace_peers(peers: list[dict]) -> list[dict]:
    with _LOCK:
        data = _load()
        data["peers"] = list(peers)
        _save(data)
        return list(peers)
