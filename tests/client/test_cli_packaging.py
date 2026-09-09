from __future__ import annotations

import pathlib
import tomllib

from backend.main import VERSION
from port_light_client import __version__


ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_package_exposes_dependency_free_console_script():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["name"] == "port-light-cli"
    assert project["project"]["dependencies"] == []
    assert project["project"]["scripts"] == {
        "port-light": "port_light_client.cli:main",
    }
    assert project["tool"]["setuptools"]["packages"] == ["port_light_client"]


def test_container_copies_shared_client_and_console_wrapper():
    dockerfile = (ROOT / "Dockerfile").read_text()
    wrapper = (ROOT / "scripts" / "port-light").read_text()
    assert "COPY port_light_client/ ./port_light_client/" in dockerfile
    assert "COPY scripts/port-light /usr/local/bin/port-light" in dockerfile
    assert "python -m port_light_client" in wrapper
    assert "ENV PORT_LIGHT_STATE_DIR=/data/cli-state" in dockerfile


def test_product_surfaces_share_one_version():
    assert VERSION == __version__
