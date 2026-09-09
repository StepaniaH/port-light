from __future__ import annotations

from backend.netaddr import (
    binding_ips,
    clean_bind_ip,
    normalize_ip,
    prefixless,
    proto_base,
    proto_family_of,
    strip_brackets,
    strip_zone,
)


def test_strip_brackets_and_zone():
    assert strip_brackets("[::1]") == "::1"
    assert strip_brackets("0.0.0.0") == "0.0.0.0"
    assert strip_zone("fe80::1%eth0") == "fe80::1"


def test_clean_bind_ip_keeps_empty_policy_with_caller():
    assert clean_bind_ip(None) == ""
    assert clean_bind_ip("") == ""
    assert clean_bind_ip("[::1]") == "::1"
    assert clean_bind_ip("fe80::1%eth0") == "fe80::1"
    assert clean_bind_ip(" 10.0.0.5 ") == "10.0.0.5"


def test_prefixless():
    assert prefixless("10.0.0.9/24") == "10.0.0.9"
    assert prefixless("10.0.0.9") == "10.0.0.9"


def test_binding_ips_dual_stack_on_empty():
    assert binding_ips(None) == ["0.0.0.0", "::"]
    assert binding_ips("") == ["0.0.0.0", "::"]
    assert binding_ips("[::1]") == ["::1"]
    assert binding_ips("127.0.0.1") == ["127.0.0.1"]


def test_normalize_ip_forms():
    assert normalize_ip("::ffff:127.0.0.1") == "127.0.0.1"
    assert normalize_ip("*") == "0.0.0.0"
    assert normalize_ip("fe80::1%eth0") == "fe80::1"
    assert normalize_ip("::ffff:c0a8:10a") == "192.168.1.10"
    assert normalize_ip("0.0.0.0") == "0.0.0.0"
    assert normalize_ip("::") == "::"
    assert normalize_ip("") == "0.0.0.0"


def test_proto_bases_and_families():
    assert proto_base("UDP") == "udp"
    assert proto_base(None) == "tcp"
    # Bug-compatible with the scanners' previous helper: a port/proto spec
    # yields its first segment; callers pass bare protocols.
    assert proto_base("53/udp") == "53"
    assert proto_family_of("udp6") == "udp"
    assert proto_family_of("SCTP") == "sctp"
    assert proto_family_of("tcp") == "tcp"
