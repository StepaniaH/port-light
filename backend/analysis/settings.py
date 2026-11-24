"""Local BYOK connections and the connection selected for analysis."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import AnalysisError

MAX_PROFILE_BYTES = 8192
MAX_CONNECTIONS = 16
MAX_STORE_BYTES = MAX_PROFILE_BYTES * MAX_CONNECTIONS + 4096
MAX_KEY_BYTES = 2048
MAX_BASE_URL_BYTES = 2048
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
    base_url: str | None = Field(default=None, max_length=MAX_BASE_URL_BYTES)
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    json_mode: bool = False

    @model_validator(mode="after")
    def custom_connection(self):
        if self.provider == "custom":
            self.base_url = normalize_base_url(self.base_url)
        elif self.model_fields_set & {"base_url", "token_parameter", "json_mode"}:
            raise ValueError("preset connection cannot override its endpoint")
        return self


class AIConnectionTestInput(SettingsInput):
    config_revision: str | None = Field(default=None, pattern=REVISION_PATTERN)
    profile_id: str | None = Field(default=None, pattern=REVISION_PATTERN)
    draft: AISettingsInput | None = None
    confirmed: bool

    @model_validator(mode="after")
    def connection_required(self):
        if self.config_revision is None and self.draft is None:
            raise ValueError("test requires a saved revision or a draft connection")
        if self.profile_id is not None and self.config_revision is None:
            raise ValueError("saved profile requires its revision")
        return self


class AISettingsEditInput(AISettingsInput):
    config_revision: str = Field(pattern=REVISION_PATTERN)


class AIProfileRevisionInput(SettingsInput):
    config_revision: str = Field(pattern=REVISION_PATTERN)


class AIProfileSelectionInput(AIProfileRevisionInput):
    profile_id: str = Field(pattern=REVISION_PATTERN)


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
    base_url: str | None = None
    token_parameter: str = "max_tokens"
    json_mode: bool = False
    identifier: str = ""

    def connection(self) -> dict | None:
        if self.provider != "custom" or self.base_url is None:
            return None
        return {
            "base_url": self.base_url,
            "token_parameter": self.token_parameter,
            "json_mode": self.json_mode,
        }

    def public(self) -> dict:
        return {
            "provider": self.provider,
            "model": "" if model_contains_key(self.model, self.key) else self.model,
            "configured": True,
            "key_saved": True,
            "revision": self.revision,
            **({"base_url": self.base_url, "token_parameter": self.token_parameter,
                "json_mode": self.json_mode} if self.provider == "custom" else {}),
        }


@dataclass(frozen=True)
class SavedConnections:
    profiles: tuple[BYOKProfile, ...] = ()
    active_id: str | None = None
    migrated: bool = False

    def find(self, identifier: str | None) -> BYOKProfile | None:
        return next((profile for profile in self.profiles if profile.identifier == identifier), None)

    @property
    def active(self) -> BYOKProfile | None:
        return self.find(self.active_id)

    def public_profile(self, profile: BYOKProfile) -> dict:
        document = profile.public()
        if any(model_contains_key(profile.model, item.key) for item in self.profiles):
            document["model"] = ""
        return document

    def public_rows(self) -> list[dict]:
        return [{"id": profile.identifier, **self.public_profile(profile)} for profile in self.profiles]


def model_contains_key(model: str, key: str) -> bool:
    return bool(key) and (model == key or len(key) >= 16 and key in model)


def _require_model_identifier(model: str, key: str) -> None:
    if model_contains_key(model, key):
        raise AnalysisError("invalid_model", "Model identifier must not contain an API key.", 422)


def empty_profile() -> dict:
    return {
        "provider": None,
        "model": None,
        "configured": False,
        "key_saved": False,
        "revision": None,
    }


def _storage_error() -> AnalysisError:
    return AnalysisError("settings_unavailable", "Local AI settings are unavailable.", 503)


DEFAULT_HUB_PORT = 2100
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})
_METADATA_IPS = frozenset({
    ipaddress.IPv4Address("100.100.100.200"),
    ipaddress.IPv6Address("fd00:ec2::254"),
})
_IPV4_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
_IPV6_LINK_LOCAL = ipaddress.ip_network("fe80::/10")
_IPV4_MULTICAST = ipaddress.ip_network("224.0.0.0/4")
_IPV6_MULTICAST = ipaddress.ip_network("ff00::/8")


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    candidate = candidate.split("%", 1)[0]
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    parts = candidate.split(".")
    if all(re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", part) for part in parts):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(candidate))
        except OSError:
            pass
    return None


def _forbidden_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        if _forbidden_ip(address.ipv4_mapped):
            return True
    if address.is_unspecified or address in _METADATA_IPS:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return address in _IPV4_LINK_LOCAL or address in _IPV4_MULTICAST
    return address in _IPV6_LINK_LOCAL or address in _IPV6_MULTICAST


def _is_loopback_host(
    host: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address | None
) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.is_loopback or address.ipv4_mapped.is_loopback
    return address.is_loopback


def _configured_hub_port() -> int:
    raw = os.environ.get("PORT_LIGHT_PORT", "").strip()
    if not raw:
        return DEFAULT_HUB_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_HUB_PORT
    return port if 1 <= port <= 65535 else DEFAULT_HUB_PORT


def _validate_ip_target(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    effective_port: int,
    *,
    check_runtime_port: bool,
) -> None:
    if _forbidden_ip(address):
        raise AnalysisError("invalid_base_url", "Target base URL is not allowed.", 422)
    if check_runtime_port and _is_loopback_host("", address):
        if effective_port == _configured_hub_port():
            raise AnalysisError(
                "invalid_base_url", "Target base URL cannot point to Port-Light itself.", 422
            )


def validate_target_url(
    value: str, *, check_runtime_port: bool = True, resolve_dns: bool = False
) -> list[tuple]:
    """Reject cloud metadata, link-local, unspecified, multicast, and self-loopback targets."""
    parsed = urlsplit(value)
    raw_host = parsed.hostname or ""
    host = raw_host.rstrip(".").lower()
    if not host or host in _METADATA_HOSTS or host.endswith(
        (".metadata.google.internal", ".metadata.goog")
    ):
        raise AnalysisError("invalid_base_url", "Target base URL is not allowed.", 422)
    effective_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    address = _parse_ip(raw_host)
    if address is not None:
        _validate_ip_target(address, effective_port, check_runtime_port=check_runtime_port)
    elif check_runtime_port and _is_loopback_host(host, None):
        if effective_port == _configured_hub_port():
            raise AnalysisError(
                "invalid_base_url", "Target base URL cannot point to Port-Light itself.", 422
            )
    if not resolve_dns:
        return []
    return resolve_target_addresses(value, check_runtime_port=check_runtime_port)


def resolve_target_addresses(
    value: str, *, check_runtime_port: bool = True, allow_unresolved: bool = False
) -> list[tuple]:
    """Resolve DNS and validate every resolved IP for connection-level socket pinning."""
    validate_target_url(value, check_runtime_port=check_runtime_port, resolve_dns=False)
    parsed = urlsplit(value)
    raw_host = parsed.hostname or ""
    effective_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(raw_host, effective_port, type=socket.SOCK_STREAM)
    except OSError:
        if allow_unresolved:
            return []
        raise AnalysisError(
            "provider_connection",
            "Unable to connect to model provider; request was not retried.",
            502,
        ) from None
    if not addresses:
        if allow_unresolved:
            return []
        raise AnalysisError(
            "provider_connection",
            "Unable to connect to model provider; request was not retried.",
            502,
        )
    for entry in addresses:
        resolved_host = entry[4][0] if len(entry) >= 5 and entry[4] else ""
        resolved_ip = _parse_ip(str(resolved_host))
        if resolved_ip is None:
            raise AnalysisError("invalid_base_url", "Target base URL is not allowed.", 422)
        _validate_ip_target(resolved_ip, effective_port, check_runtime_port=check_runtime_port)
    return list(addresses)


def normalize_base_url(value: object, *, check_runtime_port: bool = True) -> str:
    """Accept an explicit Chat Completions destination without embedded secrets."""
    try:
        if not isinstance(value, str):
            raise ValueError("missing URL")
        value = value.strip().rstrip("/")
        if not value or len(value) > MAX_BASE_URL_BYTES or any(
            ord(char) <= 32 or ord(char) == 127 or char in "\\?#" for char in value
        ):
            raise ValueError("invalid URL")
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("invalid destination")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("invalid port")
        if parsed.path.endswith("/chat/completions"):
            value = value[:-len("/chat/completions")]
        normalized = str(httpx.URL(value)).rstrip("/")
        if len(normalized) > MAX_BASE_URL_BYTES:
            raise ValueError("URL is too long")
    except (ValueError, httpx.InvalidURL):
        raise AnalysisError(
            "invalid_base_url",
            "Base URL must be an HTTP(S) URL without credentials, query, or fragment.",
            422,
        ) from None
    validate_target_url(value, check_runtime_port=check_runtime_port)
    validate_target_url(normalized, check_runtime_port=check_runtime_port)
    return normalized


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
    """Persist bounded connections below the Hub-owned data directory.

    The profile is intentionally not encrypted.  Its protection comes from the
    Hub's local, owner-only directory and regular-file checks.  A damaged
    credential file is never repaired or replaced by a request.
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
                    or info.st_size > MAX_STORE_BYTES
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise _invalid_profile()
                raw = stream.read(MAX_STORE_BYTES + 1)
                if len(raw) > MAX_STORE_BYTES:
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
        fields = {
            "schema_version",
            "provider",
            "model",
            "key",
            "revision",
        }
        if not isinstance(document, dict):
            raise _invalid_profile()
        version = document.get("schema_version")
        if type(version) is not int or not (
            (version == 1 and set(document) == fields)
            or (version == 2 and set(document) == fields | {"base_url", "token_parameter", "json_mode"})
        ):
            raise _invalid_profile()
        if (
            not _valid_profile_value(document["provider"], PROVIDER_PATTERN)
            or not _valid_profile_value(document["model"], MODEL_PATTERN)
            or not _valid_profile_value(document["revision"], REVISION_PATTERN)
            or not (
                _valid_key(document["key"])
                or (document["provider"] == "demo" and _valid_demo_key(document["key"]))
            )
        ):
            raise _invalid_profile()
        if version == 2:
            try:
                if (document["provider"] != "custom"
                        or normalize_base_url(document["base_url"], check_runtime_port=False) != document["base_url"]
                        or document["token_parameter"] not in {"max_tokens", "max_completion_tokens"}
                        or type(document["json_mode"]) is not bool):
                    raise _invalid_profile()
            except (AnalysisError, TypeError):
                raise _invalid_profile() from None
        return BYOKProfile(
            provider=document["provider"],
            model=document["model"],
            key=document["key"],
            revision=document["revision"],
            base_url=document.get("base_url"),
            token_parameter=document.get("token_parameter", "max_tokens"),
            json_mode=document.get("json_mode", False),
        )

    def load(self) -> BYOKProfile | None:
        return self.load_connections().active

    @classmethod
    def _decode_connections(cls, raw: bytes | None) -> SavedConnections:
        if raw is None:
            return SavedConnections()
        try:
            document = json.loads(raw, object_pairs_hook=_closed_object)
            if not isinstance(document, dict):
                raise ValueError("invalid collection")
            if document.get("schema_version") in {1, 2}:
                if len(raw) > MAX_PROFILE_BYTES:
                    raise ValueError("oversized profile")
                profile = cls._decode(raw)
                profile = replace(profile, identifier=profile.revision)
                return SavedConnections((profile,), profile.identifier)
            if (type(document.get("schema_version")) is not int or document["schema_version"] != 3
                    or set(document) != {"schema_version", "active_id", "profiles"}
                    or not isinstance(document["profiles"], list)
                    or len(document["profiles"]) > MAX_CONNECTIONS):
                raise ValueError("invalid collection")
            profiles = []
            for row in document["profiles"]:
                if not isinstance(row, dict) or not _valid_profile_value(row.get("id"), REVISION_PATTERN):
                    raise ValueError("invalid profile ID")
                item = {key: value for key, value in row.items() if key != "id"}
                encoded = json.dumps(item, ensure_ascii=True).encode()
                if len(encoded) > MAX_PROFILE_BYTES:
                    raise ValueError("oversized profile")
                profiles.append(replace(cls._decode(encoded), identifier=row["id"]))
            identifiers = {profile.identifier for profile in profiles}
            if len(identifiers) != len(profiles) or (
                document["active_id"] is not None and document["active_id"] not in identifiers
            ):
                raise ValueError("invalid selection")
            return SavedConnections(tuple(profiles), document["active_id"], migrated=True)
        except (TypeError, UnicodeError, ValueError):
            raise _invalid_profile() from None

    def load_connections(self) -> SavedConnections:
        with self._lock:
            directory = self._directory()
            try:
                return self._decode_connections(self._read_open_file(directory))
            finally:
                os.close(directory)

    @staticmethod
    def _profile_document(profile: BYOKProfile) -> dict:
        return {
            "schema_version": 2 if profile.base_url is not None else 1,
            "provider": profile.provider,
            "model": profile.model,
            "key": profile.key,
            "revision": profile.revision,
            **(profile.connection() or {}),
        }

    @staticmethod
    def _write_document(directory: int, document: dict, limit: int) -> None:
        raw = (json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(raw) > limit:
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

    @classmethod
    def _write(cls, directory: int, profile: BYOKProfile) -> None:
        cls._write_document(directory, cls._profile_document(profile), MAX_PROFILE_BYTES)

    @classmethod
    def _write_connections(cls, directory: int, connections: SavedConnections) -> None:
        cls._write_document(directory, {
            "schema_version": 3,
            "active_id": connections.active_id,
            "profiles": [
                {"id": profile.identifier, **cls._profile_document(profile)}
                for profile in connections.profiles
            ],
        }, MAX_STORE_BYTES)

    @staticmethod
    def _build_profile(
        connections: SavedConnections, current: BYOKProfile | None, *,
        provider: str, model: str, supplied_key: str | None,
        allowed_providers: set[str], demo: bool, base_url: str | None = None,
        token_parameter: str = "max_tokens", json_mode: bool = False,
    ) -> BYOKProfile:
        if provider not in allowed_providers:
            raise AnalysisError(
                "unsupported_provider", "Provider is not supported in the current mode.", 422
            )
        if not _valid_profile_value(model, MODEL_PATTERN):
            raise AnalysisError("invalid_input", "Invalid model identifier.", 422)
        if provider == "custom":
            base_url = normalize_base_url(base_url)
            if token_parameter not in {"max_tokens", "max_completion_tokens"} or type(json_mode) is not bool:
                raise AnalysisError("invalid_input", "Invalid connection options.", 422)
        if demo and provider == "demo":
            if supplied_key not in (None, ""):
                raise AnalysisError("invalid_key", "Demo mode does not accept an API key.", 422)
            key = ""
        elif supplied_key is None:
            if current is None or current.provider != provider or current.base_url != base_url:
                raise AnalysisError(
                    "key_required", "Enter an API key when changing provider or base URL.", 422
                )
            key = current.key
        elif not _valid_key(supplied_key):
            raise AnalysisError("invalid_key", "Enter a valid API key.", 422)
        else:
            key = supplied_key
        _require_model_identifier(model, key)
        for saved in connections.profiles:
            _require_model_identifier(model, saved.key)
        return BYOKProfile(
            provider=provider, model=model, key=key, revision=secrets.token_urlsafe(24),
            base_url=base_url if provider == "custom" else None,
            token_parameter=token_parameter, json_mode=json_mode,
            identifier=current.identifier if current else uuid.uuid4().hex,
        )

    @staticmethod
    def _match(connections: SavedConnections, identifier: str, revision: str) -> BYOKProfile:
        profile = connections.find(identifier)
        if profile is None or not isinstance(revision, str) or not secrets.compare_digest(profile.revision, revision):
            raise AnalysisError(
                "configuration_changed",
                "AI configuration changed; reload settings and try again.",
                409,
            )
        return profile

    @staticmethod
    def _replaced(connections: SavedConnections, profile: BYOKProfile) -> tuple[BYOKProfile, ...]:
        if connections.find(profile.identifier) is None:
            if len(connections.profiles) >= MAX_CONNECTIONS:
                raise AnalysisError("connection_limit", "Saved connection limit reached.", 422)
            return (*connections.profiles, profile)
        return tuple(profile if item.identifier == profile.identifier else item for item in connections.profiles)

    def save(self, **options) -> BYOKProfile:
        """Keep the legacy API editing only the active connection."""
        with self._lock:
            directory = self._directory()
            try:
                connections = self._decode_connections(self._read_open_file(directory))
                profile = self._build_profile(connections, connections.active, **options)
                if connections.migrated:
                    self._write_connections(directory, SavedConnections(
                        self._replaced(connections, profile), profile.identifier, migrated=True,
                    ))
                else:
                    profile = replace(profile, identifier=profile.revision)
                    self._write(directory, profile)
                return profile
            finally:
                os.close(directory)

    def save_connection(self, *, identifier=None, config_revision=None, **options) -> BYOKProfile:
        with self._lock:
            directory = self._directory()
            try:
                connections = self._decode_connections(self._read_open_file(directory))
                current = self._match(connections, identifier, config_revision) if identifier is not None else None
                profile = self._build_profile(connections, current, **options)
                active_id = profile.identifier if not connections.profiles else connections.active_id
                self._write_connections(directory, SavedConnections(
                    self._replaced(connections, profile), active_id, migrated=True,
                ))
                return profile
            finally:
                os.close(directory)

    def activate(self, identifier: str, revision: str, *, allowed_providers: set[str], demo: bool) -> None:
        with self._lock:
            directory = self._directory()
            try:
                connections = self._decode_connections(self._read_open_file(directory))
                profile = self._match(connections, identifier, revision)
                self._validate_saved(profile, allowed_providers=allowed_providers, demo=demo)
                for item in connections.profiles:
                    _require_model_identifier(profile.model, item.key)
                if connections.active_id != identifier:
                    # Switching away and back must not revive an earlier consent.
                    profile = replace(profile, revision=secrets.token_urlsafe(24))
                self._write_connections(directory, SavedConnections(
                    self._replaced(connections, profile), identifier, migrated=True,
                ))
            finally:
                os.close(directory)

    def delete_connection(self, identifier: str, revision: str) -> None:
        with self._lock:
            directory = self._directory()
            try:
                connections = self._decode_connections(self._read_open_file(directory))
                self._match(connections, identifier, revision)
                self._write_connections(directory, SavedConnections(
                    tuple(item for item in connections.profiles if item.identifier != identifier),
                    None if connections.active_id == identifier else connections.active_id,
                    migrated=True,
                ))
            finally:
                os.close(directory)

    def resolve_test(
        self,
        values: AIConnectionTestInput,
        supplied_key: str | None,
        *,
        allowed_providers: set[str],
    ) -> BYOKProfile:
        """Resolve a probe without persisting the draft or returning its key."""
        if values.draft is None:
            profile = self._resolve_saved(values.config_revision, identifier=values.profile_id,
                                          allowed_providers=allowed_providers, demo=False)
            self.require_safe_model(profile.model, profile.key)
            return profile
        draft = values.draft
        if draft.provider not in allowed_providers:
            raise AnalysisError(
                "unsupported_provider", "Provider is not supported in the current mode.", 422
            )
        if supplied_key is None:
            if values.config_revision is None:
                raise AnalysisError("key_required", "Enter an API key.", 422)
            current = self._resolve_saved(values.config_revision, identifier=values.profile_id,
                                          allowed_providers=allowed_providers, demo=False)
            if current.provider != draft.provider or current.base_url != draft.base_url:
                raise AnalysisError(
                    "key_required", "Enter an API key when changing provider or base URL.", 422
                )
            key = current.key
        elif not _valid_key(supplied_key):
            raise AnalysisError("invalid_key", "Enter a valid API key.", 422)
        else:
            key = supplied_key
        self.require_safe_model(draft.model, key)
        return BYOKProfile(
            provider=draft.provider, model=draft.model, key=key, revision="draft",
            base_url=draft.base_url, token_parameter=draft.token_parameter, json_mode=draft.json_mode,
        )

    def clear(self) -> None:
        with self._lock:
            directory = self._directory()
            try:
                # Validate before unlinking: do not turn a replaced or corrupt
                # path into a browser-controlled deletion primitive.
                raw = self._read_open_file(directory)
                if raw is not None:
                    self._decode_connections(raw)
                    os.unlink(PROFILE_FILE, dir_fd=directory)
                    os.fsync(directory)
            except OSError:
                raise _storage_error() from None
            finally:
                os.close(directory)

    def resolve(self, revision: str, *, allowed_providers: set[str], demo: bool) -> BYOKProfile:
        profile = self._resolve_saved(revision, allowed_providers=allowed_providers, demo=demo)
        self.require_safe_model(profile.model, profile.key)
        return profile

    def require_safe_model(self, model: str, key: str, *, check_saved_keys: bool = True) -> None:
        _require_model_identifier(model, key)
        if not check_saved_keys:
            return
        for profile in self.load_connections().profiles:
            _require_model_identifier(model, profile.key)

    @staticmethod
    def _validate_saved(profile: BYOKProfile, *, allowed_providers: set[str], demo: bool) -> None:
        if profile.provider not in allowed_providers or (
            profile.provider == "custom" and profile.base_url is None
        ):
            raise AnalysisError(
                "configuration_changed",
                "AI configuration changed; reload settings and try again.",
                409,
            )
        if demo and profile.provider == "demo" and not _valid_demo_key(profile.key):
            raise _invalid_profile()
        if not demo and not _valid_key(profile.key):
            raise _invalid_profile()

    def _resolve_saved(self, revision: str, *, allowed_providers: set[str], demo: bool,
                       identifier: str | None = None) -> BYOKProfile:
        connections = self.load_connections()
        profile = connections.find(identifier) if identifier is not None else connections.active
        if profile is None or not secrets.compare_digest(profile.revision, revision):
            raise AnalysisError(
                "configuration_changed",
                "AI configuration changed; reload settings and try again.",
                409,
            )
        self._validate_saved(profile, allowed_providers=allowed_providers, demo=demo)
        return profile
