"""Explicit scanner selection and failure semantics."""

import os


class ScanUnavailable(Exception):
    """A source did not produce an authoritative occupancy observation."""


def enabled_scanners() -> frozenset[str]:
    names = frozenset(part.strip() for part in os.environ.get(
        "PORT_LIGHT_SCANNERS", "listen,docker,compose").split(",") if part.strip())
    if not names or names - {"listen", "docker", "compose"}:
        raise ScanUnavailable("PORT_LIGHT_SCANNERS must select listen, docker, or compose")
    return names
