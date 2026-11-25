"""Port store — persistent user data: manual ports, hidden ports, peers.

Data is stored in a single JSON file under the configured data directory
(typically a Docker volume).

File format::

    {
      "manual_ports": [
        {"port": 1234, "label": "My Service", "machine": "localhost"}
      ],
      "hidden_ports": [1234, 5678],
      "peers": [
        {"id": "a1b2c3d4", "name": "NAS", "url": "http://<peer-lan-ip>:2100",
         "username": "", "password": ""}
      ]
    }

The ``machines`` array may still exist in older files. It is not scanned.
"""

from __future__ import annotations

import errno
import copy
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import degradations

MAX_PEER_DESCRIPTION = 120
MAX_MANUAL_PORTS = 4096
MAX_MANUAL_LABEL = 256
MAX_MANUAL_MACHINE = 64
REQUEST_RETENTION_S = 7 * 86400
MAX_RESERVATION_REQUESTS = 4096


class UserState(tuple):
    """Atomic (manuals, hidden) pair carrying port_rules from the same read."""

    rules: list[dict]

    def __new__(
        cls,
        pair: tuple[list[dict], list[int]],
        rules: list[dict] | None = None,
    ) -> UserState:
        instance = super().__new__(cls, pair)
        instance.rules = list(rules) if rules is not None else []
        return instance


class StoreReadError(Exception):
    """The existing data file could not be read safely."""


class StoreWriteError(Exception):
    """Raised when the data file cannot be written."""


class ReservationConflict(Exception):
    """A manual write or release does not own the current reservation."""


_LOCK = threading.Lock()
_generation = 0


def store_generation() -> int:
    """Bumps on every successful write so the monitor can detect stored-state changes."""
    return _generation


def store_revision() -> tuple:
    try:
        st = _data_file().stat()
        return (_generation, st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
    except FileNotFoundError:
        return (_generation, None, None)
    except OSError as exc:
        raise _read_error() from exc


def _data_dir() -> Path:
    return Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data"))


def _data_file() -> Path:
    return _data_dir() / "port_light.json"


_FILE_MEMO: dict[str, tuple[tuple, dict]] = {}


def _read_error() -> StoreReadError:
    degradations.report("store", "port_light.json", "data file unreadable or invalid")
    return StoreReadError("data file unreadable or invalid; repair it before retrying")


def _load() -> dict:
    """Load user data; only a missing file represents an empty store."""
    empty = {"manual_ports": [], "hidden_ports": []}
    f = _data_file()
    try:
        st = f.stat()
    except FileNotFoundError:
        _FILE_MEMO.pop(str(f), None)
        return empty
    except OSError as exc:
        raise _read_error() from exc
    token = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, _generation)
    memo = _FILE_MEMO.get(str(f))
    if memo is not None and memo[0] == token:
        return copy.deepcopy(memo[1])
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise _read_error() from exc
    if not isinstance(data, dict) or any(
        key in data and not isinstance(data[key], expected)
        for key, expected in (("manual_ports", list), ("hidden_ports", list),
                              ("peers", list), ("machines", list), ("settings", dict), ("port_rules", list),
                              ("reservation_requests", dict))
    ):
        raise _read_error()
    if any(_entry_port(entry) is None for entry in data.get("manual_ports", [])):
        raise _read_error()
    if any(_hidden_port(port) is None for port in data.get("hidden_ports", [])):
        raise _read_error()
    if any(not isinstance(peer, dict) or any(
        not isinstance(peer.get(key), str) or not peer[key].strip()
        for key in ("id", "name", "url")) for peer in data.get("peers", [])):
        raise _read_error()
    if any(len(str(peer.get("description") or "").strip()) > MAX_PEER_DESCRIPTION
           for peer in data.get("peers", [])):
        raise _read_error()
    if any(not _valid_request_record(key, value)
           for key, value in data.get("reservation_requests", {}).items()):
        raise _read_error()
    if "port_rules" in data:
        from .port_rules import load_stored_rules
        try:
            data["port_rules"] = load_stored_rules(data["port_rules"])
        except ValueError as exc:
            degradations.report("store", "port_light.json", "invalid port rules in data file")
            raise StoreReadError("invalid port rules in data file") from exc
    _FILE_MEMO[str(f)] = (token, data)
    return copy.deepcopy(data)


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
        _FILE_MEMO.pop(str(target), None)
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
        raise StoreWriteError(f"cannot write {target}: {exc.strerror}") from exc
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
    raw = entry.get("port")
    if isinstance(raw, bool):
        return None
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port


def _normalize_machine(machine: str | None) -> str:
    return str(machine or "").strip() or "localhost"


def _entry_machine(entry) -> str:
    if not isinstance(entry, dict):
        return "localhost"
    return _normalize_machine(entry.get("machine"))


def _hidden_port(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port


def _validate_manual_fields(label: str, machine: str = "localhost") -> None:
    if len(label) > MAX_MANUAL_LABEL:
        raise ValueError(f"label must be at most {MAX_MANUAL_LABEL} characters")
    if len(machine) > MAX_MANUAL_MACHINE:
        raise ValueError(f"machine must be at most {MAX_MANUAL_MACHINE} characters")


def _manuals_from(data: dict) -> list[dict]:
    out: list[dict] = []
    for entry in data.get("manual_ports") or []:
        port = _entry_port(entry)
        if port is None:
            continue
        item = {
            "port": port,
            "label": str(entry.get("label") or "")[:MAX_MANUAL_LABEL],
            "machine": _entry_machine(entry)[:MAX_MANUAL_MACHINE],
        }
        exp = entry.get("expires_at")
        if isinstance(exp, (int, float)):
            item["expires_at"] = int(exp)
        if entry.get("reservation_hash"):
            item["is_reservation"] = True
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


def _drop_expired_locked(data: dict, *, allow_readonly: bool = False) -> bool:
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
        try:
            _save(data)
        except StoreWriteError:
            if not allow_readonly:
                raise
    return changed


def get_manual_ports() -> list[dict]:
    with _LOCK:
        data = _load()
        _drop_expired_locked(data, allow_readonly=True)
        return _manuals_from(data)


def occupancy_user_state() -> UserState:
    """One locked snapshot so occupancy does not tear across reads."""
    with _LOCK:
        data = _load()
        _drop_expired_locked(data, allow_readonly=True)
        return UserState(
            (_manuals_from(data), _hidden_from(data)),
            copy.deepcopy(data.get("port_rules", [])),
        )


def allocate_ports(taken: set[int], start: int, end: int, count: int,
                   label: str, ttl: int | None, reserve: bool,
                   require_count: bool = False) -> tuple[list[int], list[dict]]:
    """Select and persist a batch under the same lock as all manual writes.

    Scanner/peer observations are advisory; this transaction serializes claims
    in one Port-Light process, not operating-system socket binds.
    """
    _validate_manual_fields(label)
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
        occupied = _occupied(data, taken)
        picks = []
        for port in range(start, end + 1):
            if port not in occupied:
                picks.append(port)
                if len(picks) == count:
                    break
        if require_count and len(picks) != count:
            return [], []
        reservations = []
        if reserve and picks:
            entries = data.setdefault("manual_ports", [])
            if len(entries) + len(picks) > MAX_MANUAL_PORTS:
                raise ValueError(f"at most {MAX_MANUAL_PORTS} manual ports are allowed")
            expires_at = _now() + ttl if ttl is not None else None
            for port in picks:
                token = secrets.token_urlsafe(32)
                entry = {"port": port, "label": label, "machine": "localhost",
                         "reservation_hash": hashlib.sha256(token.encode()).hexdigest()}
                if expires_at is not None:
                    entry["expires_at"] = expires_at
                entries.append(entry)
                reservations.append({"port": port, "token": token, "expires_at": expires_at})
            _save(data)
        return picks, reservations


def _valid_request_record(key: str, record) -> bool:
    if not isinstance(record, dict) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        return False
    result = record.get("result")
    parameters = record.get("parameters")
    if not isinstance(result, dict) or not isinstance(parameters, dict):
        return False
    ports, bounds = result.get("ports"), result.get("range")
    if not isinstance(ports, list) or not isinstance(bounds, dict):
        return False
    return (
        type(record.get("created_at")) is int
        and type(parameters.get("count")) is int and 1 <= parameters["count"] <= 64
        and len(ports) <= parameters["count"]
        and all(type(port) is int and 1 <= port <= 65535 for port in ports)
        and len(set(ports)) == len(ports)
        and result.get("reserved") == ports and result.get("failed") == []
        and type(bounds.get("start")) is int and type(bounds.get("end")) is int
        and 1 <= bounds["start"] <= bounds["end"] <= 65535
        and all(bounds["start"] <= port <= bounds["end"] for port in ports)
        and isinstance(result.get("scope"), str)
        and "expires_at" in result
        and (result["expires_at"] is None or type(result["expires_at"]) is int)
    )


def _request_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _request_token(key: str, port: int) -> str:
    # The client retains the secret key. Neither it nor release tokens are
    # stored in plaintext on the server; retries can reconstruct identical tokens.
    return hmac.new(key.encode(), f"port-light-reservation-v1:{port}".encode(), "sha256").hexdigest()


def _request_result(data: dict, key: str, parameters: dict | None = None, *, allow_partial: bool = False) -> dict | None:
    record = data.get("reservation_requests", {}).get(_request_hash(key))
    if record is None:
        return None
    if parameters is not None and record["parameters"] != parameters:
        raise ReservationConflict("idempotency key was already used with different parameters")
    result = copy.deepcopy(record["result"])
    current = {entry["port"]: entry for entry in _manuals_from(data)
               if _entry_machine(entry) == "localhost"}
    hashes = {int(entry["port"]): entry.get("reservation_hash")
              for entry in data.get("manual_ports", []) if _entry_machine(entry) == "localhost"}
    reservations = []
    inactive = []
    for port in result["ports"]:
        token = _request_token(key, port)
        entry = current.get(port, {})
        if (hashes.get(port) != hashlib.sha256(token.encode()).hexdigest()
                or (entry.get("expires_at") is not None and entry["expires_at"] <= _now())):
            if not allow_partial:
                raise ReservationConflict("reservation request is no longer fully active; use port-light recover to retrieve any remaining claims")
            inactive.append(port)
            continue
        reservations.append({"port": port, "token": token, "expires_at": result["expires_at"]})
    if inactive:
        if not reservations:
            raise ReservationConflict("reservation request has no active ports; no new ports were allocated")
        result["ports"] = result["reserved"] = [entry["port"] for entry in reservations]
        result["inactive_ports"] = inactive
    result["reservations"] = reservations
    return result


def reservation_request(key: str, parameters: dict | None = None) -> dict | None:
    with _LOCK:
        return _request_result(_load(), key, parameters, allow_partial=parameters is None)


def allocate_reservation(taken: set[int], start: int, end: int, parameters: dict,
                         key: str, scope: str) -> tuple[dict, bool]:
    """Commit the receipt and the entire claim in the same atomic replacement."""
    _validate_manual_fields(str(parameters.get("label") or ""))
    with _LOCK:
        data = _load()
        existing = _request_result(data, key, parameters)
        if existing is not None:
            return existing, False
        _drop_expired_locked(data)
        if parameters.get("rule"):
            from .port_rules import allocation_range
            try:
                current = allocation_range(data.get("port_rules", []), parameters["rule"],
                                           parameters["start"], parameters["end"])
            except ValueError as exc:
                raise ReservationConflict(str(exc)) from exc
            if current != (start, end):
                raise ReservationConflict("port rule changed during allocation; retry the request")
        occupied = _occupied(data, taken)
        picks = [port for port in range(start, end + 1) if port not in occupied][:parameters["count"]]
        if parameters["require_count"] and len(picks) != parameters["count"]:
            picks = []
        if not picks:
            return {
                "ports": [],
                "reserved": [],
                "failed": [],
                "reservations": [],
                "expires_at": None,
                "scope": scope,
                "range": {"start": start, "end": end},
            }, True
        entries = data.setdefault("manual_ports", [])
        if len(entries) + len(picks) > MAX_MANUAL_PORTS:
            raise ValueError(f"at most {MAX_MANUAL_PORTS} manual ports are allowed")
        records = data.setdefault("reservation_requests", {})
        now = _now()
        active = {
            entry.get("request_hash")
            for entry in entries
            if entry.get("request_hash")
        }
        for digest, record in list(records.items()):
            if digest not in active and record["created_at"] < now - REQUEST_RETENTION_S:
                del records[digest]
        if len(records) >= MAX_RESERVATION_REQUESTS:
            inactive_digests = sorted(
                (digest for digest in records if digest not in active),
                key=lambda digest: (records[digest]["created_at"], digest),
            )
            excess = len(records) - MAX_RESERVATION_REQUESTS + 1
            for digest in inactive_digests[:excess]:
                del records[digest]
        if len(records) >= MAX_RESERVATION_REQUESTS:
            raise ReservationConflict("reservation recovery capacity reached; release unused reservations first")
        ttl = parameters["ttl"]
        expires_at = now + ttl if ttl is not None else None
        digest = _request_hash(key)
        for port in picks:
            entry = {"port": port, "label": parameters["label"], "machine": "localhost",
                     "request_hash": digest,
                     "reservation_hash": hashlib.sha256(_request_token(key, port).encode()).hexdigest()}
            if expires_at is not None:
                entry["expires_at"] = expires_at
            entries.append(entry)
        result = {"ports": picks, "reserved": picks, "failed": [], "expires_at": expires_at,
                  "scope": scope, "range": {"start": start, "end": end}}
        records[digest] = {"parameters": parameters, "result": result, "created_at": now}
        _save(data)
        return _request_result(data, key), True


def _occupied(data: dict, taken: set[int]) -> set[int]:
    return taken | set(_hidden_from(data)) | {entry["port"] for entry in _manuals_from(data)}


def reserve_manual_range(taken: set[int], start: int, end: int, label: str) -> list[int]:
    """Claim every selected port in one store transaction, or change nothing."""
    _validate_manual_fields(label)
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
        picks = list(range(start, end + 1))
        if _occupied(data, taken).intersection(picks):
            raise ReservationConflict("selected range is no longer free; find another range")
        entries = data.setdefault("manual_ports", [])
        if len(entries) + len(picks) > MAX_MANUAL_PORTS:
            raise ValueError(f"at most {MAX_MANUAL_PORTS} manual ports are allowed")
        entries.extend(
            {"port": port, "label": label, "machine": "localhost"} for port in picks)
        _save(data)
        return picks


def release_reservation(port: int, token: str) -> bool:
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
        entries = data.get("manual_ports", [])
        for entry in entries:
            if _entry_port(entry) != port or _entry_machine(entry) != "localhost":
                continue
            expected = entry.get("reservation_hash")
            supplied = hashlib.sha256(token.encode()).hexdigest()
            if not expected or not secrets.compare_digest(supplied, expected):
                raise ReservationConflict("reservation token does not match")
            data["manual_ports"] = [item for item in entries if item is not entry]
            _save(data)
            return True
        return False


def _require_manual(entry: dict) -> None:
    if entry.get("reservation_hash"):
        raise ReservationConflict("release this reservation with its token first")


def add_manual_port(
    port: int,
    label: str = "",
    machine: str = "localhost",
    ttl: int | None = None,
) -> dict:
    if type(port) is bool or not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError("port out of range")
    _validate_manual_fields(label, machine)
    machine = _normalize_machine(machine)
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
        mp = data.setdefault("manual_ports", [])
        kept: list[dict] = []
        for entry in mp:
            if _entry_port(entry) is None:
                continue
            if _entry_port(entry) == port:
                _require_manual(entry)
                if _entry_machine(entry) == machine:
                    continue
            kept.append(entry)
        if len(kept) >= MAX_MANUAL_PORTS:
            raise ValueError(f"at most {MAX_MANUAL_PORTS} manual ports are allowed")
        entry = {"port": port, "label": label, "machine": machine}
        if ttl is not None:
            entry["expires_at"] = _now() + max(60, min(int(ttl), 604800))
        kept.append(entry)
        data["manual_ports"] = kept
        _save(data)
        return entry


def remove_manual_port(port: int, machine: str = "localhost") -> bool:
    machine = _normalize_machine(machine)
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
        mp = data.get("manual_ports", [])
        kept: list[dict] = []
        removed = False
        for entry in mp:
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                _require_manual(entry)
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
    with _LOCK:
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
    _validate_manual_fields(label, machine)
    machine = _normalize_machine(machine)
    with _LOCK:
        data = _load()
        _drop_expired_locked(data)
        mp = data.get("manual_ports", [])
        kept: list = []
        found = None
        for entry in mp:
            if _entry_port(entry) is None:
                continue
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                _require_manual(entry)
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
    with _LOCK:
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


# ── Supporter key ─────────────────────────────────────────────

def get_supporter_key_entry() -> tuple[bool, Any]:
    """Return (present, raw_value) for supporter_key without failing core store loads."""
    with _LOCK:
        data = _load()
        if "supporter_key" not in data:
            return False, None
        return True, data.get("supporter_key")


def get_supporter_key() -> str | None:
    """Return the stored raw supporter key if set to a non-empty string, else None."""
    present, raw = get_supporter_key_entry()
    if not present or not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    return trimmed or None


def set_supporter_key(key: str | None) -> None:
    """Persist or remove the stored supporter key in port_light.json."""
    with _LOCK:
        data = _load()
        if key is None or not str(key).strip():
            data.pop("supporter_key", None)
        else:
            data["supporter_key"] = str(key).strip()
        _save(data)


# ── Peers ─────────────────────────────────────────────────────

def peers_stored() -> bool:
    """True when the file has a ``peers`` key, including an empty list."""
    with _LOCK:
        return "peers" in _load()


def get_peers() -> list[dict]:
    with _LOCK:
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
            "description": str(item.get("description") or "").strip(),
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


def get_port_rules() -> list[dict]:
    with _LOCK:
        return copy.deepcopy(_load().get("port_rules", []))


def set_port_rules(rules: list[dict]) -> None:
    with _LOCK:
        data = _load()
        data["port_rules"] = copy.deepcopy(rules)
        _save(data)
