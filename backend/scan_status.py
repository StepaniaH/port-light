"""Explicit scanner selection and failure semantics."""

import os


SCANNER_NAMES = ("listen", "docker", "compose")


class ScanUnavailable(Exception):
    """A source did not produce an authoritative occupancy observation."""


def enabled_scanners(raw=None) -> frozenset[str]:
    """Normalize one resolved scanner selection.

    Reading the environment remains as a compatibility fallback for direct
    callers. Application scans pass the value resolved by ``settings``.
    """
    if raw is None:
        raw = os.environ.get("PORT_LIGHT_SCANNERS", ",".join(SCANNER_NAMES))
    parts = raw.split(",") if isinstance(raw, str) else raw
    try:
        names = frozenset(str(part).strip() for part in parts if str(part).strip())
    except TypeError as exc:
        raise ScanUnavailable("PORT_LIGHT_SCANNERS must select listen, docker, or compose") from exc
    if not names or names - set(SCANNER_NAMES):
        raise ScanUnavailable("PORT_LIGHT_SCANNERS must select listen, docker, or compose")
    return names
