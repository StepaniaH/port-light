"""Offline Ed25519 Supporter Key verification and masking.

Zero external dependencies: implements RFC 8032 Ed25519 signature verification
directly in Python stdlib so Port-Light stays lightweight and 100% offline.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

_LOG = logging.getLogger("port-light")
_WARNED_INVALID_SIGNATURES: set[tuple[str, str]] = set()

Tier = Literal["personal", "fleet"]

_VALID_TIERS = frozenset({"personal", "fleet"})
_B64U_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPKI_ED25519_PREFIX = bytes.fromhex("302a300506032b6570032100")

# Production Ed25519 SPKI public keys (PEM). Empty by default (fail-closed)
# until the production public key paired with Cloudflare Pages ED25519_PRIVATE_KEY
# is embedded here. Supports multiple entries for zero-downtime key rotation.
TRUSTED_PUBLIC_KEYS_PEM: tuple[str, ...] = ()

# ── RFC 8032 Ed25519 curve constants ──────────────────────────

_P = (1 << 255) - 19
_L = (1 << 252) + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_D2 = (2 * _D) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

_BY = (4 * pow(5, _P - 2, _P)) % _P
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_BASE_POINT: tuple[int, int, int, int] = (_BX, _BY, 1, (_BX * _BY) % _P)
_IDENTITY: tuple[int, int, int, int] = (0, 1, 1, 0)


@dataclass(frozen=True)
class SupporterKeyPayload:
    version: int
    tier: Tier
    order_id: str
    issued_at: str


def parse_public_key_pem(pem: str) -> bytes:
    """Parse an Ed25519 SPKI PEM public key into its 32-byte raw form."""
    if not isinstance(pem, str) or not pem.strip():
        raise ValueError("empty public key PEM")
    normalized = pem.strip().replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    begin = "-----BEGIN PUBLIC KEY-----"
    end = "-----END PUBLIC KEY-----"
    if begin not in normalized or end not in normalized:
        raise ValueError("missing PUBLIC KEY PEM markers")
    body = normalized.split(begin, 1)[1].split(end, 1)[0]
    body = re.sub(r"\s+", "", body)
    if not body:
        raise ValueError("empty PUBLIC KEY PEM body")
    try:
        der = base64.b64decode(body, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid base64 in PUBLIC KEY PEM") from exc
    if len(der) != 44 or not der.startswith(_SPKI_ED25519_PREFIX):
        raise ValueError("not an Ed25519 SPKI public key")
    return der[len(_SPKI_ED25519_PREFIX):]


TRUSTED_PUBLIC_KEYS: tuple[bytes, ...] = tuple(
    parse_public_key_pem(pem) for pem in TRUSTED_PUBLIC_KEYS_PEM
)


def _point_add(
    p1: tuple[int, int, int, int],
    p2: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (t1 * _D2 * t2) % _P
    d = (z1 * 2 * z2) % _P
    e = (b - a) % _P
    f = (d - c) % _P
    g = (d + c) % _P
    h = (b + a) % _P
    return (e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P


def _scalar_mul(scalar: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    acc = _IDENTITY
    addend = point
    k = scalar
    while k > 0:
        if k & 1:
            acc = _point_add(acc, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return acc


def _points_equal(
    p1: tuple[int, int, int, int],
    p2: tuple[int, int, int, int],
) -> bool:
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _decode_point(raw: bytes) -> tuple[int, int, int, int] | None:
    if len(raw) != 32:
        return None
    sign = raw[31] >> 7
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    if y >= _P:
        return None
    y2 = (y * y) % _P
    u = (y2 - 1) % _P
    v = (_D * y2 + 1) % _P
    v3 = (v * v * v) % _P
    v7 = (v3 * v3 * v) % _P
    x = (u * v3 * pow((u * v7) % _P, (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    if vx2 == (-u) % _P:
        x = (x * _SQRT_M1) % _P
    elif vx2 != u:
        return None
    if x == 0 and sign == 1:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x, y, 1, (x * y) % _P


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify an RFC 8032 Ed25519 signature using pure Python stdlib math."""
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
        return False
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    if not isinstance(message, (bytes, bytearray)):
        return False
    pub_bytes = bytes(public_key)
    r_bytes = bytes(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    point_a = _decode_point(pub_bytes)
    point_r = _decode_point(r_bytes)
    if point_a is None or point_r is None:
        return False
    h = hashlib.sha512(r_bytes + pub_bytes + bytes(message)).digest()
    k = int.from_bytes(h, "little") % _L
    lhs = _scalar_mul(s, _BASE_POINT)
    rhs = _point_add(point_r, _scalar_mul(k, point_a))
    return _points_equal(lhs, rhs)


def _b64u_decode(segment: str) -> bytes | None:
    if not isinstance(segment, str) or not segment or not _B64U_RE.fullmatch(segment):
        return None
    pad = (4 - (len(segment) % 4)) % 4
    try:
        return base64.urlsafe_b64decode(segment + ("=" * pad))
    except (ValueError, TypeError):
        return None


def parse_supporter_payload(obj: Any) -> SupporterKeyPayload | None:
    """Validate canonical v=1 supporter payload fields; ignore unknown extra keys."""
    if not isinstance(obj, dict):
        return None
    version = obj.get("v")
    if type(version) is not int or version != 1:
        return None
    tier = obj.get("tier")
    if not isinstance(tier, str) or tier not in _VALID_TIERS:
        return None
    oid = obj.get("oid")
    if (
        not isinstance(oid, str)
        or not (1 <= len(oid) <= 128)
        or oid != oid.strip()
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in oid)
    ):
        return None
    iat = obj.get("iat")
    if not isinstance(iat, str) or not _ISO_DATE_RE.fullmatch(iat):
        return None
    try:
        datetime.date.fromisoformat(iat)
    except ValueError:
        return None
    return SupporterKeyPayload(version=1, tier=tier, order_id=oid, issued_at=iat)  # type: ignore[arg-type]


def verify_supporter_key(
    key_string: str,
    *,
    public_keys: Sequence[bytes] | None = None,
) -> SupporterKeyPayload | None:
    """Verify a ``PL1.<payload_b64u>.<sig_b64u>`` Supporter Key completely offline."""
    if not isinstance(key_string, str):
        return None
    trimmed = key_string.strip()
    if not trimmed or len(trimmed) > 2048:
        return None
    parts = trimmed.split(".")
    if len(parts) != 3 or parts[0] != "PL1":
        return None
    _, payload_b64u, sig_b64u = parts
    payload_bytes = _b64u_decode(payload_b64u)
    sig_bytes = _b64u_decode(sig_b64u)
    if payload_bytes is None or sig_bytes is None or len(sig_bytes) != 64:
        return None
    try:
        raw_payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    payload = parse_supporter_payload(raw_payload)
    if payload is None:
        return None
    keys = TRUSTED_PUBLIC_KEYS if public_keys is None else tuple(public_keys)
    if not keys:
        return None
    signing_input = f"PL1.{payload_b64u}".encode("ascii")
    for pub in keys:
        if verify_ed25519(pub, signing_input, sig_bytes):
            return payload
    return None


def mask_key(key_string: str) -> str:
    """Return a non-sensitive preview prefix that never includes the signature segment."""
    trimmed = key_string.strip()
    prefix = trimmed.split(".", 2)
    head = ".".join(prefix[:2]) if len(prefix) >= 2 else trimmed
    limit = min(18, len(head))
    return head[:limit] + "…"


def mask_order_id(order_id: str) -> str:
    """Mask an order ID for display while preserving recognizable boundaries."""
    trimmed = order_id.strip()
    if len(trimmed) > 8:
        return f"{trimmed[:4]}•••••{trimmed[-3:]}"
    if len(trimmed) > 3:
        return f"•••••{trimmed[-3:]}"
    return f"•••••{trimmed}"


def validate_supporter_key(
    key_string: Any,
    *,
    public_keys: Sequence[bytes] | None = None,
) -> tuple[SupporterKeyPayload | None, str | None]:
    """Validate format, schema, and Ed25519 signature, returning (payload, error_code)."""
    if not isinstance(key_string, str):
        return None, "supporter_key_format"
    trimmed = key_string.strip()
    if not trimmed or len(trimmed) > 2048:
        return None, "supporter_key_format"
    parts = trimmed.split(".")
    if len(parts) != 3 or parts[0] != "PL1":
        return None, "supporter_key_format"
    _, payload_b64u, sig_b64u = parts
    payload_bytes = _b64u_decode(payload_b64u)
    sig_bytes = _b64u_decode(sig_b64u)
    if payload_bytes is None or sig_bytes is None or len(sig_bytes) != 64:
        return None, "supporter_key_format"
    try:
        raw_payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "supporter_key_format"
    payload = parse_supporter_payload(raw_payload)
    if payload is None:
        return None, "supporter_key_format"
    keys = TRUSTED_PUBLIC_KEYS if public_keys is None else tuple(public_keys)
    if not keys:
        return None, "supporter_key_invalid"
    signing_input = f"PL1.{payload_b64u}".encode("ascii")
    for pub in keys:
        if verify_ed25519(pub, signing_input, sig_bytes):
            return payload, None
    return None, "supporter_key_invalid"


def _warn_invalid_once(origin: str, raw_key: str) -> None:
    digest = hashlib.sha256(raw_key.encode("utf-8", errors="replace")).hexdigest()[:12]
    token = (origin, digest)
    if token in _WARNED_INVALID_SIGNATURES:
        return
    _WARNED_INVALID_SIGNATURES.add(token)
    source_desc = "PORT_LIGHT_SUPPORTER_KEY" if origin == "env" else "stored supporter key"
    _LOG.warning("Ignoring invalid %s (offline Ed25519 verification failed)", source_desc)


def resolve_supporter_state(
    values: dict[str, Any] | None = None,
    *,
    public_keys: Sequence[bytes] | None = None,
) -> dict[str, Any]:
    """Resolve current supporter status with settings.py-identical read/write precedence."""
    from . import port_store, settings as app_settings

    mode = app_settings.settings_source()
    readonly = app_settings.settings_readonly()
    if values is None:
        resolved_values, _ = app_settings.resolve()
    else:
        resolved_values = values
    show_in_header = bool(resolved_values.get("supporter_show_header", True))

    env_raw = (os.environ.get("PORT_LIGHT_SUPPORTER_KEY") or "").strip() or None
    file_raw: str | None = None
    if mode != "env":
        try:
            file_raw = port_store.get_supporter_key()
        except port_store.StoreReadError:
            file_raw = None

    origin: Literal["file", "env", "none"]
    active_raw: str | None
    if mode == "env":
        active_raw = env_raw
        origin = "env" if env_raw else "none"
    elif file_raw is not None:
        active_raw = file_raw
        origin = "file"
    elif env_raw is not None:
        active_raw = env_raw
        origin = "env"
    else:
        active_raw = None
        origin = "none"

    can_edit = not readonly
    can_clear = (not readonly) and (file_raw is not None)

    if active_raw is None:
        return {
            "active": False,
            "status": "inactive",
            "tier": None,
            "issued_at": None,
            "key_preview": None,
            "order_preview": None,
            "origin": "none",
            "env_configured": env_raw is not None,
            "can_edit": can_edit,
            "can_clear": can_clear,
            "show_in_header": show_in_header,
        }

    payload, _err = validate_supporter_key(active_raw, public_keys=public_keys)
    if payload is None:
        _warn_invalid_once(origin, active_raw)
        return {
            "active": False,
            "status": "invalid",
            "tier": None,
            "issued_at": None,
            "key_preview": None,
            "order_preview": None,
            "origin": origin,
            "env_configured": env_raw is not None,
            "can_edit": can_edit,
            "can_clear": can_clear,
            "show_in_header": show_in_header,
        }

    return {
        "active": True,
        "status": "active",
        "tier": payload.tier,
        "issued_at": payload.issued_at,
        "key_preview": mask_key(active_raw),
        "order_preview": mask_order_id(payload.order_id),
        "origin": origin,
        "env_configured": env_raw is not None,
        "can_edit": can_edit,
        "can_clear": can_clear,
        "show_in_header": show_in_header,
    }


def meta_supporter_summary(values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Minimal non-sensitive supporter summary exposed on GET /api/meta."""
    state = resolve_supporter_state(values)
    return {
        "active": state["active"],
        "tier": state["tier"],
        "issued_at": state["issued_at"],
        "show_in_header": state["show_in_header"],
    }


def settings_supporter_document(values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full masked supporter status exposed on GET /api/settings and supporter-key endpoints."""
    return resolve_supporter_state(values)
