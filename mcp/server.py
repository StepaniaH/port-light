#!/usr/bin/env python3
"""Compatibility entry point; the installable MCP adapter lives in the client."""
from __future__ import annotations

import pathlib
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from port_light_client.mcp import main

if __name__ == "__main__":
    sys.exit(main())
