"""Shared typed contracts between scanners and classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


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


class OccupancyRow(TypedDict, total=False):
    """One grid row as returned by :func:`backend.classification.classify`.

    Free-port stubs (:func:`backend.classification.free_port_payload`) emit a
    subset; every key here mirrors the JSON payload consumed by the frontend.
    """

    port: int
    status: str  # "used" | "configured" | "free"
    source_type: str  # "docker" | "system" | "host" | "manual" | "unknown"
    protocol: str
    ip: str | None
    ips: list[str]
    bind_scope: str | None  # "public" | "lan" | "link" | "localhost"
    process: str | None
    pid: int | None
    containers: list[dict]
    compose_configs: list[dict]
    manual_label: str | None
    machine: str
    known_service: dict | None
    is_hidden: bool
    conflict: bool
    urls: list[str]
