"""Private local storage for one-time reservation tokens."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .client import PortLightError, normalize_base_url


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
                f"could not write reservation tokens in {self.root}",
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
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
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
                f"could not save reservation token in {self.root}",
            ) from exc
        finally:
            if handle is not None:
                handle.close()
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

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
