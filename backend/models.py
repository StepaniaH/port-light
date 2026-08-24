"""Shared typed contracts between scanners and classification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortMapping:
    """One host-side port row as reported by a scanner.

    ``source`` is ``publish`` for Docker/Compose publishes, ``expose`` for
    host-network EXPOSE, ``macvlan`` for macvlan/ipvlan LAN occupancy.
    """

    host_port: int
    host_ip: str
    container_port: int | None
    protocol: str
    source: str = "publish"
