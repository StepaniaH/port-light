"""One local Hub BYOK profile, with a deliberately narrow file format."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import AnalysisError

MAX_PROFILE_BYTES = 4096
MAX_KEY_BYTES = 2048
PROFILE_DIRECTORY = "byok"
PROFILE_FILE = "profile.json"
PROVIDER_PATTERN = r"^[a-z][a-z-]{0,19}$"
MODEL_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$"
REVISION_PATTERN = r"^[A-Za-z0-9_-]{32,64}$"


class SettingsInput(BaseModel):
    """Reject ambiguous client documents before they reach local storage."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AISettingsInput(SettingsInput):
    provider: str = Field(pattern=PROVIDER_PATTERN)
    model: str = Field(pattern=MODEL_PATTERN)


class AIConnectionInput(SettingsInput):
    """Either an old request-scoped key or one exact saved-profile revision."""

    provider: str | None = Field(default=None, pattern=PROVIDER_PATTERN)
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    connection: Literal["saved"] | None = None
    config_revision: str | None = Field(default=None, pattern=REVISION_PATTERN)
    confirmed: bool

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value):
        if isinstance(value, dict):
            for field in ("provider", "model", "config_revision"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value

    @model_validator(mode="after")
    def exact_connection_shape(self):
        if self.connection == "saved":
            if self.model_fields_set != {"connection", "config_revision", "confirmed"}:
                raise ValueError("saved connection has unexpected fields")
            if self.config_revision is None:
                raise ValueError("saved connection requires a revision")
            return self
        if self.model_fields_set != {"provider", "model", "confirmed"}:
            raise ValueError("explicit connection has unexpected fields")
        if self.provider is None or self.model is None:
            raise ValueError("explicit connection requires provider and model")
        return self

    @property
    def saved(self) -> bool:
        return self.connection == "saved"


@dataclass(frozen=True)
class BYOKProfile:
    provider: str
    model: str
    key: str
    revision: str

    def public(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "configured": True,
            "key_saved": True,
            "revision": self.revision,
        }


def empty_profile() -> dict:
    return {
        "provider": None,
        "model": None,
        "configured": False,
        "key_saved": False,
        "revision": None,
    }


def _storage_error() -> AnalysisError:
    return AnalysisError("settings_unavailable", "本机 AI 配置不可用。", 503)


def _invalid_profile() -> AnalysisError:
    # Never distinguish malformed, replaced, or unsafe credential files to a browser.
    return _storage_error()


def _valid_key(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_KEY_BYTES
        and all(33 <= ord(character) <= 126 for character in value)
    )


def _valid_demo_key(value: object) -> bool:
    return value == ""


def _valid_profile_value(value: object, pattern: str) -> bool:
    return type(value) is str and re.fullmatch(pattern, value) is not None


def _closed_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class BYOKStore:
    """Persist one profile below the Hub-owned data directory.

    The profile is intentionally not encrypted.  Its protection comes from the
    Hub's local, owner-only directory and regular-file checks, matching the
    analysis host's refresh-token storage.  A damaged credential file is
    never repaired or replaced by a request.
    """

    def __init__(self, data_dir: Path):
        self.root = data_dir / PROFILE_DIRECTORY
        self._lock = threading.RLock()

    def _directory(self) -> int:
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise OSError("profile directory is not a directory")
            # mkdir honors the process umask. Restore the required mode before
            # opening so an unusually restrictive umask cannot strand a newly
            # created profile directory without search permission.
            os.chmod(self.root, 0o700, follow_symlinks=False)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(self.root, flags)
            try:
                os.fchmod(directory, 0o700)
            except BaseException:
                os.close(directory)
                raise
            return directory
        except (OSError, ValueError):
            raise _storage_error() from None

    @staticmethod
    def _read_open_file(directory: int) -> bytes | None:
        try:
            descriptor = os.open(
                PROFILE_FILE,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise _invalid_profile() from None
        try:
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_size > MAX_PROFILE_BYTES
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise _invalid_profile()
                raw = stream.read(MAX_PROFILE_BYTES + 1)
                if len(raw) > MAX_PROFILE_BYTES:
                    raise _invalid_profile()
                return raw
        except OSError:
            raise _invalid_profile() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _decode(raw: bytes) -> BYOKProfile:
        try:
            document = json.loads(raw, object_pairs_hook=_closed_object)
        except (TypeError, UnicodeError, ValueError):
            raise _invalid_profile() from None
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "provider",
            "model",
            "key",
            "revision",
        }:
            raise _invalid_profile()
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or not _valid_profile_value(document["provider"], PROVIDER_PATTERN)
            or not _valid_profile_value(document["model"], MODEL_PATTERN)
            or not _valid_profile_value(document["revision"], REVISION_PATTERN)
            or not (
                _valid_key(document["key"])
                or (document["provider"] == "demo" and _valid_demo_key(document["key"]))
            )
        ):
            raise _invalid_profile()
        return BYOKProfile(
            provider=document["provider"],
            model=document["model"],
            key=document["key"],
            revision=document["revision"],
        )

    def load(self) -> BYOKProfile | None:
        with self._lock:
            directory = self._directory()
            try:
                raw = self._read_open_file(directory)
                return None if raw is None else self._decode(raw)
            finally:
                os.close(directory)

    @staticmethod
    def _write(directory: int, profile: BYOKProfile) -> None:
        raw = (
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": profile.provider,
                    "model": profile.model,
                    "key": profile.key,
                    "revision": profile.revision,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        if len(raw) > MAX_PROFILE_BYTES:
            raise _storage_error()
        temporary = ".profile-" + uuid.uuid4().hex
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, PROFILE_FILE, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except OSError:
            raise _storage_error() from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            except OSError:
                # The target was atomically replaced already; this random, private
                # temporary name cannot expose a key through the API.
                pass

    def save(
        self,
        *,
        provider: str,
        model: str,
        supplied_key: str | None,
        allowed_providers: set[str],
        demo: bool,
    ) -> BYOKProfile:
        if provider not in allowed_providers:
            raise AnalysisError("unsupported_provider", "当前模式不支持该模型服务。", 422)
        with self._lock:
            directory = self._directory()
            try:
                raw = self._read_open_file(directory)
                current = None if raw is None else self._decode(raw)
                if demo and provider == "demo":
                    if supplied_key not in (None, ""):
                        raise AnalysisError("invalid_key", "演示模式不需要模型 API 密钥。", 422)
                    key = ""
                elif supplied_key is None:
                    if current is None or current.provider != provider:
                        raise AnalysisError("key_required", "更换模型服务时请重新填写模型 API 密钥。", 422)
                    key = current.key
                elif not _valid_key(supplied_key):
                    raise AnalysisError("invalid_key", "请填写有效的模型 API 密钥。", 422)
                else:
                    key = supplied_key
                profile = BYOKProfile(
                    provider=provider,
                    model=model,
                    key=key,
                    revision=secrets.token_urlsafe(24),
                )
                self._write(directory, profile)
                return profile
            finally:
                os.close(directory)

    def clear(self) -> None:
        with self._lock:
            directory = self._directory()
            try:
                # Validate before unlinking: do not turn a replaced or corrupt
                # path into a browser-controlled deletion primitive.
                raw = self._read_open_file(directory)
                if raw is not None:
                    self._decode(raw)
                    os.unlink(PROFILE_FILE, dir_fd=directory)
                    os.fsync(directory)
            except OSError:
                raise _storage_error() from None
            finally:
                os.close(directory)

    def resolve(self, revision: str, *, allowed_providers: set[str], demo: bool) -> BYOKProfile:
        profile = self.load()
        if profile is None or not secrets.compare_digest(profile.revision, revision):
            raise AnalysisError(
                "configuration_changed", "AI 配置已变更，请回到设置页面重新确认。", 409
            )
        if profile.provider not in allowed_providers:
            raise AnalysisError(
                "configuration_changed", "AI 配置已变更，请回到设置页面重新确认。", 409
            )
        if demo and profile.provider == "demo" and not _valid_demo_key(profile.key):
            raise _invalid_profile()
        if not demo and not _valid_key(profile.key):
            raise _invalid_profile()
        return profile
