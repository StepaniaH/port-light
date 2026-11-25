from __future__ import annotations

import base64
import json

import pytest

from backend import supporter

# Test-only Ed25519 keypairs and signatures generated via Node WebCrypto using
# port-light-page/functions/_lib/supporter.js (never production keys).
TEST_PUB1_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEANG45UzRcpXN5W2GGR2iCtJKtv6l78qJ5azZMz6mEyJc=\n"
    "-----END PUBLIC KEY-----"
)
TEST_PUB2_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAzitIufXJnQAZGOt9OZCfWbEb4L4nn0QC8OB0U4nW7jE=\n"
    "-----END PUBLIC KEY-----"
)

PERSONAL_KEY = (
    "PL1.eyJpYXQiOiIyMDI2LTExLTI1Iiwib2lkIjoib3JkXzU1NTQ0NDMzMyIsInRpZXIiOiJwZXJzb25hbCIsInYiOjF9."
    "j9cC3q_BhXLQO0QnBy9X-NAmvSh0hv9Y9C_5lpgd78icxtPmHRkWdfpPNZOV7SIM_tARYuulAlaRHDA9TmH4Bw"
)
FLEET_KEY = (
    "PL1.eyJpYXQiOiIyMDI2LTExLTI1Iiwib2lkIjoib3JkX2ZsZWV0Xzk4NzY1NCIsInRpZXIiOiJmbGVldCIsInYiOjF9."
    "dfYGfIwQ9x1MsdKEiFdzWs9uSutR6NtXHw6m45pGa6oFF1CBEuMZpxt6Y6gh9k8ARd8PoznPmk8QGxdkZJgmBw"
)
ROTATED_KEY = (
    "PL1.eyJpYXQiOiIyMDI2LTEyLTAxIiwib2lkIjoib3JkX3JvdF8wMDEiLCJ0aWVyIjoicGVyc29uYWwiLCJ2IjoxfQ."
    "cmyBZUXNq_9yvSMD7CWJj85gCDBXd8-R68xIKfDg_WxW-9gboc0fGEwDZRqixg4cn0g3aOpeMNhwIe5hwVmDAA"
)
EXTRA_FIELD_KEY = (
    "PL1.eyJleHRyYSI6ImZ1dHVyZSIsImlhdCI6IjIwMjYtMTEtMjUiLCJvaWQiOiJvcmRfZXh0cmFfOTkiLCJ0aWVyIjoicGVyc29uYWwiLCJ2IjoxfQ."
    "O1DKd2Fpj1_z7QmksBjwdbU0zNXe5EBdztuffeZGlSUYlQZUUkYORZDazBKp8t3vSblzHQxydMgPSurjn7XVDg"
)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_parse_public_key_pem_accepts_spki_and_rejects_bad_headers():
    raw = supporter.parse_public_key_pem(TEST_PUB1_PEM)
    assert len(raw) == 32
    # Also tolerates escaped \n in env/config copies
    escaped = TEST_PUB1_PEM.replace("\n", "\\n")
    assert supporter.parse_public_key_pem(escaped) == raw

    with pytest.raises(ValueError):
        supporter.parse_public_key_pem("not a pem")
    with pytest.raises(ValueError):
        supporter.parse_public_key_pem("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----")


def test_rfc8032_ed25519_test_vector_1():
    # RFC 8032 Section 7.1 TEST 1 (empty message)
    pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert supporter.verify_ed25519(pub, b"", sig) is True
    assert supporter.verify_ed25519(pub, b"x", sig) is False
    # Flip 1 bit in signature
    bad_sig = bytes([sig[0] ^ 1]) + sig[1:]
    assert supporter.verify_ed25519(pub, b"", bad_sig) is False


def test_verify_ed25519_rejects_non_canonical_scalar_and_bad_lengths():
    pub = supporter.parse_public_key_pem(TEST_PUB1_PEM)
    assert supporter.verify_ed25519(pub[:31], b"msg", b"\x00" * 64) is False
    assert supporter.verify_ed25519(pub, b"msg", b"\x00" * 63) is False
    # S = L (curve group order) must be rejected as non-canonical
    l_order = 2**252 + 27742317777372353535851937790883648493
    bad_s_sig = (b"\x00" * 32) + l_order.to_bytes(32, "little")
    assert supporter.verify_ed25519(pub, b"msg", bad_s_sig) is False


def test_verify_supporter_key_verifies_webcrypto_personal_and_fleet_vectors():
    keys = (supporter.parse_public_key_pem(TEST_PUB1_PEM),)
    personal = supporter.verify_supporter_key(f"  {PERSONAL_KEY}\n", public_keys=keys)
    assert personal is not None
    assert personal.tier == "personal"
    assert personal.order_id == "ord_555444333"
    assert personal.issued_at == "2026-11-25"
    assert personal.version == 1

    fleet = supporter.verify_supporter_key(FLEET_KEY, public_keys=keys)
    assert fleet is not None
    assert fleet.tier == "fleet"
    assert fleet.order_id == "ord_fleet_987654"
    assert fleet.issued_at == "2026-11-25"


def test_verify_supporter_key_supports_key_rotation_and_unknown_v1_fields():
    keys = (
        supporter.parse_public_key_pem(TEST_PUB1_PEM),
        supporter.parse_public_key_pem(TEST_PUB2_PEM),
    )
    rotated = supporter.verify_supporter_key(ROTATED_KEY, public_keys=keys)
    assert rotated is not None
    assert rotated.order_id == "ord_rot_001"
    assert rotated.issued_at == "2026-12-01"

    # Extra unknown field in v=1 is ignored for forward compatibility
    extra = supporter.verify_supporter_key(EXTRA_FIELD_KEY, public_keys=keys)
    assert extra is not None
    assert extra.order_id == "ord_extra_99"
    assert extra.tier == "personal"


def test_verify_supporter_key_fails_closed_when_no_trusted_keys_or_tampered():
    # Fail closed with empty public_keys tuple
    assert supporter.verify_supporter_key(PERSONAL_KEY, public_keys=()) is None

    pub1 = (supporter.parse_public_key_pem(TEST_PUB1_PEM),)
    # Wrong key
    assert supporter.verify_supporter_key(ROTATED_KEY, public_keys=pub1) is None

    # Tampered tier in payload without re-signing
    parts = PERSONAL_KEY.split(".")
    tampered_payload = _b64u(
        json.dumps({"iat": "2026-11-25", "oid": "ord_555444333", "tier": "fleet", "v": 1}, separators=(",", ":")).encode()
    )
    assert supporter.verify_supporter_key(f"PL1.{tampered_payload}.{parts[2]}", public_keys=pub1) is None

    # Tampered signature character
    bad_sig = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    assert supporter.verify_supporter_key(f"PL1.{parts[1]}.{bad_sig}", public_keys=pub1) is None

    # Wrong prefix / padding / segment count / non-string
    assert supporter.verify_supporter_key(f"PL2.{parts[1]}.{parts[2]}", public_keys=pub1) is None
    assert supporter.verify_supporter_key(f"PL1.{parts[1]}=.{parts[2]}", public_keys=pub1) is None
    assert supporter.verify_supporter_key("PL1.onlytwo", public_keys=pub1) is None
    assert supporter.verify_supporter_key("", public_keys=pub1) is None


def test_payload_schema_rejects_bool_version_bad_dates_and_control_chars():
    # Verify schema checks independently of signature
    assert supporter.parse_supporter_payload({"v": True, "tier": "personal", "oid": "ord_1", "iat": "2026-11-25"}) is None
    assert supporter.parse_supporter_payload({"v": 2, "tier": "personal", "oid": "ord_1", "iat": "2026-11-25"}) is None
    assert supporter.parse_supporter_payload({"v": 1, "tier": "pro", "oid": "ord_1", "iat": "2026-11-25"}) is None
    assert supporter.parse_supporter_payload({"v": 1, "tier": "personal", "oid": "", "iat": "2026-11-25"}) is None
    assert supporter.parse_supporter_payload({"v": 1, "tier": "personal", "oid": "ord\n1", "iat": "2026-11-25"}) is None
    assert supporter.parse_supporter_payload({"v": 1, "tier": "personal", "oid": "a" * 129, "iat": "2026-11-25"}) is None
    assert supporter.parse_supporter_payload({"v": 1, "tier": "personal", "oid": "ord_1", "iat": "2026-02-30"}) is None
    assert supporter.parse_supporter_payload({"v": 1, "tier": "personal", "oid": "ord_1", "iat": "2026-11-25T00:00Z"}) is None


def test_masking_helpers_never_expose_signature_or_full_order_id():
    preview = supporter.mask_key(PERSONAL_KEY)
    assert preview == "PL1.eyJpYXQiOiIyMD…"
    assert PERSONAL_KEY.split(".")[2] not in preview

    assert supporter.mask_order_id("ord_555444333") == "ord_•••••333"
    assert supporter.mask_order_id("ord_123") == "•••••123"
    assert supporter.mask_order_id("12") == "•••••12"


def test_verify_ed25519_and_pem_reject_small_order_and_non_canonical_points():
    # Identity point (0, 1) and order-2 point (0, p-1) must be rejected as weak-order public keys
    p = (1 << 255) - 19
    identity = (1).to_bytes(32, "little")
    order2 = (p - 1).to_bytes(32, "little")
    forged_sig = identity + (0).to_bytes(32, "little")
    assert supporter.verify_ed25519(identity, b"anything", forged_sig) is False
    assert supporter.verify_ed25519(order2, b"anything", forged_sig) is False

    spki_prefix = bytes.fromhex("302a300506032b6570032100")
    for weak in (identity, order2):
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            + base64.b64encode(spki_prefix + weak).decode("ascii")
            + "\n-----END PUBLIC KEY-----"
        )
        with pytest.raises(ValueError):
            supporter.parse_public_key_pem(pem)

    # Non-canonical encodings: y >= p, negative zero (x=0, sign=1), and off-curve y
    pub = supporter.parse_public_key_pem(TEST_PUB1_PEM)
    y_eq_p = p.to_bytes(32, "little")
    neg_zero = bytearray(identity)
    neg_zero[31] |= 0x80
    # Find an off-curve y (e.g. y = 2 -> u = 3, v = 4d+1; check _decode_point returns None)
    off_curve = (2).to_bytes(32, "little")
    assert supporter._decode_point(y_eq_p) is None
    assert supporter._decode_point(bytes(neg_zero)) is None
    assert supporter._decode_point(off_curve) is None
    for bad_r in (y_eq_p, bytes(neg_zero), off_curve):
        assert supporter.verify_ed25519(pub, b"msg", bad_r + (1).to_bytes(32, "little")) is False


def test_validate_supporter_key_rejects_invalid_utf8_and_non_dict_json():
    pub1 = (supporter.parse_public_key_pem(TEST_PUB1_PEM),)
    sig = _b64u(b"\x01" * 64)
    bad_utf8 = _b64u(b"\xff\xfe")
    # Invalid UTF-8 bytes
    payload, err = supporter.validate_supporter_key(f"PL1.{bad_utf8}.{sig}", public_keys=pub1)
    assert payload is None and err == "supporter_key_format"
    # Malformed JSON
    bad_json = _b64u(b"{not-json")
    payload, err = supporter.validate_supporter_key(f"PL1.{bad_json}.{sig}", public_keys=pub1)
    assert payload is None and err == "supporter_key_format"
    # Valid JSON but non-dict
    for raw in (b"[]", b'"hello"', b"123", b"null"):
        payload, err = supporter.validate_supporter_key(f"PL1.{_b64u(raw)}.{sig}", public_keys=pub1)
        assert payload is None and err == "supporter_key_format"
