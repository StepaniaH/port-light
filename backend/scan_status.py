"""Explicit scanner selection and failure semantics."""

import os


SCANNER_NAMES = ("listen", "docker", "compose")


class ScanUnavailable(Exception):
    """A source did not produce an authoritative occupancy observation."""


def normalize_scanners(raw) -> frozenset[str]:
    """Validate the shared environment, saved-setting, and API selection."""
    message = "local_scanners / PORT_LIGHT_SCANNERS must select listen, docker, or compose"
    if not isinstance(raw, (str, list, tuple, set, frozenset)):
        raise ValueError(message)
    parts = raw.split(",") if isinstance(raw, str) else raw
    names = frozenset(str(part).strip() for part in parts if str(part).strip())
    if not names or names - set(SCANNER_NAMES):
        raise ValueError(message)
    return names


def enabled_scanners(raw=None) -> frozenset[str]:
    """Normalize one resolved scanner selection.

    Reading the environment remains as a compatibility fallback for direct
    callers. Application scans pass the value resolved by ``settings``.
    """
    if raw is None:
        raw = os.environ.get("PORT_LIGHT_SCANNERS", ",".join(SCANNER_NAMES))
    try:
        return normalize_scanners(raw)
    except ValueError as exc:
        raise ScanUnavailable(str(exc)) from exc
