"""Private local storage for reservation tokens and durable recovery requests."""

from __future__ import annotations

from contextlib import contextmanager
import secrets
import re

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .client import PortLightError, normalize_base_url, validate_request_key


def default_state_dir() -> Path:
    override = os.environ.get("PORT_LIGHT_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Port-Light" / "state"
    root = os.environ.get("XDG_STATE_HOME", "").strip()
    return (Path(root).expanduser() if root else Path.home() / ".local" / "state") / "port-light"


class ReservationStore:
    """Store each server/port token in its own atomically replaced file."""

    def __init__(self, root: Path | None = None):
        self.root = root or default_state_dir()

    def ensure_writable(self) -> None:
        directory = self.root / "reservations"
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
            with tempfile.NamedTemporaryFile(dir=directory):
                pass
        except OSError as exc:
            raise PortLightError(
                "state_write_failed",
                "could not write reservation-token state",
            ) from exc

    def save(self, base_url: str, reservation: dict[str, Any]) -> None:
        port = int(reservation.get("port") or 0)
        token = str(reservation.get("token") or "")
        if not 1 <= port <= 65535 or not token:
            raise PortLightError(
                "invalid_response",
                "Port-Light returned an invalid reservation",
            )
        target = self._path(base_url, port)
        payload = {
            "schema_version": 1,
            "url": normalize_base_url(base_url),
            "port": port,
            "token": token,
            "expires_at": reservation.get("expires_at"),
        }
        handle = None
        temp_path = None
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                os.chmod(target.parent, 0o700)
            except OSError:
                pass
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{port}.",
                delete=False,
            )
            temp_path = Path(handle.name)
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            os.replace(temp_path, target)
        except OSError as exc:
            raise PortLightError(
                "state_write_failed",
                "could not save reservation-token state",
            ) from exc
        finally:
            if handle is not None:
                handle.close()
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _request_id(base_url: str, parameters: dict) -> str:
        identity = json.dumps([normalize_base_url(base_url), parameters], sort_keys=True)
        return hashlib.sha256(identity.encode()).hexdigest()

    @contextmanager
    def _request_lock(self, target: Path):
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(target.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "r+b") as lock:
            try:
                if os.name == "nt":
                    import msvcrt
                    lock.write(b"0")
                    lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise PortLightError("reservation_in_progress", "an identical reservation is in progress") from exc
            yield

    @staticmethod
    def _write_request(target: Path, payload: dict) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as pending:
                temporary = Path(pending.name)
                json.dump(payload, pending)
                pending.flush()
                os.fsync(pending.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise PortLightError("state_write_failed", "could not persist pending request; no new request should be sent") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_request(target: Path) -> dict:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            validate_request_key(payload["key"])
            if type(payload["created_at"]) is not int or payload["created_at"] < 0:
                raise ValueError("invalid creation time")
            return payload
        except FileNotFoundError:
            raise
        except (OSError, ValueError, KeyError, TypeError, PortLightError) as exc:
            raise PortLightError("state_read_failed", "invalid pending reservation state; preserve it for repair") from exc

    @contextmanager
    def pending_request(self, base_url: str, parameters: dict):
        """Persist a recovery key before sending, and serialize identical requests."""
        base_url = normalize_base_url(base_url)
        digest = self._request_id(base_url, parameters)
        target = self.root / "requests" / f"{digest}.json"
        with self._request_lock(target):
            try:
                payload = self._read_request(target)
            except FileNotFoundError:
                payload = {"key": secrets.token_urlsafe(32), "created_at": int(time.time())}
            # Attach metadata to legacy journals without replacing their secret key.
            if "url" not in payload:
                payload.update(url=base_url, parameters=parameters)
                self._write_request(target, payload)
            if payload.get("url") != base_url or payload.get("parameters") != parameters:
                raise PortLightError("state_read_failed", "pending request does not match this command")
            if time.time() - payload["created_at"] >= 7 * 86400:
                raise PortLightError("state_read_failed", f"pending request is too old to resubmit; run port-light recover {digest} with the same --url")
            try:
                yield payload["key"]
            except PortLightError as exc:
                if exc.code == "no_capacity":
                    target.unlink(missing_ok=True)
                raise
            else:
                target.unlink(missing_ok=True)

    def pending_requests(self, base_url: str) -> list[dict]:
        """List non-secret metadata for this server; never expose recovery keys."""
        rows = []
        base_url = normalize_base_url(base_url)
        for target in sorted((self.root / "requests").glob("*.json")):
            try:
                payload = self._read_request(target)
            except FileNotFoundError:
                continue  # Another process completed it during the listing.
            if "url" not in payload:
                raise PortLightError("state_read_failed", "legacy pending request: retry the original command once to attach recovery metadata")
            if payload["url"] != base_url:
                continue
            parameters = payload.get("parameters")
            if not isinstance(parameters, dict) or target.stem != self._request_id(base_url, parameters):
                raise PortLightError("state_read_failed", "pending request metadata does not match its ID")
            rows.append({"id": target.stem, "created_at": payload["created_at"],
                         "parameters": parameters})
        return rows

    @contextmanager
    def recovery_request(self, base_url: str, request_id: str):
        if not re.fullmatch(r"[a-f0-9]{64}", request_id):
            raise PortLightError("invalid_request", "request ID must come from port-light requests")
        target = self.root / "requests" / f"{request_id}.json"
        with self._request_lock(target):
            try:
                payload = self._read_request(target)
            except FileNotFoundError as exc:
                raise PortLightError("not_found", "pending request not found; run port-light requests") from exc
            parameters = payload.get("parameters")
            if (payload.get("url") != normalize_base_url(base_url)
                    or not isinstance(parameters, dict)
                    or self._request_id(base_url, parameters) != request_id):
                raise PortLightError("state_read_failed", "pending request does not match this server")
            yield payload["key"], parameters
            target.unlink(missing_ok=True)

    def load(self, base_url: str, port: int) -> str | None:
        target = self._path(base_url, port)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise PortLightError(
                "state_read_failed",
                f"could not read the saved reservation token for port {port}",
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise PortLightError(
                "state_read_failed",
                f"saved reservation token for port {port} has an unsupported format",
            )
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at <= time.time():
            self.delete(base_url, port)
            return None
        if payload.get("url") != normalize_base_url(base_url) or payload.get("port") != port:
            raise PortLightError(
                "state_read_failed",
                f"saved reservation token for port {port} does not match this server",
            )
        token = payload.get("token")
        return token if isinstance(token, str) and token else None

    def delete(self, base_url: str, port: int) -> None:
        try:
            self._path(base_url, port).unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PortLightError(
                "state_write_failed",
                f"could not remove the saved reservation token for port {port}",
            ) from exc

    def _path(self, base_url: str, port: int) -> Path:
        server = hashlib.sha256(normalize_base_url(base_url).encode()).hexdigest()[:24]
        return self.root / "reservations" / server / f"{port}.json"
