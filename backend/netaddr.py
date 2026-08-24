"""Shared IP-address and protocol normalization primitives.

Each scanner used to keep its own bracket/zone/wildcard stripper; the
variants drifted, and bind-scope / protocol bugs followed. These helpers
carry no caller policy — what an empty string means is up to the caller.
"""

from __future__ import annotations

import ipaddress


def strip_brackets(text: str) -> str:
    """``[::1]`` → ``::1``. Anything else passes through untouched."""
    if text.startswith("[") and text.endswith("]") and ":" in text:
        return text[1:-1]
    return text


def strip_zone(text: str) -> str:
    """``fe80::1%eth0`` → ``fe80::1``."""
    return text.split("%", 1)[0]


def clean_bind_ip(raw) -> str:
    """Coerce to str, trim, drop brackets and zone ids. Empty stays empty."""
    text = str(raw or "").strip()
    if not text:
        return ""
    return strip_zone(strip_brackets(text))


def prefixless(raw) -> str:
    """Drop a CIDR prefix length: ``10.0.0.9/24`` → ``10.0.0.9``."""
    text = str(raw or "").strip()
    if "/" in text:
        return text.split("/", 1)[0].strip()
    return text


def binding_ips(raw) -> list[str]:
    """Docker ``HostIp`` → concrete bind addresses; empty means dual-stack."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return ["0.0.0.0", "::"]
    text = strip_brackets(text)
    return [text] if text else ["0.0.0.0", "::"]


def normalize_ip(ip: str) -> str:
    """Collapse wildcards, zone ids, IPv4-mapped IPv6, and ``::`` to one form."""
    if not ip:
        return "0.0.0.0"
    text = ip.strip()
    if text == "*":
        return "0.0.0.0"
    text = strip_zone(text)
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    if addr.is_unspecified:
        return "0.0.0.0" if addr.version == 4 else "::"
    return str(addr)


def proto_base(proto) -> str:
    """Protocol spelling lowercased; defaults to tcp.

    A ``port/proto`` spec yields its first segment — bug-compatible with the
    scanners' previous behaviour. Callers pass bare protocols.
    """
    text = str(proto or "tcp").strip().lower() or "tcp"
    if "/" in text:
        text = text.split("/", 1)[0] or "tcp"
    return text


def proto_family_of(proto) -> str:
    """Fold protocol spellings onto the tcp / udp / sctp family."""
    base = proto_base(proto).replace("6", "")
    if base.startswith("udp"):
        return "udp"
    if base.startswith("sctp"):
        return "sctp"
    return "tcp"
