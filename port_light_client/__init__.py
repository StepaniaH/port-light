"""Shared client for Port-Light command-line and MCP adapters."""

from .client import PortLightClient, PortLightError, compact_port

__all__ = ["PortLightClient", "PortLightError", "compact_port"]
__version__ = "0.8.3"
